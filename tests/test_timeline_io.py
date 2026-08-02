from foleyflow.models import Event, Timeline
from foleyflow.timeline_io import (
    dict_to_timeline,
    load_timeline,
    save_timeline,
    timeline_to_dict,
)

SAMPLE = Timeline(
    duration=6.0,
    ambience="gentle wind",
    events=[
        Event(name="footsteps on gravel", start_time=0.5, duration=2.0,
              intensity="med", kind="rhythmic"),
        Event(name="match striking", start_time=5.4, duration=0.3,
              intensity="high", kind="discrete"),
    ],
)

def test_dict_round_trip():
    assert dict_to_timeline(timeline_to_dict(SAMPLE)) == SAMPLE

def test_save_load_round_trip(tmp_path):
    path = str(tmp_path / "timeline.json")
    save_timeline(SAMPLE, path)
    assert load_timeline(path) == SAMPLE


def test_event_seed_round_trip_dict():
    """Frozen seed survives timeline_to_dict → dict_to_timeline (A.4 trap)."""
    t = Timeline(
        duration=4.0,
        ambience="wind",
        events=[
            Event(name="footsteps on gravel", start_time=0.5, duration=2.0,
                  intensity="med", kind="rhythmic", seed=12345),
            Event(name="match striking", start_time=3.0, duration=0.3,
                  intensity="high", kind="discrete"),  # no seed
        ],
    )
    d = timeline_to_dict(t)
    assert d["events"][0]["seed"] == 12345
    assert "seed" not in d["events"][1]  # omit when None
    back = dict_to_timeline(d)
    assert back.events[0].seed == 12345
    assert back.events[1].seed is None
    assert back == t


def test_event_seed_round_trip_save_load(tmp_path):
    t = Timeline(
        duration=4.0,
        ambience="wind",
        events=[
            Event(name="footsteps on gravel", start_time=0.5, duration=2.0,
                  intensity="med", kind="rhythmic", seed=999),
        ],
    )
    path = str(tmp_path / "seeded.json")
    save_timeline(t, path)
    back = load_timeline(path)
    assert back.events[0].seed == 999
    assert back == t


def test_event_without_seed_omits_key_and_loads_as_none():
    d = timeline_to_dict(SAMPLE)
    for ed in d["events"]:
        assert "seed" not in ed
    back = dict_to_timeline(d)
    for ev in back.events:
        assert ev.seed is None


def test_event_seed_zero_round_trips():
    """seed=0 is falsy — must not be treated as absent on write or load."""
    t = Timeline(
        duration=2.0,
        ambience="wind",
        events=[
            Event(name="rain on roof", start_time=0.0, duration=2.0,
                  intensity="low", kind="rhythmic", seed=0),
        ],
    )
    d = timeline_to_dict(t)
    assert d["events"][0]["seed"] == 0
    back = dict_to_timeline(d)
    assert back.events[0].seed == 0


def test_event_seed_null_json_loads_as_none():
    d = timeline_to_dict(SAMPLE)
    d["events"][0]["seed"] = None
    back = dict_to_timeline(d)
    assert back.events[0].seed is None
    assert "seed" not in timeline_to_dict(back)["events"][0]


def test_event_seed_non_int_is_rejected():
    d = timeline_to_dict(SAMPLE)
    d["events"][0]["seed"] = "123"
    try:
        dict_to_timeline(d)
    except TypeError as e:
        assert "seed" in str(e)
    else:
        raise AssertionError("expected TypeError for non-int seed")
