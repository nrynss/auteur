"""Storage-backed cost ledger.

The default ledger is no longer a process-local JSONL file under /tmp: every
row is an immutable object under `ledger/{ulid}.json` on the shared backend
(B2 in deploy, LocalBackend in tests). These tests exercise that path with a
LocalBackend so they need no credentials and cannot touch real B2.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from foleyflow import ledger
from foleyflow.storage import LocalBackend, StorageError


@pytest.fixture
def storage_ledger(tmp_path, monkeypatch):
    """Route the ledger at a private LocalBackend and restore write-ok state."""
    backend = LocalBackend(str(tmp_path / "store"))
    monkeypatch.delenv("FOLEYFLOW_LEDGER_PATH", raising=False)
    monkeypatch.setattr(ledger, "LEDGER_PATH", None)
    monkeypatch.setattr(ledger, "_write_ok", True)
    # ledger does `from foleyflow.storage import get_backend` at call time, so
    # patching the package attribute is enough.
    monkeypatch.setattr("foleyflow.storage.get_backend", lambda *a, **k: backend)
    ledger._invalidate_row_cache()
    return backend


def test_log_transaction_writes_one_object_per_row(storage_ledger):
    ledger.log_transaction("gemini-2.5-flash", "abc", 0.005, success=True)
    ledger.log_transaction("gemini-2.5-flash", "def", 0.005, success=False,
                           error="429")

    keys = [k for k in storage_ledger.list_keys(ledger.LEDGER_PREFIX)
            if ledger._is_transaction_key(k)]
    assert len(keys) == 2
    # ULID keys sort in creation order.
    assert keys == sorted(keys)

    rows = ledger.iter_transactions()
    assert len(rows) == 2
    assert rows[0]["params_hash"] == "abc" and rows[0]["success"] is True
    assert rows[1]["success"] is False and rows[1]["error"] == "429"


def test_summarize_ledger_sums_only_successful_storage_rows(storage_ledger):
    ledger.log_transaction("a", "x", 0.10, success=True)
    ledger.log_transaction("a", "y", 0.20, success=True)
    ledger.log_transaction("a", "z", 0.50, success=False, error="nope")
    ledger.log_transaction("b", "w", 0.05, success=True)

    summary = ledger.summarize_ledger()
    assert summary["total"] == pytest.approx(0.35)
    assert summary["by_endpoint"] == {"a": pytest.approx(0.30),
                                      "b": pytest.approx(0.05)}


def test_concurrent_writes_do_not_lose_rows(storage_ledger):
    """The whole reason for one-object-per-row: no read-modify-write race."""
    n = 40

    def write(i):
        ledger.log_transaction("worker", f"h{i}", 0.001, success=True)

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(write, range(n)))

    rows = ledger.iter_transactions()
    assert len(rows) == n
    hashes = {r["params_hash"] for r in rows}
    assert hashes == {f"h{i}" for i in range(n)}


def test_storage_write_failure_marks_ledger_unwritable(storage_ledger, monkeypatch):
    """A failed put is not a silent no-op: quota must stop looking real."""

    class Broken(LocalBackend):
        def put_if_absent(self, key, data, content_type=None):
            raise StorageError("bucket rejected write")

    broken = Broken(storage_ledger.root)
    monkeypatch.setattr("foleyflow.storage.get_backend", lambda *a, **k: broken)
    monkeypatch.setattr(ledger, "_write_ok", True)

    assert ledger.ledger_writable() is True  # probe uses put_bytes, still works

    # Force the write path to use the broken put_if_absent.
    ledger.log_transaction("gemini-2.5-flash", "x", 0.01, success=True)
    assert ledger._write_ok is False
    assert ledger.ledger_writable() is False
    # No transaction object landed.
    keys = [k for k in broken.list_keys(ledger.LEDGER_PREFIX)
            if ledger._is_transaction_key(k)]
    assert keys == []


def test_ledger_writable_probe_uses_storage(storage_ledger):
    assert ledger.ledger_writable() is True
    assert storage_ledger.exists(ledger._WRITABLE_PROBE_KEY)


def test_file_override_still_appends_jsonl(tmp_path, monkeypatch):
    """FOLEYFLOW_LEDGER_PATH keeps the historical single-file path for tests."""
    path = tmp_path / "nested" / "ledger.jsonl"
    monkeypatch.setattr(ledger, "LEDGER_PATH", str(path))
    monkeypatch.setattr(ledger, "_write_ok", True)

    ledger.log_transaction("m", "h", 0.01, success=True)
    ledger.log_transaction("m", "i", 0.02, success=True)

    text = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(text) == 2
    assert json.loads(text[0])["params_hash"] == "h"
    assert ledger.summarize_ledger()["total"] == pytest.approx(0.03)


def test_default_is_storage_not_repo_root_file(monkeypatch):
    """Unset FOLEYFLOW_LEDGER_PATH means the shared backend, not ledger.jsonl."""
    monkeypatch.delenv("FOLEYFLOW_LEDGER_PATH", raising=False)
    import importlib
    reloaded = importlib.reload(ledger)
    try:
        assert reloaded.LEDGER_PATH is None
        assert reloaded.uses_storage_ledger() is True
    finally:
        importlib.reload(ledger)


def test_storage_row_cache_is_shared_and_invalidated_on_write(storage_ledger, monkeypatch):
    """Repeated reads must not re-GET every object; a write must bust the cache."""
    ledger.log_transaction("m", "a", 0.01, success=True)
    ledger.log_transaction("m", "b", 0.01, success=True)

    gets = {"n": 0}
    real_get = storage_ledger.get_bytes

    def counting_get(key):
        gets["n"] += 1
        return real_get(key)

    monkeypatch.setattr(storage_ledger, "get_bytes", counting_get)

    first = ledger.iter_transactions()
    assert len(first) == 2
    after_first = gets["n"]
    assert after_first >= 2

    second = ledger.iter_transactions()
    assert second == first
    assert gets["n"] == after_first, "cache should absorb the second full scan"

    ledger.log_transaction("m", "c", 0.01, success=True)
    third = ledger.iter_transactions()
    assert len(third) == 3
    assert gets["n"] > after_first, "a write must invalidate the snapshot"


def test_get_all_quotas_scans_the_ledger_once(storage_ledger, monkeypatch):
    from foleyflow.quota import get_all_quotas

    for i in range(5):
        ledger.log_transaction("gemini-2.5-flash", f"h{i}", 0.005, success=True)

    lists = {"n": 0}
    real_list = storage_ledger.list_keys

    def counting_list(prefix):
        lists["n"] += 1
        return real_list(prefix)

    monkeypatch.setattr(storage_ledger, "list_keys", counting_list)
    ledger._invalidate_row_cache()

    get_all_quotas()
    assert lists["n"] == 1, "one listing for every model, not one per model"


# -- project attribution ----------------------------------------------------

def test_rows_are_attributed_inside_a_project_context(storage_ledger):
    """set_project_context tags rows; outside it, rows stay unattributed."""
    with ledger.set_project_context("proj_A"):
        ledger.log_transaction("gemini-2.5-flash", "a1", 0.30, success=True)
        ledger.log_transaction("openmoss/moss-soundeffect-v2", "a2", 0.0,
                               success=True)
    ledger.log_transaction("gemini-2.5-flash", "g1", 0.20, success=True)

    rows = ledger.iter_transactions()
    by_hash = {r["params_hash"]: r for r in rows}
    assert by_hash["a1"]["project_id"] == "proj_A"
    assert by_hash["a2"]["project_id"] == "proj_A"
    assert by_hash["g1"]["project_id"] is None


def test_project_spend_isolates_projects_and_counts_failures(storage_ledger):
    with ledger.set_project_context("proj_A"):
        ledger.log_transaction("gemini-2.5-flash", "a1", 0.30, success=True)
        ledger.log_transaction("gemini-2.5-flash", "a2", 0.05, success=False,
                               error="429")
    with ledger.set_project_context("proj_B"):
        ledger.log_transaction("gemini-2.5-flash", "b1", 0.40, success=True)
    ledger.log_transaction("gemini-2.5-flash", "g1", 0.99, success=True)

    a = ledger.project_spend("proj_A")
    b = ledger.project_spend("proj_B")
    assert a["total"] == pytest.approx(0.35)      # failures counted, like quota
    assert a["by_endpoint"] == {"gemini-2.5-flash": pytest.approx(0.35)}
    assert b["total"] == pytest.approx(0.40)
    # The unattributed row belongs to no project.
    assert a["by_endpoint"].get("gemini-2.5-flash") == pytest.approx(0.35)
    assert b["by_endpoint"] == {"gemini-2.5-flash": pytest.approx(0.40)}


def test_old_rows_without_project_id_never_attribute(storage_ledger):
    """Rows written before attribution existed (no project_id key) must not be
    counted for any project."""
    from foleyflow import storage as storage_mod

    backend = storage_ledger
    row = {"endpoint": "gemini-2.5-flash", "params_hash": "old",
           "cost_estimate": 0.50, "success": True, "timestamp": 1.0}
    backend.put_bytes(f"{ledger.LEDGER_PREFIX}old.json",
                      json.dumps(row).encode())
    ledger._invalidate_row_cache()

    assert ledger.project_spend("proj_A") == {"total": 0.0, "by_endpoint": {}}
    assert ledger.project_spend("proj_B") == {"total": 0.0, "by_endpoint": {}}


# -- rescue (attribution overrides) ------------------------------------------

def test_record_attribution_claims_unattributed_rows(storage_ledger):
    """An override adopts project-less rows written before its `before`; rows
    inside a project context are untouched and never double-counted."""
    before = 1000.0
    with ledger.set_project_context("proj_A"):
        ledger.log_transaction("gemini-2.5-flash", "a1", 0.10, success=True)
    # Untagged rows, written "in the past" via a private file mode so we can
    # control timestamps: storage mode stamps time.time(), which is > before.
    from foleyflow import storage as storage_mod

    import time as _time
    old = _time.time() - 3600
    backend = storage_ledger
    for i, cost in enumerate((0.05, 0.15)):
        row = {"endpoint": "gemini-2.5-flash", "params_hash": f"u{i}",
               "cost_estimate": cost, "success": True, "timestamp": old,
               "project_id": None}
        backend.put_bytes(f"{ledger.LEDGER_PREFIX}u{i}.json",
                          json.dumps(row).encode())
    ledger._invalidate_row_cache()

    claimed = ledger.record_attribution("proj_B", before=old + 10)
    assert claimed == 2

    a = ledger.project_spend("proj_A")
    b = ledger.project_spend("proj_B")
    assert a["total"] == pytest.approx(0.10)   # own rows only
    assert b["total"] == pytest.approx(0.20)   # the rescued rows


def test_attribution_overrides_apply_first_come(storage_ledger):
    """Two rescues: the earliest `before` claims the row — never two projects."""
    import time as _time
    old = _time.time() - 3600
    backend = storage_ledger
    row = {"endpoint": "gemini-2.5-flash", "params_hash": "u",
           "cost_estimate": 0.30, "success": True, "timestamp": old,
           "project_id": None}
    backend.put_bytes(f"{ledger.LEDGER_PREFIX}u.json", json.dumps(row).encode())
    ledger._invalidate_row_cache()

    ledger.record_attribution("proj_A", before=old + 20)
    ledger.record_attribution("proj_B", before=old + 10)

    assert ledger.project_spend("proj_A")["total"] == pytest.approx(0.0)
    assert ledger.project_spend("proj_B")["total"] == pytest.approx(0.30)


def test_attribution_records_are_not_transactions(storage_ledger):
    import time
    ledger.record_attribution("proj_A", before=time.time() - 10)
    rows = ledger.iter_transactions()
    assert all(r.get("endpoint") is not None for r in rows)
    assert len(ledger.iter_attributions()) == 1


def test_record_attribution_rejects_a_future_before(storage_ledger):
    import time
    with pytest.raises(ValueError, match="future"):
        ledger.record_attribution("proj_A", before=time.time() + 3600)


def test_record_attribution_reports_only_rows_it_actually_owns(storage_ledger):
    """`claimed` must count rows whose first-come owner is THIS rescue, not
    every unattributed row inside the window — an earlier override keeps its
    rows and must not be reported as adopted twice."""
    import time as _time
    old = _time.time() - 3600
    backend = storage_ledger
    for i in range(3):
        row = {"endpoint": "gemini-2.5-flash", "params_hash": f"u{i}",
               "cost_estimate": 0.10, "success": True, "timestamp": old,
               "project_id": None}
        backend.put_bytes(f"{ledger.LEDGER_PREFIX}u{i}.json",
                          json.dumps(row).encode())
    ledger._invalidate_row_cache()

    assert ledger.record_attribution("proj_A", before=old + 10) == 3
    # Wider window, but proj_A got there first: nothing left for proj_B.
    assert ledger.record_attribution("proj_B", before=old + 20) == 0
    assert ledger.project_spend("proj_A")["total"] == pytest.approx(0.30)
    assert ledger.project_spend("proj_B")["total"] == pytest.approx(0.0)


def test_record_attribution_refuses_file_mode(tmp_path, monkeypatch):
    """A rescue in file mode would split-brain between the JSONL rows and the
    backend attr records — refuse instead of silently writing to another
    store."""
    monkeypatch.setattr(ledger, "LEDGER_PATH", str(tmp_path / "ledger.jsonl"))
    with pytest.raises(ValueError, match="storage-mode only"):
        ledger.record_attribution("proj_A")
