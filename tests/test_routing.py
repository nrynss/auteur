from foley.models import Event
from foley.routing import (
    RHYTHMIC_LOOP_S,
    _get_duration_limits,
    route_ambience,
    route_event,
)

CASSETTEAI = "cassetteai/sound-effects-generator"
ELEVENLABS = "fal-ai/elevenlabs/sound-effects/v2"
MMAUDIO = "fal-ai/mmaudio-v2/text-to-audio"


def _ev(name, kind="discrete", intensity="med", duration=1.0, start_time=0.0):
    return Event(name=name, start_time=start_time, duration=duration, intensity=intensity, kind=kind)


# -- All event names from samples/timelines/clip_1.json, clip_2.json, clip_3.json --

def test_footsteps_on_gravel_routes_cassetteai():
    req = route_event(_ev("footsteps on gravel", kind="rhythmic"))
    assert req.endpoint == CASSETTEAI
    assert req.category == "footsteps"

def test_match_strike_routes_mmaudio():
    req = route_event(_ev("match strike"))
    assert req.endpoint == MMAUDIO
    assert req.category == "impact_strike"

def test_wooden_door_creaking_routes_elevenlabs():
    req = route_event(_ev("wooden door creaking", kind="continuous"))
    assert req.endpoint == ELEVENLABS
    assert req.category == "door_creak"

def test_wooden_door_closing_routes_elevenlabs():
    req = route_event(_ev("wooden door closing"))
    assert req.endpoint == ELEVENLABS
    assert req.category == "door_creak"

def test_glass_on_wooden_table_rolling_routes_mmaudio():
    req = route_event(_ev("glass on wooden table rolling", kind="continuous"))
    assert req.endpoint == MMAUDIO
    assert req.category == "impact_strike"

def test_glass_on_wooden_table_stopping_routes_mmaudio():
    req = route_event(_ev("glass on wooden table stopping"))
    assert req.endpoint == MMAUDIO
    assert req.category == "impact_strike"

def test_footsteps_on_snow_routes_cassetteai():
    req = route_event(_ev("footsteps on snow", kind="rhythmic"))
    assert req.endpoint == CASSETTEAI
    assert req.category == "footsteps"

def test_metal_gate_squeak_routes_elevenlabs():
    req = route_event(_ev("metal gate squeak"))
    assert req.endpoint == ELEVENLABS
    assert req.category == "door_creak"

def test_unmatched_event_names_hit_default_category():
    # These must not be mis-routed to a specific category. In particular,
    # "matchbox" must not prefix-match the "strike" category's "match" idea —
    # routing.yaml deliberately omits "match" as a keyword so this stays default.
    for name, kind in [
        ("fabric rustling from jacket", "continuous"),
        ("wooden box handling", "discrete"),
        ("paper tearing from matchbox", "discrete"),
    ]:
        req = route_event(_ev(name, kind=kind))
        assert req.category == "default"
        assert req.endpoint == CASSETTEAI


# -- Intensity qualifier behavior --

def test_low_intensity_appends_soft_distant():
    req = route_event(_ev("footsteps on gravel", intensity="low"))
    assert req.prompt == "footsteps on gravel, soft, distant"

def test_med_intensity_appends_nothing():
    req = route_event(_ev("footsteps on gravel", intensity="med"))
    assert req.prompt == "footsteps on gravel"

def test_high_intensity_appends_close_loud_sharp():
    req = route_event(_ev("footsteps on gravel", intensity="high"))
    assert req.prompt == "footsteps on gravel, close, loud, sharp"


# -- Duration handling --

def test_discrete_event_duration_gets_headroom():
    req = route_event(_ev("wooden box handling", kind="discrete", duration=0.8))
    assert req.duration_s == 1.3  # 0.8 + 0.5 headroom

def test_continuous_event_duration_gets_headroom():
    req = route_event(_ev("wooden door creaking", kind="continuous", duration=1.25))
    assert abs(req.duration_s - 1.75) < 1e-9

def test_rhythmic_events_get_loop_duration_regardless_of_event_duration():
    for duration in [0.5, 1.7, 6.0, 30.0]:
        req = route_event(_ev("footsteps on gravel", kind="rhythmic", duration=duration))
        assert 1.5 <= req.duration_s <= 2.0
    assert route_event(_ev("footsteps on gravel", kind="rhythmic", duration=99.0)).duration_s == RHYTHMIC_LOOP_S


# -- Ambience routing --

def test_route_ambience_returns_full_duration_plus_headroom():
    req = route_ambience("light wind rustling dry leaves", clip_duration=6.12)
    assert req.category == "ambience"
    assert req.endpoint == CASSETTEAI
    assert req.prompt == "light wind rustling dry leaves"
    assert abs(req.duration_s - 6.62) < 1e-9  # 6.12 + 0.5 headroom


# -- Clamping --

def test_clamping_fires_and_warns_on_absurd_durations(capsys):
    lo, hi = _get_duration_limits(MMAUDIO)

    # Way over max: a huge continuous event.
    req = route_event(_ev("glass on wooden table rolling", kind="continuous", duration=999.0))
    assert req.duration_s == hi
    out = capsys.readouterr().out
    assert "clamping" in out.lower()

    # Way under min: a tiny discrete event with negative headroom impossible,
    # but duration + headroom can still land below the placeholder floor if
    # the floor is raised later — exercise the clamp path directly via a
    # near-zero duration event.
    req_low = route_event(_ev("match strike", kind="discrete", duration=0.0))
    # 0.0 + 0.5 headroom = 0.5s, below the 1.0s placeholder floor.
    assert req_low.duration_s == lo
    out_low = capsys.readouterr().out
    assert "clamping" in out_low.lower()
