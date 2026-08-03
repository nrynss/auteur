"""Director mode (unit 7.12, docs/api.md §3.16) — contract tests.

Chat proposes, never applies: the endpoint never writes a timeline version, and
a turn's outcome is a `status` on the 200 response (proposed / no_changes /
invalid_output), never an HTTP error. The provider wire is stubbed at
`execute_with_ledger` (the same seam the analysis tests use); the ledger,
quota and storage behaviour underneath run for real.
"""

import pytest
from fastapi.testclient import TestClient

from foleyflow import director
from foleyflow.api.app import API_PREFIX, create_app
from foleyflow.api.jobs import JobRunner
from foleyflow.models import Event, Timeline
from foleyflow.providers.base import GenerationResult


@pytest.fixture
def store(tmp_path):
    from foleyflow.projects import ProjectStore
    from foleyflow.storage import LocalBackend
    return ProjectStore(LocalBackend(str(tmp_path / "b2")),
                        work_dir=str(tmp_path / "w"))


@pytest.fixture
def project(store):
    return store.create_project(title="Western standoff", input_key="k",
                                source_sha256="a" * 64,
                                source_filename="standoff.mp4", duration_s=15.04)


def _timeline():
    return Timeline(duration=15.04, ambience="desert wind", events=[
        Event(name="boot on gravel", start_time=0.0, duration=0.4,
              intensity="low", kind="rhythmic"),
        Event(name="boot on gravel", start_time=4.0, duration=0.4,
              intensity="low", kind="rhythmic"),
        Event(name="gunshot", start_time=11.0, duration=1.5,
              intensity="high", kind="discrete", source_duration_s=2.0),
    ])


def _body(timeline=None, *, instruction="move the footsteps 40 ms earlier",
          timeline_version=1):
    t = timeline or _timeline()
    return {
        "instruction": instruction,
        "timeline_version": timeline_version,
        "duration": t.duration,
        "ambience": t.ambience,
        "events": [
            {"name": e.name, "start_time": e.start_time, "duration": e.duration,
             "intensity": e.intensity, "kind": e.kind, "seed": e.seed,
             "source_duration_s": e.source_duration_s}
            for e in t.events
        ],
    }


def _stub_turn(monkeypatch, output: str, *, record=None):
    """Stub the provider seam; `record` captures (endpoint, arguments)."""
    calls = record if record is not None else []

    def fake_execute(endpoint, arguments, **kwargs):
        calls.append({"endpoint": endpoint, "arguments": arguments})
        return {"output": output, "model": endpoint}

    monkeypatch.setattr(director, "execute_with_ledger", fake_execute)
    return calls


def _client(store, project):
    store.save_timeline(project.id, _timeline())
    runner = JobRunner(store, max_workers=1)
    app = create_app(store, runner)
    return TestClient(app)


@pytest.fixture
def client(store):
    """A TestClient with no timeline saved — tests that need one seed it."""
    runner = JobRunner(store, max_workers=1)
    app = create_app(store, runner)
    with TestClient(app) as c:
        yield c
    runner.shutdown(wait=True)


# -- happy paths ------------------------------------------------------------

def test_director_returns_proposal_with_edits_and_document(monkeypatch, store, project):
    calls = []
    _stub_turn(monkeypatch, """{"events": [
        {"index": 0, "start_time": 0.0, "duration": 0.4},
        {"index": 1, "start_time": 3.96, "duration": 0.4},
        {"index": 2, "start_time": 11.0, "duration": 1.5}]}""", record=calls)
    with _client(store, project) as c:
        r = c.post(f"{API_PREFIX}/projects/{project.id}/director", json=_body())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "proposed"
    assert body["base_version"] == 1
    assert body["model"] == "gemini-3.5-flash-lite"
    assert body["instruction"] == "move the footsteps 40 ms earlier"
    # Exactly the one moved event, as old → new.
    assert body["edits"] == [{
        "index": 1,
        "old": {"start_time": 4.0, "duration": 0.4},
        "new": {"start_time": 3.96, "duration": 0.4},
    }]
    # The proposed document carries every event, index = array position.
    assert [e["index"] for e in body["timeline"]["events"]] == [0, 1, 2]
    assert body["timeline"]["events"][1]["start_time"] == pytest.approx(3.96)
    # The prompt got the whole current document and the instruction.
    assert calls[0]["endpoint"] == "gemini-3.5-flash-lite"
    prompt = calls[0]["arguments"]["prompt"]
    assert "boot on gravel" in prompt
    assert "move the footsteps 40 ms earlier" in prompt
    # The call is a billed, cost-stamped turn.
    assert body["cost_usd"] == pytest.approx(0.005)
    assert body["cost_source"] == "estimate"
    assert body["ledger_writable"] is True


def test_director_no_changes_when_the_model_echoes_the_document(monkeypatch, store, project):
    _stub_turn(monkeypatch, """{"events": [
        {"index": 0, "start_time": 0.0, "duration": 0.4},
        {"index": 1, "start_time": 4.0, "duration": 0.4},
        {"index": 2, "start_time": 11.0, "duration": 1.5}]}""")
    with _client(store, project) as c:
        r = c.post(f"{API_PREFIX}/projects/{project.id}/director", json=_body())
    body = r.json()
    assert r.status_code == 200
    assert body["status"] == "no_changes"
    assert body["edits"] == []


def test_director_invalid_output_is_a_200_turn_status_not_an_http_error(
        monkeypatch, store, project):
    """Garbage model output is a billed failed turn, not a 5xx — the call
    happened, so the UI must be able to say exactly that."""
    _stub_turn(monkeypatch, "I can't do that, Dave.")
    with _client(store, project) as c:
        r = c.post(f"{API_PREFIX}/projects/{project.id}/director", json=_body())
    body = r.json()
    assert r.status_code == 200, r.text
    assert body["status"] == "invalid_output"
    assert body["edits"] == []
    assert "unparseable" in body["message"]
    # The document comes back unchanged — nothing was silently applied.
    assert [e["start_time"] for e in body["timeline"]["events"]] == \
        [0.0, 4.0, 11.0]


def test_director_validation_clamps_out_of_range_placements(monkeypatch, store, project):
    """A −0.001s slip (common from a float-drifting model) clamps to 0 and is
    shown in the diff; a zero duration clamps to the minimum span; anything
    past the clip end clamps to the clip."""
    _stub_turn(monkeypatch, """{"events": [
        {"index": 0, "start_time": -0.04, "duration": 0.0},
        {"index": 1, "start_time": 3.96, "duration": 0.4},
        {"index": 2, "start_time": 99.0, "duration": 5.0}]}""")
    with _client(store, project) as c:
        r = c.post(f"{API_PREFIX}/projects/{project.id}/director", json=_body())
    body = r.json()
    assert r.status_code == 200
    assert body["status"] == "proposed"
    events = body["timeline"]["events"]
    assert events[0]["start_time"] == 0.0
    assert events[0]["duration"] == pytest.approx(0.01)  # MIN_PROPOSED_DURATION_S
    assert events[2]["start_time"] == pytest.approx(15.04)  # clipped
    assert events[2]["duration"] == pytest.approx(0.01)


def test_director_validation_refuses_a_hallucinated_index_set(monkeypatch, store, project):
    """Dropping, inventing, duplicating or reordering an index is a
    hallucination, not an edit the director asked for — the proposal would
    silently re-key every later event."""
    for bad in [
        # dropped the gunshot
        """{"events": [
            {"index": 0, "start_time": 0.0, "duration": 0.4},
            {"index": 1, "start_time": 3.96, "duration": 0.4}]}""",
        # invented a fourth event
        """{"events": [
            {"index": 0, "start_time": 0.0, "duration": 0.4},
            {"index": 1, "start_time": 3.96, "duration": 0.4},
            {"index": 2, "start_time": 11.0, "duration": 1.5},
            {"index": 3, "start_time": 2.0, "duration": 0.5}]}""",
        # reordered (index 2 first)
        """{"events": [
            {"index": 2, "start_time": 11.0, "duration": 1.5},
            {"index": 0, "start_time": 0.0, "duration": 0.4},
            {"index": 1, "start_time": 3.96, "duration": 0.4}]}""",
        # non-numeric placement
        """{"events": [
            {"index": 0, "start_time": 0.0, "duration": 0.4},
            {"index": 1, "start_time": "soon", "duration": 0.4},
            {"index": 2, "start_time": 11.0, "duration": 1.5}]}""",
    ]:
        _stub_turn(monkeypatch, bad)
        with _client(store, project) as c:
            r = c.post(f"{API_PREFIX}/projects/{project.id}/director", json=_body())
        body = r.json()
        assert r.status_code == 200, r.text
        assert body["status"] == "invalid_output", bad


def test_director_preserves_freezes_in_the_proposed_document(monkeypatch, store, project):
    """seed / source_duration_s are copied through untouched — the proposal
    can never move a cache address or invent a clip nobody paid for. (The
    commit that later writes the document enforces conservation; the proposal
    must not even suggest a violation.)"""
    _stub_turn(monkeypatch, """{"events": [
        {"index": 0, "start_time": 0.2, "duration": 0.4},
        {"index": 1, "start_time": 4.0, "duration": 0.4},
        {"index": 2, "start_time": 10.9, "duration": 1.5}]}""")
    timeline = _timeline()
    timeline.events[1].seed = 123456789
    with _client(store, project) as c:
        r = c.post(f"{API_PREFIX}/projects/{project.id}/director",
                   json=_body(timeline))
    body = r.json()
    assert body["status"] == "proposed"
    assert body["timeline"]["events"][1]["seed"] == 123456789
    assert body["timeline"]["events"][2]["source_duration_s"] == 2.0


# -- client/API errors ------------------------------------------------------

def test_director_404_unknown_project(client):
    r = client.post(f"{API_PREFIX}/projects/{'proj_01KYJD1RKETS3KTX5HA3H5JPDQ'}/director",
                    json=_body())
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "project_not_found"


def test_director_422_before_any_timeline(client, project):
    r = client.post(f"{API_PREFIX}/projects/{project.id}/director", json=_body())
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "precondition_failed"


def test_director_400_on_empty_instruction(client, store, project):
    store.save_timeline(project.id, _timeline())
    r = client.post(f"{API_PREFIX}/projects/{project.id}/director",
                    json=_body(instruction="   "))
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_request"


def test_director_400_on_malformed_request_document(client, store, project):
    store.save_timeline(project.id, _timeline())
    body = _body()
    body["events"][0]["kind"] = "teleport"
    r = client.post(f"{API_PREFIX}/projects/{project.id}/director", json=body)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_request"


def test_director_429_when_the_models_quota_is_spent(monkeypatch, store, project,
                                                     client):
    """The turn is refused before any provider call when the daily budget is
    gone — same gate as analyze, same first-class 429."""
    from foleyflow import quota as quota_mod
    from foleyflow.quota import ModelQuota, QuotaWindow

    blocked = ModelQuota(
        model_id="gemini-3.5-flash-lite",
        per_minute=QuotaWindow(0, 15),
        per_day=QuotaWindow(500, 500),
        resets_at="2026-08-03T07:00:00+00:00",
    )
    monkeypatch.setattr(quota_mod, "get_quota", lambda *a, **k: blocked)
    store.save_timeline(project.id, _timeline())
    r = client.post(f"{API_PREFIX}/projects/{project.id}/director", json=_body())
    assert r.status_code == 429
    assert r.json()["error"]["code"] == "quota_exhausted"
    assert r.json()["error"]["detail"]["model_id"] == "gemini-3.5-flash-lite"


def test_director_502_when_the_provider_call_fails(monkeypatch, store, project):
    """A provider outage (network, credentials) is a 502 — the client can tell
    a dead turn from a hallucinated one."""
    from foleyflow import ledger as ledger_mod

    def boom(endpoint, arguments, **kwargs):
        raise RuntimeError("key rejected")

    # Stub at the adapter level so the REAL execute_with_ledger records the
    # failed row (failed billable calls still count, docs/api.md §3.10) before
    # re-raising.
    import foleyflow.providers.google as gmod
    gmod.GoogleAdapter.generate = boom
    store.save_timeline(project.id, _timeline())
    runner = JobRunner(store, max_workers=1)
    app = create_app(store, runner)
    with TestClient(app) as c:
        r = c.post(f"{API_PREFIX}/projects/{project.id}/director", json=_body())
    assert r.status_code == 502
    assert r.json()["error"]["code"] == "provider_error"
    assert ledger_mod.iter_transactions()
    assert ledger_mod.iter_transactions()[-1]["success"] is False
    runner.shutdown(wait=True)


# -- money, quota and immutability ------------------------------------------

def test_director_turn_writes_an_attributed_ledger_row_and_no_version(
        monkeypatch, store, project):
    """The real execute_with_ledger path: the row lands attributed to the
    project (the context does it), the cost shows in project spend, and the
    endpoint never allocates a timeline version — chat proposes, never
    applies."""
    import foleyflow.providers.google as gmod

    def fake_generate(self, spec, arguments, method="subscribe", **kwargs):
        return GenerationResult(raw_response={
            "output": """{"events": [
                {"index": 0, "start_time": 0.0, "duration": 0.4},
                {"index": 1, "start_time": 3.96, "duration": 0.4},
                {"index": 2, "start_time": 11.0, "duration": 1.5}]}""",
            "model": spec.model_id}, actual_cost_usd=None)

    monkeypatch.setattr(gmod.GoogleAdapter, "generate", fake_generate)
    store.save_timeline(project.id, _timeline())
    runner = JobRunner(store, max_workers=1)
    app = create_app(store, runner)
    with TestClient(app) as c:
        r = c.post(f"{API_PREFIX}/projects/{project.id}/director", json=_body())
    assert r.status_code == 200

    from foleyflow import ledger
    rows = ledger.iter_transactions()
    assert len(rows) == 1
    assert rows[0]["project_id"] == project.id
    assert rows[0]["endpoint"] == "gemini-3.5-flash-lite"
    assert rows[0]["success"] is True
    assert rows[0]["cost_estimate"] == pytest.approx(0.005)
    spend = ledger.project_spend(project.id)
    assert spend["total"] == pytest.approx(0.005)
    assert spend["by_endpoint"]["gemini-3.5-flash-lite"] == pytest.approx(0.005)

    # Immutability: still exactly one version after the turn.
    with TestClient(app) as c:
        versions = c.get(
            f"{API_PREFIX}/projects/{project.id}/timeline/versions").json()
    assert versions["versions"] == [1]
    runner.shutdown(wait=True)


def test_director_turn_counts_against_quota(monkeypatch, store, project):
    import foleyflow.providers.google as gmod

    def fake_generate(self, spec, arguments, method="subscribe", **kwargs):
        return GenerationResult(raw_response={
            "output": '{"events": []}', "model": spec.model_id},
            actual_cost_usd=None)

    monkeypatch.setattr(gmod.GoogleAdapter, "generate", fake_generate)
    from foleyflow.quota import get_quota
    assert get_quota("gemini-3.5-flash-lite").per_day.used == 0
    store.save_timeline(project.id, _timeline())
    runner = JobRunner(store, max_workers=1)
    app = create_app(store, runner)
    with TestClient(app) as c:
        r = c.post(f"{API_PREFIX}/projects/{project.id}/director", json=_body())
    assert r.status_code == 200
    assert get_quota("gemini-3.5-flash-lite").per_day.used == 1
    runner.shutdown(wait=True)


def test_director_reports_ledger_unwritable_fail_closed(monkeypatch, store, project):
    """When the ledger write fails, the row did not land but the call may still
    have billed the provider — the response says so instead of presenting a
    clean cost figure (§3.10)."""
    import foleyflow.providers.google as gmod

    def fake_generate(self, spec, arguments, method="subscribe", **kwargs):
        return GenerationResult(raw_response={
            "output": '{"events": []}', "model": spec.model_id},
            actual_cost_usd=None)

    monkeypatch.setattr(gmod.GoogleAdapter, "generate", fake_generate)
    from foleyflow import ledger as ledger_mod
    from foleyflow.storage import StorageError

    def broken_append(record, backend):
        raise StorageError("disk on fire")

    monkeypatch.setattr(ledger_mod, "_append_storage", broken_append)
    store.save_timeline(project.id, _timeline())
    runner = JobRunner(store, max_workers=1)
    app = create_app(store, runner)
    with TestClient(app) as c:
        r = c.post(f"{API_PREFIX}/projects/{project.id}/director", json=_body())
    assert r.status_code == 200
    assert r.json()["ledger_writable"] is False
    runner.shutdown(wait=True)


# -- pure validation (no HTTP) ----------------------------------------------

def test_parse_proposal_output_tolerates_fences_and_prose():
    assert director.parse_proposal_output(
        '```json\n{"events": []}\n```') == []
    assert director.parse_proposal_output('{"events": [{"index": 0}]}') == \
        [{"index": 0}]
    assert director.parse_proposal_output("not json at all") is None
    assert director.parse_proposal_output('{"edits": []}') is None
    assert director.parse_proposal_output("[]") is None


def test_validate_proposal_rounds_to_the_working_resolution():
    tl = _timeline()
    status, proposed, edits = director.validate_proposal(
        tl, [
            {"index": 0, "start_time": 0.0, "duration": 0.4},
            {"index": 1, "start_time": 3.96, "duration": 0.4},
            {"index": 2, "start_time": 11.0, "duration": 1.5},
        ], clip_duration=15.04)
    assert status == "proposed"
    assert proposed[1].start_time == pytest.approx(3.96)
    assert proposed[1].duration == pytest.approx(0.4)  # no float noise
    assert edits[0]["new"]["duration"] == pytest.approx(0.4)


def test_director_prompt_never_mentions_patches():
    prompt = director.build_prompt("move it", '{"events": []}', 15.0)
    assert "patch" not in prompt.lower()
    assert "complete placement" in prompt or "EVERY index" in prompt
