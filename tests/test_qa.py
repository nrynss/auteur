"""Tests for audio and visual QA.

Pure logic tests need no ffmpeg. The ebur128 measurement builds a tone with
lavfi so it stays offline and free — same policy as test_scenes / test_mux.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from foleyflow import qa
from foleyflow.scenes import Scene, SceneStats

HAS_FFMPEG = shutil.which("ffmpeg") is not None
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not on PATH")


def _scene(idx, luma, *, rgb=None, stddev=1.0):
    stats = SceneStats(
        mean_luma=luma,
        mean_rgb=rgb or [luma, luma, luma],
        luma_stddev=stddev,
        sample_count=4,
    )
    return Scene(id=f"scene_{idx:02d}", index=idx,
                 start_s=float(idx - 1), end_s=float(idx), stats=stats)


# -- continuity (pure) ------------------------------------------------------

def test_adjacent_luma_jump_flags_exposure_discontinuity():
    """The demo clip's 31.7 jump must fire at the default threshold of 20."""
    scenes = [_scene(4, 100.0), _scene(5, 131.7)]
    issues = qa.check_continuity(scenes)
    assert len(issues) == 1
    issue = issues[0]
    assert issue.code == qa.CODE_EXPOSURE_DISCONTINUITY
    assert issue.severity == qa.SEVERITY_WARNING
    assert issue.scene_id == "scene_05"
    assert issue.compared_with == "scene_04"
    assert issue.confidence == pytest.approx(0.79, abs=0.01)


def test_small_luma_change_is_not_a_warning():
    scenes = [_scene(1, 120.0), _scene(2, 130.0)]  # delta 10 < 20
    assert qa.check_continuity(scenes) == []


def test_luma_delta_at_threshold_fires():
    """Threshold is inclusive — 'at least', not 'strictly more than'."""
    scenes = [_scene(1, 100.0), _scene(2, 120.0)]  # delta == 20
    issues = qa.check_continuity(scenes)
    assert len(issues) == 1
    assert issues[0].code == qa.CODE_EXPOSURE_DISCONTINUITY


def test_with_preserved_audio_keeps_blocking_true_peak():
    previous = qa.build_report(audio_path=None).to_dict()
    previous = {
        "run_id": None,
        "overall_status": qa.STATUS_BLOCKED,
        "audio": qa.AudioQa(
            integrated_lufs=-12.0, true_peak_dbtp=1.4, measured=True,
            issues=[qa.QaIssue(severity=qa.SEVERITY_BLOCKING,
                               code=qa.CODE_TRUE_PEAK_EXCEEDED)],
        ).to_dict(),
        "visual": qa.VisualQa().to_dict(),
    }
    visual_only = qa.build_report([_scene(1, 40.0), _scene(2, 80.0)])
    merged = qa.with_preserved_audio(visual_only, previous)
    assert merged.audio.measured is True
    assert merged.audio.true_peak_dbtp == 1.4
    assert merged.overall_status == qa.STATUS_BLOCKED
    assert any(i.code == qa.CODE_EXPOSURE_DISCONTINUITY for i in merged.visual.issues)


def test_missing_stats_skip_rather_than_invent_a_jump():
    a = Scene(id="scene_01", index=1, start_s=0.0, end_s=1.0, stats=None)
    b = _scene(2, 200.0)
    assert qa.check_continuity([a, b]) == []


def test_continuity_accepts_raw_scene_dicts():
    scenes = [
        {"id": "scene_01", "stats": {"mean_luma": 50.0}},
        {"id": "scene_02", "stats": {"mean_luma": 90.0}},
    ]
    issues = qa.check_continuity(scenes)
    assert issues[0].scene_id == "scene_02"
    assert issues[0].compared_with == "scene_01"


# -- audio policy (pure) ----------------------------------------------------

def test_true_peak_overage_is_blocking():
    metrics = qa.AudioMetrics(integrated_lufs=-14.0, true_peak_dbtp=1.4)
    issues = qa.check_audio(metrics)
    assert len(issues) == 1
    assert issues[0].code == qa.CODE_TRUE_PEAK_EXCEEDED
    assert issues[0].severity == qa.SEVERITY_BLOCKING


def test_loudness_drift_is_a_warning_not_a_block():
    metrics = qa.AudioMetrics(integrated_lufs=-20.0, true_peak_dbtp=-3.0)
    issues = qa.check_audio(metrics)
    assert len(issues) == 1
    assert issues[0].code == qa.CODE_LOUDNESS_OUT_OF_RANGE
    assert issues[0].severity == qa.SEVERITY_WARNING


def test_audio_within_targets_is_clean():
    metrics = qa.AudioMetrics(integrated_lufs=-14.5, true_peak_dbtp=-1.5)
    assert qa.check_audio(metrics) == []


def test_build_report_without_audio_is_continuity_only():
    scenes = [_scene(1, 40.0), _scene(2, 80.0)]
    report = qa.build_report(scenes, run_id="run_01TEST")
    assert report.audio.measured is False
    assert report.audio.issues == []
    assert report.overall_status == qa.STATUS_NEEDS_REVIEW
    assert report.visual.issues[0].code == qa.CODE_EXPOSURE_DISCONTINUITY


def test_report_round_trips_through_dict():
    scenes = [_scene(1, 40.0), _scene(2, 80.0)]
    original = qa.build_report(scenes, run_id="run_01TEST")
    restored = qa.report_from_dict(original.to_dict())
    assert restored.to_dict() == original.to_dict()


def test_blocking_outranks_warnings_in_overall_status():
    report = qa.QaReport(
        run_id=None,
        overall_status=qa._overall_status([
            qa.QaIssue(severity=qa.SEVERITY_WARNING,
                       code=qa.CODE_EXPOSURE_DISCONTINUITY),
            qa.QaIssue(severity=qa.SEVERITY_BLOCKING,
                       code=qa.CODE_TRUE_PEAK_EXCEEDED),
        ]),
        audio=qa.AudioQa(integrated_lufs=None, true_peak_dbtp=None),
        visual=qa.VisualQa(),
    )
    assert report.overall_status == qa.STATUS_BLOCKED


# -- ebur128 measurement ----------------------------------------------------

@pytest.fixture
def quiet_tone(tmp_path):
    if not HAS_FFMPEG:
        pytest.skip("ffmpeg not on PATH")
    path = str(tmp_path / "quiet.wav")
    # Low-level sine so true peak stays well under -1 dBTP.
    subprocess.run([
        "ffmpeg", "-hide_banner", "-nostdin", "-y",
        "-f", "lavfi", "-i", "sine=f=440:d=3:sample_rate=48000",
        "-af", "volume=-20dB", "-ac", "2", path,
    ], capture_output=True, check=True)
    return path


@pytest.fixture
def hot_tone(tmp_path):
    """A mix that deliberately exceeds -1 dBTP.

    lavfi `sine` defaults are far below full scale, so this writes a near-clip
    float32 WAV with soundfile — same offline policy, no provider calls.
    """
    import numpy as np
    import soundfile as sf

    path = str(tmp_path / "hot.wav")
    # Peak ≈ 0 dBFS → true peak above the -1 dBTP delivery target.
    t = np.linspace(0, 3, 48000 * 3, endpoint=False, dtype=np.float32)
    tone = (0.99 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    stereo = np.column_stack([tone, tone])
    sf.write(path, stereo, 48000, subtype="FLOAT")
    return path


@needs_ffmpeg
def test_measure_loudness_returns_finite_metrics(quiet_tone):
    metrics = qa.measure_loudness(quiet_tone)
    assert metrics.integrated_lufs < -10
    assert metrics.true_peak_dbtp < -1.0


@needs_ffmpeg
def test_hot_mix_flags_true_peak(hot_tone):
    report = qa.build_report(audio_path=hot_tone)
    assert report.audio.measured is True
    assert report.audio.true_peak_dbtp > qa.TARGET_TRUE_PEAK_DBTP
    assert any(i.code == qa.CODE_TRUE_PEAK_EXCEEDED for i in report.audio.issues)
    assert report.overall_status == qa.STATUS_BLOCKED
