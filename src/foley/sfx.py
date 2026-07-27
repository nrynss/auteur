"""SFX generation + cache — T2.

get_sfx(request, cache_dir="sfx_cache") -> path to a canonical WAV.

Flow: cache lookup -> miss: execute_with_ledger(endpoint, arguments) ->
extract audio URL from the fal response -> net.download() to a temp file ->
ffmpeg-convert to canonical 48 kHz stereo float32 WAV -> RMS-normalize to
-18 dBFS (peak-guarded) -> move into the cache -> return the cache path.

Cache key: sha256(f"{endpoint}|{prompt}|{duration_s:.2f}") — stable across
runs; filename is the first 16 hex chars + ".wav". Same request always
resolves to the same file; a changed endpoint, prompt, or duration (rounded
to 2 decimals) changes the key and generates a fresh clip. Re-running the
pipeline against an already-populated cache costs $0 (no ledger rows).
"""

import hashlib
import os
import shutil
import subprocess
import tempfile

import numpy as np
import soundfile as sf

from foley import net
from foley.ledger import execute_with_ledger
from foley.routing import SfxRequest

# Numeric defaults (locked) — build_spec.md "Numeric defaults" table.
RMS_TARGET_DBFS = -18.0
PEAK_GUARD_DBFS = -1.0

# Canonical audio format everywhere (build_spec.md "Architecture").
CANONICAL_SR = 48000
CANONICAL_CHANNELS = 2

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DEFAULT_CACHE_DIR = "sfx_cache"

# Per-endpoint argument shapes — known-working from scratch/run_spike.py.
# New endpoints are one entry here.
_ARG_BUILDERS = {
    "fal-ai/elevenlabs/sound-effects/v2": lambda prompt, duration_s: {
        "text": prompt,
        "duration_seconds": duration_s,  # float; docs allow 0.5-22
    },
    "cassetteai/sound-effects-generator": lambda prompt, duration_s: {
        "prompt": prompt,
        "duration": int(round(duration_s)),  # docs: integer seconds
    },
    "fal-ai/mmaudio-v2/text-to-audio": lambda prompt, duration_s: {
        "prompt": prompt,
        "duration": duration_s,  # float; docs default 8
    },
}


def build_arguments(request: SfxRequest) -> dict:
    """Per-endpoint argument dict for execute_with_ledger. Falls back to the
    prompt/duration shape (cassetteai/mmaudio's) for any endpoint not yet
    special-cased above."""
    builder = _ARG_BUILDERS.get(request.endpoint)
    if builder is not None:
        return builder(request.prompt, request.duration_s)
    return {"prompt": request.prompt, "duration": request.duration_s}


def cache_key(endpoint: str, prompt: str, duration_s: float) -> str:
    """sha256 over f'{endpoint}|{prompt}|{duration_s:.2f}', first 16 hex
    chars. Documented, stable cache key rule — see module docstring."""
    raw = f"{endpoint}|{prompt}|{duration_s:.2f}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def cache_path(cache_dir: str, endpoint: str, prompt: str, duration_s: float) -> str:
    return os.path.join(cache_dir, f"{cache_key(endpoint, prompt, duration_s)}.wav")


def _resolve_cache_dir(cache_dir: str) -> str:
    return cache_dir if os.path.isabs(cache_dir) else os.path.join(REPO_ROOT, cache_dir)


def extract_audio_url(result) -> str:
    """Tolerant extraction: result['audio'] or result['audio_file'] (dict
    with 'url'), else result['url']. Raises with the raw result if no URL
    can be found — surfaces the actual fal response shape for debugging."""
    if isinstance(result, dict):
        audio_info = result.get("audio") or result.get("audio_file")
        if isinstance(audio_info, dict) and audio_info.get("url"):
            return audio_info["url"]
        if result.get("url"):
            return result["url"]
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
    """Cache lookup -> generate (execute_with_ledger) -> download -> convert
    -> normalize -> cache -> return path to the canonical WAV."""
    cache_dir_abs = _resolve_cache_dir(cache_dir)
    os.makedirs(cache_dir_abs, exist_ok=True)
    dest_path = cache_path(cache_dir_abs, request.endpoint, request.prompt, request.duration_s)

    if os.path.exists(dest_path):
        return dest_path

    arguments = build_arguments(request)
    result = execute_with_ledger(request.endpoint, arguments=arguments)
    audio_url = extract_audio_url(result)

    with tempfile.TemporaryDirectory() as tmp_dir:
        raw_path = os.path.join(tmp_dir, "raw_download")
        net.download(audio_url, raw_path)

        tmp_wav = os.path.join(tmp_dir, "converted.wav")
        convert_to_canonical(raw_path, tmp_wav)
        rms_normalize(tmp_wav)

        # shutil.move (not os.replace): the repo (cache_dir) and the OS temp
        # dir can sit on different Windows drives, where a plain rename
        # raises cross-device errors — shutil.move falls back to copy+delete.
        shutil.move(tmp_wav, dest_path)

    return dest_path
