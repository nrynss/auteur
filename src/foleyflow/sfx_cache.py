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
import threading
from typing import Callable, Optional

from foleyflow.routing import SfxRequest
from foleyflow.sfx import convert_to_canonical, rms_normalize
from foleyflow.storage.base import Backend

CACHE_PREFIX = "cache"

# The tier that served the most recent generation on THIS thread.
#
# `generate_canonical_wav(request, dest_path) -> path` is the one billed seam in
# the render path, and every test in the suite stands in for it by name. Adding
# an out-parameter to its signature would break each of those stand-ins, and
# returning a richer object would break every caller. A thread-local is the
# narrow way to let `backed_sfx_fn` report which tier actually answered without
# widening a contract the whole suite depends on; renders run one per worker
# thread, so there is no cross-talk. Absent (None) whenever generation was
# stubbed out, which is exactly the honest answer in that case.
_last_generation = threading.local()


def cache_key(endpoint: str, prompt: str, duration_s: float, variant: str = "") -> str:
    """sha256 over foleyflow.sfx.cache_key_raw(...), first 16 hex chars.

    With variant="" this is identical to foleyflow.sfx.cache_key — the salt segment
    is appended only when a variant is actually requested, so existing cache
    objects keep resolving. Do not "tidy" this by always appending the segment.

    Delegates the raw string to sfx rather than rebuilding it, so the two caches
    cannot disagree about what a key covers (they previously duplicated the
    format, which is how the model's extra_arguments could have been added to
    one and not the other).
    """
    from foleyflow.sfx import cache_key_raw

    raw = cache_key_raw(endpoint, prompt, duration_s, variant)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def cache_object_key(endpoint: str, prompt: str, duration_s: float, variant: str = "") -> str:
    return f"{CACHE_PREFIX}/{cache_key(endpoint, prompt, duration_s, variant)}.wav"


def is_cached(backend: Backend, work_dir: str, request: SfxRequest,
              variant: str = "") -> bool:
    """Whether prewarm / mix would treat this request as a cache hit.

    A non-empty local work-dir copy counts even when B2 was never written
    (stale work dir from an interrupted render) — same rule as prewarm's
    dry-run probe, so the projection and the bill cannot disagree.
    """
    key = cache_object_key(request.endpoint, request.prompt,
                           request.duration_s, variant)
    local = os.path.join(work_dir, os.path.basename(key))
    if os.path.exists(local) and os.path.getsize(local) > 0:
        return True
    return backend.exists(key)


def project_generation_cost(requests) -> float:
    """Sum ``estimate_cost`` for each request, with full duration args.

    Per-second models price on duration; passing ``{}`` would under-report.
    Only misses should be passed in — cache hits cost nothing.
    """
    from foleyflow.ledger import estimate_cost
    from foleyflow.sfx import build_arguments

    return sum(
        estimate_cost(r.endpoint, build_arguments(r)) for r in requests
    )


def project_mix_cost(backend: Backend, work_dir: str, requests,
                     variant: str = "",
                     variant_fn: Optional[Callable[[SfxRequest], str]] = None) -> dict:
    """Pre-flight mix projection: hits vs misses and miss cost only.

    Reused by ``foleyflow prewarm --dry-run``, the mix route, and the repair
    route so all three say the same thing. Label the figure a projection: a
    fallback escalation mid-flight can change the price.

    ``variant`` salts every request's probe address (mix route); ``variant_fn``
    salts per request (the repair route, where exactly one stem carries the
    nonce and the rest probe at their plain addresses). When both are given,
    ``variant_fn`` wins.
    """
    hits, misses = [], []
    for request in requests:
        v = (variant_fn(request) if variant_fn is not None else "") or variant
        (hits if is_cached(backend, work_dir, request, v)
         else misses).append(request)
    projected = project_generation_cost(misses)
    return {
        "stem_count": len(requests),
        "hits": len(hits),
        "misses": len(misses),
        "projected_cost_usd": round(projected, 4),
    }


def last_generation():
    """The `StemResult` from this thread's most recent `generate_canonical_wav`.

    None when nothing has generated on this thread yet, or when generation was
    stubbed. Reporting only — never load-bearing.
    """
    return getattr(_last_generation, "result", None)


def generate_canonical_wav(request: SfxRequest, dest_path: str) -> str:
    """Generate one SFX clip and write it to dest_path as a canonical
    48 kHz stereo float32 WAV, RMS-normalized to -18 dBFS.

    This is the billed path, and it runs through Genblaze: `stems.generate_one`
    builds a one-step pipeline so a single stem is timed, retried, and recorded
    exactly like one inside a fan-out. It then reuses the media core's helpers
    verbatim — ffmpeg canonicalization, normalization — so a clip generated here
    is indistinguishable from one the CLI produced.

    ## Escalation and the cache address

    If the request's tier fails, §9.1's fallback tier serves it — but the clip
    is still cached at the *requested* address, not the serving tier's. The
    cache answers "what was asked for"; which tier answered is provenance, and
    it is recorded on the stem inventory and in the ledger. The practical effect
    is the right one: during a primary-provider outage the fallback's clip is
    reused instead of re-hammering the dead endpoint on every render.
    """
    from foleyflow.stems import generate_one

    stem = generate_one(request)
    _last_generation.result = stem

    os.makedirs(os.path.dirname(os.path.abspath(dest_path)), exist_ok=True)
    # Every step happens on a temp path, and the finished file is moved into
    # place only on success. Writing ffmpeg's output straight to dest_path
    # means a failure part-way through leaves a truncated file at the canonical
    # location — and `_sfx_fn`'s first cache test is os.path.exists, so every
    # later render would treat that fragment as a hit and mix corrupt audio.
    # foley/sfx.py (the code this was ported from) got this right; the rewrite
    # dropped it.
    with tempfile.TemporaryDirectory() as tmp_dir:
        converted = os.path.join(tmp_dir, "converted.wav")
        convert_to_canonical(stem.local_path, converted)
        rms_normalize(converted)
        # shutil.move, not os.replace: the temp dir and the work dir can sit on
        # different Windows drives, where rename raises a cross-device error.
        shutil.move(converted, dest_path)
    return dest_path


def prewarm(
    backend: Backend,
    work_dir: str,
    requests,
    variant_fn: Optional[Callable[[SfxRequest], str]] = None,
    on_event: Optional[Callable[[dict], None]] = None,
    max_concurrency: Optional[int] = None,
):
    """Generate every uncached request concurrently, through Genblaze.

    `assemble_timeline` pulls stems one at a time — correct for the mixer, and
    the reason generation used to be serial. This warms the cache first, so the
    independent stems of a timeline fan out across one Genblaze run (SPEC §8.2)
    and assembly afterwards is pure cache hits.

    Returns the list of `StemResult`s for the requests that were NOT already
    cached, so the caller can record which tier served each one and how many
    escalated. Never raises: a stem that could not be produced comes back with
    `ok == False`, and `backed_sfx_fn` will try it once more (and fail loudly
    there) when assembly asks for it. That keeps the render's failure semantics
    exactly where they were rather than moving them into a warm-up step.

    Cache keys are untouched. Each request is probed at, and stored under, the
    address it already had — this is a scheduling change, not an addressing one.
    """
    from foleyflow.stems import DEFAULT_MAX_CONCURRENCY, generate_many

    variant_fn = variant_fn or (lambda _req: "")
    os.makedirs(work_dir, exist_ok=True)

    def _paths(request: SfxRequest):
        variant = variant_fn(request) or ""
        key = cache_object_key(request.endpoint, request.prompt,
                               request.duration_s, variant)
        return key, os.path.join(work_dir, os.path.basename(key))

    misses = []
    for request in requests:
        key, local_path = _paths(request)
        if os.path.exists(local_path) and os.path.getsize(local_path) > 0:
            continue
        if backend.exists(key):
            backend.get(key, local_path)
            continue
        misses.append(request)

    if not misses:
        return []

    # No `probe`: every request here is already a confirmed miss at its own
    # address, and an escalated request lands at a different address that
    # `generate_many` cannot check without knowing this render's variant. The
    # loop below stores whatever comes back at the *requested* address, which
    # is the same rule `generate_canonical_wav` documents.
    results = generate_many(
        misses, on_event=on_event,
        max_concurrency=max_concurrency or DEFAULT_MAX_CONCURRENCY,
        run_name="foleyflow-stems")

    for request, result in zip(misses, results):
        if not result.ok:
            continue
        key, local_path = _paths(request)
        with tempfile.TemporaryDirectory() as tmp_dir:
            converted = os.path.join(tmp_dir, "converted.wav")
            convert_to_canonical(result.local_path, converted)
            rms_normalize(converted)
            shutil.move(converted, local_path)
        backend.put(key, local_path, content_type="audio/wav")

    return results


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
                 {"key", "endpoint", "variant", "cached": bool}, plus
                 {"tier", "served_by", "genblaze_run_id"} once a generation has
                 actually happened.
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
                 "variant": variant, "cached": False, "tier": request.tier})
        # Cleared first: the thread-local outlives the call, so a stubbed
        # generator would otherwise let a previous stem's provenance be
        # reported as this one's.
        _last_generation.result = None
        generate_canonical_wav(request, local_path)
        backend.put(key, local_path, content_type="audio/wav")

        # Which tier actually answered, once it is known. Emitted as a second
        # event rather than folded into the first: the first has to fire
        # *before* generation so a UI can show "generating" while it happens.
        served = last_generation()
        if served is not None:
            _notify({"key": key, "endpoint": request.endpoint,
                     "variant": variant, "cached": False,
                     "tier": request.tier, "served_by": served.tier,
                     "served_endpoint": served.endpoint,
                     "attempts": [a.to_dict() for a in served.attempts]})
        return local_path

    return _sfx_fn
