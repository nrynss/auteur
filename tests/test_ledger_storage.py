"""Storage-backed cost ledger.

The default ledger is no longer a process-local JSONL file under /tmp: every
row is an immutable object under `ledger/{ulid}.json` on the shared backend
(B2 in deploy, LocalBackend in tests). These tests exercise that path with a
LocalBackend so they need no credentials and cannot touch real B2.
"""

from __future__ import annotations

import json
import threading
import time
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
    ledger._invalidate_writable_cache()
    ledger._invalidate_attr_cache()
    ledger.invalidate_project_spend_cache()
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

    empty = {"total": 0.0, "by_endpoint": {}, "estimated": 0.0, "actual": 0.0}
    assert ledger.project_spend("proj_A") == empty
    assert ledger.project_spend("proj_B") == empty


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


# -- unit 7.18: concurrent load, writable cache, single-flight --------------

def test_concurrent_row_load_preserves_spend_and_iter(storage_ledger):
    """project_spend / iter_transactions stay correct with multi-get rows."""
    with ledger.set_project_context("proj_A"):
        for i in range(12):
            ledger.log_transaction("gemini-2.5-flash", f"h{i}", 0.01,
                                   success=True)
    with ledger.set_project_context("proj_B"):
        ledger.log_transaction("gemini-2.5-flash", "b1", 0.40, success=True)

    ledger._invalidate_row_cache()
    rows = ledger.iter_transactions()
    assert len(rows) == 13
    a = ledger.project_spend("proj_A")
    b = ledger.project_spend("proj_B")
    assert a["total"] == pytest.approx(0.12)
    assert b["total"] == pytest.approx(0.40)


def test_ledger_writable_caches_probe_within_ttl(storage_ledger, monkeypatch):
    """Second ledger_writable() within TTL must not re-PUT the probe."""
    puts = {"n": 0}
    real_put = storage_ledger.put_bytes

    def counting_put(key, data, content_type=None):
        puts["n"] += 1
        return real_put(key, data, content_type=content_type)

    monkeypatch.setattr(storage_ledger, "put_bytes", counting_put)
    ledger._invalidate_writable_cache()
    monkeypatch.setattr(ledger, "_write_ok", True)

    assert ledger.ledger_writable() is True
    assert puts["n"] == 1
    assert ledger.ledger_writable() is True
    assert puts["n"] == 1, "probe cache should absorb the second call"


def test_ledger_writable_failed_write_reports_false_without_reprobe(
        storage_ledger, monkeypatch):
    """Failed log_transaction pins unwritable immediately (fail-closed)."""
    puts = {"n": 0}
    real_put = storage_ledger.put_bytes
    real_put_if = storage_ledger.put_if_absent

    def counting_put(key, data, content_type=None):
        puts["n"] += 1
        return real_put(key, data, content_type=content_type)

    class Broken(LocalBackend):
        def put_if_absent(self, key, data, content_type=None):
            raise StorageError("bucket rejected write")

        def put_bytes(self, key, data, content_type=None):
            return counting_put(key, data, content_type=content_type)

    broken = Broken(storage_ledger.root)
    monkeypatch.setattr("foleyflow.storage.get_backend", lambda *a, **k: broken)
    monkeypatch.setattr(ledger, "_write_ok", True)
    ledger._invalidate_writable_cache()

    assert ledger.ledger_writable() is True
    probe_puts = puts["n"]
    ledger.log_transaction("gemini-2.5-flash", "x", 0.01, success=True)
    assert ledger._write_ok is False
    assert ledger.ledger_writable() is False
    assert puts["n"] == probe_puts, "pinned False must not re-PUT the probe"


def test_read_storage_rows_single_flight(storage_ledger, monkeypatch):
    """Two concurrent _read_storage_rows share one list_keys scan."""
    for i in range(5):
        ledger.log_transaction("m", f"h{i}", 0.01, success=True)
    ledger._invalidate_row_cache()

    lists = {"n": 0}
    real_list = storage_ledger.list_keys
    gate = threading.Event()
    entered = threading.Event()

    def slow_list(prefix):
        lists["n"] += 1
        entered.set()
        # Hold the first scanner so a concurrent caller arrives mid-flight.
        gate.wait(timeout=2.0)
        return real_list(prefix)

    monkeypatch.setattr(storage_ledger, "list_keys", slow_list)

    results = []
    errors = []

    def reader():
        try:
            results.append(ledger._read_storage_rows(force=True))
        except Exception as exc:  # pragma: no cover - surface in assert
            errors.append(exc)

    t1 = threading.Thread(target=reader)
    t2 = threading.Thread(target=reader)
    t1.start()
    assert entered.wait(timeout=2.0)
    t2.start()
    # Give t2 a moment to hit the single-flight wait before releasing list.
    time.sleep(0.05)
    gate.set()
    t1.join(timeout=5.0)
    t2.join(timeout=5.0)

    assert errors == []
    assert len(results) == 2
    assert len(results[0]) == 5
    assert results[0] == results[1]
    assert lists["n"] == 1, "single-flight should list once for concurrent callers"


def test_row_cache_discards_pre_write_snapshot_after_mid_flight_write(
        storage_ledger, monkeypatch):
    """A write during an in-flight scan must not reinstall a stale cache.

    Without an epoch check, the leader would finish list+get of the old
    set, then overwrite the post-write invalidation with a fresh 10s TTL —
    undercounting spend/quota until expiry (adversarial review P0).
    """
    with ledger.set_project_context("proj_A"):
        ledger.log_transaction("gemini-2.5-flash", "seed", 0.10, success=True)
    ledger._invalidate_row_cache()

    real_list = storage_ledger.list_keys
    gate = threading.Event()
    entered = threading.Event()
    lists = {"n": 0}

    def slow_list(prefix):
        lists["n"] += 1
        if lists["n"] == 1:
            entered.set()
            gate.wait(timeout=3.0)
        return real_list(prefix)

    monkeypatch.setattr(storage_ledger, "list_keys", slow_list)

    result_holder: list = []
    error_holder: list = []

    def reader():
        try:
            result_holder.append(ledger._read_storage_rows(force=True))
        except Exception as exc:  # pragma: no cover
            error_holder.append(exc)

    t = threading.Thread(target=reader)
    t.start()
    assert entered.wait(timeout=2.0)

    # Write lands while the first scan is still listing — invalidates + epoch++.
    with ledger.set_project_context("proj_A"):
        ledger.log_transaction("gemini-2.5-flash", "mid", 0.25, success=True)
    gate.set()
    t.join(timeout=5.0)

    assert error_holder == []
    # The in-flight snapshot may or may not have seen "mid" (list timing),
    # but the *cache* must not pin a pre-write view. A follow-up read must
    # include the new row in spend.
    spend = ledger.project_spend("proj_A")
    assert spend["total"] == pytest.approx(0.35), (
        "post-write spend must include the mid-flight row; "
        f"got {spend!r} after {lists['n']} list(s)"
    )
    rows = ledger.iter_transactions()
    hashes = {r["params_hash"] for r in rows}
    assert "mid" in hashes
    assert "seed" in hashes


def test_single_flight_waiter_does_not_serve_stale_on_leader_failure(
        storage_ledger, monkeypatch):
    """If the leader's fetch fails, waiters must not return expired cache.

    Install a snapshot, expire its TTL, then fail the refresh: waiters that
    blindly returned non-None cache would report old money while the leader
    raises (adversarial review P1).
    """
    with ledger.set_project_context("proj_A"):
        ledger.log_transaction("gemini-2.5-flash", "old", 0.10, success=True)
    # Populate cache with the seed row.
    assert len(ledger._read_storage_rows(force=True)) == 1
    # Expire TTL but leave the list in place (the bug condition).
    ledger._row_cache_at = time.monotonic() - ledger._ROW_CACHE_TTL_S - 1.0
    stale = list(ledger._row_cache)
    assert stale is not None

    real_list = storage_ledger.list_keys
    gate = threading.Event()
    entered = threading.Event()
    calls = {"n": 0}

    def failing_list(prefix):
        calls["n"] += 1
        entered.set()
        gate.wait(timeout=2.0)
        raise StorageError("bucket unreachable")

    monkeypatch.setattr(storage_ledger, "list_keys", failing_list)

    outcomes: list = []

    def reader():
        try:
            outcomes.append(("ok", ledger._read_storage_rows(force=True)))
        except Exception as exc:
            outcomes.append(("err", exc))

    t1 = threading.Thread(target=reader)
    t2 = threading.Thread(target=reader)
    t1.start()
    assert entered.wait(timeout=2.0)
    t2.start()
    time.sleep(0.05)
    gate.set()
    t1.join(timeout=5.0)
    t2.join(timeout=5.0)

    assert len(outcomes) == 2
    # Both must fail — neither may succeed with the expired snapshot.
    assert all(kind == "err" for kind, _ in outcomes), outcomes
    assert all(isinstance(exc, StorageError) for _, exc in outcomes)
    # Cache must not look fresh after a failed flight.
    now = time.monotonic()
    if ledger._row_cache is not None:
        assert (now - ledger._row_cache_at) >= ledger._ROW_CACHE_TTL_S


# -- unit 7.18 phase-2: write-through exact aggregates ----------------------

def test_project_spend_aggregate_matches_full_scan(storage_ledger):
    """Seeded process-local spend must equal a forced full scan."""
    with ledger.set_project_context("proj_A"):
        ledger.log_transaction("gemini-2.5-flash", "a1", 0.10, success=True)
        ledger.log_transaction("gemini-2.5-flash", "a2", 0.20, success=False,
                               error="429")
        ledger.log_transaction("eleven_multilingual_v2", "a3", 0.05,
                               success=True, cost_source="actual")
    with ledger.set_project_context("proj_B"):
        ledger.log_transaction("gemini-2.5-flash", "b1", 0.40, success=True)

    # Force re-seed from scan for a clean comparison.
    ledger.invalidate_project_spend_cache()
    from_agg = ledger.project_spend("proj_A")
    # Bypass aggregate: clear and scan again via internal scan helper.
    scanned = ledger._project_spend_scan("proj_A")
    assert from_agg["total"] == pytest.approx(scanned["total"])
    assert from_agg["estimated"] == pytest.approx(scanned["estimated"])
    assert from_agg["actual"] == pytest.approx(scanned["actual"])
    assert from_agg["by_endpoint"] == scanned["by_endpoint"]
    # Failed call still counted (0.20).
    assert from_agg["total"] == pytest.approx(0.35)
    assert from_agg["by_endpoint"]["gemini-2.5-flash"] == pytest.approx(0.30)
    assert from_agg["by_endpoint"]["eleven_multilingual_v2"] == pytest.approx(0.05)
    assert from_agg["actual"] == pytest.approx(0.05)
    assert from_agg["estimated"] == pytest.approx(0.30)


def test_project_spend_write_through_stays_exact_after_new_rows(storage_ledger):
    """After seed, further log_transaction increments must match a rescan."""
    with ledger.set_project_context("proj_A"):
        ledger.log_transaction("m", "h0", 0.10, success=True)
    # Seed via public API.
    assert ledger.project_spend("proj_A")["total"] == pytest.approx(0.10)
    with ledger.set_project_context("proj_A"):
        ledger.log_transaction("m", "h1", 0.07, success=True)
        ledger.log_transaction("n", "h2", 0.03, success=False, error="x")
    agg = ledger.project_spend("proj_A")
    scanned = ledger._project_spend_scan("proj_A")
    assert agg["total"] == pytest.approx(scanned["total"]) == pytest.approx(0.20)
    assert agg["by_endpoint"] == scanned["by_endpoint"]


def test_concurrent_write_and_spend_never_undercounts(storage_ledger):
    """Concurrent stems + readers must not undercount project spend."""
    n_writers = 8
    rows_each = 5

    def writer(wid: int):
        with ledger.set_project_context("proj_C"):
            for i in range(rows_each):
                ledger.log_transaction(
                    "gemini-2.5-flash", f"w{wid}-{i}", 0.01, success=True)

    def reader():
        # Touch the aggregate path repeatedly while writers run.
        for _ in range(20):
            ledger.project_spend("proj_C")

    threads = [threading.Thread(target=writer, args=(w,))
               for w in range(n_writers)]
    threads.append(threading.Thread(target=reader))
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10.0)

    expected = n_writers * rows_each * 0.01
    agg = ledger.project_spend("proj_C")
    scanned = ledger._project_spend_scan("proj_C")
    assert scanned["total"] == pytest.approx(expected)
    assert agg["total"] == pytest.approx(expected)
    assert agg["total"] == pytest.approx(scanned["total"])


def test_quota_aggregates_match_full_scan(storage_ledger, monkeypatch):
    """endpoint_call_counts after seed equals counting raw rows."""
    from datetime import datetime
    from foleyflow.quota import RESET_TZ, get_quota

    now = datetime.now(RESET_TZ)
    with ledger.set_project_context("proj_Q"):
        for i in range(6):
            ledger.log_transaction("gemini-2.5-flash", f"q{i}", 0.01,
                                   success=(i % 2 == 0))
        ledger.log_transaction("other-model", "ox", 0.01, success=True)

    ledger.invalidate_project_spend_cache()  # also drops quota aggregates
    q_agg = get_quota("gemini-2.5-flash", now=now)
    # Force scan path with explicit rows.
    rows = ledger.iter_transactions()
    q_scan = get_quota("gemini-2.5-flash", now=now, rows=rows)
    assert q_agg.per_day.used == q_scan.per_day.used == 6
    assert q_agg.per_minute.used == q_scan.per_minute.used


def test_quota_write_through_counts_new_row_without_rescan(storage_ledger):
    """A successful log_transaction updates quota aggregates in place."""
    from datetime import datetime
    from foleyflow.quota import RESET_TZ, get_quota

    now = datetime.now(RESET_TZ)
    with ledger.set_project_context("proj_Q"):
        ledger.log_transaction("gemini-2.5-flash", "q0", 0.01, success=True)
    assert get_quota("gemini-2.5-flash", now=now).per_day.used == 1
    # Invalidate only the row cache so the next read would re-list if aggregates
    # were wrong — write-through should still see the second call.
    ledger._invalidate_row_cache()
    with ledger.set_project_context("proj_Q"):
        ledger.log_transaction("gemini-2.5-flash", "q1", 0.01, success=False,
                               error="nope")
    assert get_quota("gemini-2.5-flash", now=now).per_day.used == 2


def test_attribution_rescue_invalidates_spend_aggregate(storage_ledger):
    """Adopted rows must appear in project_spend after rescue."""
    import time as _time
    old = _time.time() - 3600
    for i in range(3):
        row = {"endpoint": "gemini-2.5-flash", "params_hash": f"u{i}",
               "cost_estimate": 0.10, "success": True, "timestamp": old,
               "project_id": None, "cost_source": "estimate"}
        storage_ledger.put_bytes(
            f"{ledger.LEDGER_PREFIX}u{i}.json",
            json.dumps(row).encode())
    ledger._invalidate_row_cache()
    ledger.invalidate_project_spend_cache()

    assert ledger.project_spend("proj_A")["total"] == pytest.approx(0.0)
    assert ledger.record_attribution("proj_A", before=old + 10) == 3
    # Cache was dropped; reseed must include adopted rows.
    spend = ledger.project_spend("proj_A")
    assert spend["total"] == pytest.approx(0.30)


def test_spend_no_overcount_when_reseeded_during_write_gap(storage_ledger, monkeypatch):
    """Invalidate + reseed mid-write must not double-count the new row.

    log_transaction seeds, releases the project lock for the durable put,
    then increments. If project_spend reseeds from a post-write scan in that
    gap, a blind increment overcounts GET /budget (phase-2 adversarial P0).
    """
    with ledger.set_project_context("proj_A"):
        ledger.log_transaction("m", "seed", 0.10, success=True)
    assert ledger.project_spend("proj_A")["total"] == pytest.approx(0.10)

    real_put = storage_ledger.put_if_absent
    gap = threading.Event()
    in_put = threading.Event()

    def put_with_gap(key, data, content_type=None):
        in_put.set()
        # Hold the write so the concurrent reseed can install a post-write map.
        # First put the object so a scan in the gap *includes* this row.
        result = real_put(key, data, content_type=content_type)
        gap.wait(timeout=3.0)
        return result

    monkeypatch.setattr(storage_ledger, "put_if_absent", put_with_gap)

    errors: list = []

    def writer():
        try:
            with ledger.set_project_context("proj_A"):
                ledger.log_transaction("m", "mid", 0.50, success=True)
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    def reseed_in_gap():
        assert in_put.wait(timeout=2.0)
        # Drop the pre-write entry and install a scan that already has "mid".
        ledger.invalidate_project_spend_cache("proj_A")
        # project_spend will scan and install post-write totals.
        ledger.project_spend("proj_A")
        gap.set()

    t_w = threading.Thread(target=writer)
    t_r = threading.Thread(target=reseed_in_gap)
    t_w.start()
    t_r.start()
    t_w.join(timeout=5.0)
    t_r.join(timeout=5.0)

    assert errors == []
    agg = ledger.project_spend("proj_A")
    scanned = ledger._project_spend_scan("proj_A")
    assert scanned["total"] == pytest.approx(0.60)
    assert agg["total"] == pytest.approx(scanned["total"]), (
        f"overcount: agg={agg['total']} scan={scanned['total']}"
    )


def test_spend_no_overcount_after_seed_failure_then_concurrent_spend(
        storage_ledger, monkeypatch):
    """If pre-write seed fails, a gap reseed + post-write path must not add twice."""
    with ledger.set_project_context("proj_A"):
        ledger.log_transaction("m", "seed", 0.10, success=True)
    ledger.invalidate_project_spend_cache("proj_A")

    calls = {"n": 0}
    real_scan = ledger._project_spend_scan

    def flaky_seed_scan(project_id):
        calls["n"] += 1
        # First call is pre-write seed inside log_transaction — fail it.
        if calls["n"] == 1:
            raise StorageError("seed scan failed")
        return real_scan(project_id)

    monkeypatch.setattr(ledger, "_project_spend_scan", flaky_seed_scan)

    with ledger.set_project_context("proj_A"):
        ledger.log_transaction("m", "after", 0.40, success=True)

    # Restore real scan for the assertion path.
    monkeypatch.setattr(ledger, "_project_spend_scan", real_scan)
    agg = ledger.project_spend("proj_A")
    scanned = ledger._project_spend_scan("proj_A")
    assert scanned["total"] == pytest.approx(0.50)
    assert agg["total"] == pytest.approx(0.50)


# -- cross-process aggregate freshness (unit 7.18 review) --------------------
#
# The process-local aggregates are exact for rows THIS process wrote and blind
# to rows any other process wrote. Seeded once and never re-scanned, a
# long-lived API server reported its own writes forever: the CLI
# (`foleyflow analyze`, scripts/seed_*.sh) appends to the same bucket in its
# own process, so quota — the guard that stops a demo blowing the 20/day free
# tier — would read 20 remaining while the provider refused the next call.
# Spend had the same shape, which makes it a money figure that under-reports.

def _foreign_row(backend, name, *, endpoint, project_id=None, cost=0.01):
    """A row written by another process: straight to storage, no write-through."""
    import json as _json
    import time as _time
    backend.put_bytes(
        f"ledger/01FOREIGN{name}.json",
        _json.dumps({
            "endpoint": endpoint, "params_hash": name, "cost_estimate": cost,
            "cost_source": "estimate", "success": True, "error": None,
            "timestamp": _time.time(), "project_id": project_id,
        }).encode("utf-8"),
    )


def test_quota_aggregate_picks_up_another_process_after_ttl(
        storage_ledger, monkeypatch):
    model = "gemini-2.5-flash"
    monkeypatch.setattr(ledger, "_AGG_TTL_S", 0.0)   # always re-seed
    monkeypatch.setattr(ledger, "_ROW_CACHE_TTL_S", 0.0)

    from foleyflow import quota

    ledger.log_transaction(model, "own", 0.005, success=True)
    assert quota.get_quota(model).to_dict()["per_day"]["used"] == 1

    for i in range(4):
        _foreign_row(storage_ledger, f"q{i}", endpoint=model)

    ledger._invalidate_row_cache()
    assert quota.get_quota(model).to_dict()["per_day"]["used"] == 5


def test_project_spend_picks_up_another_process_after_ttl(
        storage_ledger, monkeypatch):
    monkeypatch.setattr(ledger, "_AGG_TTL_S", 0.0)
    monkeypatch.setattr(ledger, "_ROW_CACHE_TTL_S", 0.0)

    project = "proj_01CROSSPROC"
    with ledger.set_project_context(project):
        ledger.log_transaction("m", "own", 0.02, success=True)
    assert ledger.project_spend(project)["total"] == pytest.approx(0.02)

    for i in range(3):
        _foreign_row(storage_ledger, f"s{i}", endpoint="m",
                     project_id=project, cost=0.05)

    ledger._invalidate_row_cache()
    assert ledger.project_spend(project)["total"] == pytest.approx(0.17)


def test_write_through_stays_exact_within_the_ttl(storage_ledger, monkeypatch):
    """The TTL must not cost exactness: own writes still count immediately."""
    monkeypatch.setattr(ledger, "_AGG_TTL_S", 300.0)   # never re-seed here

    project = "proj_01EXACTNESS"
    with ledger.set_project_context(project):
        for i in range(5):
            ledger.log_transaction("m", f"h{i}", 0.01, success=True,
                                   cost_source="actual")
    spend = ledger.project_spend(project)
    assert spend["total"] == pytest.approx(0.05)
    assert spend["actual"] == pytest.approx(0.05)

    with ledger.set_project_context(project):
        ledger.log_transaction("m", "h5", 0.01, success=True)
    assert ledger.project_spend(project)["total"] == pytest.approx(0.06)


def test_a_stale_entry_without_a_stamp_is_refused(storage_ledger):
    """Entries predating the stamp are treated as stale, never trusted."""
    assert ledger._spend_fresh({"total": 1.0}) is False
    assert ledger._spend_fresh({"total": 1.0, "seeded_at": "nope"}) is False
    assert ledger._spend_fresh(
        {"total": 1.0, "seeded_at": time.monotonic()}) is True
