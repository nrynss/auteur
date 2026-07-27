"""Content-addressed cache for analysis results.

The same idea as `sfx_cache`, applied to the VLM step. Analysis is now the
scarcest resource in the system: Gemini's free tier allows 20 requests per day
on the full Flash models, so re-analyzing a clip you have already analyzed is
the most expensive mistake available.

The key is the *video's* content hash, not its path or name. Re-uploading the
same footage under a new project, or re-running after a crash, hits cache.

## What invalidates an entry

`(video_sha256, model_id, prompt_fingerprint)`. The prompt is part of the key
because TIMELINE_PROMPT is the thing most likely to change during tuning, and a
timeline produced by an older prompt is not interchangeable with a new one —
silently reusing it would make prompt changes look like no-ops.

Model is in the key so a Flash-Lite timeline and a Flash timeline coexist rather
than overwrite. That matters while benchmarking Lite against Flash for timing
precision.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from typing import Callable, Optional, Tuple

from foley.models import Timeline
from foley.timeline_io import dict_to_timeline, timeline_to_dict
from foleyflow.storage.base import Backend

CACHE_PREFIX = "timelines"
_HASH_CHUNK = 1 << 20


def file_sha256(path: str) -> str:
    """Full-content hash. Chunked so a 200 MB source does not land in memory."""
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(_HASH_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def prompt_fingerprint(prompt: str) -> str:
    """Short, stable digest of the analysis prompt.

    Whitespace is normalized so reflowing a docstring does not invalidate every
    cached timeline, while any change to the actual wording does.
    """
    normalized = " ".join(prompt.split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]


def cache_key(video_sha256: str, model_id: str, prompt_fp: str) -> str:
    raw = f"{video_sha256}|{model_id}|{prompt_fp}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def cache_object_key(video_sha256: str, model_id: str, prompt_fp: str) -> str:
    return f"{CACHE_PREFIX}/{cache_key(video_sha256, model_id, prompt_fp)}.json"


def get_or_analyze(
    video_path: str,
    analyze_fn: Callable[[], Timeline],
    *,
    backend: Backend,
    model_id: str,
    prompt: str,
    work_dir: Optional[str] = None,
    force: bool = False,
) -> Tuple[Timeline, bool]:
    """Return `(timeline, was_cached)`, analyzing only on a miss.

    analyze_fn is a zero-argument callable so this module never needs to know
    how analysis is invoked — it stays out of the VLM's argument shapes.

    force=True bypasses the read but still writes, which is what a user asking
    to re-analyze wants: a fresh result that subsequent runs can reuse.
    """
    video_hash = file_sha256(video_path)
    fingerprint = prompt_fingerprint(prompt)
    key = cache_object_key(video_hash, model_id, fingerprint)
    work_dir = work_dir or os.path.join(tempfile.gettempdir(), "foleyflow", "work")
    os.makedirs(work_dir, exist_ok=True)
    local_path = os.path.join(work_dir, os.path.basename(key))

    if not force and backend.exists(key):
        backend.get(key, local_path)
        with open(local_path, "r", encoding="utf-8") as f:
            payload = json.load(f)
        return dict_to_timeline(payload["timeline"]), True

    timeline = analyze_fn()

    payload = {
        "cache_version": 1,
        "video_sha256": video_hash,
        "model_id": model_id,
        "prompt_fingerprint": fingerprint,
        "timeline": timeline_to_dict(timeline),
    }
    with open(local_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    backend.put(key, local_path, content_type="application/json")

    return timeline, False
