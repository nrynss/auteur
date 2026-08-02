"""Contract tests for the HTTP API.

Driven through TestClient against a LocalBackend-backed store — no network, no
credentials, no provider calls. These assert the *contract* in docs/api.md
(status codes, error shape, field names), because that is what a client is
written against; the domain behaviour underneath has its own tests.
"""

import json
import os

import pytest
from fastapi.testclient import TestClient

from foleyflow.api.app import API_PREFIX, create_app
from foleyflow.api.jobs import JobRunner
from foleyflow.projects import ProjectStore
from foleyflow.storage import LocalBackend, StorageError

VALID_ID = "proj_01KYJD1RKETS3KTX5HA3H5JPDQ"


@pytest.fixture(autouse=True)
def _no_ambient_sfx_tier(monkeypatch):
    """These tests assert tier behaviour; a developer's exported
    FOLEYFLOW_SFX_TIER must not silently re-route them. Tests that want a
    specific tier set it explicitly. (The health worker-probe tests found this
    the hard way: with `=paid` they short-circuit to not_applicable and fail.)"""
    monkeypatch.delenv("FOLEYFLOW_SFX_TIER", raising=False)


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

def test_health_reports_storage(client, monkeypatch):
    # Module constants are set at import; clear them so an ambient deploy env
    # in a developer shell cannot invent a SHA for the unstamped path.
    import foleyflow.api.app as app_mod
    monkeypatch.setattr(app_mod, "GIT_SHA", None)
    monkeypatch.setattr(app_mod, "BUILD_TIME", None)

    body = client.get(f"{API_PREFIX}/health").json()
    assert body["status"] == "ok"
    assert body["storage"] == "ok"
    assert body["version"]
    # Local / unstamped process: build identity is absent, not invented.
    assert body["git_sha"] is None
    assert body["build_time"] is None


def test_health_includes_build_identity_when_env_is_set(store, monkeypatch):
    """Deployed images bake FOLEYFLOW_GIT_SHA / FOLEYFLOW_BUILD_TIME at build.

    Module-level constants are set once at import from env. Tests patch the
    GIT_SHA / BUILD_TIME attributes directly rather than re-importing the
    module, so the rest of the suite stays unstamped.
    """
    import foleyflow.api.app as app_mod

    monkeypatch.setattr(app_mod, "GIT_SHA", "deadbeefcafebabe0123456789abcdef01234567")
    monkeypatch.setattr(app_mod, "BUILD_TIME", "2026-08-02T12:00:00Z")
    app = create_app(store, JobRunner(store, max_workers=1))
    with TestClient(app) as c:
        body = c.get(f"{API_PREFIX}/health").json()
    assert body["git_sha"] == "deadbeefcafebabe0123456789abcdef01234567"
    assert body["build_time"] == "2026-08-02T12:00:00Z"
    assert body["version"]  # product version still present
    assert body["status"] == "ok"


def test_health_does_not_crash_when_build_env_is_unset(client, monkeypatch):
    """Empty/whitespace stamps degrade to null — never a 500 or a fake SHA."""
    import foleyflow.api.app as app_mod

    monkeypatch.setattr(app_mod, "GIT_SHA", None)
    monkeypatch.setattr(app_mod, "BUILD_TIME", None)
    body = client.get(f"{API_PREFIX}/health").json()
    assert body["git_sha"] is None
    assert body["build_time"] is None
    assert body["status"] == "ok"


def test_build_info_from_env_treats_blank_as_absent(monkeypatch):
    """Unset, empty, and whitespace-only values must not appear as a real SHA."""
    import foleyflow.api.app as app_mod

    monkeypatch.delenv("FOLEYFLOW_GIT_SHA", raising=False)
    assert app_mod._build_info_from_env("FOLEYFLOW_GIT_SHA") is None
    monkeypatch.setenv("FOLEYFLOW_GIT_SHA", "")
    assert app_mod._build_info_from_env("FOLEYFLOW_GIT_SHA") is None
    monkeypatch.setenv("FOLEYFLOW_GIT_SHA", "   ")
    assert app_mod._build_info_from_env("FOLEYFLOW_GIT_SHA") is None
    # Shortest accepted SHA is 7 hex chars (git short SHA).
    monkeypatch.setenv("FOLEYFLOW_GIT_SHA", "abc1234")
    assert app_mod._build_info_from_env("FOLEYFLOW_GIT_SHA") == "abc1234"


def test_build_info_from_env_rejects_sentinels(monkeypatch):
    """Placeholder stamps (unknown, n/a, …) must not look like real identity."""
    import foleyflow.api.app as app_mod

    for junk in ("unknown", "UNKNOWN", "n/a", "none", "null", "undefined",
                 "latest", "None", "NULL"):
        monkeypatch.setenv("FOLEYFLOW_GIT_SHA", junk)
        assert app_mod._build_info_from_env("FOLEYFLOW_GIT_SHA") is None, junk
        monkeypatch.setenv("FOLEYFLOW_BUILD_TIME", junk)
        assert app_mod._build_info_from_env("FOLEYFLOW_BUILD_TIME") is None, junk


def test_build_info_from_env_rejects_non_hex_git_sha(monkeypatch):
    """GIT_SHA must be a plausible short-or-full hex SHA, not free text."""
    import foleyflow.api.app as app_mod

    for junk in ("not-a-sha", "abc12", "g" * 7, "deadbeef!", "main"):
        monkeypatch.setenv("FOLEYFLOW_GIT_SHA", junk)
        assert app_mod._build_info_from_env("FOLEYFLOW_GIT_SHA") is None, junk
    # BUILD_TIME stays an opaque string after the sentinel filter — no ISO gate.
    monkeypatch.setenv("FOLEYFLOW_BUILD_TIME", "build-42")
    assert app_mod._build_info_from_env("FOLEYFLOW_BUILD_TIME") == "build-42"


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


def test_health_does_not_probe_the_worker_unless_asked(client, monkeypatch):
    """The default path must make no outbound call.

    This endpoint is what a platform liveness check hits. If it probed the SFX
    worker every time, a restart loop would be one unplugged GPU away — and the
    offline test suite would quietly start doing network I/O.
    """
    import foleyflow.providers as providers

    def _boom(name):
        raise AssertionError("get_adapter must not be called without ?worker")

    monkeypatch.setattr(providers, "get_adapter", _boom)
    body = client.get(f"{API_PREFIX}/health").json()
    assert body["worker"] is None


def _stub_local_adapter(monkeypatch, payload=None, exc=None):
    """Replace only the adapter, so real tier resolution is still exercised.

    Clears `$FOLEYFLOW_SFX_TIER` so an operator shell with `=paid` does not
    short-circuit `_worker_health` to `not_applicable` before the stub runs.
    Tier identity still comes from the TOML default, not from this helper.
    """
    import foleyflow.providers as providers

    monkeypatch.delenv("FOLEYFLOW_SFX_TIER", raising=False)

    class Stub:
        def health(self):
            if exc is not None:
                raise exc
            return payload

    monkeypatch.setattr(providers, "get_adapter", lambda name: Stub())


def test_health_reports_a_reachable_worker(client, monkeypatch):
    _stub_local_adapter(monkeypatch, payload={
        "status": "ok", "model": "openmoss/moss-soundeffect-v2",
        "device": "NVIDIA GeForce RTX 4070 SUPER",
        "auth_required": True, "auth_configured": True,
    })
    body = client.get(f"{API_PREFIX}/health", params={"worker": "true"}).json()
    worker = body["worker"]
    assert worker["status"] == "ok"
    assert worker["device"] == "NVIDIA GeForce RTX 4070 SUPER"
    # The tier came from real config, not the stub.
    assert worker["tier"] == "test"
    assert worker["endpoint"] == "openmoss/moss-soundeffect-v2"


def test_an_unreachable_worker_does_not_make_the_api_unhealthy(client, monkeypatch):
    """A fully cached render needs no worker, so this must not read as down."""
    _stub_local_adapter(monkeypatch, exc=OSError("connection refused"))
    body = client.get(f"{API_PREFIX}/health", params={"worker": "true"}).json()
    assert body["status"] == "ok"
    assert body["storage"] == "ok"
    assert body["worker"]["status"] == "unreachable"
    assert "connection refused" in body["worker"]["detail"]


def test_health_flags_a_worker_wanting_a_token_we_do_not_have(client, monkeypatch):
    """Reads as healthy from outside and fails every generation."""
    _stub_local_adapter(monkeypatch, payload={
        "status": "ok", "auth_required": True, "auth_configured": False,
    })
    worker = client.get(f"{API_PREFIX}/health",
                        params={"worker": "true"}).json()["worker"]
    assert worker["auth_required"] is True
    assert worker["auth_configured"] is False


def test_health_reports_not_applicable_for_a_hosted_tier(client, monkeypatch):
    """Paid/fallback are vendor APIs — there is no worker to probe."""
    import foleyflow.providers as providers

    monkeypatch.setenv("FOLEYFLOW_SFX_TIER", "paid")

    def _boom(name):
        raise AssertionError("hosted tiers must not call get_adapter")

    monkeypatch.setattr(providers, "get_adapter", _boom)
    worker = client.get(f"{API_PREFIX}/health",
                        params={"worker": "true"}).json()["worker"]
    assert worker["status"] == "not_applicable"
    assert worker["tier"] == "paid"


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


def test_get_project_has_no_source_or_mix_url_before_artifacts(client, project):
    """Bare project: no uploaded bytes and no mix — signed URLs stay null.

    The fixture sets source_sha256 without putting bytes; a reserved input_key
    must still yield null source_url (B2 would otherwise presign a missing key).
    """
    r = client.get(f"{API_PREFIX}/projects/{project.id}")
    assert r.status_code == 200
    body = r.json()
    assert body.get("source_url") is None
    assert body.get("mix_url") is None


def test_get_project_source_url_null_when_key_reserved_but_object_missing(
        client, store, project):
    """input_key is allocated at create; without bytes, no source_url."""
    # Key string is present on the project record, object is not.
    assert project.input_key
    assert not store.backend.exists(project.input_key)
    body = client.get(f"{API_PREFIX}/projects/{project.id}").json()
    assert body["source_url"] is None


def test_get_project_source_url_only_after_upload(client, store, project):
    """Source alone: source_url present, mix_url still null."""
    store.backend.put_bytes(project.input_key, b"FAKEVIDEO" * 64,
                            content_type="video/mp4")
    body = client.get(f"{API_PREFIX}/projects/{project.id}").json()
    assert isinstance(body["source_url"], str) and body["source_url"]
    assert body["source_url"].startswith("file:///")
    assert project.input_key in body["source_url"] or body["source_url"].endswith(
        project.input_key.lstrip("/"))
    assert body["mix_url"] is None


def test_get_project_serves_signed_source_and_mix_urls(client, store, project):
    """Completed mix + source object: both A/B URLs are signed non-empty strings."""
    store.backend.put_bytes(project.input_key, b"FAKEVIDEO" * 64,
                            content_type="video/mp4")
    version = store.save_timeline(project.id, _timeline())
    run = store.start_run(project.id, "mix")
    mix_key = store.mix_key(project.id, run.id)
    video_key = store.mix_video_key(project.id, run.id)
    store.backend.put_bytes(mix_key, b"MIXWAV" * 8, content_type="audio/wav")
    store.backend.put_bytes(video_key, b"MP4DATA" * 8, content_type="video/mp4")
    store.record_mix(project.id, run_id=run.id,
                     mix_key=mix_key, video_key=video_key,
                     tier="full", timeline_version=version,
                     stem_count=0, generated_count=0)
    store.complete_run(run, timeline_version=version)

    body = client.get(f"{API_PREFIX}/projects/{project.id}").json()
    assert isinstance(body["source_url"], str) and body["source_url"]
    assert isinstance(body["mix_url"], str) and body["mix_url"]
    # LocalBackend returns file:// URLs; either way they are addressable strings.
    assert body["source_url"].startswith("file:///")
    assert body["mix_url"].startswith("file:///")
    # Pin the signed object: foley.mp4 (video), never the wav mix_key.
    assert "foley.mp4" in body["mix_url"]
    assert "mix.wav" not in body["mix_url"]
    assert body["mix_run_id"] == run.id


def test_get_project_mix_url_null_when_video_object_missing(client, store, project):
    """Mix recorded in the log but video bytes gone → mix_url null, not a 500."""
    store.backend.put_bytes(project.input_key, b"FAKEVIDEO" * 64,
                            content_type="video/mp4")
    version = store.save_timeline(project.id, _timeline())
    run = store.start_run(project.id, "mix")
    video_key = store.mix_video_key(project.id, run.id)
    # Record the mix without putting the video object.
    store.record_mix(project.id, run_id=run.id,
                     mix_key=store.mix_key(project.id, run.id),
                     video_key=video_key,
                     tier="full", timeline_version=version,
                     stem_count=0, generated_count=0)
    store.complete_run(run, timeline_version=version)
    assert not store.backend.exists(video_key)

    body = client.get(f"{API_PREFIX}/projects/{project.id}").json()
    assert body["source_url"] is not None
    assert body["mix_url"] is None
    assert body["mix_run_id"] == run.id


def test_get_project_signs_urls_at_standard_ttl(client, store, project, monkeypatch):
    """Acceptance: source_url and mix_url use ARTIFACT_URL_TTL_S."""
    import foleyflow.api.app as app_mod

    seen = []
    real_signed = store.backend.signed_url

    def spy(key, expires_s=3600):
        seen.append((key, expires_s))
        return real_signed(key, expires_s)

    monkeypatch.setattr(store.backend, "signed_url", spy)
    store.backend.put_bytes(project.input_key, b"FAKEVIDEO" * 64,
                            content_type="video/mp4")
    version = store.save_timeline(project.id, _timeline())
    run = store.start_run(project.id, "mix")
    video_key = store.mix_video_key(project.id, run.id)
    store.backend.put_bytes(video_key, b"MP4DATA" * 8, content_type="video/mp4")
    store.record_mix(project.id, run_id=run.id,
                     mix_key=store.mix_key(project.id, run.id),
                     video_key=video_key,
                     tier="full", timeline_version=version)

    client.get(f"{API_PREFIX}/projects/{project.id}")
    assert seen, "signed_url was never called"
    for _key, expires_s in seen:
        assert expires_s == app_mod.ARTIFACT_URL_TTL_S


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


def _timeline_commit_body(timeline=None, *, start_time_shift: float = 0.0):
    """JSON body for PUT /timeline from a domain Timeline (optional nudge)."""
    t = timeline or _timeline()
    return {
        "duration": t.duration,
        "ambience": t.ambience,
        "events": [
            {
                "name": e.name,
                "start_time": e.start_time + start_time_shift,
                "duration": e.duration,
                "intensity": e.intensity,
                "kind": e.kind,
                "seed": e.seed,
                "source_duration_s": e.source_duration_s,
            }
            for e in t.events
        ],
    }


def test_timeline_commit_creates_new_version_prior_unchanged(client, store, project):
    """Happy path: PUT modified placement → v2; v1 still readable as-is."""
    from foleyflow.models import Event, Timeline

    v1 = Timeline(duration=15.04, ambience="desert wind", events=[
        Event(name="holster creak", start_time=0.0, duration=1.0,
              intensity="low", kind="continuous", source_duration_s=1.5),
        Event(name="gunshot", start_time=11.0, duration=1.5,
              intensity="high", kind="discrete", source_duration_s=2.0),
    ])
    store.save_timeline(project.id, v1)

    body = _timeline_commit_body(v1, start_time_shift=0.2)
    r = client.put(f"{API_PREFIX}/projects/{project.id}/timeline", json=body)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["version"] == 2
    assert out["events"][0]["start_time"] == pytest.approx(0.2)
    assert out["events"][0]["source_duration_s"] == 1.5
    assert out["events"][0]["index"] == 0

    versions = client.get(
        f"{API_PREFIX}/projects/{project.id}/timeline/versions").json()
    assert versions["versions"] == [1, 2] and versions["latest"] == 2

    old = client.get(
        f"{API_PREFIX}/projects/{project.id}/timeline?version=1").json()
    assert old["version"] == 1
    assert old["events"][0]["start_time"] == pytest.approx(0.0)

    latest = client.get(f"{API_PREFIX}/projects/{project.id}/timeline").json()
    assert latest["version"] == 2
    assert latest["events"][0]["start_time"] == pytest.approx(0.2)


def test_timeline_commit_refused_before_any_timeline(client, project):
    """No plan yet → 422 precondition_failed (not invent a v1 from whole cloth)."""
    r = client.put(f"{API_PREFIX}/projects/{project.id}/timeline",
                   json=_timeline_commit_body())
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "precondition_failed"


def test_timeline_commit_refused_while_run_in_flight(store, project):
    """Any in-flight job locks editing — same 409 as analyze/mix/export."""
    import threading

    runner = JobRunner(store, max_workers=2)
    app = create_app(store, runner)
    gate = threading.Event()
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            store.save_timeline(project.id, _timeline())
            run = runner.submit(project.id, "analyze",
                                lambda r: gate.wait(5))
            r = client.put(f"{API_PREFIX}/projects/{project.id}/timeline",
                           json=_timeline_commit_body())
            assert r.status_code == 409, r.text
            err = r.json()["error"]
            assert err["code"] == "already_running"
            assert err["detail"]["run_id"] == run.id
            assert err["detail"]["kind"] == "analyze"
    finally:
        gate.set()
        runner.shutdown(wait=True)


def test_timeline_commit_rejects_altered_source_duration(client, store, project):
    """Changing a freeze is a client bug — refuse, leave version count alone."""
    from foleyflow.models import Event, Timeline

    t = Timeline(duration=15.04, ambience="desert wind", events=[
        Event(name="gunshot", start_time=11.0, duration=1.5,
              intensity="high", kind="discrete", source_duration_s=2.0),
    ])
    store.save_timeline(project.id, t)
    body = _timeline_commit_body(t)
    body["events"][0]["source_duration_s"] = 3.0  # illegal rewrite

    r = client.put(f"{API_PREFIX}/projects/{project.id}/timeline", json=body)
    assert r.status_code == 400, r.text
    err = r.json()["error"]
    assert err["code"] == "invalid_request"
    assert err.get("detail", {}).get("violations") or "freeze" in err["message"].lower()
    assert store.list_timeline_versions(project.id) == [1]


def test_timeline_commit_rejects_invented_freeze_on_new_event(
        client, store, project):
    """New content may not invent source_duration_s without Regenerate."""
    from foleyflow.models import Event, Timeline

    t = Timeline(duration=15.04, ambience="desert wind", events=[
        Event(name="gunshot", start_time=11.0, duration=1.5,
              intensity="high", kind="discrete", source_duration_s=2.0),
    ])
    store.save_timeline(project.id, t)
    body = _timeline_commit_body(t)
    body["events"].append({
        "name": "boot step",
        "start_time": 5.0,
        "duration": 0.5,
        "intensity": "med",
        "kind": "discrete",
        "seed": None,
        "source_duration_s": 1.0,  # invent freeze on new content
    })

    r = client.put(f"{API_PREFIX}/projects/{project.id}/timeline", json=body)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_request"
    assert store.list_timeline_versions(project.id) == [1]


def test_timeline_commit_rejects_empty_when_freezes_exist(client, store, project):
    """Empty document would drop freezes and enable re-add unfreeze — refuse."""
    from foleyflow.models import Event, Timeline

    t = Timeline(duration=15.04, ambience="desert wind", events=[
        Event(name="gunshot", start_time=11.0, duration=1.5,
              intensity="high", kind="discrete", source_duration_s=2.0),
    ])
    store.save_timeline(project.id, t)

    empty = {"duration": 15.04, "ambience": "desert wind", "events": []}
    r = client.put(f"{API_PREFIX}/projects/{project.id}/timeline", json=empty)
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "invalid_request"
    assert store.list_timeline_versions(project.id) == [1]


def test_timeline_commit_rejects_in_place_unfreeze(client, store, project):
    from foleyflow.models import Event, Timeline

    t = Timeline(duration=15.04, ambience="wind", events=[
        Event(name="gunshot", start_time=1.0, duration=1.0,
              intensity="high", kind="discrete", source_duration_s=2.0),
    ])
    store.save_timeline(project.id, t)
    body = _timeline_commit_body(t)
    body["events"][0]["source_duration_s"] = None
    r = client.put(f"{API_PREFIX}/projects/{project.id}/timeline", json=body)
    assert r.status_code == 400
    assert store.list_timeline_versions(project.id) == [1]


def test_timeline_commit_rejects_invent_freeze_on_existing_unfrozen_slot(
        client, store, project):
    from foleyflow.models import Event, Timeline

    t = Timeline(duration=15.04, ambience="wind", events=[
        Event(name="gunshot", start_time=1.0, duration=1.0,
              intensity="high", kind="discrete"),  # no freeze
    ])
    store.save_timeline(project.id, t)
    body = _timeline_commit_body(t)
    body["events"][0]["source_duration_s"] = 2.0
    r = client.put(f"{API_PREFIX}/projects/{project.id}/timeline", json=body)
    assert r.status_code == 400
    assert store.list_timeline_versions(project.id) == [1]


def test_timeline_commit_allows_reorder_keeping_freezes(client, store, project):
    """Freezes travel with content — reorder is legal; rebind is not."""
    from foleyflow.models import Event, Timeline

    t = Timeline(duration=15.04, ambience="wind", events=[
        Event(name="holster creak", start_time=0.0, duration=1.0,
              intensity="low", kind="continuous", source_duration_s=1.5),
        Event(name="gunshot", start_time=11.0, duration=1.5,
              intensity="high", kind="discrete", source_duration_s=2.0),
    ])
    store.save_timeline(project.id, t)
    body = _timeline_commit_body(t)
    body["events"] = list(reversed(body["events"]))  # freezes travel with events

    r = client.put(f"{API_PREFIX}/projects/{project.id}/timeline", json=body)
    assert r.status_code == 200, r.text
    names = [e["name"] for e in r.json()["events"]]
    assert names == ["gunshot", "holster creak"]
    by_name = {e["name"]: e["source_duration_s"] for e in r.json()["events"]}
    assert by_name["gunshot"] == 2.0
    assert by_name["holster creak"] == 1.5


def test_timeline_commit_rejects_rebound_freezes(client, store, project):
    from foleyflow.models import Event, Timeline

    t = Timeline(duration=15.04, ambience="wind", events=[
        Event(name="holster creak", start_time=0.0, duration=1.0,
              intensity="low", kind="continuous", source_duration_s=1.5),
        Event(name="gunshot", start_time=11.0, duration=1.5,
              intensity="high", kind="discrete", source_duration_s=2.0),
    ])
    store.save_timeline(project.id, t)
    body = _timeline_commit_body(t)
    # Swap freezes only — content keeps positions
    body["events"][0]["source_duration_s"] = 2.0
    body["events"][1]["source_duration_s"] = 1.5

    r = client.put(f"{API_PREFIX}/projects/{project.id}/timeline", json=body)
    assert r.status_code == 400
    assert store.list_timeline_versions(project.id) == [1]


def test_timeline_commit_allows_delete_of_frozen_event(client, store, project):
    """Inverted deliberately: deleting a generated sound is legal.

    This used to be refused, because the freeze multiset had to be exactly
    conserved. But conservation exists to stop an edit *re-billing* on the next
    render, and a deleted event is never rendered again — the money it cost is
    already spent either way. Refusing it meant that once anything was frozen a
    director could never remove that sound, which is a hard editor limitation
    for unit 7.10. Losing a freeze is now legal only when the content is gone
    from the document; in-place unfreeze (below) is still refused.
    """
    from foleyflow.models import Event, Timeline

    t = Timeline(duration=15.04, ambience="wind", events=[
        Event(name="holster creak", start_time=0.0, duration=1.0,
              intensity="low", kind="continuous", source_duration_s=1.5),
        Event(name="gunshot", start_time=11.0, duration=1.5,
              intensity="high", kind="discrete", source_duration_s=2.0),
    ])
    store.save_timeline(project.id, t)
    body = _timeline_commit_body(t)
    body["events"] = [body["events"][0]]  # drop frozen gunshot

    r = client.put(f"{API_PREFIX}/projects/{project.id}/timeline", json=body)
    assert r.status_code == 200, r.text
    assert [e["name"] for e in r.json()["events"]] == ["holster creak"]
    assert store.list_timeline_versions(project.id) == [1, 2]


def test_timeline_commit_rejects_delete_and_readd_unfrozen_in_one_commit(
        client, store, project):
    """Deletion is legal; laundering an unfreeze through it is not.

    The content is still in the submitted document, just without its freeze —
    so this is an in-place unfreeze however the client got there, and the next
    render would re-bill it.
    """
    from foleyflow.models import Event, Timeline

    t = Timeline(duration=15.04, ambience="wind", events=[
        Event(name="gunshot", start_time=11.0, duration=1.5,
              intensity="high", kind="discrete", source_duration_s=2.0),
    ])
    store.save_timeline(project.id, t)
    body = _timeline_commit_body(t)
    body["events"][0]["source_duration_s"] = None
    body["events"][0]["start_time"] = 4.0        # "a different event", same content

    r = client.put(f"{API_PREFIX}/projects/{project.id}/timeline", json=body)
    assert r.status_code == 400, r.text
    assert store.list_timeline_versions(project.id) == [1]


def test_timeline_commit_allows_delete_unfrozen_event(client, store, project):
    from foleyflow.models import Event, Timeline

    t = Timeline(duration=15.04, ambience="wind", events=[
        Event(name="holster creak", start_time=0.0, duration=1.0,
              intensity="low", kind="continuous", source_duration_s=1.5),
        Event(name="wind puff", start_time=3.0, duration=0.5,
              intensity="low", kind="discrete"),  # unfrozen
    ])
    store.save_timeline(project.id, t)
    body = _timeline_commit_body(t)
    body["events"] = [body["events"][0]]

    r = client.put(f"{API_PREFIX}/projects/{project.id}/timeline", json=body)
    assert r.status_code == 200, r.text
    assert len(r.json()["events"]) == 1
    assert r.json()["events"][0]["source_duration_s"] == 1.5

def test_timeline_commit_preserves_freeze_while_editing_placement(
        client, store, project):
    """Free resize/nudge: duration and start_time change; freeze stays."""
    from foleyflow.models import Event, Timeline

    t = Timeline(duration=15.04, ambience="desert wind", events=[
        Event(name="holster creak", start_time=0.0, duration=1.0,
              intensity="low", kind="continuous", seed=42,
              source_duration_s=1.5),
    ])
    store.save_timeline(project.id, t)
    body = _timeline_commit_body(t)
    body["events"][0]["start_time"] = 0.35
    body["events"][0]["duration"] = 0.8  # window shorter — free
    body["events"][0]["seed"] = 99       # seed is writable

    r = client.put(f"{API_PREFIX}/projects/{project.id}/timeline", json=body)
    assert r.status_code == 200, r.text
    ev = r.json()["events"][0]
    assert ev["start_time"] == pytest.approx(0.35)
    assert ev["duration"] == pytest.approx(0.8)
    assert ev["source_duration_s"] == 1.5
    assert ev["seed"] == 99


def test_timeline_commit_source_duration_zero_is_a_real_freeze(
        client, store, project):
    """source_duration_s=0 is present, not absent — nulling it is an unfreeze."""
    from foleyflow.models import Event, Timeline

    t = Timeline(duration=5.0, ambience="wind", events=[
        Event(name="click", start_time=0.0, duration=0.1,
              intensity="low", kind="discrete", source_duration_s=0.0),
    ])
    store.save_timeline(project.id, t)
    body = _timeline_commit_body(t)
    r = client.put(f"{API_PREFIX}/projects/{project.id}/timeline", json=body)
    assert r.status_code == 200, r.text
    assert r.json()["events"][0]["source_duration_s"] == 0.0

    body["events"][0]["source_duration_s"] = None
    r2 = client.put(f"{API_PREFIX}/projects/{project.id}/timeline", json=body)
    assert r2.status_code == 400


def test_timeline_commit_rejects_negative_start_time(client, store, project):
    store.save_timeline(project.id, _timeline())
    body = _timeline_commit_body()
    body["events"][0]["start_time"] = -0.1
    r = client.put(f"{API_PREFIX}/projects/{project.id}/timeline", json=body)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_request"


def test_timeline_commit_refused_for_storage_running_run(client, store, project):
    """Storage-side non-stale running run locks commit even without runner job."""
    store.save_timeline(project.id, _timeline())
    store.start_run(project.id, "mix")  # running in the log; no JobRunner claim

    r = client.put(f"{API_PREFIX}/projects/{project.id}/timeline",
                   json=_timeline_commit_body())
    assert r.status_code == 409, r.text
    assert r.json()["error"]["code"] == "already_running"


def test_timeline_commit_records_user_actor_and_source(client, store, project):
    from foleyflow.models import Event, Timeline
    from foleyflow.projects import EVENT_PLAN_PROPOSED

    t = Timeline(duration=5.0, ambience="wind", events=[
        Event(name="click", start_time=0.0, duration=0.1,
              intensity="low", kind="discrete"),
    ])
    store.save_timeline(project.id, t)
    r = client.put(f"{API_PREFIX}/projects/{project.id}/timeline",
                   json=_timeline_commit_body(t, start_time_shift=0.05))
    assert r.status_code == 200, r.text

    plans = [e for e in store.list_events(project.id)
             if e.type == EVENT_PLAN_PROPOSED]
    commit_events = [e for e in plans if e.payload.get("source") == "commit"]
    assert commit_events, [e.payload for e in plans]
    assert commit_events[-1].actor == "user"
    assert commit_events[-1].payload.get("timeline_version") == 2


def test_timeline_commit_preserves_history_and_extra_fields(client, store, project):
    from foleyflow.models import Event, Timeline

    t = Timeline(
        duration=5.0, ambience="wind",
        events=[Event(name="click", start_time=0.0, duration=0.1,
                      intensity="low", kind="discrete")],
        history=[{"note": "director said earlier"}],
        extra_fields={"label": "take-a"},
    )
    store.save_timeline(project.id, t)
    r = client.put(f"{API_PREFIX}/projects/{project.id}/timeline",
                   json=_timeline_commit_body(t, start_time_shift=0.1))
    assert r.status_code == 200, r.text
    loaded = store.load_timeline(project.id, 2)
    assert loaded.history == [{"note": "director said earlier"}]
    assert loaded.extra_fields.get("label") == "take-a"


def test_timeline_commit_unknown_project_is_404(client):
    r = client.put(f"{API_PREFIX}/projects/{VALID_ID}/timeline",
                   json=_timeline_commit_body())
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "project_not_found"


def test_timeline_commit_malformed_body_is_invalid_request(client, store, project):
    store.save_timeline(project.id, _timeline())
    r = client.put(f"{API_PREFIX}/projects/{project.id}/timeline",
                   json={"duration": 15.0, "ambience": "x", "events": [
                       {"name": "x"}  # missing required placement fields
                   ]})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_request"


def test_timeline_get_exposes_seed_and_source_duration(client, store, project):
    """GET must round-trip freezes for the editor (unit 7.10 prep)."""
    from foleyflow.models import Event, Timeline

    t = Timeline(duration=5.0, ambience="wind", events=[
        Event(name="rain", start_time=0.0, duration=5.0,
              intensity="low", kind="rhythmic", seed=7, source_duration_s=8.0),
        Event(name="clap", start_time=1.0, duration=0.3,
              intensity="high", kind="discrete"),  # freezes absent
    ])
    store.save_timeline(project.id, t)
    events = client.get(f"{API_PREFIX}/projects/{project.id}/timeline").json()["events"]
    assert events[0]["seed"] == 7
    assert events[0]["source_duration_s"] == 8.0
    assert events[1]["seed"] is None
    assert events[1]["source_duration_s"] is None


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


# -- mix and export ---------------------------------------------------------

def _stub_mix(store, project, monkeypatch, *, tier="full", version=1):
    """Stand in for a completed mix run without rendering one.

    The render itself is covered in test_render.py against real mix math; what
    these assert is the contract around it.
    """
    import foleyflow.api.app as app_mod

    def _work(_store, _pid, _tier, _version):
        def run_it(run):
            store.record_mix(project.id, run_id=run.id,
                             mix_key=store.mix_key(project.id, run.id),
                             video_key=store.mix_video_key(project.id, run.id),
                             tier=_tier, timeline_version=_version,
                             stem_count=3, generated_count=1)
            store.complete_run(run)
        return run_it

    monkeypatch.setattr(app_mod, "_mix_work", _work)


def _await_run(client, project_id, run_id, timeout_s=5.0):
    """Poll a 202'd run to a terminal state.

    Mutations are asynchronous by contract, so asserting on the state a run
    produces immediately after the POST is a race — one that passes on a fast
    machine and fails in CI.
    """
    import time

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        body = client.get(f"{API_PREFIX}/projects/{project_id}/runs/{run_id}").json()
        if body.get("status") in ("completed", "failed"):
            return body
        time.sleep(0.02)
    raise AssertionError(f"run {run_id} did not finish within {timeout_s}s")


def test_mix_is_refused_before_a_timeline_exists(client, project):
    r = client.post(f"{API_PREFIX}/projects/{project.id}/mixes")
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "precondition_failed"


def test_mix_rejects_an_unknown_timeline_version(client, store, project):
    store.save_timeline(project.id, _timeline())
    r = client.post(f"{API_PREFIX}/projects/{project.id}/mixes",
                    json={"timeline_version": 7})
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "timeline_not_found"


def test_mix_rejects_an_unknown_tier(client, store, project):
    store.save_timeline(project.id, _timeline())
    r = client.post(f"{API_PREFIX}/projects/{project.id}/mixes",
                    json={"tier": "cinematic"})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_request"


def test_mix_returns_202_and_shows_up_in_readiness(client, store, project,
                                                   monkeypatch):
    store.save_timeline(project.id, _timeline())
    _stub_mix(store, project, monkeypatch)

    r = client.post(f"{API_PREFIX}/projects/{project.id}/mixes")
    assert r.status_code == 202
    assert r.json()["kind"] == "mix"
    assert _await_run(client, project.id, r.json()["run_id"])["status"] == "completed"

    body = client.get(f"{API_PREFIX}/projects/{project.id}").json()
    readiness = {i["key"]: i for i in body["readiness"]}
    assert readiness["mix"]["state"] == "ok"
    assert body["mix_run_id"] == r.json()["run_id"]


def test_a_mix_from_a_superseded_timeline_warns(client, store, project,
                                                monkeypatch):
    """A mix of v1 is not evidence that v2 works."""
    store.save_timeline(project.id, _timeline())
    _stub_mix(store, project, monkeypatch, version=1)
    started = client.post(f"{API_PREFIX}/projects/{project.id}/mixes").json()
    _await_run(client, project.id, started["run_id"])
    store.save_timeline(project.id, _timeline())            # v2, after the mix

    body = client.get(f"{API_PREFIX}/projects/{project.id}").json()
    readiness = {i["key"]: i for i in body["readiness"]}
    assert readiness["mix"]["state"] == "warn"
    assert "v2" in readiness["mix"]["detail"]


def test_export_is_refused_before_a_mix_exists(client, store, project):
    store.save_timeline(project.id, _timeline())
    r = client.post(f"{API_PREFIX}/projects/{project.id}/exports")
    assert r.status_code == 422
    assert r.json()["error"]["detail"]["required"] == "POST /mixes"


def test_export_is_refused_while_qa_blocks_and_lists_the_blockers(
        client, store, project, monkeypatch):
    """The client is told what to fix, at the front door, not after polling."""
    store.save_timeline(project.id, _timeline())
    _stub_mix(store, project, monkeypatch)
    started = client.post(f"{API_PREFIX}/projects/{project.id}/mixes").json()
    _await_run(client, project.id, started["run_id"])
    store.save_qa_report(project.id, {
        "overall_status": "blocked",
        "audio": {"target_lufs": -14, "target_true_peak_dbtp": -1.0,
                  "measured": True,
                  "issues": [{"severity": "blocking",
                              "code": "true_peak_exceeded"}]},
        "visual": {"luma_delta_threshold": 20, "issues": []}})

    r = client.post(f"{API_PREFIX}/projects/{project.id}/exports")
    assert r.status_code == 422
    assert r.json()["error"]["detail"]["blockers"] == ["true_peak_exceeded"]

    override = client.post(f"{API_PREFIX}/projects/{project.id}/exports",
                           json={"override_blocking": True})
    assert override.status_code == 202
    assert _await_run(client, project.id,
                      override.json()["run_id"])["status"] == "completed"


def test_export_publishes_the_pointer(client, store, project, monkeypatch):
    store.save_timeline(project.id, _timeline())
    _stub_mix(store, project, monkeypatch)
    mix_run = client.post(f"{API_PREFIX}/projects/{project.id}/mixes").json()["run_id"]
    _await_run(client, project.id, mix_run)

    r = client.post(f"{API_PREFIX}/projects/{project.id}/exports")
    assert r.status_code == 202
    assert r.json()["kind"] == "export"
    assert _await_run(client, project.id, r.json()["run_id"])["status"] == "completed"

    body = client.get(f"{API_PREFIX}/projects/{project.id}").json()
    assert body["has_export"] is True
    assert body["status"] == "ready"
    assert store.get_latest_export(project.id)["run_id"] == mix_run


# -- dossier ----------------------------------------------------------------

def _seed_full_project(store):
    """A project with every artifact kind a dossier can list, plus the bytes
    behind them — built through the store exactly as the API's own runs would
    (the render math itself is covered in test_render.py)."""
    from foleyflow.qa import build_report
    from foleyflow.scenes import Scene

    project = store.create_project(
        title="Western standoff", input_key="source.mp4",
        source_sha256="a" * 64, source_filename="standoff.mp4",
        duration_s=15.04)
    store.backend.put_bytes(project.input_key, b"FAKEVIDEO" * 64,
                            content_type="video/mp4")

    version = store.save_timeline(project.id, _timeline())
    store.save_scenes(project.id, [
        Scene(id="scene_0001", index=0, start_s=0.0, end_s=15.04)])
    # Thumbnails are written at analysis time beside the scene record; the
    # dossier reads them from `thumbnail_key` on each scene. extract_thumbnail
    # needs a real video, so the contract test stands in for it directly.
    store.backend.put_bytes(
        store.scene_thumbnail_key(project.id, "scene_0001"),
        b"JPEGDATA", content_type="image/jpeg")
    store.backend.put_bytes(
        store.scenes_key(project.id),
        json.dumps({"scenes": [{
            "id": "scene_0001", "index": 0, "start_s": 0.0, "end_s": 15.04,
            "duration_s": 15.04,
            "thumbnail_key": store.scene_thumbnail_key(project.id,
                                                        "scene_0001")}]}
        ).encode(), content_type="application/json")
    store.save_qa_report(project.id, build_report([]))

    run = store.start_run(project.id, "mix")
    stem = {"cache_key": "abc",
            "b2_key": "cache/0123456789abcdef.wav",
            "endpoint": "openmoss/moss-soundeffect-v2",
            "prompt": "gunshot", "duration_s": 2.0, "variant": "",
            "cached": True, "sha256": "c" * 64}
    store.save_stem_inventory(project.id, run.id, [stem])
    store.backend.put_bytes(store.mix_key(project.id, run.id), b"MIXWAV" * 8,
                            content_type="audio/wav")
    store.backend.put_bytes(store.mix_video_key(project.id, run.id),
                            b"MP4DATA" * 8, content_type="video/mp4")
    store.backend.put_bytes("cache/0123456789abcdef.wav", b"STEM" * 8,
                            content_type="audio/wav")
    store.record_mix(project.id, run_id=run.id,
                     mix_key=store.mix_key(project.id, run.id),
                     video_key=store.mix_video_key(project.id, run.id),
                     tier="full", timeline_version=version,
                     stem_count=1, generated_count=0)
    store.complete_run(run, timeline_version=version)

    export = store.start_run(project.id, "export")
    store.save_manifest(project.id, export.id, {"manifest_version": "1.0"})
    store.set_latest_export(project.id, run_id=run.id,
                            export_key=store.mix_video_key(project.id, run.id),
                            manifest_key=store.manifest_key(project.id,
                                                            export.id))
    store.complete_run(export)
    return project


def test_dossier_lists_every_artifact_kind(client, store):
    project = _seed_full_project(store)
    body = client.get(f"{API_PREFIX}/projects/{project.id}/dossier").json()
    assert body["project_id"] == project.id
    kinds = [a["kind"] for a in body["artifacts"]]
    for expected in ("source", "timeline", "scene_index", "scene_thumbnail",
                     "qa_report", "event", "run_record", "stems", "stem",
                     "mix", "final_export", "manifest", "export_pointer"):
        assert expected in kinds, kinds
    assert body["artifact_count"] == len(body["artifacts"])
    # Every artifact is addressable through a signed URL, never a raw key a
    # client could use directly (docs/api.md §1), and carries a real size.
    for a in body["artifacts"]:
        assert a["key"]
        assert a["url"].startswith("file:///")
        assert a["size_bytes"] > 0
    assert body["total_bytes"] == sum(a["size_bytes"]
                                       for a in body["artifacts"])


def test_dossier_carries_recorded_hashes_not_recomputed_ones(client, store):
    """sha256 is provenance where the system has one: the source's recorded
    hash must not change even though the object it names is stubbed bytes, and
    the stem hash comes from the inventory record."""
    project = _seed_full_project(store)
    body = client.get(f"{API_PREFIX}/projects/{project.id}/dossier").json()
    by_key = {a["key"]: a for a in body["artifacts"]}
    assert by_key["source.mp4"]["sha256"] == "a" * 64
    assert by_key["cache/0123456789abcdef.wav"]["sha256"] == "c" * 64
    # Whatever fills the column, it is a sha256 hexdigest or an honest None.
    assert all(a["sha256"] is None or len(a["sha256"]) == 64
               for a in body["artifacts"])


def test_dossier_counts_physical_objects_not_references(client, store):
    """Two runs sharing one content-addressed stem list one artifact: the
    dossier lists what exists, not how often it is referenced."""
    project = _seed_full_project(store)
    run2 = store.start_run(project.id, "mix")
    store.save_stem_inventory(project.id, run2.id, [{
        "cache_key": "abc", "b2_key": "cache/0123456789abcdef.wav",
        "endpoint": "openmoss/moss-soundeffect-v2", "prompt": "gunshot",
        "duration_s": 2.0, "variant": "", "cached": True,
        "sha256": "c" * 64}])
    store.record_mix(project.id, run_id=run2.id,
                     mix_key=store.mix_key(project.id, run2.id),
                     video_key=store.mix_video_key(project.id, run2.id),
                     tier="full", timeline_version=1, stem_count=1)
    store.complete_run(run2, timeline_version=1)
    body = client.get(f"{API_PREFIX}/projects/{project.id}/dossier").json()
    stems = [a for a in body["artifacts"] if a["kind"] == "stem"]
    assert len(stems) == 1


def test_dossier_omits_artifacts_whose_objects_are_absent(client, project):
    """A project record without its bytes lists only what exists: the upload
    was never confirmed, so there is no source artifact."""
    body = client.get(f"{API_PREFIX}/projects/{project.id}/dossier").json()
    kinds = [a["kind"] for a in body["artifacts"]]
    assert "source" not in kinds
    assert all(k == "event" for k in kinds)   # the event log is all there is


def test_dossier_404s_for_unknown_project(client):
    r = client.get(f"{API_PREFIX}/projects/{VALID_ID}/dossier")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "project_not_found"


def test_dossier_503s_when_storage_cannot_answer(client, store, monkeypatch):
    """A listing assembled during an outage would be a confident half-truth:
    absent and unknown must stay distinct, so the route says storage is down
    rather than returning a dossier full of holes."""
    project = _seed_full_project(store)

    def _boom(key):
        raise StorageError("credentials rejected")

    monkeypatch.setattr(store.backend, "head", _boom)
    r = client.get(f"{API_PREFIX}/projects/{project.id}/dossier")
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "storage_unavailable"


def test_dossier_503s_when_signing_fails_after_a_successful_head(
        client, store, monkeypatch):
    """A head that succeeded and a sign that failed is a storage fault, not an
    absent object: the dossier must never list an artifact with url: null."""
    project = _seed_full_project(store)

    def _boom(key, expires_s=3600):
        raise StorageError("presign failed")

    monkeypatch.setattr(store.backend, "signed_url", _boom)
    r = client.get(f"{API_PREFIX}/projects/{project.id}/dossier")
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "storage_unavailable"


# -- repair -----------------------------------------------------------------

def _stub_repair_work(store, project, monkeypatch, captured):
    """Stand in for the repair work: record what the route wired through and
    complete the run with a lineage event. The render itself is covered in
    test_render.py; these assert the contract around it."""
    import foleyflow.api.app as app_mod
    from foleyflow.projects import EVENT_REPAIR_APPROVED

    def _work(_store, _pid, version, event_index, request, note, provider):
        def run_it(run):
            captured["version"] = version
            captured["event_index"] = event_index
            captured["request"] = request
            captured["note"] = note
            captured["provider"] = provider
            # The real work allocates the new version; the stub does the same
            # so the lineage and version listing stay honest.
            timeline = _store.load_timeline(_pid, version)
            new_version = _store.save_timeline(_pid, timeline, run_id=run.id)
            store.append_event(project.id, EVENT_REPAIR_APPROVED, actor="user",
                               run_id=run.id,
                               payload={"event_index": event_index,
                                        "superseded": {"b2_key": "old-take"},
                                        "variant": "rX",
                                        "timeline_version": new_version})
            store.complete_run(run, timeline_version=new_version,
                               metadata={"event_index": event_index,
                                         "variant": "rX"})
        return run_it

    monkeypatch.setattr(app_mod, "_repair_work", _work)


def test_regenerate_404s_for_an_unknown_project(client):
    r = client.post(f"{API_PREFIX}/projects/{VALID_ID}/events/0/regenerate",
                    json={})
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "project_not_found"


def test_regenerate_is_refused_before_a_timeline_exists(client, project):
    r = client.post(f"{API_PREFIX}/projects/{project.id}/events/0/regenerate",
                    json={})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "precondition_failed"


def test_regenerate_rejects_an_out_of_range_event_index(client, store, project):
    store.save_timeline(project.id, _timeline())
    r = client.post(f"{API_PREFIX}/projects/{project.id}/events/99/regenerate",
                    json={})
    assert r.status_code == 400
    detail = r.json()["error"]["detail"]
    assert detail["event_count"] == len(_timeline().events)


def test_regenerate_maps_index_to_request_and_records_the_lineage(
        client, store, project, monkeypatch):
    """Event index -> SfxRequest at the route, and the run's lineage names the
    superseded take and the new timeline version."""
    store.save_timeline(project.id, _timeline())
    captured = {}
    _stub_repair_work(store, project, monkeypatch, captured)

    r = client.post(f"{API_PREFIX}/projects/{project.id}/events/2/regenerate",
                    json={"note": "the take was thin"})
    assert r.status_code == 202
    body = r.json()
    assert body["kind"] == "repair"
    assert body["estimated_cost_usd"] == 0.0   # the default tier is free

    _await_run(client, project.id, body["run_id"])

    assert captured["event_index"] == 2
    assert captured["note"] == "the take was thin"
    assert captured["provider"] is None
    assert captured["request"].prompt.startswith("gunshot")

    # The repair produced a new timeline version beside the original.
    assert store.list_timeline_versions(project.id) == [1, 2]

    from foleyflow.projects import EVENT_REPAIR_APPROVED
    lineage = [e for e in store.list_events(project.id)
               if e.type == EVENT_REPAIR_APPROVED]
    assert len(lineage) == 1
    assert lineage[0].payload["event_index"] == 2
    assert lineage[0].payload["superseded"] == {"b2_key": "old-take"}
    assert lineage[0].payload["timeline_version"] == 2


def test_regenerate_requires_confirmation_when_billable(
        client, store, project, monkeypatch):
    """SPEC §9.4: a billable regeneration must be confirmed with the exact
    estimate the client was shown."""
    store.save_timeline(project.id, _timeline())
    _stub_repair_work(store, project, monkeypatch, {})
    monkeypatch.setenv("FOLEYFLOW_SFX_TIER", "paid")

    r = client.post(f"{API_PREFIX}/projects/{project.id}/events/2/regenerate",
                    json={})
    assert r.status_code == 422
    detail = r.json()["error"]["detail"]
    assert detail["estimated_cost_usd"] == pytest.approx(0.30)
    assert detail["require"] == "confirm_cost_usd"

    r = client.post(f"{API_PREFIX}/projects/{project.id}/events/2/regenerate",
                    json={"confirm_cost_usd": 0.99})
    assert r.status_code == 422

    r = client.post(f"{API_PREFIX}/projects/{project.id}/events/2/regenerate",
                    json={"confirm_cost_usd": 0.30})
    assert r.status_code == 202
    assert r.json()["estimated_cost_usd"] == pytest.approx(0.30)


def test_regenerate_rejects_an_unknown_provider(client, store, project):
    store.save_timeline(project.id, _timeline())
    r = client.post(f"{API_PREFIX}/projects/{project.id}/events/0/regenerate",
                    json={"provider": "not-a-tier"})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_request"


def test_regenerate_provider_reroutes_to_an_approved_tier(
        client, store, project, monkeypatch):
    """`provider` names a declared SFX tier: the request is re-aimed at it and
    the estimate is that tier's — the free tier needs no confirmation even
    when the active tier is paid."""
    store.save_timeline(project.id, _timeline())
    captured = {}
    _stub_repair_work(store, project, monkeypatch, captured)
    monkeypatch.setenv("FOLEYFLOW_SFX_TIER", "paid")

    r = client.post(f"{API_PREFIX}/projects/{project.id}/events/2/regenerate",
                    json={"provider": "test"})
    assert r.status_code == 202
    assert r.json()["estimated_cost_usd"] == 0.0
    _await_run(client, project.id, r.json()["run_id"])
    assert captured["request"].endpoint == "openmoss/moss-soundeffect-v2"


# -- budget -----------------------------------------------------------------

@pytest.fixture
def budget_ledger(store, monkeypatch):
    """Point the cost ledger at the store's own backend so budget tests can
    attribute rows and read them back without touching any global ledger.

    Storage mode is required: file-mode ledgers skip budget alerts and
    attributions (see ledger._maybe_emit_budget_alerts / iter_attributions).
    """
    import foleyflow.ledger as ledger_mod
    import foleyflow.storage as storage_mod

    monkeypatch.delenv("FOLEYFLOW_LEDGER_PATH", raising=False)
    monkeypatch.setattr(ledger_mod, "LEDGER_PATH", None)
    monkeypatch.setattr(storage_mod, "get_backend", lambda *a, **k: store.backend)
    monkeypatch.setattr(ledger_mod, "_write_ok", True)
    ledger_mod._invalidate_row_cache()
    ledger_mod.invalidate_project_spend_cache()
    return ledger_mod


def _spend(budget_ledger, project_id, endpoint, cost, success=True,
           cost_source="estimate"):
    with budget_ledger.set_project_context(project_id):
        budget_ledger.log_transaction(endpoint, "h", cost, success=success,
                                      cost_source=cost_source)


def test_budget_reports_spend_against_the_cap(client, store, budget_ledger):
    project = store.create_project(title="A", input_key="k", source_sha256="",
                                   source_filename="a.mp4", budget_usd=1.0)
    _spend(budget_ledger, project.id, "gemini-2.5-flash", 0.30)
    _spend(budget_ledger, project.id, "gemini-2.5-flash", 0.05, success=False)

    body = client.get(f"{API_PREFIX}/projects/{project.id}/budget").json()
    assert body["project_id"] == project.id
    assert body["budget_usd"] == 1.0
    # Failed calls are counted, the same rule as /quota.
    assert body["spent"] == pytest.approx(0.35)
    assert body["remaining"] == pytest.approx(0.65)
    assert body["estimate"] is True
    assert body["ledger_writable"] is True
    assert body["by_model"] == [{"endpoint": "gemini-2.5-flash",
                                  "spent": pytest.approx(0.35)}]


def test_budget_isolates_projects(client, store, budget_ledger):
    a = store.create_project(title="A", input_key="ka", source_sha256="",
                             source_filename="a.mp4", budget_usd=1.0)
    b = store.create_project(title="B", input_key="kb", source_sha256="",
                             source_filename="b.mp4", budget_usd=1.0)
    _spend(budget_ledger, a.id, "gemini-2.5-flash", 0.30)
    _spend(budget_ledger, b.id, "gemini-2.5-flash", 0.60)

    a_body = client.get(f"{API_PREFIX}/projects/{a.id}/budget").json()
    b_body = client.get(f"{API_PREFIX}/projects/{b.id}/budget").json()
    assert a_body["spent"] == pytest.approx(0.30)
    assert a_body["remaining"] == pytest.approx(0.70)
    assert b_body["spent"] == pytest.approx(0.60)
    assert b_body["remaining"] == pytest.approx(0.40)


def test_budget_is_advisory_and_never_refuses_work(client, store, budget_ledger,
                                                   monkeypatch):
    """Task 9d: a soft budget never gates analyze/regenerate. Overspend is
    reported, not refused; confirm_cost_usd remains the consent gate."""
    project = store.create_project(title="B", input_key="k", source_sha256="",
                                   source_filename="s.mp4", budget_usd=0.50)
    store.save_timeline(project.id, _timeline())
    _spend(budget_ledger, project.id, "eleven_text_to_sound_v2", 0.30)
    monkeypatch.setenv("FOLEYFLOW_SFX_TIER", "paid")

    r = client.post(
        f"{API_PREFIX}/projects/{project.id}/events/2/regenerate",
        json={"confirm_cost_usd": 0.30})
    assert r.status_code == 202, r.text

    body = client.get(f"{API_PREFIX}/projects/{project.id}/budget").json()
    assert body["budget_usd"] == 0.50
    assert body["spent"] == pytest.approx(0.30)
    assert body["remaining"] == pytest.approx(0.20)


def test_budget_allows_work_with_no_advisory_figure(client, store, budget_ledger,
                                                    monkeypatch):
    project = store.create_project(title="B", input_key="k", source_sha256="",
                                   source_filename="s.mp4", budget_usd=None)
    store.save_timeline(project.id, _timeline())
    _spend(budget_ledger, project.id, "eleven_text_to_sound_v2", 100.0)
    monkeypatch.setenv("FOLEYFLOW_SFX_TIER", "paid")

    r = client.post(
        f"{API_PREFIX}/projects/{project.id}/events/2/regenerate",
        json={"confirm_cost_usd": 0.30})
    assert r.status_code == 202, r.text


def test_budget_still_returns_when_the_ledger_is_unwritable(
        client, project, monkeypatch):
    import foleyflow.ledger as ledger_mod

    monkeypatch.setattr(ledger_mod, "ledger_writable", lambda *a, **k: False)
    body = client.get(f"{API_PREFIX}/projects/{project.id}/budget").json()
    assert body["ledger_writable"] is False
    assert body["estimate"] is True
    assert "spent" in body


def test_create_project_applies_the_budget_default(client, monkeypatch):
    """Omitted budget_usd resolves to the providers.toml [budgets] default and
    is seeded as the first budget.set event."""
    import foleyflow.api.app as app_mod

    # LocalBackend cannot sign upload URLs (B2-only), so stub the target.
    monkeypatch.setattr(app_mod, "create_upload_target",
                        lambda *a, **k: ("https://upload.example/x",
                                         "2026-01-01T00:00:00Z", {}))
    r = client.post(f"{API_PREFIX}/projects",
                    json={"title": "x", "filename": "a.mp4"})
    assert r.status_code == 201, r.text
    assert r.json()["project"]["budget_usd"] == 5.0   # providers.toml [budgets]


def test_create_project_records_an_explicit_budget(client, monkeypatch):
    import foleyflow.api.app as app_mod

    monkeypatch.setattr(app_mod, "create_upload_target",
                        lambda *a, **k: ("https://upload.example/x",
                                         "2026-01-01T00:00:00Z", {}))
    r = client.post(f"{API_PREFIX}/projects",
                    json={"title": "x", "filename": "a.mp4",
                          "budget_usd": 2.5})
    assert r.status_code == 201, r.text
    assert r.json()["project"]["budget_usd"] == 2.5


def test_create_project_rejects_a_non_positive_budget(client):
    for value in (0, -1):
        r = client.post(f"{API_PREFIX}/projects",
                        json={"title": "x", "filename": "a.mp4",
                              "budget_usd": value})
        assert r.status_code == 400, r.text


def test_dossier_carries_the_budget_block(client, store, budget_ledger):
    project = store.create_project(title="A", input_key="k", source_sha256="",
                                   source_filename="a.mp4", budget_usd=2.0)
    _spend(budget_ledger, project.id, "gemini-2.5-flash", 0.87)
    body = client.get(f"{API_PREFIX}/projects/{project.id}/dossier").json()
    budget = body["budget"]
    assert budget["budget_usd"] == 2.0
    assert budget["spent"] == pytest.approx(0.87)
    assert budget["remaining"] == pytest.approx(1.13)
    assert budget["estimate"] is True


def test_put_budget_sets_changes_and_clears(client, store, budget_ledger):
    """Task 9a: budget is mutable via PUT; the log records every change."""
    from foleyflow.projects import EVENT_BUDGET_SET

    project = store.create_project(title="A", input_key="k", source_sha256="",
                                   source_filename="a.mp4", budget_usd=1.0)
    r = client.put(f"{API_PREFIX}/projects/{project.id}/budget",
                   json={"budget_usd": 3.5})
    assert r.status_code == 200, r.text
    assert r.json()["budget_usd"] == 3.5

    r = client.put(f"{API_PREFIX}/projects/{project.id}/budget",
                   json={"budget_usd": None})
    assert r.status_code == 200, r.text
    assert r.json()["budget_usd"] is None
    assert r.json()["remaining"] is None

    # Creation seed + raise + clear.
    sets = [e for e in store.list_events(project.id)
            if e.type == EVENT_BUDGET_SET]
    assert len(sets) == 3
    assert sets[0].payload["budget_usd"] == 1.0
    assert sets[1].payload["budget_usd"] == 3.5
    assert sets[2].payload["budget_usd"] is None
    assert all(e.actor == "user" or e.actor == "system" for e in sets)


def test_put_budget_rejects_non_positive(client, store):
    project = store.create_project(title="A", input_key="k", source_sha256="",
                                   source_filename="a.mp4", budget_usd=1.0)
    for value in (0, -2):
        r = client.put(f"{API_PREFIX}/projects/{project.id}/budget",
                       json={"budget_usd": value})
        assert r.status_code == 400, r.text


def test_put_budget_below_spend_emits_alerts(client, store, budget_ledger):
    """Task 17: lowering the budget beneath existing spend alerts immediately.

    Alerts fire only on ledger append without this path — setting $3 against
    $4 already spent must announce every crossed threshold once, with
    reason=budget_set so History can say why.
    """
    from foleyflow.projects import EVENT_BUDGET_ALERT, EVENT_BUDGET_SET

    budget_ledger.invalidate_project_spend_cache()
    budget_ledger.invalidate_budget_cache()

    project = store.create_project(title="A", input_key="k", source_sha256="",
                                   source_filename="a.mp4", budget_usd=None)
    _spend(budget_ledger, project.id, "gemini-2.5-flash", 4.0)
    assert not any(e.type == EVENT_BUDGET_ALERT
                   for e in store.list_events(project.id))

    r = client.put(f"{API_PREFIX}/projects/{project.id}/budget",
                   json={"budget_usd": 3.0})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["budget_usd"] == 3.0
    assert body["spent"] == pytest.approx(4.0)
    assert body["remaining"] == pytest.approx(-1.0)
    # Panel is honest either way; the lifecycle events are the point.
    assert all(t["crossed"] for t in body["thresholds"])

    alerts = [e for e in store.list_events(project.id)
              if e.type == EVENT_BUDGET_ALERT]
    thresholds = sorted(e.payload["threshold"] for e in alerts)
    assert thresholds == [0.5, 0.8, 1.0, 1.2]
    assert all(e.payload.get("reason") == "budget_set" for e in alerts)
    assert all(e.payload.get("spent") == pytest.approx(4.0) for e in alerts)

    # Same value again: no-op — no new set event, no re-alerts.
    sets_before = sum(1 for e in store.list_events(project.id)
                      if e.type == EVENT_BUDGET_SET)
    r = client.put(f"{API_PREFIX}/projects/{project.id}/budget",
                   json={"budget_usd": 3.0})
    assert r.status_code == 200
    sets_after = sum(1 for e in store.list_events(project.id)
                     if e.type == EVENT_BUDGET_SET)
    assert sets_after == sets_before
    assert sum(1 for e in store.list_events(project.id)
               if e.type == EVENT_BUDGET_ALERT) == 4


def test_put_budget_clear_and_raise_emit_nothing(client, store, budget_ledger):
    """Clearing removes the figure; raising above spend crosses nothing."""
    from foleyflow.projects import EVENT_BUDGET_ALERT

    budget_ledger.invalidate_project_spend_cache()
    budget_ledger.invalidate_budget_cache()

    project = store.create_project(title="A", input_key="k", source_sha256="",
                                   source_filename="a.mp4", budget_usd=5.0)
    _spend(budget_ledger, project.id, "gemini-2.5-flash", 1.0)

    r = client.put(f"{API_PREFIX}/projects/{project.id}/budget",
                   json={"budget_usd": 10.0})
    assert r.status_code == 200
    assert not any(e.type == EVENT_BUDGET_ALERT
                   for e in store.list_events(project.id))

    r = client.put(f"{API_PREFIX}/projects/{project.id}/budget",
                   json={"budget_usd": None})
    assert r.status_code == 200
    assert r.json()["budget_usd"] is None
    assert not any(e.type == EVENT_BUDGET_ALERT
                   for e in store.list_events(project.id))


def test_put_budget_lower_again_alerts_under_new_generation(
        client, store, budget_ledger):
    """Lower → raise → lower again: the second lower is a genuine re-cross."""
    from foleyflow.projects import EVENT_BUDGET_ALERT

    budget_ledger.invalidate_project_spend_cache()
    budget_ledger.invalidate_budget_cache()

    project = store.create_project(title="A", input_key="k", source_sha256="",
                                   source_filename="a.mp4", budget_usd=None)
    _spend(budget_ledger, project.id, "gemini-2.5-flash", 4.0)

    client.put(f"{API_PREFIX}/projects/{project.id}/budget",
               json={"budget_usd": 3.0})
    first = [e for e in store.list_events(project.id)
             if e.type == EVENT_BUDGET_ALERT]
    assert len(first) == 4

    client.put(f"{API_PREFIX}/projects/{project.id}/budget",
               json={"budget_usd": 10.0})
    # Raise is silent; prior alerts remain.
    assert sum(1 for e in store.list_events(project.id)
               if e.type == EVENT_BUDGET_ALERT) == 4

    client.put(f"{API_PREFIX}/projects/{project.id}/budget",
               json={"budget_usd": 2.0})
    second = [e for e in store.list_events(project.id)
              if e.type == EVENT_BUDGET_ALERT
              and e.payload.get("budget_usd") == 2.0]
    assert len(second) == 4
    assert all(e.payload.get("reason") == "budget_set" for e in second)


def test_put_budget_survives_alert_failure(client, store, budget_ledger,
                                           monkeypatch):
    """An alerting bug must not fail a durable budget.set."""
    import foleyflow.ledger as ledger_mod

    project = store.create_project(title="A", input_key="k", source_sha256="",
                                   source_filename="a.mp4", budget_usd=None)
    _spend(budget_ledger, project.id, "gemini-2.5-flash", 4.0)

    def boom(*a, **k):
        raise RuntimeError("alert path exploded")

    monkeypatch.setattr(ledger_mod, "evaluate_budget_alerts", boom)
    r = client.put(f"{API_PREFIX}/projects/{project.id}/budget",
                   json={"budget_usd": 3.0})
    assert r.status_code == 200, r.text
    assert r.json()["budget_usd"] == 3.0


def test_budget_alert_fires_once_per_threshold(client, store, budget_ledger):
    """Task 9b: crossing 80% during fan-out emits exactly one alert event."""
    from foleyflow.projects import EVENT_BUDGET_ALERT

    budget_ledger.invalidate_project_spend_cache()
    budget_ledger.invalidate_budget_cache()

    project = store.create_project(title="A", input_key="k", source_sha256="",
                                   source_filename="a.mp4", budget_usd=1.0)
    # 0.50 → 50%, 0.80 → 80%, 1.00 → 100%. Two more rows past 80% must not
    # re-emit 80%.
    _spend(budget_ledger, project.id, "gemini-2.5-flash", 0.50,
           cost_source="actual")
    _spend(budget_ledger, project.id, "gemini-2.5-flash", 0.30,
           cost_source="estimate")
    _spend(budget_ledger, project.id, "gemini-2.5-flash", 0.10,
           cost_source="estimate")
    _spend(budget_ledger, project.id, "gemini-2.5-flash", 0.05,
           cost_source="estimate")

    alerts = [e for e in store.list_events(project.id)
              if e.type == EVENT_BUDGET_ALERT]
    thresholds = sorted(e.payload["threshold"] for e in alerts)
    assert 0.5 in thresholds
    assert 0.8 in thresholds
    assert thresholds.count(0.5) == 1
    assert thresholds.count(0.8) == 1

    # Alert carries cost_source split.
    at_80 = next(e for e in alerts if e.payload["threshold"] == 0.8)
    assert at_80.payload["spent"] == pytest.approx(0.80)
    assert at_80.payload["spent_actual"] == pytest.approx(0.50)
    assert at_80.payload["spent_estimated"] == pytest.approx(0.30)

    body = client.get(f"{API_PREFIX}/projects/{project.id}/budget").json()
    crossed = {t["threshold"]: t["crossed"] for t in body["thresholds"]}
    assert crossed[0.5] is True
    assert crossed[0.8] is True
    assert body["spent_actual"] == pytest.approx(0.50)
    assert body["spent_estimated"] == pytest.approx(0.45)


def test_budget_alert_is_idempotent_under_concurrent_stems(
        client, store, budget_ledger):
    """put_if_absent on the alert marker admits exactly one writer at 80%."""
    import concurrent.futures
    from foleyflow.projects import EVENT_BUDGET_ALERT

    budget_ledger.invalidate_project_spend_cache()
    budget_ledger.invalidate_budget_cache()

    project = store.create_project(title="A", input_key="k", source_sha256="",
                                   source_filename="a.mp4", budget_usd=1.0)
    # Seed just under 80%.
    _spend(budget_ledger, project.id, "gemini-2.5-flash", 0.79)

    def cross():
        _spend(budget_ledger, project.id, "gemini-2.5-flash", 0.05)

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _: cross(), range(8)))

    alerts_80 = [
        e for e in store.list_events(project.id)
        if e.type == EVENT_BUDGET_ALERT and e.payload.get("threshold") == 0.8
    ]
    assert len(alerts_80) == 1, [e.payload for e in alerts_80]


def test_mix_response_carries_a_zero_projection_when_fully_cached(
        client, store, budget_ledger, monkeypatch):
    """Task 9c: a fully cached mix projects $0.00 and is never gated."""
    from foleyflow.routing import SfxRequest

    project = store.create_project(title="A", input_key="k", source_sha256="a" * 64,
                                   source_filename="a.mp4", budget_usd=5.0,
                                   duration_s=15.04)
    store.save_timeline(project.id, _timeline())
    _spend(budget_ledger, project.id, "gemini-2.5-flash", 1.0)

    # Every planned request looks cached → projected $0.
    monkeypatch.setattr(
        "foleyflow.sfx_cache.is_cached", lambda *a, **k: True)
    monkeypatch.setattr(
        "foleyflow.assemble.plan_requests",
        lambda *a, **k: [SfxRequest(endpoint="openmoss/moss-soundeffect-v2",
                                    prompt="x", duration_s=1.0,
                                    category="foley", tier="test")])

    r = client.post(f"{API_PREFIX}/projects/{project.id}/mixes", json={})
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["projected_cost_usd"] == pytest.approx(0.0)
    assert body["estimated_cost_usd"] == pytest.approx(0.0)
    assert body["spent_usd"] == pytest.approx(1.0)
    assert body["projected_total_usd"] == pytest.approx(1.0)
    assert body["budget_usd"] == 5.0
    assert body["cache_misses"] == 0


def test_running_spend_tracks_rows_written_before_budget_is_set(
        client, store, budget_ledger):
    """Clear/absent budget must not drop running totals (adversarial #1)."""
    from foleyflow.projects import EVENT_BUDGET_ALERT

    budget_ledger.invalidate_project_spend_cache()
    budget_ledger.invalidate_budget_cache()

    project = store.create_project(title="A", input_key="k", source_sha256="",
                                   source_filename="a.mp4", budget_usd=None)
    # Spend while no advisory figure is set.
    _spend(budget_ledger, project.id, "gemini-2.5-flash", 0.60)
    _spend(budget_ledger, project.id, "gemini-2.5-flash", 0.30)

    r = client.put(f"{API_PREFIX}/projects/{project.id}/budget",
                   json={"budget_usd": 1.0})
    assert r.status_code == 200
    # Live crossed from spend vs threshold, even with no new ledger row yet.
    body = r.json()
    assert body["spent"] == pytest.approx(0.90)
    assert body["remaining"] == pytest.approx(0.10)
    crossed = {t["threshold"]: t["crossed"] for t in body["thresholds"]}
    assert crossed[0.5] is True
    assert crossed[0.8] is True

    # Next attributed row must still increment and can fire 100%.
    _spend(budget_ledger, project.id, "gemini-2.5-flash", 0.15)
    alerts = [e for e in store.list_events(project.id)
              if e.type == EVENT_BUDGET_ALERT]
    assert any(e.payload.get("threshold") == 1.0 for e in alerts)


def test_budget_remaining_goes_negative_when_overspent(client, store,
                                                      budget_ledger):
    project = store.create_project(title="A", input_key="k", source_sha256="",
                                   source_filename="a.mp4", budget_usd=1.0)
    _spend(budget_ledger, project.id, "gemini-2.5-flash", 1.50)
    body = client.get(f"{API_PREFIX}/projects/{project.id}/budget").json()
    assert body["remaining"] == pytest.approx(-0.50)
    assert body["spent"] == pytest.approx(1.50)


def test_raise_budget_allows_realert_at_new_figure(client, store,
                                                   budget_ledger):
    """Marker keys include generation so a raise can re-fire thresholds."""
    from foleyflow.projects import EVENT_BUDGET_ALERT

    budget_ledger.invalidate_project_spend_cache()
    budget_ledger.invalidate_budget_cache()

    project = store.create_project(title="A", input_key="k", source_sha256="",
                                   source_filename="a.mp4", budget_usd=1.0)
    _spend(budget_ledger, project.id, "gemini-2.5-flash", 0.85)
    first = [e for e in store.list_events(project.id)
             if e.type == EVENT_BUDGET_ALERT and e.payload.get("threshold") == 0.8]
    assert len(first) == 1

    client.put(f"{API_PREFIX}/projects/{project.id}/budget",
               json={"budget_usd": 2.0})
    budget_ledger.invalidate_budget_cache(project.id)
    # Under the new $2 figure, 0.85 is only 42.5% — no 80% yet.
    # Spend up to 80% of $2.
    _spend(budget_ledger, project.id, "gemini-2.5-flash", 0.80)
    second = [e for e in store.list_events(project.id)
              if e.type == EVENT_BUDGET_ALERT
              and e.payload.get("threshold") == 0.8
              and e.payload.get("budget_usd") == 2.0]
    assert len(second) == 1


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


# -- ingest: Genblaze in the live path ---------------------------------------

def _uploadable_project(store, tmp_path):
    """A project whose source object really exists, so /uploaded can run.

    A tiny non-video file is enough: ffprobe is stubbed by the caller, and what
    is under test is that the ingest job reaches Genblaze at all.
    """
    project = store.create_project(title="Standoff", input_key="in/source.mp4",
                                   source_sha256="", source_filename="standoff.mp4")
    src = tmp_path / "source.mp4"
    src.write_bytes(b"\0" * 512)
    store.backend.put(project.input_key, str(src), content_type="video/mp4")
    return project


@pytest.fixture
def stub_ffprobe(monkeypatch):
    """ffprobe stays the sole duration authority — this just supplies its answer."""
    import foleyflow.analyze as analyze_mod

    monkeypatch.setattr(analyze_mod, "get_video_duration", lambda path: 15.04)


def test_uploaded_is_refused_when_no_object_was_uploaded(client, project):
    r = client.post(f"{API_PREFIX}/projects/{project.id}/uploaded")
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "precondition_failed"


def test_ingest_runs_through_genblaze_and_records_its_run_id(
        client, store, tmp_path, stub_ffprobe):
    """SPEC §8.2 and §11.3: `genblaze_run_id` was modelled but never populated.

    The sink is None here (the store is LocalBackend, not B2), which Genblaze
    supports: the run and the canonical-hashed manifest are still produced, only
    the artifact upload is skipped. That is exactly the offline posture, and it
    is why this test needs no credentials.
    """
    project = _uploadable_project(store, tmp_path)

    r = client.post(f"{API_PREFIX}/projects/{project.id}/uploaded")
    assert r.status_code == 202
    body = _await_run(client, project.id, r.json()["run_id"])

    assert body["status"] == "completed"
    assert body["genblaze_run_id"], "the Genblaze run id must reach the Run record"
    assert body["metadata"]["canonical_hash"], "a judge verifies this with `genblaze verify`"
    assert body["metadata"]["duration_s"] == 15.04


def test_ingest_reports_its_stage_in_the_job_history(client, store, tmp_path,
                                                     stub_ffprobe):
    """SPEC §8.2: run id, current stage, and stage-level outcome."""
    project = _uploadable_project(store, tmp_path)
    started = client.post(f"{API_PREFIX}/projects/{project.id}/uploaded").json()
    body = _await_run(client, project.id, started["run_id"])

    assert body["current_stage"] == "ingest"
    stage = body["stages"][0]
    assert stage["name"] == "ingest"
    assert stage["status"] == "succeeded"
    assert stage["provider"] == "genblaze"
    assert stage["genblaze_run_id"] == body["genblaze_run_id"]


def test_the_stage_history_is_also_on_the_run_listing(client, store, tmp_path,
                                                      stub_ffprobe):
    project = _uploadable_project(store, tmp_path)
    started = client.post(f"{API_PREFIX}/projects/{project.id}/uploaded").json()
    _await_run(client, project.id, started["run_id"])

    runs = client.get(f"{API_PREFIX}/projects/{project.id}/runs").json()["runs"]
    ingest = next(r for r in runs if r["id"] == started["run_id"])
    assert [s["name"] for s in ingest["stages"]] == ["ingest"]
    assert ingest["current_stage"] == "ingest"


def test_ingest_still_records_the_source_facts(client, store, tmp_path,
                                               stub_ffprobe):
    """A manifest is provenance; the duration is what every later stage's
    arithmetic depends on. Losing the former must not lose the latter."""
    project = _uploadable_project(store, tmp_path)
    started = client.post(f"{API_PREFIX}/projects/{project.id}/uploaded").json()
    _await_run(client, project.id, started["run_id"])

    body = client.get(f"{API_PREFIX}/projects/{project.id}").json()
    assert body["source_sha256"], "ffprobe/sha256 must survive the Genblaze stage"
    readiness = {i["key"]: i for i in body["readiness"]}
    assert readiness["source"]["state"] == "ok"
    assert store.get_state(project.id).duration_s == 15.04


def test_a_local_store_gets_no_b2_sink(store):
    """Never reach for credentials an offline deployment was not given."""
    from foleyflow.api.app import _ingest_sink

    assert _ingest_sink(store) is None


def test_attributions_rescue_unattributed_rows_into_the_budget(
        client, store, budget_ledger, monkeypatch):
    """POST /attributions adopts project-less rows; they show up in /budget."""
    import time

    project = store.create_project(title="A", input_key="k", source_sha256="",
                                   source_filename="a.mp4", budget_usd=2.0)
    # An unattributed row, written in the past (storage mode stamps now, so
    # write it directly with a controlled timestamp).
    old = time.time() - 3600
    store.backend.put_bytes(
        f"ledger/old.json",
        json.dumps({"endpoint": "gemini-2.5-flash", "params_hash": "u",
                    "cost_estimate": 0.30, "success": True, "timestamp": old,
                    "project_id": None}).encode())
    budget_ledger._invalidate_row_cache()

    before_rescue = client.get(
        f"{API_PREFIX}/projects/{project.id}/budget").json()
    assert before_rescue["spent"] == 0.0

    r = client.post(f"{API_PREFIX}/projects/{project.id}/attributions",
                    json={"before": old + 10})
    assert r.status_code == 201
    assert r.json()["claimed"] == 1

    after = client.get(f"{API_PREFIX}/projects/{project.id}/budget").json()
    assert after["spent"] == pytest.approx(0.30)


def test_attributions_404_for_an_unknown_project(client):
    r = client.post(f"{API_PREFIX}/projects/{VALID_ID}/attributions", json={})
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "project_not_found"


def test_attributions_reject_a_future_before(client, store, budget_ledger):
    """A `before` in the future would claim rows written after the rescue; the
    API refuses it rather than recording a time-bomb override."""
    import time
    project = store.create_project(title="A", input_key="k", source_sha256="",
                                   source_filename="a.mp4", budget_usd=1.0)
    r = client.post(f"{API_PREFIX}/projects/{project.id}/attributions",
                    json={"before": time.time() + 3600})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_request"


def test_unwritable_ledger_does_not_block_work(client, store, budget_ledger,
                                               monkeypatch):
    """Task 9d: an unwritable ledger means alerts are unreliable, not that
    work is refused. Repair still needs confirm_cost_usd consent."""
    import foleyflow.ledger as ledger_mod

    project = store.create_project(title="B", input_key="k", source_sha256="",
                                   source_filename="s.mp4", budget_usd=1.0)
    store.save_timeline(project.id, _timeline())
    monkeypatch.setenv("FOLEYFLOW_SFX_TIER", "paid")
    monkeypatch.setattr(ledger_mod, "ledger_writable", lambda *a, **k: False)

    r = client.post(
        f"{API_PREFIX}/projects/{project.id}/events/2/regenerate",
        json={"confirm_cost_usd": 0.30})
    assert r.status_code == 202, r.text

    body = client.get(f"{API_PREFIX}/projects/{project.id}/budget").json()
    assert body["ledger_writable"] is False


def test_budget_read_503s_on_a_ledger_outage(client, store, project,
                                             monkeypatch):
    """A ledger storage failure must read as 503 storage_unavailable, never a
    500 or a $0 balance."""
    import foleyflow.ledger as ledger_mod

    def boom(project_id):
        raise StorageError("ledger down")

    monkeypatch.setattr(ledger_mod, "project_spend", boom)
    r = client.get(f"{API_PREFIX}/projects/{project.id}/budget")
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "storage_unavailable"


# -- waveform peaks (unit 7.5) ----------------------------------------------

def _put_canonical_wav(backend, key, duration_s=0.05, amp=0.4):
    """Upload a short stereo sine so the peaks route has real audio to decode."""
    import numpy as np
    import soundfile as sf
    import tempfile

    n = int(round(duration_s * 48000))
    t = np.arange(n) / 48000.0
    tone = (amp * np.sin(2 * np.pi * 100 * t)).astype(np.float32)
    stereo = np.column_stack([tone, tone])
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        path = f.name
    try:
        sf.write(path, stereo, 48000, format="WAV", subtype="FLOAT")
        backend.put(key, path, content_type="audio/wav")
    finally:
        os.unlink(path)
    return key


def _seed_stem_inventory(store, project, stem_key):
    """Record a mix run whose stem inventory lists ``stem_key``."""
    version = store.save_timeline(project.id, _timeline())
    run = store.start_run(project.id, "mix")
    store.save_stem_inventory(project.id, run.id, [
        {"cache_key": "deadbeef", "b2_key": stem_key, "prompt": "gunshot"},
    ])
    mix_key = store.mix_key(project.id, run.id)
    video_key = store.mix_video_key(project.id, run.id)
    store.backend.put_bytes(video_key, b"MP4", content_type="video/mp4")
    store.record_mix(project.id, run_id=run.id,
                     mix_key=mix_key, video_key=video_key,
                     tier="full", timeline_version=version,
                     stem_count=1, generated_count=0)
    store.complete_run(run, timeline_version=version)
    return run


def test_stem_peaks_404_unknown_project(client):
    r = client.get(f"{API_PREFIX}/projects/{VALID_ID}/peaks/stem",
                   params={"key": "cache/abc.wav"})
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "project_not_found"


def test_stem_peaks_rejects_foreign_key(client, store, project):
    """A key not in any stem inventory for this project is refused."""
    r = client.get(f"{API_PREFIX}/projects/{project.id}/peaks/stem",
                   params={"key": "cache/not-ours.wav"})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_request"
    assert r.json()["error"]["detail"]["key"] == "cache/not-ours.wav"


def test_stem_peaks_404_when_audio_missing(client, store, project):
    """Authorised key, but the WAV was never uploaded → audio_not_found."""
    stem_key = "cache/ghost.wav"
    _seed_stem_inventory(store, project, stem_key)
    assert not store.backend.exists(stem_key)

    r = client.get(f"{API_PREFIX}/projects/{project.id}/peaks/stem",
                   params={"key": stem_key})
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "audio_not_found"


def test_stem_peaks_200_returns_envelope(client, store, project):
    stem_key = "cache/realstem.wav"
    _put_canonical_wav(store.backend, stem_key, duration_s=0.05, amp=0.4)
    _seed_stem_inventory(store, project, stem_key)

    r = client.get(f"{API_PREFIX}/projects/{project.id}/peaks/stem",
                   params={"key": stem_key})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["peaks_per_second"] == 100
    assert body["sample_rate"] == 48000
    assert body["channels"] == 2
    assert body["duration_s"] == pytest.approx(0.05, abs=1e-4)
    # length ≈ duration * pps
    assert len(body["min"]) == len(body["max"])
    assert len(body["max"]) == pytest.approx(0.05 * 100, abs=1)
    assert max(body["max"]) == pytest.approx(0.4, abs=0.05)
    # Peaks object cached beside the audio.
    assert store.backend.exists(stem_key + ".peaks.json")


def test_mix_peaks_404_when_no_mix(client, project):
    r = client.get(f"{API_PREFIX}/projects/{project.id}/peaks/mix")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "mix_not_found"


def test_mix_peaks_200_for_latest_mix(client, store, project):
    version = store.save_timeline(project.id, _timeline())
    run = store.start_run(project.id, "mix")
    mix_key = store.mix_key(project.id, run.id)
    _put_canonical_wav(store.backend, mix_key, duration_s=0.08, amp=0.55)
    video_key = store.mix_video_key(project.id, run.id)
    store.backend.put_bytes(video_key, b"MP4", content_type="video/mp4")
    store.record_mix(project.id, run_id=run.id,
                     mix_key=mix_key, video_key=video_key,
                     tier="full", timeline_version=version,
                     stem_count=0, generated_count=0)
    store.complete_run(run, timeline_version=version)

    r = client.get(f"{API_PREFIX}/projects/{project.id}/peaks/mix")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["peaks_per_second"] == 100
    assert len(body["max"]) == pytest.approx(0.08 * 100, abs=1)
    assert max(abs(v) for v in body["max"]) == pytest.approx(0.55, abs=0.05)

    # Explicit run_id works the same.
    r2 = client.get(f"{API_PREFIX}/projects/{project.id}/peaks/mix",
                    params={"run_id": run.id})
    assert r2.status_code == 200
    assert r2.json()["min"] == body["min"]


def test_mix_peaks_404_unknown_run(client, store, project):
    r = client.get(f"{API_PREFIX}/projects/{project.id}/peaks/mix",
                   params={"run_id": "run_01KYJD1RKETS3KTX5HA3H5JPDQ"})
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "run_not_found"


def test_mix_peaks_404_run_without_mix(client, store, project):
    """Run exists (started) but never produced a mix → mix_not_found, not run_not_found."""
    run = store.start_run(project.id, "mix")
    r = client.get(f"{API_PREFIX}/projects/{project.id}/peaks/mix",
                   params={"run_id": run.id})
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "mix_not_found"
    assert r.json()["error"]["detail"]["run_id"] == run.id


def test_mix_peaks_404_missing_wav(client, store, project):
    """Mix recorded in the log but mix.wav never uploaded."""
    version = store.save_timeline(project.id, _timeline())
    run = store.start_run(project.id, "mix")
    mix_key = store.mix_key(project.id, run.id)
    video_key = store.mix_video_key(project.id, run.id)
    store.backend.put_bytes(video_key, b"MP4", content_type="video/mp4")
    store.record_mix(project.id, run_id=run.id,
                     mix_key=mix_key, video_key=video_key,
                     tier="full", timeline_version=version)
    store.complete_run(run, timeline_version=version)
    assert not store.backend.exists(mix_key)

    r = client.get(f"{API_PREFIX}/projects/{project.id}/peaks/mix",
                   params={"run_id": run.id})
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "audio_not_found"


def test_stem_peaks_recomputes_corrupt_cache(client, store, project):
    """Corrupt peaks JSON beside a valid stem still yields 200 + valid envelope."""
    stem_key = "cache/recompute.wav"
    _put_canonical_wav(store.backend, stem_key, duration_s=0.05, amp=0.4)
    _seed_stem_inventory(store, project, stem_key)
    store.backend.put_bytes(
        stem_key + ".peaks.json", b"{not valid json", content_type="application/json")

    r = client.get(f"{API_PREFIX}/projects/{project.id}/peaks/stem",
                   params={"key": stem_key})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["peaks_per_second"] == 100
    assert len(body["min"]) == len(body["max"])
    assert len(body["max"]) == pytest.approx(0.05 * 100, abs=1)
    # Cache overwritten with valid peaks.
    stored = json.loads(
        store.backend.get_bytes(stem_key + ".peaks.json").decode("utf-8"))
    assert stored["min"] == body["min"]
    assert stored["max"] == body["max"]


def test_stem_peaks_400_invalid_wav(client, store, project):
    stem_key = "cache/garbage.wav"
    store.backend.put_bytes(stem_key, b"not a wav file", content_type="audio/wav")
    _seed_stem_inventory(store, project, stem_key)

    r = client.get(f"{API_PREFIX}/projects/{project.id}/peaks/stem",
                   params={"key": stem_key})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_request"


def test_batch_stem_peaks_returns_every_stem_for_the_run(client, store, project):
    """One request, one inventory read, one entry per stem (docs/api.md §3.12)."""
    good = _put_canonical_wav(store.backend, "cache/one.wav",
                              duration_s=0.05, amp=0.4)
    other = _put_canonical_wav(store.backend, "cache/two.wav",
                               duration_s=0.03, amp=0.2)
    version = store.save_timeline(project.id, _timeline())
    run = store.start_run(project.id, "mix")
    store.save_stem_inventory(project.id, run.id, [
        {"b2_key": good, "prompt": "gunshot"},
        {"b2_key": other, "prompt": "holster creak"},
        # Repeated address: several events share one stem, and the response is
        # keyed by address, so it must appear once.
        {"b2_key": good, "prompt": "gunshot"},
    ])
    store.backend.put_bytes(store.mix_video_key(project.id, run.id), b"MP4",
                            content_type="video/mp4")
    store.record_mix(project.id, run_id=run.id,
                     mix_key=store.mix_key(project.id, run.id),
                     video_key=store.mix_video_key(project.id, run.id),
                     tier="full", timeline_version=version,
                     stem_count=2, generated_count=0)
    store.complete_run(run, timeline_version=version)

    r = client.get(f"{API_PREFIX}/projects/{project.id}/peaks/stems")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["run_id"] == run.id
    assert set(body["stems"]) == {good, other}
    entry = body["stems"][good]
    assert entry["error"] is None
    assert entry["peaks"]["peaks_per_second"] == 100
    assert entry["peaks"]["channels"] == 2
    assert max(entry["peaks"]["max"]) == pytest.approx(0.4, abs=0.05)
    # Same envelope the per-key route serves — one cache, two doors.
    single = client.get(f"{API_PREFIX}/projects/{project.id}/peaks/stem",
                        params={"key": good})
    assert single.json() == entry["peaks"]

    # Explicit run_id selects the same run.
    r2 = client.get(f"{API_PREFIX}/projects/{project.id}/peaks/stems",
                    params={"run_id": run.id})
    assert r2.status_code == 200
    assert r2.json() == body


def test_batch_stem_peaks_reports_a_missing_stem_rather_than_skipping_it(
        client, store, project):
    """A hole in the bucket is evidence, not an omission (§A.3)."""
    good = _put_canonical_wav(store.backend, "cache/present.wav",
                              duration_s=0.04, amp=0.3)
    version = store.save_timeline(project.id, _timeline())
    run = store.start_run(project.id, "mix")
    store.save_stem_inventory(project.id, run.id, [
        {"b2_key": good},
        {"b2_key": "cache/vanished.wav"},
        {"b2_key": "cache/corrupt.wav"},
    ])
    store.backend.put_bytes("cache/corrupt.wav", b"not a wav at all",
                            content_type="audio/wav")
    store.backend.put_bytes(store.mix_video_key(project.id, run.id), b"MP4",
                            content_type="video/mp4")
    store.record_mix(project.id, run_id=run.id,
                     mix_key=store.mix_key(project.id, run.id),
                     video_key=store.mix_video_key(project.id, run.id),
                     tier="full", timeline_version=version,
                     stem_count=3, generated_count=0)
    store.complete_run(run, timeline_version=version)

    r = client.get(f"{API_PREFIX}/projects/{project.id}/peaks/stems")
    assert r.status_code == 200, r.text
    stems = r.json()["stems"]
    assert set(stems) == {good, "cache/vanished.wav", "cache/corrupt.wav"}
    assert stems[good]["peaks"] is not None
    assert stems["cache/vanished.wav"]["peaks"] is None
    assert stems["cache/vanished.wav"]["error"] == "audio_not_found"
    assert stems["cache/corrupt.wav"]["error"] == "invalid_request"
    assert stems["cache/corrupt.wav"]["message"]


def test_batch_stem_peaks_404s_like_the_mix_route(client, store, project):
    r = client.get(f"{API_PREFIX}/projects/{project.id}/peaks/stems")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "mix_not_found"

    r2 = client.get(f"{API_PREFIX}/projects/{VALID_ID}/peaks/stems")
    assert r2.status_code == 404
    assert r2.json()["error"]["code"] == "project_not_found"

    r3 = client.get(f"{API_PREFIX}/projects/{project.id}/peaks/stems",
                    params={"run_id": "nope"})
    assert r3.status_code == 400
    assert r3.json()["error"]["code"] == "invalid_id"


# -- commit rules the freeze mechanism depends on (findings 1b, 2, 5) --------

def test_timeline_commit_allows_a_first_freeze_at_the_routed_duration(
        client, store, project):
    """The editor may record an address that already exists.

    Without this the whole 7.2 mechanism is unreachable from the API: nothing a
    client can send ever produces a freeze, so resize-is-free never engages.
    The only legal value is the one the event already routes to.
    """
    from foleyflow.models import Event, Timeline
    from foleyflow.routing import route_event

    event = Event(name="gunshot", start_time=1.0, duration=1.0,
                  intensity="high", kind="discrete")
    store.save_timeline(project.id, Timeline(duration=15.04, ambience="wind",
                                             events=[event]))
    legit = route_event(event).duration_s

    body = _timeline_commit_body(Timeline(duration=15.04, ambience="wind",
                                          events=[event]))
    body["events"][0]["source_duration_s"] = legit
    r = client.put(f"{API_PREFIX}/projects/{project.id}/timeline", json=body)
    assert r.status_code == 200, r.text
    assert r.json()["events"][0]["source_duration_s"] == pytest.approx(legit)

    # And any other value is still an invented address.
    body["events"][0]["source_duration_s"] = legit + 1.0
    r2 = client.put(f"{API_PREFIX}/projects/{project.id}/timeline", json=body)
    assert r2.status_code == 400
    assert r2.json()["error"]["code"] == "invalid_request"


def test_timeline_commit_allows_kind_change_on_a_frozen_event(
        client, store, project):
    """kind is not in the freeze identity: changing it moves no cache address.

    With source_duration_s set, route_event takes the duration from the freeze
    regardless of kind, and endpoint/prompt/variant do not depend on kind — so
    a kind change bills nothing and must not fail conservation.
    """
    from foleyflow.models import Event, Timeline
    from foleyflow.routing import route_event

    t = Timeline(duration=15.04, ambience="wind", events=[
        Event(name="gunshot", start_time=1.0, duration=1.0,
              intensity="high", kind="discrete", source_duration_s=1.5),
    ])
    store.save_timeline(project.id, t)
    body = _timeline_commit_body(t)
    body["events"][0]["kind"] = "continuous"

    r = client.put(f"{API_PREFIX}/projects/{project.id}/timeline", json=body)
    assert r.status_code == 200, r.text
    assert r.json()["events"][0]["kind"] == "continuous"

    # The claim the rule rests on: same request either way.
    before = route_event(t.events[0])
    after = route_event(Event(name="gunshot", start_time=1.0, duration=1.0,
                              intensity="high", kind="continuous",
                              source_duration_s=1.5))
    assert (before.endpoint, before.prompt, before.duration_s) == \
        (after.endpoint, after.prompt, after.duration_s)


def test_timeline_commit_still_rejects_rename_of_a_frozen_event(
        client, store, project):
    """name feeds the prompt, so renaming is a different generation."""
    from foleyflow.models import Event, Timeline

    t = Timeline(duration=15.04, ambience="wind", events=[
        Event(name="gunshot", start_time=1.0, duration=1.0,
              intensity="high", kind="discrete", source_duration_s=1.5),
    ])
    store.save_timeline(project.id, t)
    body = _timeline_commit_body(t)
    body["events"][0]["name"] = "shotgun blast"

    r = client.put(f"{API_PREFIX}/projects/{project.id}/timeline", json=body)
    assert r.status_code == 400
    assert store.list_timeline_versions(project.id) == [1]


def test_timeline_commit_ignores_the_bodys_duration(client, store, project):
    """duration is display-only with ffprobe as sole source of truth."""
    store.save_timeline(project.id, _timeline())
    body = _timeline_commit_body()
    body["duration"] = 999.0

    r = client.put(f"{API_PREFIX}/projects/{project.id}/timeline", json=body)
    assert r.status_code == 200, r.text
    assert r.json()["duration"] == pytest.approx(15.04)
    assert store.load_timeline(project.id, 2).duration == pytest.approx(15.04)


def test_timeline_commit_reads_the_event_log_once(client, store, project,
                                                  monkeypatch):
    """Finding 3: one log read per commit, not three.

    `list_events` is one object GET per event, so folding the log for the 404
    check, the run lock and the live-run scan separately cost hundreds of B2
    round trips on a busy project.
    """
    store.save_timeline(project.id, _timeline())
    real = ProjectStore.list_events
    calls = []

    def _counted(self, project_id):
        calls.append(project_id)
        return real(self, project_id)

    monkeypatch.setattr(ProjectStore, "list_events", _counted)
    r = client.put(f"{API_PREFIX}/projects/{project.id}/timeline",
                   json=_timeline_commit_body())
    assert r.status_code == 200, r.text
    assert calls.count(project.id) == 1, calls


def test_create_app_without_a_store_does_not_reach_a_real_backend(monkeypatch):
    """The last hole in the suite's offline isolation.

    `create_app()` used to build its store from a `get_backend` bound at import
    time, before conftest could patch it — so the default path could reach live
    B2. The lookup is late now, and this asserts it by patching the module the
    call goes through.
    """
    import foleyflow.storage as storage_pkg

    seen = []
    sentinel = object()

    def _spy(kind=None):
        seen.append(kind)
        return sentinel

    monkeypatch.setattr(storage_pkg, "get_backend", _spy)
    app = create_app()
    assert seen == [None]
    assert app is not None


def test_budget_reports_an_unwritable_ledger(client, store, project):
    """Unit 7.8's warning chip needs the false branch to actually flow through.

    conftest pins `ledger._write_ok = True` suite-wide, so without this the
    false branch is never exercised end to end: a budget payload that always
    claims the ledger is writable would hide exactly the condition the chip
    exists to surface.
    """
    import foleyflow.ledger as ledger

    r = client.get(f"{API_PREFIX}/projects/{project.id}/budget")
    assert r.status_code == 200, r.text
    assert r.json()["ledger_writable"] is True

    ledger._write_ok = False
    try:
        r2 = client.get(f"{API_PREFIX}/projects/{project.id}/budget")
        assert r2.status_code == 200, r2.text
        assert r2.json()["ledger_writable"] is False
        # The rest of the payload still answers — an unwritable ledger degrades
        # the spend figure's authority, it does not fail the request.
        assert r2.json()["spent"] == 0.0
    finally:
        ledger._write_ok = True
