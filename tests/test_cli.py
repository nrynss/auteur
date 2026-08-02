"""Tests for the `foleyflow` CLI.

Driven against an in-process ASGI app by injecting Starlette's TestClient —
itself an httpx.Client — so the CLI runs its real request, error-unwrapping and
polling code with no server, no network, and no credentials. That matters: the
CLI exists to prove the contract is usable by something other than the
dashboard, and a test that stubbed the transport out would prove nothing.

(httpx's own ASGITransport is async-only, so it cannot back a sync client.)
"""

import json
import os

import pytest
from fastapi.testclient import TestClient

from foleyflow.api.app import create_app
from foleyflow.api.jobs import JobRunner
from foleyflow.cli import (
    EXIT_API_ERROR,
    EXIT_OK,
    EXIT_QUOTA,
    EXIT_USAGE,
    Client,
    main,
)
from foleyflow.projects import ProjectStore
from foleyflow.storage import LocalBackend


@pytest.fixture
def store(tmp_path):
    return ProjectStore(LocalBackend(str(tmp_path / "b2")), work_dir=str(tmp_path / "w"))


@pytest.fixture(autouse=True)
def stub_worker_probe(monkeypatch):
    """`foleyflow health` always asks the API to probe the SFX worker.

    Autouse, and deliberately blunt: without it this module's "no network"
    promise quietly becomes false the moment a test touches health, and it
    would pass anyway on a machine that happens to have the worker running.
    Individual tests override it by patching `get_adapter` again.

    Also clears `$FOLEYFLOW_SFX_TIER`: with `=paid` the probe short-circuits
    to `not_applicable` and never reaches the stub, which is correct product
    behaviour but not what these tests are measuring.
    """
    import foleyflow.providers as providers

    monkeypatch.delenv("FOLEYFLOW_SFX_TIER", raising=False)

    class Stub:
        def health(self):
            return {"status": "ok", "model": "openmoss/moss-soundeffect-v2",
                    "device": "stub-gpu", "auth_required": False}

    monkeypatch.setattr(providers, "get_adapter", lambda name: Stub())


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
    out = capsys.readouterr().out
    assert "storage=ok" in out
    assert "worker  ok" in out
    assert "stub-gpu" in out


def test_health_warns_when_the_worker_wants_a_token_we_lack(client, capsys, monkeypatch):
    """Healthy-looking and unable to generate a single stem."""
    import foleyflow.providers as providers

    class Stub:
        def health(self):
            return {"status": "ok", "auth_required": True,
                    "auth_configured": False}

    monkeypatch.setattr(providers, "get_adapter", lambda name: Stub())
    assert run(client, "health") == EXIT_OK
    captured = capsys.readouterr()
    assert "FOLEYFLOW_SFX_WORKER_TOKEN" in captured.err


def test_health_warns_but_succeeds_when_the_worker_is_down(client, capsys, monkeypatch):
    """Exit 0: cached renders still work, so this is not a failed health check."""
    import foleyflow.providers as providers

    class Stub:
        def health(self):
            raise OSError("connection refused")

    monkeypatch.setattr(providers, "get_adapter", lambda name: Stub())
    assert run(client, "health") == EXIT_OK
    captured = capsys.readouterr()
    assert "worker  unreachable" in captured.out
    assert "connection refused" in captured.err


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


def test_dossier_lists_artifacts_with_signed_urls(client, store, project, capsys):
    """The CLI surfaces the §15 closing-beat call: every artifact, sizes, and
    the signed URLs rather than the raw keys a client could misuse."""
    store.save_timeline(project.id, _timeline())
    assert run(client, "dossier", project.id) == EXIT_OK
    out = capsys.readouterr().out
    assert "artifacts" in out
    assert "Signed URLs" in out
    assert "timeline.v1.json" in out
    assert "file:///" in out


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


def test_regenerate_waits_and_reports_the_repaired_event(
        client, store, project, capsys, monkeypatch):
    """The repair verb picks the event by index and polls the run."""
    import foleyflow.api.app as app_mod

    store.save_timeline(project.id, _timeline())

    def _work(_store, _pid, version, event_index, request, note, provider):
        def run_it(job):
            store.complete_run(job, timeline_version=version + 1,
                               metadata={"event_index": event_index,
                                         "event_name": request.prompt,
                                         "variant": "rX",
                                         "ledger_delta_usd": 0.0})
        return run_it

    monkeypatch.setattr(app_mod, "_repair_work", _work)

    assert run(client, "regenerate", project.id, "2", "--wait") == EXIT_OK
    out = capsys.readouterr().out
    assert "event 2" in out
    assert "regenerated" in out


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


# -- prewarm -----------------------------------------------------------------

@pytest.fixture
def timeline_file(tmp_path):
    from foleyflow.timeline_io import save_timeline

    path = tmp_path / "tl.json"
    save_timeline(_timeline(), str(path))
    return str(path)


@pytest.fixture
def prewarm_backend(monkeypatch, tmp_path):
    """A LocalBackend standing in for B2, plus a recording `prewarm`.

    Patched at the module the command imports from, because `cmd_prewarm`
    imports inside the function body — the point is to keep `fal_client` and
    boto3 off the import path of a CLI that mostly does not need them.
    """
    import foleyflow.sfx_cache as sfx_cache
    import foleyflow.storage as storage
    from foleyflow.storage import LocalBackend

    backend = LocalBackend(str(tmp_path / "cache"))
    monkeypatch.setattr(storage, "get_backend", lambda *a, **k: backend)

    calls = []

    class Result:
        def __init__(self, request):
            self.request = request
            self.ok = True
            self.error = None

    def _prewarm(be, work, requests, **kw):
        calls.append(list(requests))
        return [Result(r) for r in requests]

    monkeypatch.setattr(sfx_cache, "prewarm", _prewarm)
    return backend, calls


def test_prewarm_dry_run_generates_nothing(client, capsys, timeline_file,
                                           prewarm_backend):
    _, calls = prewarm_backend
    assert run(client, "prewarm", timeline_file, "--dry-run") == EXIT_OK
    out = capsys.readouterr().out
    assert "to generate" in out
    assert "--dry-run" in out
    assert calls == [], "dry-run must not generate"


def test_prewarm_generates_only_the_misses(client, capsys, timeline_file,
                                           prewarm_backend):
    """A warm stem must not be regenerated — that is the whole point."""
    from foleyflow.assemble import plan_requests
    from foleyflow.sfx_cache import cache_object_key
    from foleyflow.timeline_io import load_timeline

    backend, calls = prewarm_backend
    timeline = load_timeline(timeline_file)
    planned = plan_requests(timeline, timeline.duration)
    assert len(planned) > 1, "need at least two stems for this to mean anything"

    # Seed exactly one stem into the cache.
    warm = planned[0]
    key = cache_object_key(warm.endpoint, warm.prompt, warm.duration_s, "")
    seed = backend.local_path(key) if hasattr(backend, "local_path") else None
    import os
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        f.write(b"RIFF0000WAVE")
        tmp_wav = f.name
    backend.put(key, tmp_wav, content_type="audio/wav")
    os.unlink(tmp_wav)

    assert run(client, "prewarm", timeline_file) == EXIT_OK
    out = capsys.readouterr().out
    assert f"{len(planned)} stems planned: 1 cached" in out

    assert len(calls) == 1
    requested = {r.prompt for r in calls[0]}
    assert warm.prompt not in requested
    assert len(calls[0]) == len(planned) - 1


def test_prewarm_reports_failures_and_exits_nonzero(client, capsys, timeline_file,
                                                    monkeypatch, prewarm_backend):
    import foleyflow.sfx_cache as sfx_cache

    class Failed:
        def __init__(self, request):
            self.request = request
            self.ok = False
            self.error = "worker unreachable"

    monkeypatch.setattr(sfx_cache, "prewarm",
                        lambda be, work, reqs, **kw: [Failed(r) for r in reqs])
    assert run(client, "prewarm", timeline_file) == EXIT_API_ERROR
    assert "worker unreachable" in capsys.readouterr().err


def test_prewarm_sfx_tier_selects_a_different_endpoint(client, capsys,
                                                       timeline_file,
                                                       prewarm_backend, monkeypatch):
    """`--sfx-tier` must apply before planning, or it warms the wrong keys.

    Also asserts the env override is restored: a leftover FOLEYFLOW_SFX_TIER
    would silently re-route every subsequent test onto the paid endpoint.
    """
    monkeypatch.delenv("FOLEYFLOW_SFX_TIER", raising=False)

    run(client, "prewarm", timeline_file, "--sfx-tier", "test", "--dry-run")
    test_out = capsys.readouterr().out
    assert "FOLEYFLOW_SFX_TIER" not in os.environ

    run(client, "prewarm", timeline_file, "--sfx-tier", "paid", "--dry-run")
    paid_out = capsys.readouterr().out
    assert "FOLEYFLOW_SFX_TIER" not in os.environ

    assert "tier test" in test_out and "moss-soundeffect" in test_out
    assert "tier paid" in paid_out
    assert "moss-soundeffect" not in paid_out.splitlines()[0]


def test_prewarm_rejects_an_unreadable_timeline(client, capsys, tmp_path,
                                                prewarm_backend):
    missing = str(tmp_path / "nope.json")
    assert run(client, "prewarm", missing) == EXIT_USAGE
    assert "cannot read timeline" in capsys.readouterr().err


def test_prewarm_rejects_an_unknown_sfx_tier(client, capsys, timeline_file,
                                             prewarm_backend):
    """A typo in --sfx-tier is a usage error, not an API failure."""
    assert run(client, "prewarm", timeline_file,
               "--sfx-tier", "does-not-exist", "--dry-run") == EXIT_USAGE
    err = capsys.readouterr().err
    assert "does-not-exist" in err
    assert "FOLEYFLOW_SFX_TIER" in err or "not a declared tier" in err


# -- current project (CLI-level context) ------------------------------------

def test_use_then_budget_without_an_id(client, store, project, capsys,
                                       monkeypatch, tmp_path):
    """`foleyflow use` persists a current project; the budget verb then works
    with no id, exactly the CLI-level project context the product wants."""
    monkeypatch.setenv("HOME", str(tmp_path))

    assert run(client, "use", project.id) == EXIT_OK
    assert project.id in capsys.readouterr().out

    assert run(client, "budget") == EXIT_OK
    out = capsys.readouterr().out
    assert project.id in out
    assert "(none)" in out            # fixture project has no budget_usd
    assert "spent $0.0000" in out


def test_use_rejects_a_malformed_id(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert run(None, "use", "nonsense") == EXIT_USAGE
    assert "not a valid project id" in capsys.readouterr().err


def test_project_flag_resolves_the_current_project(client, store, project,
                                                   capsys):
    # argparse subparsers don't accept top-level flags after the verb, so the
    # flag precedes it: `foleyflow --project <id> show`.
    assert run(client, "--project", project.id, "show") == EXIT_OK
    assert project.id in capsys.readouterr().out


def test_env_project_resolves_the_current_project(client, store, project,
                                                  capsys, monkeypatch):
    monkeypatch.setenv("FOLEYFLOW_PROJECT", project.id)
    assert run(client, "show") == EXIT_OK
    assert project.id in capsys.readouterr().out


def test_missing_project_is_a_usage_error(client, capsys, monkeypatch,
                                          tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("FOLEYFLOW_PROJECT", raising=False)
    assert run(client, "show") == EXIT_USAGE
    err = capsys.readouterr().err
    assert "no project given" in err
    assert "FOLEYFLOW_PROJECT" in err


def test_attribute_adopts_unattributed_rows(client, store, project, capsys,
                                            monkeypatch):
    import json
    import time

    import foleyflow.ledger as ledger_mod
    import foleyflow.storage as storage_mod

    monkeypatch.setattr(storage_mod, "get_backend", lambda *a, **k: store.backend)
    old = time.time() - 3600
    store.backend.put_bytes(
        "ledger/old.json",
        json.dumps({"endpoint": "gemini-2.5-flash", "params_hash": "u",
                    "cost_estimate": 0.30, "success": True,
                    "timestamp": old, "project_id": None}).encode())
    ledger_mod._invalidate_row_cache()

    assert run(client, "attribute", project.id,
               "--before", str(int(old + 10))) == EXIT_OK
    out = capsys.readouterr().out
    assert "adopted 1" in out
    assert project.id in out


def test_render_project_tags_its_ledger_rows(monkeypatch, tmp_path):
    """`foleyflow render --project <id>` attributes the render's rows; without
    it, rows stay unattributed (project-less work is never forced into one)."""
    import argparse

    import foleyflow.cli as cli_mod
    import foleyflow.ledger as ledger_mod

    seen = {}

    def fake_run(*args, **kwargs):
        seen["project"] = ledger_mod._current_project.get()

    monkeypatch.setattr(cli_mod, "run", fake_run)
    monkeypatch.setattr(cli_mod, "load_dotenv", lambda *a, **k: None)
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"x")
    base = {"clip": str(clip), "out": None, "tier": "full", "model": "m",
            "from_timeline": None, "timeline_out": None, "cache_dir": "c"}

    rc = cli_mod.run_render(argparse.Namespace(**base, project="proj_X"))
    assert rc == 0
    assert seen["project"] == "proj_X"

    rc = cli_mod.run_render(argparse.Namespace(**base, project=None))
    assert rc == 0
    assert seen["project"] is None


def test_global_project_flag_survives_into_render():
    """`foleyflow --project X render …` must keep X — a subparser default must
    not clobber the top-level flag and silently drop the attribution."""
    from foleyflow.cli import build_parser

    args = build_parser().parse_args(
        ["--project", "proj_01K", "render", "clip.mp4"])
    assert args.project == "proj_01K"
    assert args.clip == "clip.mp4"
