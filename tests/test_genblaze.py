"""Genblaze as the orchestration spine — tiers, lifted providers, fan-out, stages.

Fully offline and credential-free. Nothing here reads ELEVENLABS_API_KEY,
FAL_KEY or FOLEYFLOW_SFX_WORKER_TOKEN, and the only thing stubbed is the wire
itself: `ledger.execute_with_ledger` and `net.download` for the lifted adapters,
and the adapter's own HTTP client for the worker-auth tests. The Genblaze
pipelines — step construction, fan-out, per-step status, timing, retry
accounting — all run for real, which is the point: if the orchestration were
mocked these tests would be asserting the mock.
"""

import os

import pytest

from foleyflow import ledger, net, routing, stages, stems, tiers
from foleyflow.models import Event
from foleyflow.providers import genblaze as gb
from foleyflow.providers.config import ConfigError, _validate
from foleyflow.routing import SfxRequest

MOSS = "openmoss/moss-soundeffect-v2"
FAL_ELEVEN = "fal-ai/elevenlabs/sound-effects/v2"
ELEVEN = "eleven_text_to_sound_v2"


def _req(endpoint=MOSS, prompt="boot scuff on gravel", duration_s=2.0,
         tier="test", category="footsteps"):
    return SfxRequest(endpoint=endpoint, prompt=prompt, duration_s=duration_s,
                      category=category, tier=tier)


# -- tier table --------------------------------------------------------------

def test_the_three_tiers_are_declared_and_chain_as_documented():
    table = tiers.load_tiers()
    assert set(table) == {"paid", "fallback", "test"}
    assert table["paid"].endpoint == ELEVEN
    assert table["fallback"].endpoint == FAL_ELEVEN
    assert table["test"].endpoint == MOSS

    assert [t.name for t in tiers.tier_chain("paid")] == ["paid", "fallback"]
    # The free local tier deliberately does not escalate to a paid API.
    assert [t.name for t in tiers.tier_chain("test")] == ["test"]


def test_each_tier_resolves_to_a_distinct_adapter():
    """The whole point of the tier shape: three routes, one code path."""
    assert tiers.get_tier("paid").adapter == "elevenlabs"
    assert tiers.get_tier("fallback").adapter == "fal"
    assert tiers.get_tier("test").adapter == "local"


def test_default_tier_keeps_the_existing_cache_keys(monkeypatch):
    """Regression guard on ~170 already-cached stems.

    The SFX cache is keyed on the endpoint. If the shipped default resolved to
    anything but the endpoint the routing categories already declare, every
    cached object in B2 would be orphaned the moment this landed.
    """
    monkeypatch.delenv(tiers.ENV_TIER, raising=False)
    assert tiers.active_tier_name() == "test"
    assert tiers.resolve_endpoint(MOSS) == MOSS

    from foleyflow.sfx import cache_key_raw
    # Byte-for-byte the string the pre-tier code hashed.
    assert cache_key_raw(MOSS, "boot scuff", 2.0).startswith(
        f"{MOSS}|boot scuff|2.00|x=")


def test_env_overrides_the_configured_tier(monkeypatch):
    monkeypatch.setenv(tiers.ENV_TIER, "paid")
    assert tiers.active_tier_name() == "paid"
    assert tiers.resolve_endpoint(MOSS) == ELEVEN


def test_an_unknown_tier_in_the_env_fails_loudly(monkeypatch):
    monkeypatch.setenv(tiers.ENV_TIER, "gratis")
    with pytest.raises(tiers.TierError, match="gratis"):
        tiers.active_tier_name()


def test_a_fallback_cycle_terminates():
    """A config typo must not hang a render."""
    config = {"models": {MOSS: {"adapter": "local", "role": "sfx"}},
              "sfx_tiers": {"active": "a",
                            "a": {"endpoint": MOSS, "fallback": "b"},
                            "b": {"endpoint": MOSS, "fallback": "a"}}}
    chain = tiers.tier_chain("a", _config=config)
    assert [t.name for t in chain] == ["a", "b"]


def test_config_rejects_a_tier_pointing_at_an_undeclared_model():
    raw = {"models": {}, "routing": {"categories": [{"name": "ambience"}]},
           "sfx_tiers": {"nowhere": {"endpoint": "vendor/does-not-exist"}}}
    with pytest.raises(ConfigError, match="undeclared model"):
        _validate(raw, "providers.toml")


def test_config_rejects_an_active_tier_that_does_not_exist():
    raw = {"models": {MOSS: {"adapter": "local", "role": "sfx"}},
           "routing": {"categories": [{"name": "ambience"}]},
           "sfx_tiers": {"active": "paid", "test": {"endpoint": MOSS}}}
    with pytest.raises(ConfigError, match="active="):
        _validate(raw, "providers.toml")


# -- routing through the tier ------------------------------------------------

def test_routing_stamps_the_tier_on_every_request(monkeypatch):
    monkeypatch.delenv(tiers.ENV_TIER, raising=False)
    request = routing.route_event(Event(name="footsteps on gravel",
                                        start_time=1.0, duration=1.0,
                                        intensity="med", kind="discrete"))
    assert request.tier == "test"
    assert request.endpoint == MOSS
    assert request.category == "footsteps"


def test_escalation_re_clamps_against_the_new_endpoints_bounds(monkeypatch):
    """A 25s local bed cannot be asked of an endpoint that caps at 22s."""
    monkeypatch.setenv(tiers.ENV_TIER, "paid")
    escalated = routing.escalate(_req(endpoint=ELEVEN, duration_s=25.0,
                                      tier="paid"))
    assert [r.endpoint for r in escalated] == [FAL_ELEVEN]
    assert escalated[0].duration_s == 22.0
    assert escalated[0].prompt == "boot scuff on gravel"


def test_the_offline_tier_does_not_escalate():
    assert routing.escalate(_req(tier="test")) == []


# -- the lifted providers ----------------------------------------------------

def test_provider_resolution_matches_the_tier_adapters():
    assert isinstance(gb.get_provider("local"), gb.LocalSfxProvider)
    assert isinstance(gb.get_provider("fal"), gb.FalSfxProvider)


def test_the_elevenlabs_provider_is_the_shipped_connector_and_needs_no_key(monkeypatch):
    """`genblaze-elevenlabs` is used as-is — there is nothing to lift.

    Constructing it must not require a credential: app startup and the whole
    test suite resolve providers, and only an actual generation should care
    whether a key exists.
    """
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    from genblaze_elevenlabs import ElevenLabsSFXProvider

    provider = gb.get_provider("elevenlabs", refresh=True)
    assert isinstance(provider, ElevenLabsSFXProvider)
    assert provider.name == "elevenlabs-sfx"


def test_a_lifted_adapter_produces_a_file_asset(monkeypatch, tmp_path):
    """Every tier hands back a local file, so the caller can treat them alike.

    An https-only guard (`genblaze_core.validate_asset_url`) would reject the
    self-hosted worker's plain-HTTP loopback URL, so providers materialize their
    own bytes rather than passing a URL along.
    """
    from genblaze_core import Step

    monkeypatch.setattr(
        ledger, "execute_with_ledger",
        lambda endpoint, arguments, **kw: {"audio": {"url": "https://x.invalid/a.wav"}})

    def _download(url, dest_path, timeout=60.0, **kw):
        with open(dest_path, "wb") as fh:
            fh.write(b"RIFFfake")
        return dest_path

    monkeypatch.setattr(net, "download", _download)

    provider = gb.FalSfxProvider(output_dir=str(tmp_path))
    step = provider.generate(Step(provider=provider.name, model=FAL_ELEVEN,
                                  prompt="thunder",
                                  params={"text": "thunder",
                                          "duration_seconds": 2.0}))
    assert len(step.assets) == 1
    path = gb.asset_local_path(step.assets[0])
    assert os.path.exists(path)
    assert open(path, "rb").read() == b"RIFFfake"


# -- self-hosted worker authentication ---------------------------------------

def test_no_token_means_no_authorization_header(monkeypatch):
    """A loopback worker running open must keep working."""
    from foleyflow.providers.local import LocalAdapter

    monkeypatch.delenv("FOLEYFLOW_SFX_WORKER_TOKEN", raising=False)
    assert LocalAdapter().auth_headers() == {}


def test_a_blank_token_is_treated_as_unset(monkeypatch):
    """An exported-but-empty variable must not send `Bearer ` and 401."""
    from foleyflow.providers.local import LocalAdapter

    monkeypatch.setenv("FOLEYFLOW_SFX_WORKER_TOKEN", "   ")
    assert LocalAdapter().auth_headers() == {}


def test_the_token_is_sent_on_generate_and_on_the_file_download(monkeypatch):
    """Two requests, one credential. The download is the easy one to miss."""
    from foleyflow.providers.local import LocalAdapter

    monkeypatch.setenv("FOLEYFLOW_SFX_WORKER_TOKEN", "s3cret")
    adapter = LocalAdapter(base_url="http://worker.invalid:8600")

    assert adapter.auth_headers() == {"Authorization": "Bearer s3cret"}
    assert adapter.download_headers(
        "http://worker.invalid:8600/files/abc.wav") == {"Authorization": "Bearer s3cret"}


def test_the_worker_token_never_rides_along_to_a_vendor_cdn(monkeypatch):
    """`net.download` is shared with fal and ElevenLabs.

    A blanket Authorization header would hand this project's worker token to
    whichever CDN a paid tier happened to return a URL from.
    """
    from foleyflow.providers.local import LocalAdapter

    monkeypatch.setenv("FOLEYFLOW_SFX_WORKER_TOKEN", "s3cret")
    adapter = LocalAdapter(base_url="http://worker.invalid:8600")

    assert adapter.download_headers("https://cdn.fal.media/x.wav") == {}
    # A prefix that merely *starts* like the base URL is a different host.
    assert adapter.download_headers("http://worker.invalid:8600.evil.test/x") == {}


def test_the_token_is_not_part_of_the_cache_key(monkeypatch):
    """A transport credential does not change the audio.

    Folding it in would orphan every cached stem the moment it was set or
    rotated — and the shared cache is the demo's reliability guarantee.
    """
    from foleyflow.sfx import cache_key

    monkeypatch.delenv("FOLEYFLOW_SFX_WORKER_TOKEN", raising=False)
    without = cache_key(MOSS, "boot scuff on gravel", 2.0)
    monkeypatch.setenv("FOLEYFLOW_SFX_WORKER_TOKEN", "s3cret")
    assert cache_key(MOSS, "boot scuff on gravel", 2.0) == without


# -- fan-out and targeted retry ----------------------------------------------

@pytest.fixture
def stub_providers(monkeypatch, tmp_path):
    """Route every tier at an in-process provider that writes a tiny file.

    A subclass of the real lift rather than a fake pipeline: the fan-out,
    per-step status and retry accounting under test are Genblaze's, and a fake
    pipeline would be testing the fake.
    """
    from genblaze_core import Asset
    from genblaze_core._utils import local_file_url
    from genblaze_core.exceptions import ProviderError

    calls = []
    failing = set()

    class _Stub(gb.FoleyAdapterProvider):
        name = "stub-sfx"

        def generate(self, step, config=None):
            calls.append((step.model, step.prompt))
            if step.model in failing:
                raise ProviderError(f"{step.model} is down")
            path = tmp_path / f"{step.step_id}.wav"
            path.write_bytes(b"RIFFstub")
            step.assets.append(Asset(url=local_file_url(path.resolve()),
                                     media_type="audio/wav"))
            return step

    monkeypatch.setattr(stems, "_provider_for", lambda endpoint: _Stub())
    return {"calls": calls, "failing": failing}


def test_independent_stems_fan_out_in_one_genblaze_run(stub_providers):
    requests = [_req(prompt=f"stem {i}") for i in range(5)]
    results = stems.generate_many(requests, max_concurrency=4)

    assert all(r.ok for r in results)
    assert len(stub_providers["calls"]) == 5
    # One run for the whole fan-out, not one per stem.
    run_ids = {a.genblaze_run_id for r in results for a in r.attempts}
    assert len(run_ids) == 1


def test_only_the_failed_stem_is_retried_and_it_escalates(stub_providers, monkeypatch):
    """SPEC §8.2: retry only the failed stem, not the whole batch."""
    monkeypatch.setenv(tiers.ENV_TIER, "paid")
    stub_providers["failing"].add(ELEVEN)

    good = [_req(endpoint=ELEVEN, prompt=f"fine {i}", tier="paid") for i in range(3)]
    # The one that fails is the paid tier's; it escalates to fal.
    results = stems.generate_many(good, max_concurrency=3)

    # Round one: three paid attempts, all failed. Round two: three fal attempts.
    attempted = [model for model, _prompt in stub_providers["calls"]]
    assert attempted.count(ELEVEN) == 3
    assert attempted.count(FAL_ELEVEN) == 3
    assert all(r.ok for r in results)
    assert all(r.tier == "fallback" for r in results)
    assert [a.status for a in results[0].attempts] == ["failed", "succeeded"]


def test_a_stem_that_exhausts_its_chain_fails_without_taking_the_batch_down(
        stub_providers, monkeypatch):
    monkeypatch.setenv(tiers.ENV_TIER, "paid")
    stub_providers["failing"].update({ELEVEN, FAL_ELEVEN})

    results = stems.generate_many(
        [_req(endpoint=ELEVEN, prompt="doomed", tier="paid")])
    assert not results[0].ok
    assert "is down" in results[0].error
    assert [a.status for a in results[0].attempts] == ["failed", "failed"]


def test_generate_one_raises_when_every_tier_fails(stub_providers):
    stub_providers["failing"].add(MOSS)
    with pytest.raises(stems.StemGenerationError, match="is down"):
        stems.generate_one(_req())


def test_a_cache_probe_hit_skips_generation_entirely(stub_providers, tmp_path):
    hit = tmp_path / "already-there.wav"
    hit.write_bytes(b"RIFFcached")

    results = stems.generate_many([_req()], probe=lambda _r: str(hit))
    assert results[0].cached is True
    assert results[0].local_path == str(hit)
    assert stub_providers["calls"] == []


# -- local stages as Genblaze providers --------------------------------------

def test_local_stages_run_in_order_and_are_recorded():
    order = []
    log = stages.StageLog()

    results = stages.run_stages("t", [
        ("mix", lambda p: order.append("mix") or {"n": 1}),
        ("mux", lambda p: order.append("mux") or {"n": 2}),
        ("qa", lambda p: order.append("qa") or {"n": 3}),
    ], log=log)

    assert order == ["mix", "mux", "qa"]
    assert results["qa"] == {"n": 3}
    assert [r.name for r in log.records] == ["mix", "mux", "qa"]
    assert all(r.status == "succeeded" for r in log.records)
    assert all(r.provider == "foleyflow-ffmpeg" for r in log.records)
    assert all(r.genblaze_run_id for r in log.records)
    assert log.current == "qa"


def test_a_failed_stage_stops_the_rest_and_says_which_one():
    log = stages.StageLog()
    reached = []

    def _boom(_params):
        raise RuntimeError("ffmpeg exploded")

    with pytest.raises(stages.StageFailed) as caught:
        stages.run_stages("t", [
            ("mix", lambda p: {"output_path": None}),
            ("mux", _boom),
            ("qa", lambda p: reached.append("qa") or {}),
        ], log=log)

    assert caught.value.record.name == "mux"
    assert "ffmpeg exploded" in caught.value.record.error
    assert reached == []
    # The partial history survives, so the job can record where it got to.
    assert [r.name for r in caught.value.log.records] == ["mix", "mux"]


def test_the_stage_log_sink_publishes_each_stage_as_it_lands():
    """A progress feed that only updates at the end is not a progress feed."""
    published = []
    log = stages.StageLog(sink=published.append)
    stages.run_stages("t", [("mix", lambda p: {}), ("mux", lambda p: {})], log=log)
    assert [r.name for r in published] == ["mix", "mux"]


def test_a_broken_sink_never_fails_a_stage_that_succeeded():
    def _boom(_record):
        raise RuntimeError("event log unreachable")

    log = stages.StageLog(sink=_boom)
    stages.run_stages("t", [("mix", lambda p: {"ok": True})], log=log)
    assert log.records[0].status == "succeeded"


def test_a_stage_output_path_becomes_the_steps_asset(tmp_path):
    out = tmp_path / "mix.wav"
    out.write_bytes(b"RIFF")
    provider = gb.FFmpegStageProvider(
        {"mix": lambda p: {"output_path": str(out), "media_type": "audio/wav"}})

    from genblaze_core import Step

    step = provider.generate(Step(provider=provider.name, model="mix"))
    assert gb.asset_local_path(step.assets[0]) == str(out)
    assert provider.result_for("mix")["media_type"] == "audio/wav"


def test_an_unregistered_stage_is_an_input_error_not_a_crash():
    from genblaze_core import Step
    from genblaze_core.exceptions import ProviderError

    provider = gb.FFmpegStageProvider({})
    with pytest.raises(ProviderError, match="no stage registered"):
        provider.generate(Step(provider=provider.name, model="nope"))


# -- the paid tier, end to end with the wire stubbed -------------------------

def test_the_paid_tier_runs_through_the_elevenlabs_connector(monkeypatch, tmp_path):
    """Proof the paid path is wired, not merely importable.

    The only thing replaced is the ElevenLabs HTTP client. Everything else is
    real: providers.toml's field mapping, `build_arguments`, the Genblaze
    pipeline, the connector's own `generate`, and our extraction of the local
    file it writes. No ELEVENLABS_API_KEY is needed because the client is never
    constructed.
    """
    from foleyflow.sfx import build_arguments

    monkeypatch.setenv(tiers.ENV_TIER, "paid")
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)

    request = _req(endpoint=ELEVEN, prompt="revolver hammer cocking",
                   duration_s=2.0, tier="paid")

    # providers.toml must name the fields the connector actually reads.
    assert build_arguments(request) == {"prompt": "revolver hammer cocking",
                                        "duration_seconds": 2.0}

    seen = {}

    class _FakeSfxEndpoint:
        @staticmethod
        def convert(**kwargs):
            seen.update(kwargs)
            return iter([b"ID3fake-mp3-bytes"])

    class _FakeClient:
        text_to_sound_effects = _FakeSfxEndpoint()

    provider = gb.get_provider("elevenlabs", refresh=True)
    provider._output_dir = tmp_path
    provider._client = _FakeClient()
    monkeypatch.setattr(stems, "_provider_for", lambda endpoint: provider)

    result = stems.generate_one(request)

    assert result.ok
    assert result.tier == "paid"
    assert result.endpoint == ELEVEN
    assert seen["text"] == "revolver hammer cocking"
    assert seen["duration_seconds"] == 2.0
    assert open(result.local_path, "rb").read() == b"ID3fake-mp3-bytes"
    # Genblaze recorded the attempt, with its own run id.
    assert result.attempts[0].status == "succeeded"
    assert result.attempts[0].genblaze_run_id


def test_the_paid_tier_falls_back_to_fal_on_error(monkeypatch, tmp_path):
    """The escalation the demo depends on, exercised without either credential."""
    from genblaze_core import Asset
    from genblaze_core._utils import local_file_url
    from genblaze_core.exceptions import ProviderError

    monkeypatch.setenv(tiers.ENV_TIER, "paid")
    tried = []

    class _Eleven(gb.FoleyAdapterProvider):
        name = "stub-eleven"

        def generate(self, step, config=None):
            tried.append(step.model)
            raise ProviderError("401 Unauthorized")

    class _Fal(gb.FoleyAdapterProvider):
        name = "stub-fal"

        def generate(self, step, config=None):
            tried.append(step.model)
            path = tmp_path / f"{step.step_id}.mp3"
            path.write_bytes(b"ID3fal")
            step.assets.append(Asset(url=local_file_url(path.resolve()),
                                     media_type="audio/mpeg"))
            return step

    monkeypatch.setattr(
        stems, "_provider_for",
        lambda endpoint: _Eleven() if endpoint == ELEVEN else _Fal())

    result = stems.generate_one(_req(endpoint=ELEVEN, tier="paid"))

    assert tried == [ELEVEN, FAL_ELEVEN]
    assert result.tier == "fallback"
    assert open(result.local_path, "rb").read() == b"ID3fal"
    assert "401 Unauthorized" in result.attempts[0].error
