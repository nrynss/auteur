"""Routing: event name -> category -> endpoint, prompt, and duration.

Categories now all resolve to the self-hosted tier (SPEC §9.1), so what these
assert is the **category** — the decision routing actually makes. Endpoint is
checked once, in test_every_category_routes_to_the_self_hosted_tier, so that
re-pointing the table is one edit here rather than ten.
"""

from foleyflow.models import Event
from foleyflow.routing import (
    RHYTHMIC_LOOP_S,
    _clamp_duration,
    _get_duration_limits,
    route_ambience,
    route_event,
)

SELF_HOSTED = "openmoss/moss-soundeffect-v2"
ELEVENLABS = "fal-ai/elevenlabs/sound-effects/v2"


def _ev(name, kind="discrete", intensity="med", duration=1.0, start_time=0.0):
    return Event(name=name, start_time=start_time, duration=duration, intensity=intensity, kind=kind)


# -- All event names from samples/timelines/clip_1.json, clip_2.json, clip_3.json --

def test_footsteps_on_gravel_is_the_footsteps_category():
    req = route_event(_ev("footsteps on gravel", kind="rhythmic"))
    assert req.category == "footsteps"

def test_match_strike_is_the_impact_category():
    req = route_event(_ev("match strike"))
    assert req.category == "impact_strike"

def test_wooden_door_creaking_is_the_door_category():
    req = route_event(_ev("wooden door creaking", kind="continuous"))
    assert req.category == "door_creak"

def test_wooden_door_closing_is_the_door_category():
    req = route_event(_ev("wooden door closing"))
    assert req.category == "door_creak"

def test_glass_rolling_is_the_impact_category():
    req = route_event(_ev("glass on wooden table rolling", kind="continuous"))
    assert req.category == "impact_strike"

def test_glass_stopping_is_the_impact_category():
    req = route_event(_ev("glass on wooden table stopping"))
    assert req.category == "impact_strike"

def test_footsteps_on_snow_is_the_footsteps_category():
    req = route_event(_ev("footsteps on snow", kind="rhythmic"))
    assert req.category == "footsteps"

def test_metal_gate_squeak_is_the_door_category():
    req = route_event(_ev("metal gate squeak"))
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
    assert req.prompt == "light wind rustling dry leaves"
    assert abs(req.duration_s - 6.62) < 1e-9  # 6.12 + 0.5 headroom


# -- Clamping --

def test_clamping_fires_and_warns_on_absurd_durations(capsys):
    lo, hi = _get_duration_limits(SELF_HOSTED)

    # Way over max: a huge continuous event.
    req = route_event(_ev("glass on wooden table rolling", kind="continuous", duration=999.0))
    assert req.duration_s == hi
    assert "clamping" in capsys.readouterr().out.lower()

    # The floor is exercised directly. route_event cannot reach it any more:
    # the smallest request it can build is 0.0 + 0.5 headroom, and the
    # self-hosted model's floor is 0.3 — below which there is not enough audio
    # for trim_onset to find a transient. Going through route_event here would
    # assert nothing while looking like it did.
    assert _clamp_duration(SELF_HOSTED, 0.05) == lo
    assert "clamping" in capsys.readouterr().out.lower()


def test_every_category_routes_to_the_self_hosted_tier():
    """§9.1: free local inference is the default; the paid tier is opt-in.

    A category pointing at fal is a run that dies on a missing key — those
    credits are gone — so this is a config assertion worth making loudly.
    """
    names = ["desert wind", "footsteps on gravel", "wooden door creaking",
             "match strike", "fabric rustling from jacket"]
    for name in names:
        assert route_event(_ev(name)).endpoint == SELF_HOSTED
    assert route_ambience("desert wind", 10.0).endpoint == SELF_HOSTED
