"""Analysis through Genblaze — the last provider call outside the spine.

Fully offline: the vendor wire is stubbed at ``execute_with_ledger`` (same seam
the SFX lifts use). The Genblaze pipeline — one step, timing, provider_payload
envelope — runs for real.
"""

from __future__ import annotations

import pytest

from foleyflow import ledger
from foleyflow.providers import genblaze as gb
from foleyflow.providers.genblaze import (
    AnalysisProvider,
    GoogleAnalysisProvider,
    OpenAIAnalysisProvider,
    run_analysis_step,
)


@pytest.fixture(autouse=True)
def _clear_provider_cache():
    gb._PROVIDER_CACHE.clear()
    yield
    gb._PROVIDER_CACHE.clear()


def test_google_and_openai_declare_analysis_providers():
    assert isinstance(gb.get_provider("google"), GoogleAnalysisProvider)
    assert isinstance(gb.get_provider("openai"), OpenAIAnalysisProvider)


def test_run_analysis_step_goes_through_genblaze_and_the_ledger(monkeypatch):
    calls = []

    def fake_execute(endpoint, arguments, adapter=None, **kw):
        calls.append({"endpoint": endpoint, "arguments": arguments,
                      "adapter": getattr(adapter, "name", adapter)})
        return {"output": '{"duration": 1.0, "ambience": "wind", "events": []}'}

    monkeypatch.setattr(ledger, "execute_with_ledger", fake_execute)

    raw = run_analysis_step(
        "gemini-2.5-flash",
        {"prompt": "analyze this", "video_urls": ["file:///tmp/x.mp4"],
         "model": "gemini-2.5-flash"},
    )
    assert raw["output"].startswith("{")
    assert len(calls) == 1
    assert calls[0]["endpoint"] == "gemini-2.5-flash"
    assert calls[0]["adapter"] == "google"
    assert calls[0]["arguments"]["prompt"] == "analyze this"


def test_run_analysis_step_routes_openai_models_to_the_openai_adapter(monkeypatch):
    seen = {}

    def fake_execute(endpoint, arguments, adapter=None, **kw):
        seen["adapter"] = getattr(adapter, "name", None)
        seen["endpoint"] = endpoint
        return {"output": '{"duration": 1.0, "ambience": "room tone", "events": []}'}

    monkeypatch.setattr(ledger, "execute_with_ledger", fake_execute)

    raw = run_analysis_step(
        "gpt-5.6-luna",
        {"prompt": TIMELINE_PROMPT_SNIPPET, "video_urls": ["file:///tmp/c.mp4"]},
    )
    assert seen["adapter"] == "openai"
    assert seen["endpoint"] == "gpt-5.6-luna"
    assert "ambience" in raw["output"]


TIMELINE_PROMPT_SNIPPET = "You are a professional Foley artist"


def test_analyze_video_direct_uses_genblaze_not_bare_ledger(monkeypatch, tmp_path):
    """The product path must not call execute_with_ledger outside Genblaze."""
    from foleyflow import analyze

    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"not-a-real-mp4")

    monkeypatch.setattr(analyze, "get_video_duration", lambda p: 3.0)
    monkeypatch.setattr(analyze, "upload_video", lambda p, model_id=None: f"file://{p}")

    def fake_run(endpoint, arguments, **kw):
        assert endpoint == "gpt-5.6-luna"
        assert arguments["video_urls"]
        return {
            "output": (
                '{"duration": 3.0, "ambience": "wind", "events": ['
                '{"name": "footsteps on gravel", "start_time": 0.5, '
                '"duration": 1.0, "intensity": "med", "kind": "rhythmic"}]}'
            )
        }

    monkeypatch.setattr(analyze, "_vlm_call", fake_run)
    # Also ensure the module no longer exposes a direct ledger import that
    # callers might reach around Genblaze.
    assert not hasattr(analyze, "execute_with_ledger")

    timeline = analyze.analyze_video_direct(str(clip), model="gpt-5.6-luna")
    assert timeline.duration == 3.0
    assert timeline.ambience == "wind"
    assert len(timeline.events) == 1
    assert timeline.events[0].name == "footsteps on gravel"


def test_analysis_provider_records_failures_on_the_step(monkeypatch):
    def boom(endpoint, arguments, adapter=None, **kw):
        raise RuntimeError("quota exceeded")

    monkeypatch.setattr(ledger, "execute_with_ledger", boom)
    provider = AnalysisProvider(adapter=type("A", (), {"name": "stub"})())
    from genblaze_core import Step, StepType, Modality

    step = Step(
        step_id="s1", provider="stub", model="gpt-5.6-luna",
        modality=Modality.TEXT, step_type=StepType.GENERATE,
        params={"prompt": "x"},
    )
    from genblaze_core.exceptions import ProviderError
    with pytest.raises(ProviderError, match="quota exceeded"):
        provider.generate(step)
