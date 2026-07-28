"""Tests for scene detection.

The ffmpeg-backed tests build their own two-shot video from lavfi sources, so
they are deterministic and need no fixture file: a black shot followed by a
white one has a cut at a known time and known luma on either side. Offline and
free, same policy as the existing mux test.
"""

import os
import shutil
import subprocess

import pytest

from foleyflow.models import Event, Timeline
from foleyflow import scenes as sc

HAS_FFMPEG = shutil.which("ffmpeg") is not None
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not on PATH")


@pytest.fixture(scope="module")
def two_shot_video(tmp_path_factory):
    """2s of black then 2s of white — a cut at exactly 2.0s."""
    if not HAS_FFMPEG:
        pytest.skip("ffmpeg not on PATH")
    path = str(tmp_path_factory.mktemp("video") / "two_shot.mp4")
    subprocess.run([
        "ffmpeg", "-hide_banner", "-nostdin", "-y",
        "-f", "lavfi", "-i", "color=c=black:s=160x120:d=2:r=10",
        "-f", "lavfi", "-i", "color=c=white:s=160x120:d=2:r=10",
        "-filter_complex", "[0:v][1:v]concat=n=2:v=1[out]",
        "-map", "[out]", "-pix_fmt", "yuv420p", path,
    ], capture_output=True, check=True)
    return path


def _scene(idx, start, end):
    return sc.Scene(id=f"scene_{idx:02d}", index=idx, start_s=start, end_s=end)


# -- pure logic, no ffmpeg --------------------------------------------------

def test_scene_geometry():
    s = _scene(1, 2.0, 6.0)
    assert s.duration_s == 4.0
    assert s.midpoint_s == 4.0


def test_time_lookup_is_half_open_at_the_cut():
    """An event landing exactly on a cut belongs to the incoming shot —
    otherwise the sound of an impact would be filed against the outgoing one."""
    scenes = [_scene(1, 0.0, 5.0), _scene(2, 5.0, 10.0)]
    assert sc.scene_for_time(scenes, 4.999).id == "scene_01"
    assert sc.scene_for_time(scenes, 5.0).id == "scene_02"


def test_time_past_the_end_clamps_to_the_last_scene():
    """Timeline durations come from the VLM and can overshoot ffprobe's
    duration slightly; an event must still land somewhere."""
    scenes = [_scene(1, 0.0, 5.0)]
    assert sc.scene_for_time(scenes, 99.0).id == "scene_01"


def test_events_map_to_their_scenes():
    scenes = [_scene(1, 0.0, 5.0), _scene(2, 5.0, 10.0)]
    timeline = Timeline(duration=10.0, ambience="wind", events=[
        Event(name="boot step", start_time=1.0, duration=0.5, intensity="low", kind="discrete"),
        Event(name="gunshot", start_time=7.5, duration=1.0, intensity="high", kind="discrete"),
    ])
    assert sc.assign_scene_ids(timeline, scenes) == ["scene_01", "scene_02"]


def test_repeated_event_names_map_independently():
    """Regression: the mapping used to be keyed by event name, but names are
    descriptions rather than identifiers. A real timeline repeats them —
    "leather holster creaking" at 0.0s and again at 4.0s — and the name-keyed
    version filed the first occurrence against the second's shot.
    """
    scenes = [_scene(1, 0.0, 3.67), _scene(2, 3.67, 6.67)]
    timeline = Timeline(duration=6.67, ambience="wind", events=[
        Event(name="leather holster creaking", start_time=0.0, duration=1.0,
              intensity="low", kind="continuous"),
        Event(name="leather holster creaking", start_time=4.0, duration=1.0,
              intensity="low", kind="continuous"),
    ])
    assert sc.assign_scene_ids(timeline, scenes) == ["scene_01", "scene_02"]


def test_mapping_is_positionally_aligned_with_events():
    scenes = [_scene(1, 0.0, 10.0)]
    timeline = Timeline(duration=10.0, ambience="wind", events=[
        Event(name=f"step {i}", start_time=float(i), duration=0.5,
              intensity="low", kind="discrete") for i in range(6)
    ])
    assert len(sc.assign_scene_ids(timeline, scenes)) == len(timeline.events)


def test_scene_ids_are_stable_and_padded():
    """scene_id is used as an object key and shown in the UI, so the format
    must not drift between runs."""
    scenes = [_scene(i, i, i + 1) for i in range(1, 12)]
    assert scenes[0].id == "scene_01"
    assert scenes[10].id == "scene_11"


def test_to_dict_is_json_serializable():
    import json
    s = sc.Scene(id="scene_01", index=1, start_s=0.0, end_s=3.0,
                 stats=sc.SceneStats(mean_luma=120.0, mean_rgb=[1.0, 2.0, 3.0],
                                     luma_stddev=4.0, sample_count=6))
    json.dumps(s.to_dict())
    assert json.loads(sc.scenes_to_json([s]))[0]["duration_s"] == 3.0


# -- ffmpeg-backed ----------------------------------------------------------

@needs_ffmpeg
def test_detects_the_cut(two_shot_video):
    cuts = sc.detect_cuts(two_shot_video)
    assert cuts, "a black-to-white transition must register as a cut"
    assert any(abs(c - 2.0) < 0.3 for c in cuts), f"expected a cut near 2.0s, got {cuts}"


@needs_ffmpeg
def test_splits_into_two_scenes_covering_the_whole_clip(two_shot_video):
    scenes = sc.detect_scenes(two_shot_video, 4.0)
    assert len(scenes) == 2
    assert scenes[0].start_s == 0.0
    assert scenes[-1].end_s == 4.0
    # No gaps: the rail must account for every frame.
    for a, b in zip(scenes, scenes[1:]):
        assert a.end_s == b.start_s


@needs_ffmpeg
def test_measured_luma_matches_the_known_shots(two_shot_video):
    """Black then white — if these come back reversed or equal, the stats
    pass is measuring the wrong frames and continuity QA would be noise."""
    scenes = sc.detect_scenes(two_shot_video, 4.0)
    dark, light = scenes[0].stats, scenes[1].stats
    assert dark.mean_luma < 40, f"black shot measured {dark.mean_luma}"
    assert light.mean_luma > 200, f"white shot measured {light.mean_luma}"
    assert light.mean_luma - dark.mean_luma > 150


@needs_ffmpeg
def test_single_shot_clip_yields_one_scene(tmp_path):
    """The common case for short AI footage — callers must not have to
    special-case it."""
    path = str(tmp_path / "one_shot.mp4")
    subprocess.run([
        "ffmpeg", "-hide_banner", "-nostdin", "-y",
        "-f", "lavfi", "-i", "color=c=blue:s=160x120:d=3:r=10",
        "-pix_fmt", "yuv420p", path,
    ], capture_output=True, check=True)
    scenes = sc.detect_scenes(path, 3.0)
    assert len(scenes) == 1
    assert (scenes[0].start_s, scenes[0].end_s) == (0.0, 3.0)


@needs_ffmpeg
def test_slivers_are_folded_away(two_shot_video):
    """A very low threshold invents cuts; none of the resulting scenes may be
    shorter than the floor, or the rail fills with unusable entries."""
    scenes = sc.detect_scenes(two_shot_video, 4.0, threshold=0.001,
                              min_scene_s=1.0, with_stats=False)
    assert all(s.duration_s >= 1.0 for s in scenes), \
        [(s.id, s.duration_s) for s in scenes]


@needs_ffmpeg
def test_thumbnail_is_written(two_shot_video, tmp_path):
    scenes = sc.detect_scenes(two_shot_video, 4.0, with_stats=False)
    dest = str(tmp_path / "thumbs" / "scene_01.jpg")
    sc.extract_thumbnail(two_shot_video, scenes[0], dest)
    assert os.path.getsize(dest) > 0
