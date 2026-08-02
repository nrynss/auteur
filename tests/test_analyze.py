import json
import pytest
from foleyflow.models import Event, Timeline
from foleyflow.analyze import parse_timeline_json

def test_parse_timeline_json_valid():
    raw_json = """
    {
      "duration": 6.0,
      "ambience": "light wind",
      "events": [
        {
          "name": "footsteps on gravel",
          "start_time": 0.5,
          "duration": 2.0,
          "intensity": "med",
          "kind": "rhythmic"
        }
      ]
    }
    """
    timeline = parse_timeline_json(raw_json)
    assert timeline.duration == 6.0
    assert timeline.ambience == "light wind"
    assert len(timeline.events) == 1
    assert timeline.events[0].name == "footsteps on gravel"
    assert timeline.events[0].start_time == 0.5
    assert timeline.events[0].duration == 2.0
    assert timeline.events[0].intensity == "med"
    assert timeline.events[0].kind == "rhythmic"

def test_parse_timeline_json_drops_nameless_events():
    raw_json = json.dumps({
        "duration": 6.0,
        "ambience": "wind",
        "events": [
            {"name": "", "start_time": 1.0, "duration": 0.5, "intensity": "med", "kind": "discrete"},
            {"start_time": 2.0, "duration": 0.5, "intensity": "med", "kind": "discrete"},
            {"name": "door creak", "start_time": 3.0, "duration": 0.5, "intensity": "med", "kind": "discrete"},
        ]
    })
    timeline = parse_timeline_json(raw_json, clip_duration=6.0)
    assert len(timeline.events) == 1
    assert timeline.events[0].name == "door creak"

def test_parse_timeline_json_drops_events_without_start_time():
    raw_json = json.dumps({
        "duration": 6.0,
        "ambience": "wind",
        "events": [
            {"name": "mystery sound", "duration": 0.5, "intensity": "med", "kind": "discrete"},
        ]
    })
    timeline = parse_timeline_json(raw_json, clip_duration=6.0)
    assert len(timeline.events) == 0

def test_parse_timeline_json_clamps_out_of_range_times():
    raw_json = json.dumps({
        "duration": 99.0,  # model-reported duration is NOT used for clamping
        "ambience": "wind",
        "events": [
            {"name": "early sound", "start_time": -1.0, "duration": 0.5, "intensity": "med", "kind": "discrete"},
            {"name": "late sound", "start_time": 10.0, "duration": 2.0, "intensity": "med", "kind": "discrete"},
            {"name": "long sound", "start_time": 4.0, "duration": 5.0, "intensity": "med", "kind": "continuous"},
        ]
    })
    timeline = parse_timeline_json(raw_json, clip_duration=6.0)
    early, late, long_ev = timeline.events
    assert early.start_time == 0.0
    assert late.start_time == 6.0       # clamped to clip end (ffprobe duration)
    assert late.duration == 0.0
    assert long_ev.start_time == 4.0
    assert long_ev.duration == 2.0      # end clamped to clip end

def test_parse_timeline_json_normalizes_bad_kind_and_intensity():
    raw_json = json.dumps({
        "duration": 6.0,
        "ambience": "wind",
        "events": [
            {"name": "weird sound", "start_time": 1.0, "duration": 0.5,
             "intensity": "extreme", "kind": "looping"},
            {"name": "shouty sound", "start_time": 2.0, "duration": 0.5,
             "intensity": "HIGH", "kind": "Discrete"},
        ]
    })
    timeline = parse_timeline_json(raw_json, clip_duration=6.0)
    weird, shouty = timeline.events
    assert weird.kind == "discrete"       # safe default
    assert weird.intensity == "med"       # safe default
    assert shouty.kind == "discrete"      # case-normalized
    assert shouty.intensity == "high"

def test_parse_timeline_json_preserves_optional_seed():
    """A.4 trap on the analyze path: seed must not be silently dropped."""
    raw_json = json.dumps({
        "duration": 6.0,
        "ambience": "wind",
        "events": [
            {"name": "footsteps on gravel", "start_time": 0.5, "duration": 2.0,
             "intensity": "med", "kind": "rhythmic", "seed": 424242},
            {"name": "match strike", "start_time": 3.0, "duration": 0.3,
             "intensity": "high", "kind": "discrete"},
        ],
    })
    timeline = parse_timeline_json(raw_json, clip_duration=6.0)
    assert timeline.events[0].seed == 424242
    assert timeline.events[1].seed is None


def test_parse_timeline_json_preserves_seed_zero():
    raw_json = json.dumps({
        "duration": 4.0,
        "ambience": "wind",
        "events": [
            {"name": "rain", "start_time": 0.0, "duration": 2.0,
             "intensity": "low", "kind": "rhythmic", "seed": 0},
        ],
    })
    timeline = parse_timeline_json(raw_json, clip_duration=4.0)
    assert timeline.events[0].seed == 0


def test_parse_timeline_json_preserves_optional_source_duration_s():
    """A.4 trap on the analyze path: source_duration_s must not be dropped."""
    raw_json = json.dumps({
        "duration": 6.0,
        "ambience": "wind",
        "events": [
            {"name": "match strike", "start_time": 1.0, "duration": 0.5,
             "intensity": "high", "kind": "discrete", "source_duration_s": 2.0},
            {"name": "footsteps on gravel", "start_time": 2.0, "duration": 2.0,
             "intensity": "med", "kind": "rhythmic"},
        ],
    })
    timeline = parse_timeline_json(raw_json, clip_duration=6.0)
    assert timeline.events[0].source_duration_s == 2.0
    assert timeline.events[1].source_duration_s is None


def test_parse_timeline_json_preserves_source_duration_s_zero():
    """0.0 is falsy but a real freeze — must not be treated as absent."""
    raw_json = json.dumps({
        "duration": 4.0,
        "ambience": "wind",
        "events": [
            {"name": "match strike", "start_time": 1.0, "duration": 0.3,
             "intensity": "high", "kind": "discrete", "source_duration_s": 0.0},
        ],
    })
    timeline = parse_timeline_json(raw_json, clip_duration=4.0)
    assert len(timeline.events) == 1
    assert timeline.events[0].source_duration_s == 0.0


def test_parse_timeline_json_drops_event_on_invalid_source_duration_s(capsys):
    """Invalid freeze must hard-drop the event, not soft-null (which re-bills)."""
    raw_json = json.dumps({
        "duration": 6.0,
        "ambience": "wind",
        "events": [
            {"name": "bad freeze", "start_time": 1.0, "duration": 0.5,
             "intensity": "high", "kind": "discrete",
             "source_duration_s": "2.0"},
            {"name": "negative freeze", "start_time": 2.0, "duration": 0.5,
             "intensity": "med", "kind": "discrete",
             "source_duration_s": -1.0},
            {"name": "ok event", "start_time": 3.0, "duration": 0.5,
             "intensity": "med", "kind": "discrete"},
        ],
    })
    timeline = parse_timeline_json(raw_json, clip_duration=6.0)
    assert len(timeline.events) == 1
    assert timeline.events[0].name == "ok event"
    assert timeline.events[0].source_duration_s is None
    out = capsys.readouterr().out.lower()
    assert "dropping" in out
    assert "source_duration_s" in out


def test_parse_timeline_json_with_markdown_fences():
    raw_json = """```json
    {
      "duration": 5.5,
      "ambience": "room tone",
      "events": []
    }
    ```"""
    timeline = parse_timeline_json(raw_json)
    assert timeline.duration == 5.5
    assert timeline.ambience == "room tone"
    assert len(timeline.events) == 0
