"""SFX generation + cache — T2.

get_sfx(request, cache_dir="sfx_cache") -> path to a canonical WAV.

Flow: cache lookup -> miss: `stems.generate_one` (a Genblaze pipeline of one
step, with §9.1 tier escalation) -> ffmpeg-convert to canonical 48 kHz stereo
float32 WAV -> RMS-normalize to -18 dBFS (peak-guarded) -> move into the cache
-> return the cache path.

The provider call goes through Genblaze here too, not only in the project lane
(`sfx_cache`). Keeping one orchestrated path and one unorchestrated one would
mean the CLI and the API exercised different code, and the difference would
only show up when one of them broke.

Cache key: sha256(f"{endpoint}|{prompt}|{duration_s:.2f}") — stable across
runs; filename is the first 16 hex chars + ".wav". Same request always
resolves to the same file; a changed endpoint, prompt, or duration (rounded
to 2 decimals) changes the key and generates a fresh clip. Re-running the
pipeline against an already-populated cache costs $0 (no ledger rows).

A model's ``extra_arguments`` from providers.toml join the key when it declares
any, because they change the audio: generating at num_inference_steps=50 and
then at 100 must not resolve to the same file. They are looked up here rather
than passed in, so every call site agrees without threading an argument through
pipeline.py and sfx_cache.py — a missed site would silently report a cache miss.
Models that declare no extras keep byte-identical keys, so nothing already
cached is orphaned.
"""

import hashlib
import json
import os
import shutil
import subprocess
import tempfile

import numpy as np
import soundfile as sf

# Imported for the module attribute, not for a call site here: providers
# materialize their own bytes (see providers/genblaze._materialize) and reach
# net.download through this same module object.
from foleyflow import net  # noqa: F401
from foleyflow.routing import SfxRequest

# Numeric defaults (locked) — build_spec.md "Numeric defaults" table.
RMS_TARGET_DBFS = -18.0
PEAK_GUARD_DBFS = -1.0

# Canonical audio format everywhere (build_spec.md "Architecture").
CANONICAL_SR = 48000
CANONICAL_CHANNELS = 2

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DEFAULT_CACHE_DIR = "sfx_cache"


def build_arguments(request: SfxRequest) -> dict:
    """Config-driven argument dict for execute_with_ledger.

    Reads ``prompt_field``, ``duration_field``, ``duration_type``, and
    ``extra_arguments`` from providers.toml for the request's endpoint.
    Falls back to the prompt/duration shape for any endpoint not declared
    in config.
    """
    from foleyflow.providers.config import get_model_spec

    try:
        spec = get_model_spec(request.endpoint)
    except KeyError:
        # Unknown endpoint: fall back to the generic shape
        return {"prompt": request.prompt, "duration": request.duration_s}

    prompt_field = spec.prompt_field or "prompt"
    duration_field = spec.duration_field or "duration"
    duration_type = spec.duration_type or "float"

    if duration_type == "int":
        duration_val = int(round(request.duration_s))
    else:
        duration_val = request.duration_s

    args = {prompt_field: request.prompt, duration_field: duration_val}

    # Merge any static extra_arguments from config
    if spec.extra_arguments:
        args.update(spec.extra_arguments)

    return args


def _extras_segment(endpoint: str) -> str:
    """Canonical '|x={...}' segment for a model's extra_arguments, or ''.

    Sorted keys and compact separators so the segment is stable across runs and
    across TOML key ordering. An unknown endpoint (or unreadable config) yields
    '' rather than raising: the cache must keep working for endpoints that are
    not declared, exactly as build_arguments falls back for them.
    """
    try:
        from foleyflow.providers.config import get_model_spec
        extras = get_model_spec(endpoint).extra_arguments
    except Exception:
        return ""
    if not extras:
        return ""
    return "|x=" + json.dumps(extras, sort_keys=True, separators=(",", ":"))


def cache_key_raw(endpoint: str, prompt: str, duration_s: float,
                  variant: str = "") -> str:
    """The exact string hashed into a cache key. Shared with sfx_cache so the
    local and B2 caches cannot drift apart.

    Segment order is load-bearing: extras before the variant salt, and both
    omitted when empty, so a model with no extras and no variant hashes the
    original 'endpoint|prompt|duration' and keeps its existing objects.
    """
    raw = f"{endpoint}|{prompt}|{duration_s:.2f}{_extras_segment(endpoint)}"
    if variant:
        raw = f"{raw}|v={variant}"
    return raw


def cache_key(endpoint: str, prompt: str, duration_s: float) -> str:
    """sha256 over cache_key_raw(...), first 16 hex chars. Documented, stable
    cache key rule — see module docstring."""
    raw = cache_key_raw(endpoint, prompt, duration_s)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def cache_path(cache_dir: str, endpoint: str, prompt: str, duration_s: float) -> str:
    return os.path.join(cache_dir, f"{cache_key(endpoint, prompt, duration_s)}.wav")


def _resolve_cache_dir(cache_dir: str) -> str:
    return cache_dir if os.path.isabs(cache_dir) else os.path.join(REPO_ROOT, cache_dir)


def extract_audio_url(result) -> str:
    """Tolerant extraction: result['audio'] or result['audio_file'] (dict
    with 'url'), else result['url']. Raises with the raw result if no URL
    can be found — surfaces the actual fal response shape for debugging.

    This is a thin wrapper over the adapter's extraction for backward
    compatibility with callers (sfx_cache.py, tests).
    """
    from foleyflow.providers.fal import extract_audio_url as _extract
    url = _extract(result)
    if url is not None:
        return url
    raise ValueError(f"sfx.py: no audio URL found in fal response: {result!r}")


def convert_to_canonical(src_path: str, dest_path: str) -> None:
    """ffmpeg -> 48 kHz stereo float32 WAV. Mono sources are duplicated to
    L/R by -ac 2 (satisfies the mono->L/R rule; no fake stereo width)."""
    cmd = [
        "ffmpeg", "-y", "-i", src_path,
        "-ar", str(CANONICAL_SR), "-ac", str(CANONICAL_CHANNELS),
        "-c:a", "pcm_f32le", dest_path,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffmpeg conversion failed ({src_path} -> {dest_path}): {proc.stderr}"
        )


def rms_normalize(path: str, target_dbfs: float = RMS_TARGET_DBFS,
                   peak_guard_dbfs: float = PEAK_GUARD_DBFS) -> None:
    """Load, compute RMS over both channels, scale to target_dbfs. Peak-guard:
    if the resulting peak would exceed peak_guard_dbfs, scale down to that
    peak instead (prints a warning). Writes float32 in place."""
    data, sr = sf.read(path, dtype="float32", always_2d=True)
    rms = float(np.sqrt(np.mean(np.square(data))))
    if rms < 1e-12:
        print(f"[sfx] WARNING: near-silent clip, skipping RMS normalize: {path}")
        return

    target_rms = 10.0 ** (target_dbfs / 20.0)
    gain = target_rms / rms
    normalized = data * gain

    peak = float(np.max(np.abs(normalized)))
    peak_guard_linear = 10.0 ** (peak_guard_dbfs / 20.0)
    if peak > peak_guard_linear:
        achieved_dbfs = 20.0 * np.log10(peak) if peak > 0 else float("-inf")
        print(f"[sfx] WARNING: RMS-normalized peak {achieved_dbfs:.1f} dBFS exceeds "
              f"peak guard {peak_guard_dbfs} dBFS ({os.path.basename(path)}) — "
              f"scaling down to the guard instead.")
        normalized = normalized * (peak_guard_linear / peak)

    sf.write(path, normalized.astype(np.float32), sr, subtype="FLOAT")


def get_sfx(request: SfxRequest, cache_dir: str = DEFAULT_CACHE_DIR) -> str:
    """Cache lookup -> generate (Genblaze) -> convert -> normalize -> cache ->
    return path to the canonical WAV."""
    from foleyflow.stems import generate_one

    cache_dir_abs = _resolve_cache_dir(cache_dir)
    os.makedirs(cache_dir_abs, exist_ok=True)
    dest_path = cache_path(cache_dir_abs, request.endpoint, request.prompt, request.duration_s)

    if os.path.exists(dest_path):
        return dest_path

    stem = generate_one(request)

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_wav = os.path.join(tmp_dir, "converted.wav")
        convert_to_canonical(stem.local_path, tmp_wav)
        rms_normalize(tmp_wav)

        # shutil.move (not os.replace): the repo (cache_dir) and the OS temp
        # dir can sit on different Windows drives, where a plain rename
        # raises cross-device errors — shutil.move falls back to copy+delete.
        shutil.move(tmp_wav, dest_path)

    return dest_path
