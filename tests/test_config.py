"""Read-only configuration sheet (task 20, docs/api.md §3.17) — contract
tests.

The sheet answers "is this deployment wired up correctly?" with the same
verdicts `foleyflow doctor` prints and an integration presence list that
never carries a credential value (the service deploys --allow-unauthenticated,
so a value-echoing route would be an open credential endpoint).
"""

import pytest
from fastapi.testclient import TestClient

from foleyflow.api.app import API_PREFIX, create_app
from foleyflow.api.jobs import JobRunner
from foleyflow.api.schemas import WorkerHealth
from foleyflow.projects import ProjectStore
from foleyflow.storage import LocalBackend


@pytest.fixture
def store(tmp_path):
    return ProjectStore(LocalBackend(str(tmp_path / "b2")),
                        work_dir=str(tmp_path / "w"))


@pytest.fixture
def client(store):
    runner = JobRunner(store, max_workers=1)
    app = create_app(store, runner)
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    runner.shutdown(wait=True)


def _config(client):
    r = client.get(f"{API_PREFIX}/config")
    assert r.status_code == 200, r.text
    return r.json()


# -- contract ---------------------------------------------------------------

def test_config_shape_and_check_keys(client):
    body = _config(client)
    assert set(body) == {"ok", "version", "brand", "git_sha", "build_time",
                         "checks", "integrations"}
    keys = {c["key"] for c in body["checks"]}
    assert {"api", "storage", "worker", "ledger", "quota"} <= keys
    assert body["version"]
    # The runtime brand travels so the screen can compare it with the UI's
    # build-time name and report a disagreement as a blocked row.
    assert body["brand"] == "FoleyFlow"
    for c in body["checks"]:
        assert c["state"] in ("ok", "warn", "blocked")
        assert c["label"]


def test_healthy_deployment_is_ok(client):
    body = _config(client)
    # Offline suite: local backend up, no worker tier to probe (test tier is
    # the local worker — reachable check is adapter-level and absent here, so
    # the verdict is whatever the adapter says; the aggregate must still
    # resolve), ledger writable, quota green.
    assert body["ok"] is True, body
    by_key = {c["key"]: c for c in body["checks"]}
    assert by_key["storage"]["state"] == "ok"
    assert by_key["ledger"]["state"] == "ok"
    assert by_key["quota"]["state"] == "ok"


# -- verdicts: same rules as foleyflow doctor -------------------------------

def test_storage_down_is_blocked_and_poisons_api(client, monkeypatch):
    def boom(_key):
        raise OSError("b2 unreachable")
    monkeypatch.setattr(client.app.state.store.backend, "exists", boom)
    body = _config(client)
    by_key = {c["key"]: c for c in body["checks"]}
    assert by_key["storage"]["state"] == "blocked"
    assert "reads/writes will fail" in by_key["storage"]["detail"]
    # The api row reports the degraded health verdict as evidence.
    assert "status=degraded" in by_key["api"]["detail"]
    assert body["ok"] is False


def test_worker_unreachable_is_blocked(client, monkeypatch):
    import foleyflow.api.app as app_mod
    monkeypatch.setattr(
        app_mod, "_worker_health",
        lambda: WorkerHealth(status="unreachable", tier="test",
                             detail="connection refused"))
    body = _config(client)
    by_key = {c["key"]: c for c in body["checks"]}
    assert by_key["worker"]["state"] == "blocked"
    assert "connection refused" in by_key["worker"]["detail"]
    assert body["ok"] is False


def test_worker_auth_required_but_token_unset_is_blocked(client, monkeypatch):
    """Doctor's sharpest rule: a worker that demands a bearer token this
    deployment lacks reads healthy from /health but fails every generation."""
    import foleyflow.api.app as app_mod
    monkeypatch.setattr(
        app_mod, "_worker_health",
        lambda: WorkerHealth(status="ok", tier="test", device="rtx4070",
                             auth_required=True, auth_configured=False))
    body = _config(client)
    by_key = {c["key"]: c for c in body["checks"]}
    assert by_key["worker"]["state"] == "blocked"
    assert "FOLEYFLOW_SFX_WORKER_TOKEN unset" in by_key["worker"]["detail"]
    assert body["ok"] is False


def test_worker_not_applicable_is_warn_not_blocked(client, monkeypatch):
    """A hosted paid/fallback tier has nothing to probe — a paid-only demo is
    still demo-ready, so this must not flip the sheet to false."""
    import foleyflow.api.app as app_mod
    monkeypatch.setattr(
        app_mod, "_worker_health",
        lambda: WorkerHealth(status="not_applicable", tier="paid"))
    body = _config(client)
    by_key = {c["key"]: c for c in body["checks"]}
    assert by_key["worker"]["state"] == "warn"
    assert body["ok"] is True


def test_ledger_unwritable_is_blocked_with_the_fiction_consequence(
        client, monkeypatch):
    """§3.10 fail-closed: an unwritable ledger makes every spend figure
    fiction, so the check must say exactly that (strict True, like doctor)."""
    from foleyflow import ledger as ledger_mod
    monkeypatch.setattr(ledger_mod, "ledger_writable", lambda *a, **k: False)
    body = _config(client)
    by_key = {c["key"]: c for c in body["checks"]}
    assert by_key["ledger"]["state"] == "blocked"
    assert "spend figures are fiction" in by_key["ledger"]["detail"]
    assert body["ok"] is False


def test_quota_any_blocked_model_is_a_stop(client, monkeypatch):
    """ANY quota-blocked model is a STOP (doctor's rule), not just the
    default analysis VLM."""
    from foleyflow import quota as quota_mod
    from foleyflow.quota import ModelQuota, QuotaWindow
    monkeypatch.setattr(quota_mod, "get_all_quotas", lambda: [
        ModelQuota(model_id="gemini-2.5-flash",
                   per_minute=QuotaWindow(0, 5),
                   per_day=QuotaWindow(20, 20),
                   resets_at="2026-08-04T00:00:00Z"),
    ])
    body = _config(client)
    by_key = {c["key"]: c for c in body["checks"]}
    assert by_key["quota"]["state"] == "blocked"
    assert "gemini-2.5-flash" in by_key["quota"]["detail"]
    assert body["ok"] is False


def test_quota_warn_when_nearing_the_limit(client, monkeypatch):
    from foleyflow import quota as quota_mod
    from foleyflow.quota import ModelQuota, QuotaWindow
    monkeypatch.setattr(quota_mod, "get_all_quotas", lambda: [
        ModelQuota(model_id="gemini-2.5-flash",
                   per_minute=QuotaWindow(2, 5),
                   per_day=QuotaWindow(17, 20),
                   resets_at="2026-08-04T00:00:00Z"),
    ])
    body = _config(client)
    by_key = {c["key"]: c for c in body["checks"]}
    assert by_key["quota"]["state"] == "warn"
    assert "17/20 today" in by_key["quota"]["detail"]
    # warn is not blocked — the sheet stays ok.
    assert body["ok"] is True


# -- integrations: presence only, never a value ------------------------------

def test_integrations_report_presence_and_source(client):
    body = _config(client)
    by_key = {i["key"]: i for i in body["integrations"]}
    # Every declared integration is present with the contract source.
    assert by_key["b2"]["source"] == "secret-manager"
    assert by_key["gemini"]["source"] == "secret-manager"
    assert by_key["openai"]["source"] == "env"
    assert by_key["elevenlabs"]["source"] == "secret-manager"
    assert by_key["sfx_worker"]["source"] == "env"
    assert by_key["sfx_worker_token"]["source"] == "secret-manager"
    # env carries NAMES only.
    assert by_key["b2"]["env"] == ["B2_APPLICATION_KEY", "B2_KEY_ID"]
    assert set(by_key["b2"]) == {"key", "label", "source", "configured",
                                 "env", "missing", "unresolved"}


def test_integration_presence_follows_the_environment(client, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    body = _config(client)
    by_key = {i["key"]: i for i in body["integrations"]}
    assert by_key["openai"]["configured"] is False
    assert by_key["openai"]["missing"] == ["OPENAI_API_KEY"]

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-123")
    body = _config(client)
    by_key = {i["key"]: i for i in body["integrations"]}
    assert by_key["openai"]["configured"] is True
    assert by_key["openai"]["missing"] == []


def test_gemini_is_configured_by_either_key(client, monkeypatch):
    """GEMINI_API_KEY or GOOGLE_API_KEY — either satisfies the integration."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    body = _config(client)
    by_key = {i["key"]: i for i in body["integrations"]}
    assert by_key["gemini"]["configured"] is False

    monkeypatch.setenv("GOOGLE_API_KEY", "google-test-123")
    body = _config(client)
    by_key = {i["key"]: i for i in body["integrations"]}
    assert by_key["gemini"]["configured"] is True


def test_unresolved_secret_manager_reference_is_called_out(
        client, monkeypatch):
    """The classic misconfiguration: a literal reference in env.public.yaml
    instead of letting Cloud Run resolve it. Named, never echoed."""
    monkeypatch.setenv(
        "GEMINI_API_KEY",
        "projects/my-proj/secrets/foleyflow-gemini-api-key/versions/latest")
    body = _config(client)
    by_key = {i["key"]: i for i in body["integrations"]}
    assert by_key["gemini"]["unresolved"] == ["GEMINI_API_KEY"]
    # A reference in the wrong place is not a configured secret.
    assert by_key["gemini"]["configured"] is True  # the var IS set
    assert "projects/" not in str(body)  # the reference never leaves


def test_no_credential_value_appears_anywhere(client, monkeypatch):
    """The acceptance's sharpest rule: configure real-looking values and
    assert the entire response body carries none of them."""
    monkeypatch.setenv("GEMINI_API_KEY", "AIzaSy-very-secret-key-value-123")
    monkeypatch.setenv("B2_APPLICATION_KEY", "0042-super-secret-application-key")
    monkeypatch.setenv("FOLEYFLOW_SFX_WORKER_URL", "https://sfx.secret-host.example")
    monkeypatch.setenv("FOLEYFLOW_SFX_WORKER_TOKEN", "tok_verysecret123")
    r = client.get(f"{API_PREFIX}/config")
    text = r.text
    for secret in ("AIzaSy-very-secret-key-value-123",
                   "0042-super-secret-application-key",
                   "https://sfx.secret-host.example",
                   "tok_verysecret123"):
        assert secret not in text, secret
