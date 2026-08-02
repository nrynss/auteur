#!/usr/bin/env python3
"""Seed the shared B2 timeline cache with a validated sample timeline.

The deployed API has no --from-timeline route. Analysis always goes through
get_or_analyze (video_sha256 + model + TIMELINE_PROMPT fingerprint). Putting a
hand-authored timeline at that key makes create --analyze --wait reuse it and
spend zero Gemini quota — the online equivalent of offline --from-timeline.

Usage:
  uv run python scripts/seed_timeline_cache.py \\
    --video /path/to/clip.mp4 \\
    --timeline samples/timelines/duel_kling.json

Requires FOLEYFLOW_CACHE_BACKEND / B2_* (or local cache) like every other
storage client in this repo. Does not upload project artifacts.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", required=True, help="Exact bytes that will be uploaded")
    parser.add_argument("--timeline", required=True, help="Validated sample timeline JSON")
    parser.add_argument(
        "--model",
        default=None,
        help="Analysis model id (default: providers.toml default_vlm)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite an existing cache entry (default: leave it)",
    )
    args = parser.parse_args(argv)

    if not os.path.isfile(args.video):
        print(f"error: no such video: {args.video}", file=sys.stderr)
        return 2
    if not os.path.isfile(args.timeline):
        print(f"error: no such timeline: {args.timeline}", file=sys.stderr)
        return 2

    # Late imports so --help works without storage config.
    from foleyflow.analyze import DEFAULT_MODEL, TIMELINE_PROMPT
    from foleyflow.storage import get_backend
    from foleyflow.timeline_cache import (
        cache_object_key,
        file_sha256,
        prompt_fingerprint,
    )
    from foleyflow.timeline_io import load_timeline, timeline_to_dict

    model = args.model or DEFAULT_MODEL
    video_hash = file_sha256(args.video)
    fp = prompt_fingerprint(TIMELINE_PROMPT)
    key = cache_object_key(video_hash, model, fp)
    backend = get_backend()

    print(f"video_sha256={video_hash}")
    print(f"model={model}  prompt_fp={fp}")
    print(f"cache_key={key}")

    timeline = load_timeline(args.timeline)

    # Soft check: sample timelines sometimes carry a model-reported duration
    # that disagrees with ffprobe. The API clamps on analyze, but a large gap
    # here means the seeded cache entry may not match a live re-analyze.
    _warn_duration_mismatch(args.video, timeline.duration)

    if backend.exists(key) and not args.force:
        print("exists: leaving existing entry in place (pass --force to replace)")
        return 0

    payload = {
        "cache_version": 1,
        "video_sha256": video_hash,
        "model_id": model,
        "prompt_fingerprint": fp,
        "timeline": timeline_to_dict(timeline),
        "_seeded_from": os.path.abspath(args.timeline),
        "_note": (
            "Seeded from a validated sample timeline so API analyze can hit "
            "cache without Gemini (Task 15 / --from-timeline stand-in)."
        ),
    }
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(payload, f, indent=2)
        tmp = f.name
    backend.put(key, tmp, content_type="application/json")
    print(f"seeded from {args.timeline}")
    return 0


# Seconds of absolute difference before we warn. Sample timelines are hand
# authoring; small rounding is fine, multi-second drift is a footgun.
_DURATION_WARN_ABS_S = 0.5
_DURATION_WARN_REL = 0.05  # 5% of the longer duration


def _warn_duration_mismatch(video_path: str, timeline_duration: float | None) -> None:
    """Warn when timeline.duration differs significantly from ffprobe (if available)."""
    if not timeline_duration:
        return
    try:
        from foleyflow.analyze import get_video_duration

        probe = get_video_duration(video_path)
    except Exception as exc:  # ffprobe missing, unreadable file, etc.
        print(
            f"note: could not ffprobe video duration ({type(exc).__name__}: {exc}); "
            "skipping timeline-vs-probe check",
            file=sys.stderr,
        )
        return
    delta = abs(float(timeline_duration) - float(probe))
    threshold = max(
        _DURATION_WARN_ABS_S,
        _DURATION_WARN_REL * max(float(timeline_duration), float(probe), 1e-6),
    )
    if delta > threshold:
        print(
            f"WARNING: timeline.duration={timeline_duration} differs from "
            f"ffprobe={probe:.3f}s (delta={delta:.3f}s > {threshold:.3f}s). "
            "Seeded cache may not match a live analyze of this video.",
            file=sys.stderr,
        )



if __name__ == "__main__":
    raise SystemExit(main())
