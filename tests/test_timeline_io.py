from foley.models import Event, Timeline
from foley.timeline_io import (
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
