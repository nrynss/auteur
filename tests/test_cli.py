"""Tests for the `foleyflow` CLI.

Driven against an in-process ASGI app by injecting Starlette's TestClient —
itself an httpx.Client — so the CLI runs its real request, error-unwrapping and
polling code with no server, no network, and no credentials. That matters: the
CLI exists to prove the contract is usable by something other than the
dashboard, and a test that stubbed the transport out would prove nothing.

(httpx's own ASGITransport is async-only, so it cannot back a sync client.)
"""

import json

import pytest
from fastapi.testclient import TestClient

from foleyflow.api.app import create_app
from foleyflow.api.jobs import JobRunner
from foleyflow.cli import EXIT_API_ERROR, EXIT_OK, EXIT_QUOTA, Client, main
from foleyflow.projects import ProjectStore
from foleyflow.storage import LocalBackend


@pytest.fixture
def store(tmp_path):
    return ProjectStore(LocalBackend(str(tmp_path / "b2")), work_dir=str(tmp_path / "w"))


@pytest.fixture
def client(store):
    runner = JobRunner(store, max_workers=2)
    app = create_app(store, runner)
    with TestClient(app, base_url="http://testserver",
                    raise_server_exceptions=False) as http:
        yield Client("http://testserver", http=http)
    runner.shutdown(wait=True)


@pytest.fixture
def project(store):
    return store.create_project(title="Western standoff", input_key="k",
                                source_sha256="a" * 64,
                                source_filename="standoff.mp4", duration_s=15.04)


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


def run(client, *argv):
    return main(list(argv), client=client)


# -- basics ------------------------------------------------------------------

def test_health(client, capsys):
    assert run(client, "health") == EXIT_OK
    assert "storage=ok" in capsys.readouterr().out


def test_quota_reports_estimates(client, capsys):
    assert run(client, "quota") == EXIT_OK
    out = capsys.readouterr().out
    assert "gemini" in out
    assert "estimate" in out.lower(), "the CLI must not present these as facts"


def test_list_is_empty_without_projects(client, capsys):
    assert run(client, "list") == EXIT_OK
    assert "no projects" in capsys.readouterr().out


def test_list_shows_a_project(client, project, capsys):
    assert run(client, "list") == EXIT_OK
    out = capsys.readouterr().out
    assert project.id in out and "Western standoff" in out


# -- json mode ---------------------------------------------------------------

@pytest.mark.parametrize("argv", [("health",), ("quota",), ("list",)])
def test_json_mode_emits_only_parseable_json(client, capsys, argv):
    """A script must never have to strip human formatting out of --json."""
    assert run(client, "--json", *argv) == EXIT_OK
    json.loads(capsys.readouterr().out)


def test_json_show(client, project, capsys):
    assert run(client, "--json", "show", project.id) == EXIT_OK
    body = json.loads(capsys.readouterr().out)
    assert body["id"] == project.id
    assert isinstance(body["readiness"], list)


# -- project detail ----------------------------------------------------------

def test_show_renders_the_readiness_checklist(client, project, capsys):
    assert run(client, "show", project.id) == EXIT_OK
    out = capsys.readouterr().out
    assert "readiness" in out.lower()
    assert "STOP" in out, "unmet blocking checks must be visible"
    assert "export blocked" in out


def test_timeline_lists_events_by_index(client, store, project, capsys):
    """Repair addresses events by index because names repeat — the CLI has to
    show the handle a user would actually pass."""
    store.save_timeline(project.id, _timeline())
    assert run(client, "timeline", project.id) == EXIT_OK
    out = capsys.readouterr().out
    assert "holster creak" in out
    assert out.count("holster creak") == 2, "both duplicates must be listed"
    for index in ("0", "1", "2"):
        assert index in out


def test_scenes_before_analysis_says_so(client, project, capsys):
    assert run(client, "scenes", project.id) == EXIT_OK
    assert "run analysis first" in capsys.readouterr().out


def test_qa_renders_continuity_warnings(client, store, project, capsys):
    from foleyflow.qa import build_report
    from foleyflow.scenes import Scene, SceneStats

    store.save_qa_report(project.id, build_report([
        Scene(id="scene_01", index=1, start_s=0.0, end_s=1.0,
              stats=SceneStats(mean_luma=40.0, mean_rgb=[40, 40, 40],
                               luma_stddev=1.0, sample_count=4)),
        Scene(id="scene_02", index=2, start_s=1.0, end_s=2.0,
              stats=SceneStats(mean_luma=90.0, mean_rgb=[90, 90, 90],
                               luma_stddev=1.0, sample_count=4)),
    ]))
    assert run(client, "qa", project.id) == EXIT_OK
    out = capsys.readouterr().out
    assert "needs_review" in out
    assert "exposure_discontinuity" in out
    assert "not measured" in out


def test_events_shows_the_audit_trail(client, store, project, capsys):
    store.save_timeline(project.id, _timeline())
    assert run(client, "events", project.id) == EXIT_OK
    out = capsys.readouterr().out
    assert "project.created" in out
    assert "foley.plan.proposed" in out


# -- error handling ----------------------------------------------------------

def test_unknown_project_reports_the_api_error_code(client, capsys):
    code = run(client, "show", "proj_01KYJD1RKETS3KTX5HA3H5JPDQ")
    assert code == EXIT_API_ERROR
    assert "project_not_found" in capsys.readouterr().err


def test_malformed_id_is_reported_not_crashed(client, capsys):
    assert run(client, "show", "nonsense") == EXIT_API_ERROR
    assert "invalid_id" in capsys.readouterr().err


def test_quota_exhaustion_has_its_own_exit_code(client, project, capsys, monkeypatch):
    """A scheduled job needs to distinguish "out of budget until tomorrow" from
    a transient failure, so it can back off instead of retrying into a wall."""
    from foleyflow.quota import ModelQuota, QuotaWindow
    import foleyflow.quota as quota_mod

    monkeypatch.setattr(quota_mod, "get_quota", lambda m, **k: ModelQuota(
        model_id=m, per_minute=QuotaWindow(0, 5), per_day=QuotaWindow(20, 20),
        resets_at="2026-07-29T00:00:00-07:00"))

    assert run(client, "analyze", project.id) == EXIT_QUOTA
    err = capsys.readouterr().err
    assert "resets at 2026-07-29" in err


def test_unreachable_api_exits_with_a_usable_message(capsys):
    """The default base URL points at localhost; a stopped server must produce
    an instruction, not a traceback."""
    unreachable = Client("http://127.0.0.1:1")
    with pytest.raises(SystemExit) as excinfo:
        main(["health"], client=unreachable)
    assert excinfo.value.code == 2
    assert "cannot reach the API" in capsys.readouterr().err


# -- the 202 contract --------------------------------------------------------

def test_analyze_without_wait_returns_a_pollable_run(client, store, project,
                                                     capsys, monkeypatch):
    import foleyflow.api.app as app_mod
    monkeypatch.setattr(app_mod, "_analysis_work",
                        lambda *a, **k: (lambda run: store.complete_run(run)))

    assert run(client, "analyze", project.id) == EXIT_OK
    out = capsys.readouterr().out
    assert "analysis run run_" in out
    assert "foleyflow run" in out, "it must tell the user how to poll"


def test_analyze_with_wait_blocks_until_terminal(client, store, project,
                                                 capsys, monkeypatch):
    import foleyflow.api.app as app_mod
    monkeypatch.setattr(app_mod, "_analysis_work",
                        lambda *a, **k: (lambda run: store.complete_run(
                            run, timeline_version=1)))

    assert run(client, "analyze", project.id, "--wait") == EXIT_OK
    assert "completed" in capsys.readouterr().out


def test_a_failed_run_is_a_nonzero_exit(client, store, project, capsys, monkeypatch):
    import foleyflow.api.app as app_mod
    monkeypatch.setattr(app_mod, "_analysis_work",
                        lambda *a, **k: (lambda run: 1 / 0))

    assert run(client, "analyze", project.id, "--wait") == EXIT_API_ERROR
    assert "failed" in capsys.readouterr().err


# -- mix and export ----------------------------------------------------------

def _stub_mix_work(store, monkeypatch, *, tier="full", version=1):
    import foleyflow.api.app as app_mod

    def _work(_store, project_id, _tier, _version):
        def run_it(job):
            store.record_mix(project_id, run_id=job.id,
                             mix_key=store.mix_key(project_id, job.id),
                             video_key=store.mix_video_key(project_id, job.id),
                             tier=_tier, timeline_version=_version,
                             stem_count=4, generated_count=1)
            store.complete_run(job, metadata={
                "tier": _tier, "stem_count": 4, "generated_count": 1,
                "ledger_delta_usd": 0.02, "qa_status": "ok"})
        return run_it

    monkeypatch.setattr(app_mod, "_mix_work", _work)


def test_mix_reports_spend_and_qa(client, store, project, capsys, monkeypatch):
    """The cost of the run is the number the user is deciding on."""
    store.save_timeline(project.id, _timeline())
    _stub_mix_work(store, monkeypatch)

    assert run(client, "mix", project.id, "--wait") == EXIT_OK
    out = capsys.readouterr().out
    assert "1 of 4 stems generated" in out
    assert "$0.0200" in out
    assert "QA: ok" in out
    assert "foleyflow export" in out, "it must name the next step"


def test_mix_without_a_timeline_exits_nonzero(client, project, capsys):
    assert run(client, "mix", project.id) == EXIT_API_ERROR
    assert "timeline" in capsys.readouterr().err.lower()


def test_export_is_blocked_until_a_mix_exists(client, store, project, capsys):
    store.save_timeline(project.id, _timeline())
    assert run(client, "export", project.id) == EXIT_API_ERROR
    assert "mix" in capsys.readouterr().err.lower()


def test_export_publishes_and_show_reports_ready(client, store, project, capsys,
                                                 monkeypatch):
    store.save_timeline(project.id, _timeline())
    _stub_mix_work(store, monkeypatch)
    assert run(client, "mix", project.id, "--wait") == EXIT_OK
    capsys.readouterr()

    assert run(client, "export", project.id, "--wait") == EXIT_OK
    assert "manifest" in capsys.readouterr().out

    assert run(client, "--json", "show", project.id) == EXIT_OK
    body = json.loads(capsys.readouterr().out)
    assert body["status"] == "ready"
    readiness = {i["key"]: i["state"] for i in body["readiness"]}
    assert readiness["mix"] == "ok"
    assert readiness["export"] == "ok"


def test_create_rejects_a_missing_file(client, capsys):
    assert run(client, "create", "does/not/exist.mp4") == 2
    assert "no such file" in capsys.readouterr().err


# -- file verbs (render / direct; no HTTP) -----------------------------------

def test_render_help_works(capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["render", "--help"])
    assert excinfo.value.code == 0
    assert "clip" in capsys.readouterr().out.lower()


def test_render_does_not_touch_http(monkeypatch, capsys):
    """File verbs must not construct a Client or hit the API."""
    from foleyflow import cli as foley_cli
    from foleyflow.pipeline import FoleyResult

    called = {}

    def fake_run(clip_path, **kwargs):
        called["clip_path"] = clip_path
        return FoleyResult(out_path="x", timeline_path="y", mix_path="z")

    def boom_client(*a, **k):
        raise AssertionError("Client must not be constructed for render")

    monkeypatch.setattr(foley_cli, "run", fake_run)
    monkeypatch.setattr("foleyflow.cli.Client", boom_client)

    assert main(["render", "clip.mp4", "--tier", "ambience"], client=None) == 0
    assert called["clip_path"] == "clip.mp4"


def test_render_missing_clip_exits_nonzero(monkeypatch, capsys):
    from foleyflow import cli as foley_cli

    def failing_run(clip_path, **kwargs):
        raise FileNotFoundError(f"no such file: {clip_path}")

    monkeypatch.setattr(foley_cli, "run", failing_run)
    assert main(["render", "does/not/exist.mp4"], client=None) == 1
    assert "foleyflow render:" in capsys.readouterr().err


def test_render_unknown_flag_exits_nonzero():
    with pytest.raises(SystemExit) as excinfo:
        main(["render", "clip.mp4", "--not-a-real-flag"])
    assert excinfo.value.code != 0
