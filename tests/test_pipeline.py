"""Offline unit tests for src/foleyflow/pipeline.py, cli.py, and mux.py.

NO fal API calls anywhere: analyze_video is monkeypatched to RAISE if the
orchestrator ever touches it on the --from-timeline path, and sfx/assemble/mux
are monkeypatched fakes. The one real-ffmpeg test (mux) uses only local files
and local ffmpeg/ffprobe binaries -- $0, same policy as tests/test_sfx.py.
"""

import json
import os
import subprocess

import numpy as np
import pytest

from foleyflow import cli, pipeline
from foleyflow.mux import mux
from foleyflow.pipeline import FoleyResult, run
from foleyflow.cli import main as foleyflow_main

CLIP_1 = os.path.join("scratch", "output", "clip_1.mp4")
RENDER_1 = os.path.join("scratch", "output", "render_clip1_ambience.wav")
TIMELINE_1 = os.path.join("samples", "timelines", "clip_1.json")


# -- run() orchestration (fully offline) --

def _patch_offline(monkeypatch, tmp_path, calls):
    """Monkeypatch every side-effectful stage of pipeline.run. analyze_video
    raises -- the --from-timeline path must NEVER analyze."""

    def fake_analyze(*args, **kwargs):
        raise AssertionError("analyze_video must not be called on the timeline_json path")

    def fake_duration(path):
        calls.setdefault("duration", []).append(path)
        return 6.12

    def fake_assemble(timeline, clip_duration_s, tier="full", sfx_fn=None, orig_audio_path=None):
        calls.setdefault("assemble", []).append((clip_duration_s, tier))
        return np.zeros((100, 2), dtype=np.float32)

    def fake_write_mix(mix, path):
        calls.setdefault("write_mix", []).append(path)
        with open(path, "wb") as f:
            f.write(b"fake wav")

    def fake_mux(video_path, wav_path, out_path):
        calls.setdefault("mux", []).append((video_path, wav_path, out_path))
        with open(out_path, "wb") as f:
            f.write(b"fake mp4")
        return out_path

    monkeypatch.setattr(pipeline, "analyze_video", fake_analyze)
    monkeypatch.setattr(pipeline, "get_video_duration", fake_duration)
    monkeypatch.setattr(pipeline, "assemble_timeline", fake_assemble)
    monkeypatch.setattr(pipeline, "write_mix", fake_write_mix)
    monkeypatch.setattr(pipeline, "mux_video", fake_mux)


def test_run_from_timeline_never_analyzes_and_populates_result(monkeypatch, tmp_path):
    calls = {}
    _patch_offline(monkeypatch, tmp_path, calls)
    out_path = str(tmp_path / "clip_1.foley.mp4")

    result = run(CLIP_1, out_path=out_path, tier="ambience",
                 timeline_json=TIMELINE_1)

    assert isinstance(result, FoleyResult)
    assert result.out_path == out_path
    assert result.tier == "ambience"
    assert result.duration_s == 6.12
    assert result.ledger_delta == 0.0                      # $0: nothing billed
    assert result.mix_path == str(tmp_path / "clip_1.foley.mix.wav")
    assert result.timeline_path == str(tmp_path / "clip_1.foley.timeline.json")
    assert os.path.exists(result.out_path)
    assert os.path.exists(result.mix_path)
    assert os.path.exists(result.timeline_path)            # timeline re-saved
    # Assembly got the ffprobe duration and the requested tier.
    assert calls["assemble"] == [(6.12, "ambience")]
    assert calls["mux"] == [(CLIP_1, result.mix_path, out_path)]


def test_run_events_report_covers_ambience_plus_all_events(monkeypatch, tmp_path):
    calls = {}
    _patch_offline(monkeypatch, tmp_path, calls)
    result = run(CLIP_1, out_path=str(tmp_path / "out.mp4"),
                 tier="ambience", timeline_json=TIMELINE_1)

    # clip_1.json has 5 events; report = 1 ambience row + 5 event rows.
    assert len(result.events) == 6
    amb = result.events[0]
    assert amb["kind"] == "ambience"
    assert amb["endpoint"] == "openmoss/moss-soundeffect-v2"
    # NOTE: deliberately does not assert amb["cached"] is True. Cache state is
    # environment, not behavior — upstream this passed only because a populated
    # local sfx_cache/ happened to sit next to the test. The per-row isinstance
    # check below is the real contract.
    for row in result.events:
        assert set(row) == {"name", "kind", "intensity", "start_time",
                            "category", "endpoint", "cached", "skipped",
                            "cost_est", "status", "state"}
        assert isinstance(row["cached"], bool)
    by_name = {r["name"]: r for r in result.events}
    # The category is the routing decision; every category resolves to the
    # self-hosted tier (SPEC §9.1), asserted once in test_routing.py.
    assert by_name["match strike"]["category"] == "impact_strike"
    assert by_name["footsteps on gravel"]["kind"] == "rhythmic"


def test_run_default_out_and_timeline_paths(monkeypatch, tmp_path):
    calls = {}
    _patch_offline(monkeypatch, tmp_path, calls)
    clip = str(tmp_path / "myclip.mp4")
    with open(clip, "wb") as f:
        f.write(b"fake")

    result = run(clip, timeline_json=TIMELINE_1, tier="ambience")

    assert result.out_path == str(tmp_path / "myclip.foley.mp4")
    assert result.mix_path == str(tmp_path / "myclip.foley.mix.wav")
    assert result.timeline_path == str(tmp_path / "myclip.foley.timeline.json")


def test_run_save_timeline_to_overrides_default(monkeypatch, tmp_path):
    calls = {}
    _patch_offline(monkeypatch, tmp_path, calls)
    custom = str(tmp_path / "custom_timeline.json")

    result = run(CLIP_1, out_path=str(tmp_path / "out.mp4"), tier="ambience",
                 timeline_json=TIMELINE_1, save_timeline_to=custom)

    assert result.timeline_path == custom
    assert os.path.exists(custom)
    with open(custom, "r", encoding="utf-8") as f:
        saved = json.load(f)
    assert saved["ambience"]                                # round-tripped


# -- CLI arg parsing (run monkeypatched; no pipeline work happens) --

def _capture_cli(monkeypatch, argv):
    captured = {}

    def fake_run(clip_path, **kwargs):
        captured["clip_path"] = clip_path
        captured.update(kwargs)
        return FoleyResult(out_path="x", timeline_path="y", mix_path="z")

    monkeypatch.setattr(cli, "run", fake_run)
    exit_code = foleyflow_main(["render"] + argv, client=None)
    return exit_code, captured


def test_cli_from_timeline_and_tier_reach_run(monkeypatch):
    code, got = _capture_cli(monkeypatch, [
        "clip.mp4", "--from-timeline", TIMELINE_1, "--tier", "ambience",
        "-o", "out.mp4",
    ])
    assert code == 0
    assert got["clip_path"] == "clip.mp4"
    assert got["timeline_json"] == TIMELINE_1
    assert got["tier"] == "ambience"
    assert got["out_path"] == "out.mp4"


def test_cli_defaults(monkeypatch):
    code, got = _capture_cli(monkeypatch, ["clip.mp4"])
    assert code == 0
    assert got["tier"] == "full"
    assert got["timeline_json"] is None
    assert got["out_path"] is None
    assert got["save_timeline_to"] is None
    assert got["model"] == cli.DEFAULT_MODEL
    assert got["cache_dir"] == "sfx_cache"


def test_cli_timeline_and_cache_dir_flags(monkeypatch):
    code, got = _capture_cli(monkeypatch, [
        "clip.mp4", "--timeline", "t.json", "--cache-dir", "alt_cache",
    ])
    assert code == 0
    assert got["save_timeline_to"] == "t.json"
    assert got["cache_dir"] == "alt_cache"


def test_cli_error_exits_1(monkeypatch, capsys):
    def failing_run(clip_path, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(cli, "run", failing_run)
    assert foleyflow_main(["render", "clip.mp4"], client=None) == 1
    err = capsys.readouterr().err
    assert "boom" in err
    assert "foleyflow render:" in err


# -- mux(): one REAL local-ffmpeg test ($0, no network) --

def _ffprobe_streams(path):
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format",
         "-of", "json", path],
        capture_output=True, text=True, check=True,
    )
    return json.loads(proc.stdout)


@pytest.mark.skipif(
    not (os.path.exists(CLIP_1) and os.path.exists(RENDER_1)),
    reason="needs local media fixtures (scratch/output/) that are not tracked in the repo",
)
def test_mux_real_ffmpeg_copies_video_and_adds_audio(tmp_path):
    out_path = str(tmp_path / "muxed.mp4")
    returned = mux(CLIP_1, RENDER_1, out_path)
    assert returned == out_path
    assert os.path.exists(out_path)

    info = _ffprobe_streams(out_path)
    kinds = {s["codec_type"]: s for s in info["streams"]}
    assert "video" in kinds and "audio" in kinds
    assert kinds["video"]["codec_name"] == "h264"           # -c:v copy
    assert float(info["format"]["duration"]) == pytest.approx(6.12, abs=0.15)


def test_mux_failure_raises_runtime_error_with_stderr(tmp_path):
    missing = str(tmp_path / "does_not_exist.mp4")
    with pytest.raises(RuntimeError, match="ffmpeg mux failed"):
        mux(missing, RENDER_1, str(tmp_path / "out.mp4"))
