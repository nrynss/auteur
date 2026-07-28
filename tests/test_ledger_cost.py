"""Cost-estimation tests for foleyflow.ledger.

The estimate produced here is shown to users before a billable regeneration
(SPEC.md §9.4) and is what the ledger records when a provider reports no actual
cost. Under-reporting is the dangerous direction, so several of these assert
that an unresolvable duration does NOT collapse to $0.
"""

import pytest

from foleyflow.ledger import estimate_cost
from foleyflow.providers.base import ModelSpec
from foleyflow.providers.config import get_model_spec

CASSETTEAI = "cassetteai/sound-effects-generator"
ELEVENLABS = "fal-ai/elevenlabs/sound-effects/v2"
LTX = "fal-ai/ltx-2.3/text-to-video"
KLING = "fal-ai/kling-video/v3/pro/text-to-video"


# -- per-call pricing --

def test_per_call_model_ignores_duration():
    assert estimate_cost(CASSETTEAI, {"prompt": "rain", "duration": 8}) == 0.02
    assert estimate_cost(CASSETTEAI, {"prompt": "rain"}) == 0.02


def test_unknown_endpoint_falls_back_to_default():
    assert estimate_cost("some/unlisted-model", {}) == 0.01


# -- per-second pricing --

def test_per_second_model_multiplies_by_duration():
    assert estimate_cost(LTX, {"duration": 10}) == pytest.approx(0.60)


def test_per_second_model_uses_declared_default_when_duration_absent():
    """Matches the pre-refactor behavior, which defaulted LTX to 6s."""
    assert estimate_cost(LTX, {"prompt": "a drone shot"}) == pytest.approx(0.36)


def test_kling_default_duration_matches_pre_refactor():
    assert estimate_cost(KLING, {"prompt": "x"}) == pytest.approx(5 * 0.168)


# -- the field-name coupling bug --

def test_duration_is_read_from_the_configured_field_name(monkeypatch):
    """Regression: estimate_cost used to look only for the literal key
    "duration". Because the argument field name is per-model config, a
    per-second model naming it `duration_seconds` priced every call at $0.
    """
    spec = ModelSpec(
        model_id="vendor/per-second-sfx",
        adapter="fal",
        role="sfx",
        cost_per_second_usd=0.001,
        duration_field="duration_seconds",
    )
    monkeypatch.setattr("foleyflow.ledger.get_model_spec", lambda _id: spec)

    cost = estimate_cost("vendor/per-second-sfx", {"text": "door", "duration_seconds": 15})
    assert cost == pytest.approx(0.015), "duration_seconds must be found"
    assert cost > 0


def test_unresolvable_duration_warns_and_never_reports_zero(monkeypatch, capsys):
    spec = ModelSpec(
        model_id="vendor/per-second-sfx",
        adapter="fal",
        role="sfx",
        cost_per_second_usd=0.001,
        duration_field="duration_seconds",
    )
    monkeypatch.setattr("foleyflow.ledger.get_model_spec", lambda _id: spec)

    cost = estimate_cost("vendor/per-second-sfx", {"text": "door"})
    assert cost > 0, "a spend guard must never silently estimate $0"
    assert "WARNING" in capsys.readouterr().out


def test_zero_and_garbage_durations_do_not_produce_zero(monkeypatch):
    spec = ModelSpec(
        model_id="vendor/per-second",
        adapter="fal",
        role="video",
        cost_per_second_usd=0.05,
        cost_per_call_usd=0.25,
        duration_field="duration",
    )
    monkeypatch.setattr("foleyflow.ledger.get_model_spec", lambda _id: spec)

    for arguments in ({"duration": 0}, {"duration": None}, {"duration": "abc"}):
        assert estimate_cost("vendor/per-second", arguments) == 0.25


# -- config integrity --

@pytest.mark.parametrize("model_id", [LTX, "fal-ai/ltx-2.3/image-to-video", KLING])
def test_every_per_second_model_declares_a_default_duration(model_id):
    """Guards the class of bug rather than one instance: a per-second model
    with no default_duration_s can be priced at $0 by a call that omits it."""
    spec = get_model_spec(model_id)
    assert spec.cost_per_second_usd is not None
    assert spec.default_duration_s, f"{model_id} needs default_duration_s"


def test_sfx_models_are_priced_per_call_not_per_second():
    for model_id in (CASSETTEAI, ELEVENLABS, "fal-ai/mmaudio-v2/text-to-audio"):
        spec = get_model_spec(model_id)
        assert spec.cost_per_call_usd is not None
        assert spec.cost_per_second_usd is None
