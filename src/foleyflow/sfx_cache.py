"""Storage-backed SFX acquisition — the seam into the media core.

`assemble_timeline(..., sfx_fn=...)` already takes injected SFX acquisition, so
FoleyFlow swaps in shared, B2-backed, content-addressed caching without editing
a line of mix math. `foleyflow.sfx.get_sfx` remains the standalone-CLI path.

## The variant nonce

The media core keys its cache on (endpoint, prompt, duration). That is exactly
right for the normal path — the same request should never be billed twice — but
it silently breaks SPEC.md §5's "Targeted repair". A user clicking *Regenerate
this event* with an unchanged prompt would hit the cache and get the byte-
identical stem back, having been told a new take was produced. Worse, it fails
invisibly: same audio, new version number, satisfied UI.

So a caller may attach a `variant` string to a request. Empty variant (the
default) reproduces the upstream key *byte for byte*, so cache entries generated
by the prior pipeline stay valid. A non-empty variant salts the key, forcing a
genuine new generation that is then cached under its own address — meaning v2 is
itself reusable, and re-requesting v2 is free.
"""

import hashlib
import os
import shutil
import tempfile
from typing import Callable, Optional

from foleyflow import net
from foleyflow.ledger import execute_with_ledger
from foleyflow.routing import SfxRequest
from foleyflow.sfx import (
    build_arguments,
    convert_to_canonical,
    extract_audio_url,
    rms_normalize,
)
from foleyflow.storage.base import Backend

CACHE_PREFIX = "cache"


def cache_key(endpoint: str, prompt: str, duration_s: float, variant: str = "") -> str:
    """sha256 over 'endpoint|prompt|duration', first 16 hex chars.

    With variant="" this is identical to foleyflow.sfx.cache_key — the salt segment
    is appended only when a variant is actually requested, so existing cache
    objects keep resolving. Do not "tidy" this by always appending the segment.
    """
    raw = f"{endpoint}|{prompt}|{duration_s:.2f}"
    if variant:
        raw = f"{raw}|v={variant}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def cache_object_key(endpoint: str, prompt: str, duration_s: float, variant: str = "") -> str:
    return f"{CACHE_PREFIX}/{cache_key(endpoint, prompt, duration_s, variant)}.wav"


def generate_canonical_wav(request: SfxRequest, dest_path: str) -> str:
    """Generate one SFX clip and write it to dest_path as a canonical
    48 kHz stereo float32 WAV, RMS-normalized to -18 dBFS.

    This is the billed path. It reuses the media core's helpers verbatim —
    argument shaping, response parsing, ffmpeg canonicalization, normalization —
    so a clip generated here is indistinguishable from one the CLI produced.
    """
    result = execute_with_ledger(request.endpoint, arguments=build_arguments(request))
    audio_url = extract_audio_url(result)

    os.makedirs(os.path.dirname(os.path.abspath(dest_path)), exist_ok=True)
    # Every step happens on a temp path, and the finished file is moved into
    # place only on success. Writing ffmpeg's output straight to dest_path
    # means a failure part-way through leaves a truncated file at the canonical
    # location — and `_sfx_fn`'s first cache test is os.path.exists, so every
    # later render would treat that fragment as a hit and mix corrupt audio.
    # foley/sfx.py (the code this was ported from) got this right; the rewrite
    # dropped it.
    with tempfile.TemporaryDirectory() as tmp_dir:
        raw_path = os.path.join(tmp_dir, "raw_download")
        net.download(audio_url, raw_path)
        converted = os.path.join(tmp_dir, "converted.wav")
        convert_to_canonical(raw_path, converted)
        rms_normalize(converted)
        # shutil.move, not os.replace: the temp dir and the work dir can sit on
        # different Windows drives, where rename raises a cross-device error.
        shutil.move(converted, dest_path)
    return dest_path


def backed_sfx_fn(
    backend: Backend,
    work_dir: str,
    variant_fn: Optional[Callable[[SfxRequest], str]] = None,
    on_event: Optional[Callable[[dict], None]] = None,
) -> Callable[[SfxRequest], str]:
    """Build an sfx_fn for `assemble_timeline`.

    backend    — where canonical clips live (LocalBackend or B2Backend).
    work_dir   — local scratch the mixer reads from; soundfile needs a real path.
    variant_fn — request -> variant string. Return "" for the shared cache entry
                 (the default). A repair router closes over the event being
                 regenerated and returns e.g. "r2" for that request only, so one
                 stem regenerates and every other event still hits cache.
    on_event   — optional observer for stage reporting. Payloads carry
                 {"key", "endpoint", "variant", "cached": bool}.
    """
    variant_fn = variant_fn or (lambda _req: "")
    os.makedirs(work_dir, exist_ok=True)

    def _notify(payload: dict) -> None:
        if on_event is None:
            return
        try:
            on_event(payload)
        except Exception:
            # An observer must never break a render mid-mix.
            pass

    def _sfx_fn(request: SfxRequest) -> str:
        variant = variant_fn(request) or ""
        key = cache_object_key(request.endpoint, request.prompt, request.duration_s, variant)
        local_path = os.path.join(work_dir, os.path.basename(key))

        # A previous event in this same render may already have pulled it.
        # Size-checked: a zero-length file is a failed write, not a cache entry.
        if os.path.exists(local_path) and os.path.getsize(local_path) > 0:
            _notify({"key": key, "endpoint": request.endpoint,
                     "variant": variant, "cached": True})
            return local_path

        if backend.exists(key):
            _notify({"key": key, "endpoint": request.endpoint,
                     "variant": variant, "cached": True})
            return backend.get(key, local_path)

        _notify({"key": key, "endpoint": request.endpoint,
                 "variant": variant, "cached": False})
        generate_canonical_wav(request, local_path)
        backend.put(key, local_path, content_type="audio/wav")
        return local_path

    return _sfx_fn
