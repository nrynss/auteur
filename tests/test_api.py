"""Contract tests for the HTTP API.

Driven through TestClient against a LocalBackend-backed store — no network, no
credentials, no provider calls. These assert the *contract* in docs/api.md
(status codes, error shape, field names), because that is what a client is
written against; the domain behaviour underneath has its own tests.
"""

import pytest
from fastapi.testclient import TestClient

from foleyflow.api.app import API_PREFIX, create_app
from foleyflow.api.jobs import JobRunner
from foleyflow.projects import ProjectStore
from foleyflow.storage import LocalBackend, StorageError

VALID_ID = "proj_01KYJD1RKETS3KTX5HA3H5JPDQ"


@pytest.fixture
def store(tmp_path):
    return ProjectStore(LocalBackend(str(tmp_path / "b2")), work_dir=str(tmp_path / "w"))


@pytest.fixture
def client(store):
    runner = JobRunner(store, max_workers=2)
    app = create_app(store, runner)
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    runner.shutdown(wait=True)


@pytest.fixture
def project(store):
    return store.create_project(title="Western standoff", input_key="k",
                                source_sha256="a" * 64, source_filename="standoff.mp4",
                                duration_s=15.04)


def _timeline():
    from foleyflow.models import Event, Timeline
    return Timeline(duration=15.04, ambience="desert wind", events=[
        Event(name="holster creak", start_time=0.0, duration=1.0,
              intensity="low", kind="continuous"),
        Event(name="holster creak", start_time=4.0, duration=1.0,
              intensity="low", kind="continuous"),
        Event(name="gunshot", start_time=11.0, duration=1.5,
              intensity="high", kind="discrete"),
    ])


# -- error contract ---------------------------------------------------------

def test_every_error_uses_the_same_shape(client):
    """A client must need exactly one error parser."""
    responses = [
        client.get(f"{API_PREFIX}/projects/not-an-id"),                    # 400
        client.get(f"{API_PREFIX}/projects/{VALID_ID}"),                   # 404
        client.post(f"{API_PREFIX}/projects", json={"title": ""}),         # 400 validation
        client.get(f"{API_PREFIX}/nonexistent"),                           # 404 routing
    ]
    for r in responses:
        assert r.status_code >= 400
        body = r.json()
        assert set(body) == {"error"}, body
        assert set(body["error"]) >= {"code", "message"}
        assert isinstance(body["error"]["code"], str)


def test_validation_failures_are_not_fastapis_native_shape(client):
    """FastAPI would return a list under `detail`; that would be the one
    response a client had to special-case."""
    r = client.post(f"{API_PREFIX}/projects", json={"title": "x"})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_request"
    assert "errors" in r.json()["error"]["detail"]


@pytest.mark.parametrize("bad_id", [
    "not-an-id", "proj_short", "proj_" + "a" * 26, "proj_lowercase0123456789ab",
])
def test_malformed_id_is_a_400_from_the_handler(client, bad_id):
    """Asserts 400 exactly. The previous version accepted 400-or-404, and the
    URL it used was normalised by the client before routing — so it passed on
    a router 404 without ever reaching the id check."""
    r = client.get(f"{API_PREFIX}/projects/{bad_id}")
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "invalid_id"


def test_unknown_project_is_404_with_the_id_in_detail(client):
    r = client.get(f"{API_PREFIX}/projects/{VALID_ID}")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "project_not_found"
    assert r.json()["error"]["detail"]["project_id"] == VALID_ID


# -- health -----------------------------------------------------------------

def test_health_reports_storage(client):
    body = client.get(f"{API_PREFIX}/health").json()
    assert body["status"] == "ok"
    assert body["storage"] == "ok"
    assert body["version"]


def test_health_reports_degraded_when_storage_is_broken(store):
    """The previous version only exercised the healthy branch against a
    LocalBackend that cannot fail, so it could not have caught a deployment
    reporting `storage: ok` with dead credentials."""
    class Broken:
        def exists(self, key):
            raise StorageError("credentials rejected")

    store.backend = Broken()
    app = create_app(store, JobRunner(store, max_workers=1))
    with TestClient(app, raise_server_exceptions=False) as c:
        body = c.get(f"{API_PREFIX}/health").json()
    assert body["storage"] == "unavailable"
    assert body["status"] == "degraded"


# -- projects ---------------------------------------------------------------

def test_create_rejects_a_path_separator_in_the_filename(client):
    """The filename is untrusted and informs a storage key."""
    r = client.post(f"{API_PREFIX}/projects",
                    json={"title": "T", "filename": "../../etc/passwd"})
    assert r.status_code == 400


def test_create_rejects_an_unsupported_content_type(client):
    r = client.post(f"{API_PREFIX}/projects",
                    json={"title": "T", "filename": "x.mp4",
                          "content_type": "application/zip"})
    assert r.status_code == 400


def test_create_succeeds_and_returns_an_upload_target(store):
    """The success path was untested — only its failure branch was."""
    import foleyflow.api.app as app_mod
    original = app_mod.create_upload_target
    app_mod.create_upload_target = (
        lambda backend, key, ct, expires_s=900: (
            f"https://example-b2/{key}", "2026-07-28T12:00:00Z", {"Content-Type": ct}))
    try:
        app = create_app(store, JobRunner(store, max_workers=1))
        with TestClient(app, raise_server_exceptions=False) as c:
            r = c.post(f"{API_PREFIX}/projects",
                       json={"title": "Standoff", "filename": "clip.mp4"})
    finally:
        app_mod.create_upload_target = original

    assert r.status_code == 201, r.text
    body = r.json()
    assert body["project"]["status"] == "awaiting_upload"
    assert body["upload"]["method"] == "PUT"
    assert body["upload"]["headers"]["Content-Type"] == "video/mp4"
    # The project record exists before its bytes do.
    assert store.get_project(body["project"]["id"]).source_sha256 == ""


def test_analyze_is_refused_before_the_upload_is_confirmed(client, store):
    """Analysing a project whose PUT was abandoned used to fail deep inside a
    worker; it now fails at the front door."""
    p = store.create_project(title="T", input_key="k", source_sha256="",
                             source_filename="f.mp4")
    r = client.post(f"{API_PREFIX}/projects/{p.id}/analyze")
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "precondition_failed"


def test_analyze_rejects_a_model_with_no_declared_budget(client, project):
    """limit=None is "unknown", not "unlimited" — an undeclared model used to
    sail past the quota gate and die inside the worker after the 202 had
    already promised a cost estimate."""
    r = client.post(f"{API_PREFIX}/projects/{project.id}/analyze",
                    json={"model": "totally/undeclared-model"})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_request"


def test_storage_outage_is_503_not_404(store, project):
    """A B2 outage reported "this project does not exist", which tells the user
    their work is gone and stops the client retrying."""
    class Flaky(LocalBackend):
        def get_bytes(self, key):
            raise StorageError("backend unreachable")

    store.backend = Flaky(store.backend.root)
    app = create_app(store, JobRunner(store, max_workers=1))
    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.get(f"{API_PREFIX}/projects/{project.id}")
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "storage_unavailable"


def test_create_requires_b2_and_says_so(client):
    """LocalBackend cannot presign; the failure must be a clear 503 rather than
    a file:// URL a browser cannot PUT to."""
    r = client.post(f"{API_PREFIX}/projects",
                    json={"title": "T", "filename": "clip.mp4"})
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "storage_unavailable"


def test_get_project_returns_readiness(client, store, project):
    body = client.get(f"{API_PREFIX}/projects/{project.id}").json()
    assert body["id"] == project.id
    assert body["title"] == "Western standoff"
    assert isinstance(body["readiness"], list) and body["readiness"]
    assert set(body["readiness"][0]) >= {"key", "label", "state"}
    assert 0 <= body["readiness_percent"] <= 100


def test_readiness_blocks_export_before_analysis(client, project):
    body = client.get(f"{API_PREFIX}/projects/{project.id}").json()
    assert body["blocks_export"] is True
    blocked = {i["key"] for i in body["readiness"] if i["state"] == "blocked"}
    assert {"scenes", "timeline"} <= blocked


def test_list_is_newest_first_and_paginates(client, store):
    made = [store.create_project(title=f"P{i}", input_key="k",
                                 source_sha256=str(i), source_filename="f.mp4")
            for i in range(5)]
    body = client.get(f"{API_PREFIX}/projects?limit=2").json()
    assert [p["id"] for p in body["projects"]] == [made[4].id, made[3].id]
    assert body["next_cursor"] == made[3].id

    page2 = client.get(f"{API_PREFIX}/projects?limit=2&cursor={body['next_cursor']}").json()
    assert [p["id"] for p in page2["projects"]] == [made[2].id, made[1].id]


def test_last_page_has_no_cursor(client, store):
    store.create_project(title="only", input_key="k", source_sha256="1",
                         source_filename="f.mp4")
    assert client.get(f"{API_PREFIX}/projects?limit=25").json()["next_cursor"] is None


def test_limit_is_bounded(client):
    assert client.get(f"{API_PREFIX}/projects?limit=1000").status_code == 400
    assert client.get(f"{API_PREFIX}/projects?limit=0").status_code == 400


# -- timeline ---------------------------------------------------------------

def test_timeline_404s_before_analysis(client, project):
    r = client.get(f"{API_PREFIX}/projects/{project.id}/timeline")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "timeline_not_found"


def test_timeline_events_are_indexed_for_repair(client, store, project):
    """Repair addresses an event by index because names repeat — this timeline
    has 'holster creak' twice, and a name-addressed API could not target one."""
    store.save_timeline(project.id, _timeline())
    body = client.get(f"{API_PREFIX}/projects/{project.id}/timeline").json()
    assert [e["index"] for e in body["events"]] == [0, 1, 2]
    names = [e["name"] for e in body["events"]]
    assert names.count("holster creak") == 2, "duplicate names must both survive"


def test_timeline_versions_coexist(client, store, project):
    store.save_timeline(project.id, _timeline())
    store.save_timeline(project.id, _timeline())
    body = client.get(f"{API_PREFIX}/projects/{project.id}/timeline/versions").json()
    assert body["versions"] == [1, 2] and body["latest"] == 2
    assert client.get(f"{API_PREFIX}/projects/{project.id}/timeline?version=1"
                      ).json()["version"] == 1


def test_unknown_timeline_version_404s(client, store, project):
    store.save_timeline(project.id, _timeline())
    r = client.get(f"{API_PREFIX}/projects/{project.id}/timeline?version=99")
    assert r.status_code == 404


# -- scenes -----------------------------------------------------------------

def test_scenes_empty_before_analysis(client, project):
    assert client.get(f"{API_PREFIX}/projects/{project.id}/scenes"
                      ).json()["scenes"] == []


def test_scenes_carry_stats_and_geometry(client, store, project):
    from foleyflow.scenes import Scene, SceneStats
    store.save_scenes(project.id, [
        Scene(id="scene_01", index=1, start_s=0.0, end_s=3.67,
              stats=SceneStats(mean_luma=133.1, mean_rgb=[136.6, 133.9, 120.1],
                               luma_stddev=1.9, sample_count=7))])
    scene = client.get(f"{API_PREFIX}/projects/{project.id}/scenes").json()["scenes"][0]
    assert scene["id"] == "scene_01"
    assert scene["duration_s"] == pytest.approx(3.67)
    assert scene["stats"]["mean_luma"] == 133.1


def test_timeline_events_carry_scene_ids(client, store, project):
    from foleyflow.scenes import Scene
    store.save_scenes(project.id, [
        Scene(id="scene_01", index=1, start_s=0.0, end_s=3.67),
        Scene(id="scene_02", index=2, start_s=3.67, end_s=15.04)])
    store.save_timeline(project.id, _timeline())
    events = client.get(f"{API_PREFIX}/projects/{project.id}/timeline").json()["events"]
    assert [e["scene_id"] for e in events] == ["scene_01", "scene_02", "scene_02"]


# -- QA ---------------------------------------------------------------------

def test_qa_404s_before_a_report_exists(client, project):
    r = client.get(f"{API_PREFIX}/projects/{project.id}/qa")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "qa_not_found"


def test_qa_report_distinguishes_blocking_and_warning(client, store, project):
    from foleyflow.qa import build_report
    from foleyflow.scenes import Scene, SceneStats

    scenes = [
        Scene(id="scene_01", index=1, start_s=0.0, end_s=1.0,
              stats=SceneStats(mean_luma=40.0, mean_rgb=[40, 40, 40],
                               luma_stddev=1.0, sample_count=4)),
        Scene(id="scene_02", index=2, start_s=1.0, end_s=2.0,
              stats=SceneStats(mean_luma=90.0, mean_rgb=[90, 90, 90],
                               luma_stddev=1.0, sample_count=4)),
    ]
    store.save_qa_report(project.id, build_report(scenes, run_id="run_01TEST"))
    body = client.get(f"{API_PREFIX}/projects/{project.id}/qa").json()
    assert body["overall_status"] == "needs_review"
    assert body["audio"]["measured"] is False
    issue = body["visual"]["issues"][0]
    assert issue["severity"] == "warning"
    assert issue["code"] == "exposure_discontinuity"
    assert issue["scene_id"] == "scene_02"
    assert issue["compared_with"] == "scene_01"


def test_project_readiness_surfaces_qa_warnings(client, store, project):
    from foleyflow.qa import QaIssue, QaReport, AudioQa, VisualQa, STATUS_NEEDS_REVIEW
    store.save_qa_report(project.id, QaReport(
        run_id=None, overall_status=STATUS_NEEDS_REVIEW,
        audio=AudioQa(integrated_lufs=None, true_peak_dbtp=None),
        visual=VisualQa(issues=[QaIssue(
            severity="warning", code="exposure_discontinuity",
            scene_id="scene_02", compared_with="scene_01", confidence=0.8)]),
    ))
    readiness = {r["key"]: r for r in
                 client.get(f"{API_PREFIX}/projects/{project.id}").json()["readiness"]}
    assert readiness["qa"]["state"] == "warn"
    assert "exposure_discontinuity" in readiness["qa"]["detail"]


# -- runs -------------------------------------------------------------------

def test_running_run_is_pollable_before_it_finishes(client, store, project):
    """A terminal record is only written at the end, so an in-flight run must
    be answered from the event log or every 202 would be followed by a 404."""
    run = store.start_run(project.id, "analyze")
    body = client.get(f"{API_PREFIX}/projects/{project.id}/runs/{run.id}").json()
    assert body["status"] == "running"
    assert body["kind"] == "analyze"


def test_completed_run_reads_from_its_record(client, store, project):
    run = store.complete_run(store.start_run(project.id, "analyze"),
                             timeline_version=1)
    body = client.get(f"{API_PREFIX}/projects/{project.id}/runs/{run.id}").json()
    assert body["status"] == "completed"
    assert body["timeline_version"] == 1


def test_unknown_run_404s(client, project):
    r = client.get(f"{API_PREFIX}/projects/{project.id}/runs/run_01ZZZZZZZZZZZZZZZZZZZZZZZZ")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "run_not_found"


def test_event_log_is_exposed_in_order(client, store, project):
    store.save_timeline(project.id, _timeline())
    events = client.get(f"{API_PREFIX}/projects/{project.id}/events").json()["events"]
    types = [e["type"] for e in events]
    assert types[0] == "postproduction.project.created"
    assert "postproduction.foley.plan.proposed" in types


# -- analysis ---------------------------------------------------------------

def test_analyze_on_unknown_project_404s_before_spending_anything(client):
    r = client.post(f"{API_PREFIX}/projects/{VALID_ID}/analyze")
    assert r.status_code == 404


def test_analyze_returns_202_with_a_pollable_run(client, store, project, monkeypatch):
    """The 202 contract: a run id the client can poll immediately."""
    import foleyflow.api.app as app_mod
    monkeypatch.setattr(app_mod, "_analysis_work",
                        lambda *a, **k: (lambda run: store.complete_run(run)))

    r = client.post(f"{API_PREFIX}/projects/{project.id}/analyze")
    assert r.status_code == 202
    run_id = r.json()["run_id"]
    assert r.json()["kind"] == "analyze"

    poll = client.get(f"{API_PREFIX}/projects/{project.id}/runs/{run_id}")
    assert poll.status_code == 200


def test_analyze_is_rejected_when_the_daily_budget_is_spent(client, project, monkeypatch):
    """429 must be distinguishable from a transient failure, and must carry the
    reset time — 'out of budget until tomorrow' is a normal state on a 20/day
    free tier."""
    from foleyflow.quota import ModelQuota, QuotaWindow
    import foleyflow.quota as quota_mod

    monkeypatch.setattr(quota_mod, "get_quota", lambda m, **k: ModelQuota(
        model_id=m, per_minute=QuotaWindow(0, 5), per_day=QuotaWindow(20, 20),
        resets_at="2026-07-29T00:00:00-07:00"))

    r = client.post(f"{API_PREFIX}/projects/{project.id}/analyze")
    assert r.status_code == 429
    detail = r.json()["error"]["detail"]
    assert r.json()["error"]["code"] == "quota_exhausted"
    assert detail["resets_at"] == "2026-07-29T00:00:00-07:00"
    assert detail["estimate"] is True


# -- quota ------------------------------------------------------------------

def test_quota_is_flagged_as_an_estimate(client):
    """Google exposes no remaining-balance field. A UI must not be able to
    present these as authoritative without ignoring an explicit signal."""
    body = client.get(f"{API_PREFIX}/quota").json()
    assert body["estimate"] is True
    assert body["models"]
    assert {"model_id", "status", "per_day"} <= set(body["models"][0])


# -- openapi ----------------------------------------------------------------

def test_openapi_is_served(client):
    """API-first: the schema is a deliverable, not a debugging aid."""
    spec = client.get(f"{API_PREFIX}/openapi.json").json()
    assert spec["info"]["title"] == "FoleyFlow"
    assert f"{API_PREFIX}/projects" in spec["paths"]
