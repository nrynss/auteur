"""Offline unit tests for src/foley/direct.py and the `foley direct` CLI
subcommand (T5b). NO fal API calls anywhere: execute_with_ledger and
apply_notes are monkeypatched throughout -- same policy as
tests/test_pipeline.py's analyze_video guard.
"""

import json
import os
import sys

import pytest

from foley import cli, direct
from foley.models import Event, Timeline
from foley.pipeline import FoleyResult
from foley.timeline_io import save_timeline


def _timeline() -> Timeline:
    return Timeline(
        duration=6.12,
        ambience="light wind rustling dry leaves and distant bird chirps",
        events=[
            Event(name="footsteps on gravel", start_time=0.0, duration=1.7,
                  intensity="med", kind="rhythmic"),
            Event(name="paper tearing from matchbox", start_time=4.9, duration=0.4,
                  intensity="low", kind="discrete"),
            Event(name="match strike", start_time=5.4, duration=0.2,
                  intensity="low", kind="discrete"),
        ],
    )


# -- apply_notes() --

def test_apply_notes_prompt_includes_notes_and_serialized_timeline(monkeypatch):
    captured = {}

    def fake_execute(endpoint, arguments):
        captured["endpoint"] = endpoint
        captured["arguments"] = arguments
        return {"output": json.dumps({"duration": 6.12, "ambience": "wind", "events": []})}

    monkeypatch.setattr(direct, "execute_with_ledger", fake_execute)
    notes = "move the match strike 200ms later and make it high intensity"

    direct.apply_notes(_timeline(), notes, model="test/model")

    assert captured["endpoint"] == "openrouter/router/vision"
    assert captured["arguments"]["image_urls"] == []       # text-only call
    assert captured["arguments"]["model"] == "test/model"
    prompt = captured["arguments"]["prompt"]
    assert notes in prompt
    assert '"match strike"' in prompt                       # actual timeline JSON, not a summary
    assert '"start_time": 5.4' in prompt
    assert "Apply ONLY what the notes require" in prompt
    assert '"low", "med", "high"' in prompt or "low" in prompt and "high" in prompt


def test_apply_notes_parses_response_into_timeline(monkeypatch):
    def fake_execute(endpoint, arguments):
        return {"output": json.dumps({
            "duration": 6.12,
            "ambience": "light wind rustling dry leaves and distant bird chirps",
            "events": [
                {"name": "footsteps on gravel", "start_time": 0.0, "duration": 1.7,
                 "intensity": "med", "kind": "rhythmic"},
                {"name": "match strike", "start_time": 5.6, "duration": 0.2,
                 "intensity": "high", "kind": "discrete"},
            ],
        })}

    monkeypatch.setattr(direct, "execute_with_ledger", fake_execute)
    result = direct.apply_notes(_timeline(), "strike 200ms later, high; drop paper tear")

    assert len(result.events) == 2
    strike = next(e for e in result.events if e.name == "match strike")
    assert strike.start_time == 5.6
    assert strike.intensity == "high"


def test_apply_notes_response_goes_through_existing_validation(monkeypatch):
    """A hallucinated out-of-range edit gets clamped/normalized by the
    EXISTING parse_timeline_json exactly like a fresh video analysis."""
    def fake_execute(endpoint, arguments):
        return {"output": json.dumps({
            "duration": 6.12, "ambience": "wind",
            "events": [
                {"name": "late event", "start_time": 999.0, "duration": 1.0,
                 "intensity": "med", "kind": "discrete"},
                {"name": "bad enum", "start_time": 1.0, "duration": 0.5,
                 "intensity": "extreme", "kind": "unknown"},
            ],
        })}

    monkeypatch.setattr(direct, "execute_with_ledger", fake_execute)
    result = direct.apply_notes(_timeline(), "notes", clip_duration=6.12)

    late = next(e for e in result.events if e.name == "late event")
    assert late.start_time == 6.12         # clamped to the ffprobe clip end
    bad = next(e for e in result.events if e.name == "bad enum")
    assert bad.intensity == "med"          # safe default
    assert bad.kind == "discrete"          # safe default


def test_apply_notes_no_clip_duration_skips_clamping(monkeypatch):
    def fake_execute(endpoint, arguments):
        return {"output": json.dumps({
            "duration": 6.12, "ambience": "wind",
            "events": [{"name": "late event", "start_time": 999.0, "duration": 1.0,
                        "intensity": "med", "kind": "discrete"}],
        })}

    monkeypatch.setattr(direct, "execute_with_ledger", fake_execute)
    result = direct.apply_notes(_timeline(), "notes")  # no clip_duration
    assert result.events[0].start_time == 999.0        # nothing to clamp against


def test_apply_notes_strips_markdown_fences(monkeypatch):
    def fake_execute(endpoint, arguments):
        return {"output": "```json\n" + json.dumps(
            {"duration": 6.12, "ambience": "wind", "events": []}) + "\n```"}

    monkeypatch.setattr(direct, "execute_with_ledger", fake_execute)
    result = direct.apply_notes(_timeline(), "notes")
    assert result.ambience == "wind"


# -- default_output_path() --

def test_default_output_path_basic():
    assert direct.default_output_path("samples/timelines/clip_1.json") == \
        os.path.join("samples/timelines", "clip_1.directed.json")


def test_default_output_path_no_directory():
    assert direct.default_output_path("clip.json") == "clip.directed.json"


def test_default_output_path_bumps_when_input_already_directed():
    assert direct.default_output_path("clip_1.directed.json") == "clip_1.directed2.json"
    assert direct.default_output_path("clip_1.directed2.json") == "clip_1.directed3.json"
    assert direct.default_output_path("clip_1.directed9.json") == "clip_1.directed10.json"


def test_default_output_path_bumps_when_file_exists_on_disk(tmp_path):
    in_path = tmp_path / "clip_1.json"
    in_path.touch()

    # If clip_1.directed.json exists, it should bump to directed2
    out1 = tmp_path / "clip_1.directed.json"
    out1.touch()
    assert direct.default_output_path(str(in_path)) == str(tmp_path / "clip_1.directed2.json")

    # If clip_1.directed2.json also exists, it should bump to directed3
    out2 = tmp_path / "clip_1.directed2.json"
    out2.touch()
    assert direct.default_output_path(str(in_path)) == str(tmp_path / "clip_1.directed3.json")


# -- diff_timelines() --

def test_diff_timelines_changed_field_and_removed_event():
    old = _timeline()
    new = Timeline(
        duration=old.duration, ambience=old.ambience,
        events=[
            old.events[0],
            Event(name="match strike", start_time=5.6, duration=0.2, intensity="high", kind="discrete"),
        ],
    )
    out = direct.diff_timelines(old, new)
    assert "REMOVED: 'paper tearing from matchbox'" in out
    assert "'match strike'" in out
    assert "start_time 5.4 -> 5.6" in out
    assert "intensity low -> high" in out
    assert "footsteps" not in out            # unchanged event omitted entirely


def test_diff_timelines_added_event_and_top_level_change():
    old = _timeline()
    new = Timeline(
        duration=old.duration, ambience="heavy rain",
        events=old.events + [
            Event(name="thunder crack", start_time=3.0, duration=0.5, intensity="high", kind="discrete"),
        ],
    )
    out = direct.diff_timelines(old, new)
    assert "ambience:" in out and "heavy rain" in out
    assert "ADDED: 'thunder crack'" in out


def test_diff_timelines_no_changes():
    t = _timeline()
    same = Timeline(duration=t.duration, ambience=t.ambience, events=list(t.events))
    assert direct.diff_timelines(t, same) == "(no changes)"


# -- CLI plumbing (apply_notes monkeypatched; no LLM call) --

def test_cli_direct_writes_default_output_and_prints_diff(monkeypatch, tmp_path, capsys):
    in_path = tmp_path / "clip_1.json"
    save_timeline(_timeline(), str(in_path))

    base = _timeline()
    edited = Timeline(
        duration=base.duration, ambience=base.ambience,
        events=[
            base.events[0],
            Event(name="match strike", start_time=5.6, duration=0.2, intensity="high", kind="discrete"),
        ],
    )
    captured = {}

    def fake_apply_notes(timeline, notes, model=cli.DEFAULT_MODEL, clip_duration=None):
        captured["notes"] = notes
        captured["model"] = model
        captured["clip_duration"] = clip_duration
        return edited

    monkeypatch.setattr(cli, "apply_notes", fake_apply_notes)
    monkeypatch.setattr(sys, "argv", [
        "foley", "direct", str(in_path),
        "--notes", "strike 200ms later, high intensity; drop paper tear",
    ])

    code = cli.main()
    out = capsys.readouterr().out

    assert code == 0
    assert captured["notes"] == "strike 200ms later, high intensity; drop paper tear"
    assert captured["clip_duration"] is None        # no --clip given
    expected_out = tmp_path / "clip_1.directed.json"
    assert os.path.exists(expected_out)
    assert "REMOVED: 'paper tearing from matchbox'" in out
    assert "start_time 5.4 -> 5.6" in out
    assert str(expected_out) in out


def test_cli_direct_respects_explicit_out_and_clip(monkeypatch, tmp_path):
    in_path = tmp_path / "clip_1.json"
    save_timeline(_timeline(), str(in_path))
    out_path = tmp_path / "custom.json"

    def fake_apply_notes(timeline, notes, model=cli.DEFAULT_MODEL, clip_duration=None):
        assert clip_duration == 6.12
        return timeline

    def fake_get_video_duration(path):
        assert path == "video.mp4"
        return 6.12

    monkeypatch.setattr(cli, "apply_notes", fake_apply_notes)
    monkeypatch.setattr(cli, "get_video_duration", fake_get_video_duration)
    monkeypatch.setattr(sys, "argv", [
        "foley", "direct", str(in_path), "--notes", "x",
        "-o", str(out_path), "--clip", "video.mp4",
    ])

    assert cli.main() == 0
    assert os.path.exists(out_path)


def test_cli_direct_refuses_to_overwrite_input(monkeypatch, tmp_path, capsys):
    in_path = tmp_path / "clip_1.directed.json"
    save_timeline(_timeline(), str(in_path))

    def fake_apply_notes(*a, **k):
        raise AssertionError("apply_notes must not run when output would overwrite the input")

    monkeypatch.setattr(cli, "apply_notes", fake_apply_notes)
    monkeypatch.setattr(sys, "argv", [
        "foley", "direct", str(in_path), "--notes", "x", "-o", str(in_path),
    ])

    code = cli.main()
    assert code == 1
    assert "overwrite" in capsys.readouterr().err


def test_cli_direct_default_naming_bumps_for_already_directed_input(monkeypatch, tmp_path):
    in_path = tmp_path / "clip_1.directed.json"
    save_timeline(_timeline(), str(in_path))

    monkeypatch.setattr(cli, "apply_notes", lambda timeline, notes, **k: timeline)
    monkeypatch.setattr(sys, "argv", ["foley", "direct", str(in_path), "--notes", "x"])

    assert cli.main() == 0
    assert os.path.exists(tmp_path / "clip_1.directed2.json")


def test_cli_direct_error_from_apply_notes_exits_1(monkeypatch, tmp_path, capsys):
    in_path = tmp_path / "clip_1.json"
    save_timeline(_timeline(), str(in_path))

    def failing_apply_notes(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(cli, "apply_notes", failing_apply_notes)
    monkeypatch.setattr(sys, "argv", ["foley", "direct", str(in_path), "--notes", "x"])

    assert cli.main() == 1
    assert "boom" in capsys.readouterr().err


def test_cli_direct_requires_notes_flag(monkeypatch, tmp_path):
    in_path = tmp_path / "clip_1.json"
    save_timeline(_timeline(), str(in_path))
    monkeypatch.setattr(sys, "argv", ["foley", "direct", str(in_path)])
    with pytest.raises(SystemExit):
        cli.main()


def test_cli_render_path_unaffected_by_direct_subcommand(monkeypatch):
    """Backward compatibility: a bare `foley <clip> ...` call (first token
    not literally "direct") must still dispatch to the original render
    flow, exactly as the README documents."""
    called = {}

    def fake_run(clip_path, **kwargs):
        called["clip_path"] = clip_path
        called.update(kwargs)
        return FoleyResult(out_path="x", timeline_path="y", mix_path="z")

    monkeypatch.setattr(cli, "run", fake_run)
    monkeypatch.setattr(sys, "argv", ["foley", "clip.mp4", "--tier", "ambience"])

    assert cli.main() == 0
    assert called["clip_path"] == "clip.mp4"
    assert called["tier"] == "ambience"


def test_apply_notes_appends_to_history(monkeypatch):
    def fake_execute(endpoint, arguments):
        return {"output": json.dumps({"duration": 6.12, "ambience": "wind", "events": []})}

    monkeypatch.setattr(direct, "execute_with_ledger", fake_execute)

    t = _timeline()
    assert len(t.history) == 0

    result = direct.apply_notes(t, "make it rain", model="test/model")
    assert len(result.history) == 1
    assert result.history[0]["notes"] == "make it rain"
    assert result.history[0]["model"] == "test/model"
    assert "REMOVED" in result.history[0]["changes"]

    # Run again to verify it appends
    result2 = direct.apply_notes(result, "add thunder", model="test/model2")
    assert len(result2.history) == 2
    assert result2.history[0]["notes"] == "make it rain"
    assert result2.history[1]["notes"] == "add thunder"
    assert result2.history[1]["model"] == "test/model2"
