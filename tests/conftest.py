"""Suite-wide offline isolation.

Developers' `.env` typically sets `FOLEYFLOW_CACHE_BACKEND=b2` (and real B2
credentials). With the default unset `FOLEYFLOW_LEDGER_PATH`, the cost ledger
and every `get_quota` / `project_spend` / analyze gate then list the **live**
bucket — ~9 s per call and accidental writes to production.

This autouse fixture pins the storage backend and the ledger to a private
LocalBackend for every test, unless the test itself re-points them (budget
fixtures, storage-ledger tests, explicit `get_backend("b2")` factory checks).
"""

from __future__ import annotations

import pytest

from foleyflow.storage.local import LocalBackend


@pytest.fixture(autouse=True)
def _isolate_ambient_deploy_config(tmp_path, monkeypatch):
    """Keep the offline suite offline and fast.

    - Default `get_backend()` → private LocalBackend (not B2 from `.env`).
    - Ledger stays in storage mode (`LEDGER_PATH is None`) so budget alerts
      and attributions still exercise the real path — just against local.
    - Explicit `get_backend("b2"|"dropbox")` still hits the real factory so
      the factory unit tests keep their contract.
    """
    backend = LocalBackend(str(tmp_path / "offline_store"))

    # Ignore ambient deploy selection for the default path.
    monkeypatch.setenv("FOLEYFLOW_CACHE_BACKEND", "local")
    monkeypatch.setenv("FOLEYFLOW_LOCAL_CACHE_DIR", str(tmp_path / "sfx_cache"))
    monkeypatch.delenv("FOLEYFLOW_LEDGER_PATH", raising=False)
    # A reachable worker URL in .env would make unstubbed health probes hang.
    monkeypatch.delenv("FOLEYFLOW_SFX_WORKER_URL", raising=False)
    monkeypatch.delenv("FOLEYFLOW_SFX_TIER", raising=False)

    import foleyflow.ledger as ledger
    import foleyflow.storage as storage_pkg
    import foleyflow.storage.factory as factory

    monkeypatch.setattr(ledger, "LEDGER_PATH", None)
    monkeypatch.setattr(ledger, "_write_ok", True)

    real_get_backend = factory.get_backend

    def offline_get_backend(kind=None):
        # Default / local → private temp store. Explicit non-local kinds
        # (factory contract tests) still go through the real selector.
        if kind is None:
            return backend
        normalized = str(kind).strip().lower()
        if normalized in ("", "local"):
            return backend
        return real_get_backend(kind)

    monkeypatch.setattr(factory, "get_backend", offline_get_backend)
    monkeypatch.setattr(storage_pkg, "get_backend", offline_get_backend)

    ledger._invalidate_row_cache()
    ledger._invalidate_writable_cache()
    ledger.invalidate_project_spend_cache()

    yield

    # Drop any rows this test left in process-local caches so the next test
    # starts from a clean slate even if it reuses the same project ids.
    ledger._invalidate_row_cache()
    ledger._invalidate_writable_cache()
    ledger.invalidate_project_spend_cache()
