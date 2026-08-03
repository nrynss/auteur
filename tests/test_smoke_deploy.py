"""Offline tests for scripts/smoke_deploy.py.

Uses httpx.MockTransport so nothing hits the network. Covers the post-deploy
acceptance cases: all pass, wrong SHA, ledger_writable false, storage not ok.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "smoke_deploy.py"


def _load_smoke():
    """Load scripts/smoke_deploy.py as a module (not on PYTHONPATH as a package)."""
    name = "smoke_deploy"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


smoke = _load_smoke()

FULL_SHA = "deadbeefcafebabe0123456789abcdef01234567"
SHORT_SHA = "deadbee"
BASE = "https://example.test"


def _health(
    *,
    status: str = "ok",
    storage: str = "ok",
    git_sha=FULL_SHA,
    worker=None,
):
    if worker is None:
        worker = {
            "status": "ok",
            "tier": "test",
            "endpoint": "openmoss/moss-soundeffect-v2",
            "model": "openmoss/moss-soundeffect-v2",
            "device": "stub-gpu",
            "auth_required": False,
            "auth_configured": True,
            "detail": None,
        }
    return {
        "status": status,
        "storage": storage,
        "version": "0.1.0",
        "git_sha": git_sha,
        "build_time": "2026-08-02T12:00:00Z",
        "worker": worker,
    }


def _quota(*, ledger_writable: bool = True):
    return {
        "estimate": True,
        "ledger_writable": ledger_writable,
        "models": [],
    }


def _projects(ids=None):
    ids = ids if ids is not None else ["proj_01KTEST000000000000000000"]
    return {
        "projects": [{"id": i, "title": "demo"} for i in ids],
        "next_cursor": None,
    }


def _dossier(project_id: str):
    return {
        "project_id": project_id,
        "artifact_count": 1,
        "total_bytes": 100,
        "artifacts": [
            {"kind": "source", "key": "k", "url": "https://signed", "size_bytes": 100},
        ],
        "budget": {"ledger_writable": True, "estimate": True},
    }


def _openapi():
    return {"openapi": "3.1.0", "paths": {"/api/v1/health": {}}}


def _router(overrides: dict | None = None):
    """Map path (+ optional query key) -> (status, body).

    A str body is served as text/html — used for GET / (the built SPA),
    which `check_spa` probes.
    """
    table = {
        "/api/v1/health": (200, _health()),
        "/api/v1/quota": (200, _quota()),
        "/api/v1/projects": (200, _projects()),
        "/api/v1/openapi.json": (200, _openapi()),
        "/": (200, '<!doctype html><div id="app"></div>Auteur control room'),
    }
    pid = "proj_01KTEST000000000000000000"
    table[f"/api/v1/projects/{pid}/dossier"] = (200, _dossier(pid))
    if overrides:
        table.update(overrides)

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        # Normalize trailing slash
        if path != "/" and path.endswith("/"):
            path = path.rstrip("/")
        if path not in table:
            return httpx.Response(404, json={"error": {"code": "not_found"}})
        code, body = table[path]
        if body is None:
            return httpx.Response(code, content=b"")
        if isinstance(body, str):
            return httpx.Response(
                code, text=body, headers={"content-type": "text/html"}
            )
        if isinstance(body, bytes):
            return httpx.Response(code, content=body)
        return httpx.Response(code, json=body)

    return handler


def _client(overrides=None) -> httpx.Client:
    transport = httpx.MockTransport(_router(overrides))
    return httpx.Client(transport=transport, base_url=BASE, timeout=5.0)


def _failed_names(results):
    return [r.name for r in results if not r.ok]


def _messages(results):
    return {r.name: r.message for r in results}


# -- happy path -------------------------------------------------------------

def test_all_pass():
    with _client() as client:
        results = smoke.run_checks(BASE, FULL_SHA, client=client)
    assert smoke.all_passed(results), [r.line() for r in results if not r.ok]
    names = [r.name for r in results]
    assert names == [
        "health", "storage", "worker", "git_sha",
        "ledger_writable", "projects", "read_content", "spa",
    ]


def test_bundleless_image_fails_the_spa_check():
    """The 'UI not built' page is a 200, so a status-code probe cannot catch
    a bundle-less image — check_spa must. (Decision 8 keeps the 200 for local
    dev; the smoke path asserts the built SPA specifically.)"""
    overrides = {
        "/": (200, "<html><h2>The control-room UI is not built</h2></html>"),
    }
    with _client(overrides) as client:
        results = smoke.run_checks(BASE, FULL_SHA, client=client)
    assert not smoke.all_passed(results)
    assert "spa" in _failed_names(results)
    assert "not built" in _messages(results)["spa"]


def test_spa_check_rejects_wrong_content_type():
    overrides = {"/": (200, b"")}
    with _client(overrides) as client:
        results = smoke.run_checks(BASE, FULL_SHA, client=client)
    assert "spa" in _failed_names(results)


def test_spa_check_requires_the_mount_point():
    overrides = {"/": (200, "<!doctype html><html></html>")}
    with _client(overrides) as client:
        results = smoke.run_checks(BASE, FULL_SHA, client=client)
    assert "spa" in _failed_names(results)
    assert "mount point" in _messages(results)["spa"]


def test_short_sha_prefix_match():
    with _client() as client:
        results = smoke.run_checks(BASE, SHORT_SHA, client=client)
    assert smoke.all_passed(results)
    assert "matches" in _messages(results)["git_sha"]


def test_worker_unreachable_still_ok():
    """Offline GPU is allowed; worker status must merely be present."""
    overrides = {
        "/api/v1/health": (200, _health(worker={
            "status": "unreachable",
            "tier": "test",
            "detail": "connection refused",
        })),
    }
    with _client(overrides) as client:
        results = smoke.run_checks(BASE, FULL_SHA, client=client)
    assert smoke.all_passed(results)
    assert "unreachable" in _messages(results)["worker"]


def test_no_projects_falls_back_to_openapi():
    overrides = {
        "/api/v1/projects": (200, _projects(ids=[])),
    }
    with _client(overrides) as client:
        results = smoke.run_checks(BASE, FULL_SHA, client=client)
    assert smoke.all_passed(results)
    assert "openapi" in _messages(results)["read_content"]


# -- failures that must name the reason -------------------------------------

def test_wrong_sha_fails_with_clear_message():
    with _client() as client:
        results = smoke.run_checks(BASE, "cafebabe" * 4, client=client)
    assert not smoke.all_passed(results)
    assert _failed_names(results) == ["git_sha"]
    msg = _messages(results)["git_sha"]
    assert "does not match" in msg
    assert "stale" in msg
    assert FULL_SHA in msg


def test_null_git_sha_fails():
    overrides = {
        "/api/v1/health": (200, _health(git_sha=None)),
    }
    with _client(overrides) as client:
        results = smoke.run_checks(BASE, FULL_SHA, client=client)
    assert "git_sha" in _failed_names(results)
    assert "null" in _messages(results)["git_sha"]
    assert "unstamped" in _messages(results)["git_sha"]


def test_ledger_writable_false_fails():
    overrides = {
        "/api/v1/quota": (200, _quota(ledger_writable=False)),
    }
    with _client(overrides) as client:
        results = smoke.run_checks(BASE, FULL_SHA, client=client)
    assert not smoke.all_passed(results)
    assert "ledger_writable" in _failed_names(results)
    msg = _messages(results)["ledger_writable"]
    assert "False" in msg or "false" in msg
    assert "FOLEYFLOW_LEDGER_PATH" in msg or "ephemeral" in msg


def test_storage_not_ok_fails():
    overrides = {
        "/api/v1/health": (200, _health(status="degraded", storage="error")),
    }
    with _client(overrides) as client:
        results = smoke.run_checks(BASE, FULL_SHA, client=client)
    assert "storage" in _failed_names(results)
    assert "must be ok" in _messages(results)["storage"]
    # Health top-level may still pass when degraded
    by_name = {r.name: r for r in results}
    assert by_name["health"].ok is True
    assert by_name["storage"].ok is False


def test_missing_worker_fails():
    overrides = {
        "/api/v1/health": (200, _health(worker=None)),
    }
    # _health(worker=None) would fill the default; force null via raw body
    body = _health()
    body["worker"] = None
    overrides["/api/v1/health"] = (200, body)
    with _client(overrides) as client:
        results = smoke.run_checks(BASE, FULL_SHA, client=client)
    assert "worker" in _failed_names(results)
    assert "missing" in _messages(results)["worker"]


def test_dossier_non_200_fails():
    pid = "proj_01KTEST000000000000000000"
    overrides = {
        f"/api/v1/projects/{pid}/dossier": (500, {"error": {"code": "boom"}}),
    }
    with _client(overrides) as client:
        results = smoke.run_checks(BASE, FULL_SHA, client=client)
    assert "read_content" in _failed_names(results)
    assert "500" in _messages(results)["read_content"]


def test_main_exits_nonzero_on_failure(capsys):
    # Wire main() through MockTransport by patching run_checks is heavier;
    # exercise run_checks + print path the same way main does.
    with _client({"/api/v1/quota": (200, _quota(ledger_writable=False))}) as client:
        results = smoke.run_checks(BASE, FULL_SHA, client=client)
    for r in results:
        print(r.line())
    assert not smoke.all_passed(results)
    out = capsys.readouterr().out
    assert "FAIL  ledger_writable:" in out
    assert "OK  health:" in out


def test_main_requires_expect_sha_outside_git(monkeypatch, capsys):
    monkeypatch.setattr(smoke, "_git_head", lambda: None)
    code = smoke.main(["--base-url", BASE])
    assert code == 2
    err = capsys.readouterr().err
    assert "--expect-sha" in err


def test_main_all_pass_exit_zero(monkeypatch, capsys):
    def fake_run(base_url, expect_sha, **kwargs):
        return [
            smoke.CheckResult("health", True, "status='ok'"),
            smoke.CheckResult("storage", True, "storage='ok'"),
            smoke.CheckResult("worker", True, "status='ok'"),
            smoke.CheckResult("git_sha", True, "match"),
            smoke.CheckResult("ledger_writable", True, "true"),
            smoke.CheckResult("projects", True, "1"),
            smoke.CheckResult("read_content", True, "dossier"),
        ]

    monkeypatch.setattr(smoke, "run_checks", fake_run)
    code = smoke.main(["--base-url", BASE, "--expect-sha", FULL_SHA])
    assert code == 0
    out = capsys.readouterr().out
    assert "smoke ok" in out


def test_main_fail_exit_one(monkeypatch, capsys):
    def fake_run(base_url, expect_sha, **kwargs):
        return [
            smoke.CheckResult("health", True, "ok"),
            smoke.CheckResult("git_sha", False, "does not match expected"),
        ]

    monkeypatch.setattr(smoke, "run_checks", fake_run)
    code = smoke.main(["--base-url", BASE, "--expect-sha", FULL_SHA])
    assert code == 1
    captured = capsys.readouterr()
    assert "FAIL  git_sha:" in captured.out
    assert "smoke FAILED" in captured.err


def test_sha_matches_helper():
    assert smoke._sha_matches(FULL_SHA, SHORT_SHA)
    assert smoke._sha_matches(SHORT_SHA, FULL_SHA)
    assert smoke._sha_matches(FULL_SHA, FULL_SHA)
    assert not smoke._sha_matches(None, FULL_SHA)
    assert not smoke._sha_matches("abc1234", FULL_SHA)
    # Prefix shorter than 7 chars must not match.
    assert not smoke._sha_matches("deadbe", FULL_SHA)
    assert not smoke._sha_matches(FULL_SHA, "deadbe")
    # Two full-length SHAs that only share a prefix are different commits.
    other_full = "deadbeefcafebabe0123456789abcdef01234568"
    assert not smoke._sha_matches(FULL_SHA, other_full)
