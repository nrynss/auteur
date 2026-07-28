"""Scene detection, thumbnails, and per-scene frame statistics.

SPEC.md threads `scene_id` through the QA report (§10.3), the scene rail
(§6.1), and the continuity warnings (§5) — so scenes are the coordinate system
the rest of the UI is addressed in, not a nice-to-have.

## Why ffmpeg rather than PySceneDetect

ffmpeg is already a hard dependency of the media core. PySceneDetect would pull
in opencv for a job ffmpeg's own `select='gt(scene,…)'` already does, and every
extra dependency is another thing to install in the deploy image.

## How the statistics are collected

Per-scene colour is measured by asking ffmpeg to scale each sampled frame to a
single pixel: that pixel *is* the frame's average colour, computed by ffmpeg's
scaler in C, so there is no per-pixel Python and no numpy pass over the video.
`fps=N,scale=1:1` then `-f rawvideo -pix_fmt rgb24` yields exactly three bytes
per sampled frame.

These statistics are deliberately deterministic. SPEC.md §12 requires frame
statistics first and an optional multimodal explanation second — a continuity
warning must be able to point at a number, not at a model's opinion.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import asdict, dataclass, field
from typing import List, Optional, Sequence

from foley.models import Timeline

# Frame-difference score above which a cut is called. ffmpeg's `scene` value is
# 0..1; 0.3 is the conventional starting point and errs toward reporting a cut,
# which for a warning-only feature is the safer direction.
DEFAULT_THRESHOLD = 0.3

# A detected "scene" shorter than this is a flash, a dissolve, or a false
# positive on a fast pan — not a shot worth its own rail entry.
MIN_SCENE_S = 0.4

# Frames sampled per second when measuring colour.
STATS_FPS = 2.0


@dataclass(frozen=True)
class SceneStats:
    """Mean colour of a scene, and how much it varies within the scene."""

    mean_luma: float          # 0..255, Rec. 601
    mean_rgb: Sequence[float]
    luma_stddev: float
    sample_count: int

    def to_dict(self) -> dict:
        return {**asdict(self), "mean_rgb": list(self.mean_rgb)}


@dataclass(frozen=True)
class Scene:
    id: str
    index: int
    start_s: float
    end_s: float
    stats: Optional[SceneStats] = None
    thumbnail_key: Optional[str] = None

    @property
    def duration_s(self) -> float:
        return self.end_s - self.start_s

    @property
    def midpoint_s(self) -> float:
        return self.start_s + self.duration_s / 2

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["duration_s"] = self.duration_s
        if self.stats:
            payload["stats"] = self.stats.to_dict()
        return payload


# ffmpeg on a corrupt or partially-downloaded file can block indefinitely.
# Without a bound it pins a threadpool worker for the life of the process.
FFMPEG_TIMEOUT_S = 120


class SceneDetectionError(RuntimeError):
    """ffmpeg failed. Distinct from "no cuts found" — see detect_cuts."""


def _run(cmd: List[str], *, check: bool = True) -> subprocess.CompletedProcess:
    """Run ffmpeg, failing loudly by default.

    The exit code has to be checked. A failed probe produces no `pts_time:`
    matches, which is indistinguishable from a genuine single-shot clip — so an
    unreadable or truncated video silently became "one scene spanning the whole
    duration", and save_scenes then recorded ANALYSIS_COMPLETED for a fabricated
    breakdown that every scene_id and continuity warning was built on.
    """
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=FFMPEG_TIMEOUT_S)
    except subprocess.TimeoutExpired as e:
        raise SceneDetectionError(
            f"ffmpeg timed out after {FFMPEG_TIMEOUT_S}s") from e
    if check and proc.returncode != 0:
        tail = proc.stderr.decode("utf-8", errors="replace").strip()[-300:]
        raise SceneDetectionError(f"ffmpeg failed (exit {proc.returncode}): {tail}")
    return proc


def detect_cuts(video_path: str, threshold: float = DEFAULT_THRESHOLD) -> List[float]:
    """Timestamps (seconds) where ffmpeg reports a scene change."""
    proc = _run([
        "ffmpeg", "-hide_banner", "-i", video_path,
        "-vf", f"select='gt(scene,{threshold})',showinfo",
        "-f", "null", "-",
    ])
    stderr = proc.stderr.decode("utf-8", errors="replace")
    times = [float(m) for m in re.findall(r"pts_time:([0-9.]+)", stderr)]
    return sorted(set(times))


def detect_scenes(video_path: str, duration_s: float, *,
                  threshold: float = DEFAULT_THRESHOLD,
                  min_scene_s: float = MIN_SCENE_S,
                  with_stats: bool = True) -> List[Scene]:
    """Split `video_path` into scenes.

    A single-shot clip yields exactly one scene spanning the whole duration —
    the common case for short AI-generated footage, and the caller should not
    have to special-case it.
    """
    cuts = [t for t in detect_cuts(video_path, threshold) if 0 < t < duration_s]

    boundaries = [0.0]
    for cut in cuts:
        if cut - boundaries[-1] >= min_scene_s:
            boundaries.append(cut)
    boundaries.append(duration_s)

    # A final scene shorter than the floor is folded back into its predecessor
    # rather than left as a sliver.
    if len(boundaries) > 2 and boundaries[-1] - boundaries[-2] < min_scene_s:
        boundaries.pop(-2)

    scenes = []
    for i in range(len(boundaries) - 1):
        start, end = boundaries[i], boundaries[i + 1]
        stats = measure_scene(video_path, start, end) if with_stats else None
        scenes.append(Scene(id=f"scene_{i + 1:02d}", index=i + 1,
                            start_s=round(start, 3), end_s=round(end, 3),
                            stats=stats))
    return scenes


def measure_scene(video_path: str, start_s: float, end_s: float,
                  fps: float = STATS_FPS) -> Optional[SceneStats]:
    """Average colour across sampled frames of one scene.

    Each frame is scaled to 1x1 by ffmpeg, so the returned pixel is that
    frame's mean colour and the whole measurement is three bytes per sample.
    """
    duration = max(end_s - start_s, 0.05)
    proc = _run([
        "ffmpeg", "-hide_banner", "-nostdin",
        "-ss", f"{start_s:.3f}", "-t", f"{duration:.3f}", "-i", video_path,
        "-vf", f"fps={fps},scale=1:1",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
    ])
    raw = proc.stdout
    if len(raw) < 3:
        return None

    samples = [tuple(raw[i:i + 3]) for i in range(0, len(raw) - 2, 3)]
    n = len(samples)
    mean_rgb = [sum(s[c] for s in samples) / n for c in range(3)]
    # Rec. 601 luma — the standard weighting for perceived brightness.
    lumas = [0.299 * r + 0.587 * g + 0.114 * b for r, g, b in samples]
    mean_luma = sum(lumas) / n
    variance = sum((x - mean_luma) ** 2 for x in lumas) / n

    return SceneStats(
        mean_luma=round(mean_luma, 2),
        mean_rgb=[round(c, 2) for c in mean_rgb],
        luma_stddev=round(variance ** 0.5, 2),
        sample_count=n,
    )


def extract_thumbnail(video_path: str, scene: Scene, dest_path: str) -> str:
    """Write a JPEG from the middle of `scene`.

    The midpoint rather than the first frame: a cut's first frame is often a
    dissolve or motion-blurred, which makes a poor rail thumbnail.
    """
    os.makedirs(os.path.dirname(os.path.abspath(dest_path)), exist_ok=True)
    proc = _run([
        "ffmpeg", "-hide_banner", "-nostdin", "-y",
        "-ss", f"{scene.midpoint_s:.3f}", "-i", video_path,
        "-frames:v", "1", "-q:v", "3", dest_path,
    ])
    if proc.returncode != 0 or not os.path.exists(dest_path):
        raise RuntimeError(
            f"thumbnail extraction failed for {scene.id}: "
            f"{proc.stderr.decode('utf-8', errors='replace')[-400:]}"
        )
    return dest_path


def scene_for_time(scenes: Sequence[Scene], t: float) -> Optional[Scene]:
    """The scene containing `t`. Intervals are half-open [start, end) so an
    event landing exactly on a cut belongs to the incoming shot."""
    for scene in scenes:
        if scene.start_s <= t < scene.end_s:
            return scene
    return scenes[-1] if scenes and t >= scenes[-1].end_s else None


def assign_scene_ids(timeline: Timeline, scenes: Sequence[Scene]) -> List[Optional[str]]:
    """Scene id for each timeline event, positionally aligned with
    `timeline.events`.

    A list, not a `{name: scene_id}` dict. Event names are descriptions, not
    identifiers — a timeline routinely repeats one ("leather holster creaking"
    at 0.0s and again at 4.0s), and a name-keyed mapping silently drops all but
    the last occurrence, filing earlier events against the wrong shot.

    Not mutated onto the events themselves: foley.models.Event belongs to the
    ported media core and has no scene field, and widening it would push a
    FoleyFlow concern into the pipeline.
    """
    return [
        (scene.id if (scene := scene_for_time(scenes, event.start_time)) else None)
        for event in timeline.events
    ]


def scenes_to_json(scenes: Sequence[Scene]) -> str:
    return json.dumps([s.to_dict() for s in scenes], indent=2)
