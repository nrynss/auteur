"""Per-call cost accounting.

Every provider invocation goes through :func:`execute_with_ledger`, which
records a row with endpoint, cost, success, and timestamp. Quota accounting
(:mod:`foleyflow.quota`) counts those rows; the CLI footer sums them.

## Where the rows live

Object storage has no append. A single `ledger.jsonl` rewritten on every call
would lose concurrent writes the same way a shared `events.jsonl` would — see
:mod:`foleyflow.projects` for the same problem and the same answer.

Default layout (shared storage backend — B2 in deploy, LocalBackend in dev)::

    ledger/{ulid}.json     one immutable object per transaction

`put_if_absent` is the write primitive: the object appears complete or not at
all, and two concurrent writers cannot clobber each other because each row has
its own ULID key. Sorted listing is chronological order for free.

## Local-file override

``FOLEYFLOW_LEDGER_PATH`` forces the historical single-file JSONL append. That
path is for offline tests and deliberate local overrides — it is **not** the
deploy path. A container with a read-only root and a ledger under ``/tmp``
loses every row on restart, which is exactly the failure this module was moved
off of.

## Write failures

``log_transaction`` still does not raise: a successful generation must not be
retried solely because the cost record failed (that would double-bill). Against
object storage a failed write is not a silent no-op either — the process marks
itself unwritable so :func:`ledger_writable` returns False and the API stops
presenting quota figures as real.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import hashlib
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Dict, List, Optional

from foleyflow.providers.config import get_defaults, get_model_spec

# Prefix under the shared storage backend. One object per transaction; the
# trailing slash keeps list_keys scoped to the ledger alone.
LEDGER_PREFIX = "ledger/"

# Rescue overrides: an immutable record claiming unattributed rows written
# before a moment in time for one project. Rows themselves are never rewritten
# (SPEC.md §10); this is how a project adopts work that ran without a context.
ATTR_PREFIX = f"{LEDGER_PREFIX}attr/"

# Probe key for ledger_writable(). Overwritten freely — put, not put_if_absent
# — so the probe does not accumulate objects and does not race with real rows.
_WRITABLE_PROBE_KEY = f"{LEDGER_PREFIX}_writable_probe"

# Historical single-file override. Set → file mode. Unset → storage backend.
# Kept as a module-level name because quota tests and older call sites import it.
LEDGER_PATH: Optional[str] = os.environ.get("FOLEYFLOW_LEDGER_PATH") or None

# Process-local: flipped False on a failed write so ledger_writable() does not
# need a second round-trip to discover that quota figures are fiction. A later
# successful write flips it back. In storage mode that pin is fail-closed and
# self-healing; file mode always re-probes the path (see ledger_writable).
_write_ok: bool = True

# Storage-mode probe cache for ledger_writable(). A real PUT on every /quota
# and /budget poll was measured at multi-second cost against B2 (unit 7.18).
# Short TTL is enough to collapse poll storms; failed log_transaction still
# pins False immediately via _set_write_ok(False).
_WRITABLE_CACHE_TTL_S = 15.0
_writable_cache: Optional[bool] = None
_writable_cache_at: float = 0.0
_writable_cache_lock = threading.Lock()

# The project a row belongs to. Set by the job runner around each run's work
# (see set_project_context); execute_with_ledger and log_transaction never see
# a project — the context does the attribution. None means unattributed: a
# direct `foleyflow render` writes global rows, and the CLI footer still sums
# them.
_current_project: ContextVar[Optional[str]] = ContextVar(
    "foleyflow_current_project", default=None)


@contextmanager
def set_project_context(project_id: Optional[str]):
    """Tag every ledger row written inside the block with `project_id`.

    The job runner wraps each run's work in this, so analysis, mix and repair
    rows are attributed to the project that triggered them without the deep
    provider seam knowing anything about projects. Nested contexts restore the
    outer value.
    """
    token = _current_project.set(project_id)
    try:
        yield
    finally:
        _current_project.reset(token)

# Short-lived snapshot of storage-backed rows. The ledger is append-only, so a
# cached listing is safe for a few seconds; without it every get_quota call
# re-lists and re-GETs every row, and get_all_quotas multiplies that by the
# number of quota-bearing models. Invalidated on every successful write.
#
# TTL raised from 2s → 10s for read-heavy GET /quota and /budget (unit 7.18):
# concurrent polls share one scan (single-flight below). A successful write
# bumps ``_row_cache_epoch`` so an in-flight scan that started *before* the
# write cannot reinstall a pre-write snapshot under a fresh TTL (that would
# undercount spend/quota for up to 10s). Tradeoff: continuous writes under a
# long scan can force a re-fetch; the money figures stay exact.
_ROW_CACHE_TTL_S = 10.0
_row_cache: Optional[List[dict]] = None
_row_cache_at: float = 0.0
_row_cache_epoch: int = 0
# Single-flight: concurrent _read_storage_rows callers share one in-flight
# list+multi-get rather than stampeding the backend.
_row_sf_lock = threading.Lock()
_row_sf_cv = threading.Condition(_row_sf_lock)
_row_sf_in_flight: bool = False

# Exact process-local aggregates (unit 7.18 phase-2).
#
# Per-project spend was first introduced for the alert path (task 9b): one seed
# scan per project per process, then O(1) increments so N concurrent stems do
# not each re-list the bucket. Phase-2 extends the same structure with
# ``by_endpoint`` and serves GET /budget from it once seeded — still exact
# (failed calls counted; attributions applied at seed), never an approximate
# sketch. Multi-instance: each process holds its own totals (same limitation
# as other process-local caches). Unlike the 10s row cache, spend/quota
# aggregates have no TTL: a process that only reads never re-lists until
# invalidate or restart. Single Cloud Run instance + in-process jobs is the
# deploy topology that makes this exact; multi-instance would need a shared
# memo or accept per-process seeds. A process that never wrote still seeds
# on first read from storage.
#
# Quota aggregates keep per-endpoint timestamps so daily/minute windows stay
# exact as wall-clock advances. Write-through on successful log_transaction;
# full scan seeds on first read when empty.
_spend_lock = threading.Lock()
# project_id -> Lock serialising seed/increment/alert claim for that project
_project_locks: Dict[str, threading.Lock] = {}
# project_id -> {"total", "estimated", "actual", "by_endpoint": {endpoint: float}}
_running_spend: Dict[str, Dict[str, Any]] = {}
# project_id -> (budget_usd, generation_event_id, monotonic_time_loaded)
_budget_cache: Dict[str, tuple] = {}
_BUDGET_CACHE_TTL_S = 2.0
# In-process claim set so B2's non-atomic put_if_absent cannot double-fire
# alerts under same-process fan-out. Key: (project_id, generation, threshold).
_alert_claims: set = set()
_alert_claim_lock = threading.Lock()

# Both process-local aggregates below are exact for rows THIS process wrote,
# and blind to rows any other process wrote. That matters: the CLI
# (`foleyflow analyze`, `foleyflow render`, scripts/seed_*.sh) appends to the
# same bucket in its own process, so a long-lived API server seeded once would
# report its own writes forever and never see theirs. Quota is precisely the
# guard that stops a demo blowing the 20/day Gemini free tier, and spend is
# money, so neither may drift without bound.
#
# So the aggregates are re-seeded from a full scan once they age past this TTL.
# Warm reads stay sub-millisecond in between; one scan per TTL is nothing
# against the multi-second per-request scans this replaced. Staleness is now
# bounded by TTL + the row cache's own TTL, matching the row cache's contract
# rather than lasting until process restart.
_AGG_TTL_S = 30.0

# Quota timestamp aggregates: endpoint -> list of call timestamps (all outcomes).
_quota_agg_lock = threading.Lock()
_quota_ts: Dict[str, List[float]] = {}
_quota_agg_seeded: bool = False
_quota_agg_at: float = 0.0
_quota_agg_epoch: int = 0


def _file_ledger_path(path: Optional[str] = None) -> Optional[str]:
    """Resolved local-file path, or None when the storage backend is in use."""
    if path is not None:
        return path
    return LEDGER_PATH


def uses_storage_ledger() -> bool:
    """True when rows go to the shared backend rather than a local JSONL file."""
    return _file_ledger_path() is None


def _set_write_ok(ok: bool) -> None:
    """Pin process writability and keep the probe cache consistent.

    False is fail-closed: the next ledger_writable() returns False without a
    PUT, so /quota stops presenting fiction immediately. True is set after a
    successful log_transaction and also refreshes the positive probe cache.
    """
    global _write_ok, _writable_cache, _writable_cache_at
    _write_ok = ok
    with _writable_cache_lock:
        _writable_cache = ok
        _writable_cache_at = time.monotonic()


def _invalidate_row_cache() -> None:
    """Drop the row snapshot and bump the epoch so in-flight scans discard."""
    global _row_cache, _row_cache_at, _row_cache_epoch
    with _row_sf_lock:
        _row_cache = None
        _row_cache_at = 0.0
        _row_cache_epoch += 1


def _invalidate_writable_cache() -> None:
    """Drop the storage-mode probe result (tests / deliberate re-probe)."""
    global _writable_cache, _writable_cache_at
    with _writable_cache_lock:
        _writable_cache = None
        _writable_cache_at = 0.0


def invalidate_ledger_aggregates() -> None:
    """Drop spend + quota write-through aggregates (tests / backend rewire).

    Prefer :func:`invalidate_project_spend_cache` for a single project after
    attribution rescue. This clears everything, including quota timestamps.
    """
    global _quota_agg_seeded, _quota_agg_epoch
    with _spend_lock:
        _running_spend.clear()
        _alert_claims.clear()
        _budget_cache.clear()
    with _quota_agg_lock:
        _quota_ts.clear()
        _quota_agg_seeded = False
        _quota_agg_epoch += 1


def _quota_note_transaction(endpoint: str, timestamp: float) -> None:
    """Write-through one row into the quota aggregate (storage mode).

    When the aggregate is not yet seeded, bump the epoch so any in-flight seed
    discards its pre-write snapshot and re-scans (includes this row).
    """
    global _quota_agg_epoch
    with _quota_agg_lock:
        # Always bump: a concurrent seed OR re-seed that started before this
        # write must discard its snapshot rather than install one missing it.
        _quota_agg_epoch += 1
        if _quota_agg_seeded:
            _quota_ts.setdefault(endpoint, []).append(float(timestamp))


def _ensure_quota_agg_seeded() -> None:
    """Seed per-endpoint timestamps from a full ledger scan.

    Re-seeds once the aggregate ages past ``_AGG_TTL_S`` so rows written by
    another process (the CLI writes to the same bucket) become visible. A
    write landing mid-scan bumps the epoch and the scan is retried; under
    continuous writes an already-seeded aggregate keeps its exact
    write-through value rather than spinning, and tries again next TTL.
    """
    global _quota_agg_seeded, _quota_ts, _quota_agg_at

    if _file_ledger_path() is not None:
        return

    attempts = 0
    while True:
        attempts += 1
        with _quota_agg_lock:
            fresh = (_quota_agg_seeded
                     and (time.monotonic() - _quota_agg_at) < _AGG_TTL_S)
            if fresh:
                return
            # Contended re-seed: keep the exact write-through aggregate we
            # already have and defer. Only reachable when already seeded, so
            # nothing is missing that this process wrote.
            if _quota_agg_seeded and attempts > 3:
                _quota_agg_at = time.monotonic()
                return
            epoch_at_start = _quota_agg_epoch

        rows = iter_transactions()
        built: Dict[str, List[float]] = {}
        for row in rows:
            ep = row.get("endpoint") or "unknown"
            ts = row.get("timestamp")
            if isinstance(ts, (int, float)):
                built.setdefault(ep, []).append(float(ts))

        with _quota_agg_lock:
            if _quota_agg_epoch != epoch_at_start:
                # Write landed mid-seed; retry so the map includes new rows.
                continue
            _quota_ts = built
            _quota_agg_seeded = True
            _quota_agg_at = time.monotonic()
            return


def endpoint_call_counts(
    endpoint: str,
    *,
    minute_ago: float,
    day_start: float,
    rows: Optional[List[dict]] = None,
    ledger_path: Optional[str] = None,
) -> tuple:
    """``(per_minute, per_day)`` call counts for one endpoint.

    Failed calls are included (same rule as :func:`foleyflow.quota.get_quota`).
    ``rows`` / ``ledger_path`` force a scan path (unit tests); storage mode
    otherwise answers from write-through aggregates after one seed scan.
    """
    if rows is not None or ledger_path is not None or _file_ledger_path() is not None:
        per_minute = per_day = 0
        for row in (rows if rows is not None else iter_transactions(ledger_path)):
            if row.get("endpoint") != endpoint:
                continue
            ts = row.get("timestamp")
            if not isinstance(ts, (int, float)):
                continue
            if ts >= day_start:
                per_day += 1
            if ts >= minute_ago:
                per_minute += 1
        return per_minute, per_day

    _ensure_quota_agg_seeded()
    with _quota_agg_lock:
        stamps = list(_quota_ts.get(endpoint, ()))
    per_minute = per_day = 0
    for ts in stamps:
        if ts >= day_start:
            per_day += 1
        if ts >= minute_ago:
            per_minute += 1
    return per_minute, per_day


def get_params_hash(arguments: Dict[str, Any]) -> str:
    """Generate a stable hash for input arguments."""
    serialized = json.dumps(arguments, sort_keys=True, default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _resolve_duration(spec, arguments: Dict[str, Any]) -> Optional[float]:
    """Find the duration in `arguments` for a per-second-priced model.

    The argument's field name is per-model config (`duration_field`), so this
    cannot assume "duration": ElevenLabs calls it `duration_seconds`. Looking
    only for the literal "duration" would miss it and price the call at $0.
    Falls back to the literal name for models that declare no duration_field.
    """
    for key in (spec.duration_field, "duration"):
        if key and key in arguments:
            try:
                value = float(arguments[key])
            except (TypeError, ValueError):
                continue
            if value > 0:
                return value
    return spec.default_duration_s


def estimate_cost(endpoint: str, arguments: Dict[str, Any]) -> float:
    """Estimate cost for an API call based on endpoint and params.

    Reads pricing from providers.toml. If the model declares
    ``cost_per_second_usd``, cost is ``duration * rate``; otherwise
    ``cost_per_call_usd``. Falls back to ``defaults.cost_per_call_usd``
    for unknown endpoints.

    A per-second model whose duration cannot be resolved warns and falls back
    rather than returning 0 — this figure guards spend and is shown to users
    before a billable regeneration (SPEC.md §9.4), so silently reporting $0
    is the one outcome worse than reporting the wrong number.
    """
    default_cost = get_defaults().get("cost_per_call_usd", 0.01)

    try:
        spec = get_model_spec(endpoint)
    except KeyError:
        return default_cost

    if spec.cost_per_second_usd is not None:
        duration = _resolve_duration(spec, arguments)
        if duration is not None:
            return duration * spec.cost_per_second_usd
        fallback = spec.cost_per_call_usd or default_cost
        print(f"[ledger] WARNING: {endpoint} is priced per second but the call "
              f"carries no usable duration (looked for "
              f"{spec.duration_field or 'duration'!r}) and declares no "
              f"default_duration_s — estimating ${fallback:.4f} instead of $0.")
        return fallback

    if spec.cost_per_call_usd is not None:
        return spec.cost_per_call_usd

    return default_cost


def _append_file(path: str, record: dict) -> None:
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


def _append_storage(record: dict) -> None:
    """One immutable object per row — the projects event-log pattern.

    ``put_if_absent`` is the atomic create: either the object is fully written
    or it is absent. ULID keys mean concurrent writers never share an address,
    so a PreconditionFailed here is a vanishingly rare id collision and we
    retry rather than drop the row.
    """
    from foleyflow.ids import new_ulid
    from foleyflow.storage import PreconditionFailed, StorageError, get_backend

    backend = get_backend()
    body = json.dumps(record, separators=(",", ":")).encode("utf-8")
    last_err: Optional[Exception] = None
    for _ in range(3):
        key = f"{LEDGER_PREFIX}{new_ulid()}.json"
        try:
            backend.put_if_absent(key, body, content_type="application/json")
            return
        except PreconditionFailed as exc:
            last_err = exc
            continue
        except StorageError:
            raise
    raise StorageError(
        f"could not allocate a unique ledger key after retries: {last_err}"
    )


def log_transaction(endpoint: str, params_hash: str, cost: float, success: bool,
                    error: Optional[str] = None, cost_source: str = "estimate"):
    """Record one provider call.

    `cost_estimate` keeps its historical name for backward compatibility with
    existing rows; `cost_source` says whether the value is "actual" (the
    response carried a cost field) or an "estimate" from COST_ESTIMATES.

    Never raises: a successful generation must not be retried solely because
    the cost record failed. A failed storage write is not silent either — the
    process is marked unwritable so quota figures stop being presented as real.

    After a successful append attributed to a project, advisory budget
    thresholds are checked (task 9). That path also never raises — an
    alerting bug must not become a render bug.
    """
    project_id = _current_project.get()
    timestamp = time.time()
    record = {
        "endpoint": endpoint,
        "params_hash": params_hash,
        "cost_estimate": cost,
        "cost_source": cost_source,
        "success": success,
        "error": error,
        "timestamp": timestamp,
        # Present-but-None when unattributed (a direct render): project_spend
        # filters on this key, and old rows without it read as None too.
        "project_id": project_id,
    }
    # Seed *before* the write (scan excludes this row); increment after only
    # when the same entry object is still installed. If a concurrent
    # project_spend / attribution rescue reseeded from a post-write scan in
    # the unlocked gap, that map already includes this row — incrementing
    # would double-count GET /budget (phase-2 adversarial P0).
    plock = _project_lock(project_id) if project_id else None
    seed_entry_id: Optional[int] = None
    if plock is not None:
        try:
            with plock:
                _ensure_running_spend_seeded(project_id)
                entry = _running_spend.get(project_id)
                if entry is not None and "by_endpoint" in entry:
                    seed_entry_id = id(entry)
        except Exception as exc:
            print(f"[ledger] WARNING: budget spend seed failed for "
                  f"{project_id}: {type(exc).__name__}: {exc}",
                  file=sys.stderr, flush=True)
    try:
        path = _file_ledger_path()
        if path is not None:
            _append_file(path, record)
        else:
            _append_storage(record)
        _set_write_ok(True)
        # New row is on the backend; drop any snapshot so the next read sees it.
        _invalidate_row_cache()
        # Write-through exact aggregates (storage mode). File mode has no
        # process-local spend cache for the alert path either.
        if path is None:
            _quota_note_transaction(endpoint, timestamp)
    except Exception as e:
        _set_write_ok(False)
        print(f"[ledger] WARNING: failed to record transaction for "
              f"{endpoint}: {type(e).__name__}: {e}",
              file=sys.stderr, flush=True)
        return

    if plock is not None and project_id:
        try:
            with plock:
                spend = _running_spend_after(
                    project_id, cost, cost_source, endpoint=endpoint,
                    seed_entry_id=seed_entry_id)
                _maybe_emit_budget_alerts(project_id, spend=spend)
        except Exception as exc:
            print(f"[ledger] WARNING: budget alert check failed for "
                  f"{project_id}: {type(exc).__name__}: {exc}",
                  file=sys.stderr, flush=True)


def get_actual_cost(result: Any) -> Optional[float]:
    """Inspect response payload for cost or billing metadata."""
    if isinstance(result, dict):
        # Look for explicit cost/billing fields returned by openrouter or other models
        if "cost" in result:
            try:
                return float(result["cost"])
            except (ValueError, TypeError):
                pass
        if "billing" in result and isinstance(result["billing"], dict):
            try:
                return float(result["billing"].get("cost", 0.0))
            except (ValueError, TypeError):
                pass
    return None


def execute_with_ledger(endpoint: str, arguments: Dict[str, Any],
                        method: str = "subscribe", adapter=None, **kwargs) -> Any:
    """Wrap provider calls to log their cost and inputs.

    ``adapter`` is an optional Adapter instance; when omitted, the adapter
    configured for this endpoint in providers.toml is used. method can be
    'subscribe', 'run', or 'submit'.
    """
    params_hash = get_params_hash(arguments)
    estimated = estimate_cost(endpoint, arguments)

    # Resolve adapter
    if adapter is None:
        from foleyflow.providers import get_adapter
        try:
            spec = get_model_spec(endpoint)
            adapter = get_adapter(spec.adapter)
        except KeyError:
            # Fallback for unknown endpoints: use fal
            from foleyflow.providers import get_adapter
            adapter = get_adapter("fal")
            from foleyflow.providers.base import ModelSpec
            spec = ModelSpec(model_id=endpoint, adapter="fal", role="unknown")
    else:
        try:
            spec = get_model_spec(endpoint)
        except KeyError:
            from foleyflow.providers.base import ModelSpec
            spec = ModelSpec(model_id=endpoint, adapter="fal", role="unknown")

    try:
        result = adapter.generate(spec, arguments, method=method, **kwargs)

        if method == "submit":
            # submit returns a handle — log estimate at creation time
            log_transaction(endpoint, params_hash, estimated, success=True)
            return result.raw_response

        # Check if actual cost is available in response
        actual_cost = result.actual_cost_usd
        if actual_cost is None:
            actual_cost = get_actual_cost(result.raw_response)
        final_cost = actual_cost if actual_cost is not None else estimated
        source = "actual" if actual_cost is not None else "estimate"

        log_transaction(endpoint, params_hash, final_cost, success=True, cost_source=source)
        return result.raw_response
    except Exception as e:
        log_transaction(endpoint, params_hash, estimated, success=False, error=str(e))
        raise e


def _read_file_rows(path: str) -> List[dict]:
    """JSONL rows, oldest first. A truncated final line is skipped rather than
    raising — the file is append-only and may be read mid-write."""
    if not os.path.exists(path):
        return []
    rows: List[dict] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def _is_transaction_key(key: str) -> bool:
    """Skip probe objects, attribution overrides, and anything that is not a
    ledger row."""
    name = key.rsplit("/", 1)[-1]
    return (name.endswith(".json") and not name.startswith("_")
            and not key.startswith(ATTR_PREFIX))


def _fetch_storage_rows() -> List[dict]:
    """List ledger keys and multi-get their bodies (no cache).

    Concurrent GETs: serial per-row downloads dominated GET /quota and
    /budget against B2 (unit 7.18). ``skip_missing`` matches the old loop —
    a row may vanish between list and get; other storage faults propagate.
    """
    from foleyflow.storage import StorageError, get_backend, get_bytes_many

    backend = get_backend()
    try:
        keys = backend.list_keys(LEDGER_PREFIX)
    except StorageError:
        raise
    tx_keys = [k for k in keys if _is_transaction_key(k)]
    if not tx_keys:
        return []
    blobs = get_bytes_many(backend, tx_keys, skip_missing=True)
    rows: List[dict] = []
    for key in tx_keys:
        raw = blobs.get(key)
        if raw is None:
            continue
        try:
            rows.append(json.loads(raw))
        except (TypeError, json.JSONDecodeError):
            continue
    return rows


def _read_storage_rows(*, force: bool = False) -> List[dict]:
    """Every stored transaction, oldest first (ULID keys sort by creation).

    A short process-local cache absorbs the repeated full scans that
    ``get_all_quotas`` and before/after ``summarize_ledger`` pairs would
    otherwise pay against object storage. Append-only semantics make the
    snapshot safe; successful writes invalidate it.

    Concurrent callers share one in-flight scan (single-flight) so a poll
    storm does not multiply list+GET traffic against the bucket.

    Correctness under concurrency (money surfaces):
    * Invalidate bumps an epoch; a flight only installs the cache when the
      epoch is unchanged — a write mid-scan cannot reinstall a pre-write
      snapshot with a fresh TTL.
    * Waiters only return a *fresh* cache after wait (TTL still valid). A
      leader that fails leaves waiters to re-lead, not to serve expired
      data as success while the leader 503s.
    """
    global _row_cache, _row_cache_at, _row_sf_in_flight

    while True:
        with _row_sf_cv:
            while True:
                now = time.monotonic()
                cache_fresh = (
                    _row_cache is not None
                    and (now - _row_cache_at) < _ROW_CACHE_TTL_S
                )
                # Cold hit: only non-force callers. force=True re-scans when
                # it becomes leader, but a waiter that shared an in-flight
                # force scan still accepts the installed result below.
                if cache_fresh and not force:
                    return _row_cache
                if not _row_sf_in_flight:
                    _row_sf_in_flight = True
                    epoch_at_start = _row_cache_epoch
                    break
                _row_sf_cv.wait(timeout=60.0)
                # Shared flight finished (or timed out). Accept only a
                # still-fresh install — never a non-None expired snapshot.
                now = time.monotonic()
                if (_row_cache is not None
                        and (now - _row_cache_at) < _ROW_CACHE_TTL_S):
                    return _row_cache

        try:
            rows = _fetch_storage_rows()
            with _row_sf_cv:
                if _row_cache_epoch == epoch_at_start:
                    _row_cache = rows
                    _row_cache_at = time.monotonic()
                    return rows
                # Write landed during the scan: discard. Outer loop re-leads
                # so the answer includes the new row rather than caching a lie.
        finally:
            with _row_sf_cv:
                _row_sf_in_flight = False
                _row_sf_cv.notify_all()



def iter_transactions(path: Optional[str] = None) -> List[dict]:
    """All ledger rows, oldest first.

    ``path`` forces a local JSONL file (used by quota unit tests that build a
    private ledger). With no path, reads the configured default: the file named
    by ``FOLEYFLOW_LEDGER_PATH`` if set, otherwise the storage backend.
    """
    file_path = _file_ledger_path(path)
    if file_path is not None:
        return _read_file_rows(file_path)
    return _read_storage_rows()


def ledger_writable(path: Optional[str] = None) -> bool:
    """Whether quota counting can actually observe new rows.

    False means every figure is fiction: writes are not landing, so usage
    always reads whatever was already stored (often zero) no matter how much
    budget is spent. The API surfaces this so a UI stops presenting the numbers.

    A recent failed ``log_transaction`` short-circuits to False without another
    round-trip — that is the non-silent part of a swallowed write failure.
    That pin is storage-mode only and clears on the next successful write; file
    mode always re-probes the path, so the two modes are not symmetric.

    In storage mode the probe result is cached briefly (see
    ``_WRITABLE_CACHE_TTL_S``): /quota and /budget used to PUT a probe object
    on every request, which dominated latency against B2. Cache does not
    change the contract — figures are still fiction when unwritable.
    """
    if not _write_ok and path is None and _file_ledger_path() is None:
        return False

    file_path = _file_ledger_path(path)
    if file_path is not None:
        try:
            directory = os.path.dirname(file_path) or "."
            os.makedirs(directory, exist_ok=True)
            with open(file_path, "a", encoding="utf-8"):
                return True
        except OSError:
            return False

    # Storage mode: serve a recent probe result rather than PUT every time.
    global _writable_cache, _writable_cache_at
    with _writable_cache_lock:
        now = time.monotonic()
        if (_writable_cache is not None
                and (now - _writable_cache_at) < _WRITABLE_CACHE_TTL_S):
            return _writable_cache

    try:
        from foleyflow.storage import get_backend
        get_backend().put_bytes(
            _WRITABLE_PROBE_KEY, b"ok", content_type="text/plain")
        result = True
    except Exception:
        result = False

    with _writable_cache_lock:
        _writable_cache = result
        _writable_cache_at = time.monotonic()
    return result


def summarize_ledger(path: Optional[str] = None) -> Dict[str, Any]:
    """Sum costs of successful calls: grand total + per-endpoint totals.
    Powers the CLI exit footer (T4). Tolerates old rows without cost_source."""
    total = 0.0
    by_endpoint: Dict[str, float] = {}
    for row in iter_transactions(path):
        if not row.get("success"):
            continue
        cost = float(row.get("cost_estimate") or 0.0)
        endpoint = row.get("endpoint", "unknown")
        total += cost
        by_endpoint[endpoint] = by_endpoint.get(endpoint, 0.0) + cost
    return {"total": total, "by_endpoint": by_endpoint}


def _project_spend_scan(project_id: str) -> Dict[str, Any]:
    """Full-ledger scan for one project's spend (source of truth for seeding)."""
    total = 0.0
    estimated = 0.0
    actual = 0.0
    by_endpoint: Dict[str, float] = {}
    attributions = iter_attributions()
    for row in iter_transactions():
        owner = row.get("project_id")
        if owner is None:
            owner = _attribution_owner(row, attributions)
        if owner != project_id:
            continue
        cost = float(row.get("cost_estimate") or 0.0)
        endpoint = row.get("endpoint", "unknown")
        total += cost
        by_endpoint[endpoint] = by_endpoint.get(endpoint, 0.0) + cost
        # Old rows without cost_source read as estimate — the historical default.
        if row.get("cost_source") == "actual":
            actual += cost
        else:
            estimated += cost
    return {"total": total, "by_endpoint": by_endpoint,
            "estimated": estimated, "actual": actual}


def _spend_entry_to_result(entry: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "total": float(entry["total"]),
        "estimated": float(entry["estimated"]),
        "actual": float(entry["actual"]),
        "by_endpoint": dict(entry.get("by_endpoint") or {}),
    }


def _install_running_spend(project_id: str, spend: Dict[str, Any]) -> Dict[str, Any]:
    """Install a scan result into the process-local exact aggregate."""
    entry = {
        "total": float(spend["total"]),
        "estimated": float(spend["estimated"]),
        "actual": float(spend["actual"]),
        "by_endpoint": dict(spend.get("by_endpoint") or {}),
        # When this snapshot was taken. Write-through keeps it exact for rows
        # THIS process wrote; the stamp bounds how long another process's rows
        # can stay invisible (see _AGG_TTL_S).
        "seeded_at": time.monotonic(),
    }
    _running_spend[project_id] = entry
    return _spend_entry_to_result(entry)


def project_spend(project_id: str) -> Dict[str, Any]:
    """Spend attributed to one project: total + per-endpoint + cost_source split.

    Failed calls are counted, the same rule quota uses — a request that failed
    may still have consumed provider quota. Rows written before attribution
    existed, and rows from a direct (project-less) render, carry no
    `project_id`; those are claimed by a rescue override (see
    :func:`record_attribution`) when one covers their timestamp, else they
    belong to no project.

    ``estimated`` / ``actual`` sum the portions whose ``cost_source`` is
    estimate vs actual — budget alerts carry both so "$4.90 spent, $3.10 of
    it estimated" is a different claim from all-actual.

    Storage mode answers from the process-local exact aggregate when seeded
    (write-through on :func:`log_transaction`); otherwise one full scan seeds
    it. Semantics match a full scan. File-mode ledger always scans.
    """
    if _file_ledger_path() is not None:
        return _project_spend_scan(project_id)

    plock = _project_lock(project_id)
    with plock:
        entry = _running_spend.get(project_id)
        if entry is not None and "by_endpoint" in entry and _spend_fresh(entry):
            return _spend_entry_to_result(entry)

    # Seed outside the short critical path of concurrent readers that already
    # have an entry; first seeder does the scan under the project lock so two
    # concurrent cold reads cannot double-install from racing scans that each
    # miss a concurrent write-through (writes also hold this lock).
    with plock:
        entry = _running_spend.get(project_id)
        if entry is not None and "by_endpoint" in entry and _spend_fresh(entry):
            return _spend_entry_to_result(entry)
        # Cold, or aged past the TTL: re-scan so rows another process wrote
        # against the same bucket are counted. The scan happens under the
        # project lock, which write-through also takes, so a concurrent write
        # cannot be lost between the scan and the install.
        spend = _project_spend_scan(project_id)
        return _install_running_spend(project_id, spend)


def _spend_fresh(entry: Dict[str, Any]) -> bool:
    """Whether a running-spend entry is young enough to answer from.

    An entry with no stamp predates the TTL and is treated as stale, so a
    process that has been up a while re-scans once rather than trusting a
    snapshot of unknown age.
    """
    seeded_at = entry.get("seeded_at")
    if not isinstance(seeded_at, (int, float)):
        return False
    return (time.monotonic() - seeded_at) < _AGG_TTL_S


def _project_lock(project_id: str) -> threading.Lock:
    """Per-project lock for seed + increment + in-process alert claim."""
    with _spend_lock:
        lock = _project_locks.get(project_id)
        if lock is None:
            lock = threading.Lock()
            _project_locks[project_id] = lock
        return lock


def invalidate_project_spend_cache(project_id: Optional[str] = None) -> None:
    """Drop running-spend cache (one project, or all).

    Call after an attribution rescue adopts rows the running total never
    saw, or from tests that rewire the ledger backend. When ``project_id``
    is None also drops quota timestamp aggregates (full backend rewire).
    """
    global _quota_agg_seeded, _quota_agg_epoch
    with _spend_lock:
        if project_id is None:
            _running_spend.clear()
            _alert_claims.clear()
        else:
            _running_spend.pop(project_id, None)
            _alert_claims.difference_update(
                {c for c in _alert_claims if c[0] == project_id})
    if project_id is None:
        with _quota_agg_lock:
            _quota_ts.clear()
            _quota_agg_seeded = False
            _quota_agg_epoch += 1


def invalidate_budget_cache(project_id: Optional[str] = None) -> None:
    """Drop the short-lived advisory-budget cache after PUT /budget."""
    with _spend_lock:
        if project_id is None:
            _budget_cache.clear()
        else:
            _budget_cache.pop(project_id, None)


def _ensure_running_spend_seeded(project_id: str) -> None:
    """One full project spend scan per project per process, before the write.

    Caller holds the per-project lock. The scan must run *before* the new row
    is written so it is not double-counted when we add ``cost``.
    """
    entry = _running_spend.get(project_id)
    if entry is not None and "by_endpoint" in entry:
        return
    spend = _project_spend_scan(project_id)
    if project_id not in _running_spend or "by_endpoint" not in _running_spend.get(
            project_id, {}):
        _install_running_spend(project_id, spend)


def _running_spend_after(project_id: str, cost: float,
                         cost_source: str, *,
                         endpoint: str = "unknown",
                         seed_entry_id: Optional[int] = None) -> Dict[str, float]:
    """Add this row's cost to the running total. Caller holds the project lock.

    Increment only when ``seed_entry_id`` still names the installed entry —
    that object was seeded *before* the write and does not yet include
    ``cost``. If the map was cleared or replaced (attribution rescue,
    concurrent reseed after the durable write, failed pre-write seed), a
    post-write full scan already includes this row; install without adding.
    Never installs a zero baseline that would permanently undercount.
    """
    entry = _running_spend.get(project_id)
    if (entry is not None and "by_endpoint" in entry
            and seed_entry_id is not None and id(entry) == seed_entry_id):
        entry["total"] += cost
        if cost_source == "actual":
            entry["actual"] += cost
        else:
            entry["estimated"] += cost
        by_ep = entry.setdefault("by_endpoint", {})
        by_ep[endpoint] = float(by_ep.get(endpoint, 0.0)) + cost
        return _spend_entry_to_result(entry)

    # Seed missing, replaced, or never landed. Scan includes this row.
    spend = _project_spend_scan(project_id)
    return _install_running_spend(project_id, spend)


def _cached_budget(project_id: str) -> tuple:
    """``(budget_usd, generation_event_id)``, process-cached briefly.

    Generation is the latest ``budget.set`` event id; alert marker keys
    include it so clear→re-set the same dollar amount can alert again.
    """
    now = time.monotonic()
    with _spend_lock:
        cached = _budget_cache.get(project_id)
        if cached is not None and (now - cached[2]) < _BUDGET_CACHE_TTL_S:
            return cached[0], cached[1]
    try:
        from foleyflow.projects import ProjectStore
        from foleyflow.storage import get_backend
        state = ProjectStore(get_backend()).get_state(project_id)
        budget = state.budget_usd
        generation = state.budget_generation or "none"
    except Exception:
        return None, "none"
    with _spend_lock:
        _budget_cache[project_id] = (budget, generation, now)
    return budget, generation


def evaluate_budget_alerts(project_id: str, *,
                           reason: str = "spend") -> None:
    """Evaluate thresholds against a full ``project_spend`` scan.

    Used by ``PUT /budget`` (task 17): the API process may never have seeded
    the running total for this project, so the hot-path cache would read
    zero. One accurate scan is the right cost for a metadata route.

    Never raises — an alerting bug must not fail a durable budget.set.
    """
    try:
        if _file_ledger_path() is not None:
            return
        spend = project_spend(project_id)
        _maybe_emit_budget_alerts(project_id, spend=spend, reason=reason)
    except Exception as exc:
        print(f"[ledger] WARNING: budget alert evaluation failed for "
              f"{project_id}: {type(exc).__name__}: {exc}",
              file=sys.stderr, flush=True)


def _maybe_emit_budget_alerts(project_id: str, *, spend: Dict[str, float],
                              reason: str = "spend") -> None:
    """Emit lifecycle alerts for newly crossed thresholds.

    On the ledger append path the caller holds the per-project lock and has
    already updated running spend. ``PUT /budget`` calls via
    :func:`evaluate_budget_alerts` with a full scan instead.

    ``reason`` is ``"spend"`` (crossed by a billed row) or ``"budget_set"``
    (figure lowered beneath existing spend) — same event, different sentence
    for the History view.

    Idempotent under concurrent fan-out: in-process claim set plus
    ``put_if_absent`` marker keyed by (project, generation, threshold).
    Never raises — see ``log_transaction``.
    """
    # File-mode ledger has no project event log on the same backend; skip.
    if _file_ledger_path() is not None:
        return

    from foleyflow.projects import (
        EVENT_BUDGET_ALERT,
        ProjectStore,
    )
    from foleyflow.providers.config import get_budget_thresholds
    from foleyflow.storage import PreconditionFailed, get_backend

    budget, generation = _cached_budget(project_id)
    if budget is None or budget <= 0:
        return

    total = float(spend["total"])
    thresholds = get_budget_thresholds()
    # Fast path: nothing crossed yet — no storage round-trips.
    if all(total < budget * t for t in thresholds):
        return

    backend = get_backend()
    store = ProjectStore(backend)
    for threshold in thresholds:
        if total < budget * threshold:
            continue
        claim = (project_id, generation, float(threshold))
        with _alert_claim_lock:
            if claim in _alert_claims:
                continue
            # Claim in-process first so same-process fan-out cannot double-fire
            # even when B2 put_if_absent is exists-then-put.
            _alert_claims.add(claim)
        key = store.budget_alert_key(
            project_id, budget, threshold, generation=generation)
        marker = {
            "project_id": project_id,
            "budget_usd": budget,
            "threshold": threshold,
            "generation": generation,
            "spent": round(total, 4),
            "spent_estimated": round(float(spend["estimated"]), 4),
            "spent_actual": round(float(spend["actual"]), 4),
            "reason": reason,
            "created_at": time.time(),
        }
        body = json.dumps(marker, separators=(",", ":")).encode("utf-8")
        try:
            backend.put_if_absent(key, body, content_type="application/json")
        except PreconditionFailed:
            # Another process already announced this threshold.
            continue
        except Exception as exc:
            # Release in-process claim so a later stem can retry.
            with _alert_claim_lock:
                _alert_claims.discard(claim)
            print(f"[ledger] WARNING: budget alert marker failed for "
                  f"{project_id} @{threshold}: {type(exc).__name__}: {exc}",
                  file=sys.stderr, flush=True)
            continue
        # Marker landed — emit the user-visible lifecycle event. If this
        # fails the marker still blocks re-fire (split-brain); log loudly.
        try:
            store.append_event(
                project_id, EVENT_BUDGET_ALERT, actor="system",
                payload={
                    "threshold": threshold,
                    "budget_usd": budget,
                    "generation": generation,
                    "spent": round(total, 4),
                    "spent_estimated": round(float(spend["estimated"]), 4),
                    "spent_actual": round(float(spend["actual"]), 4),
                    "fraction": round(total / budget, 4) if budget else None,
                    "reason": reason,
                })
        except Exception as exc:
            print(f"[ledger] WARNING: budget alert event failed for "
                  f"{project_id} @{threshold} (marker kept): "
                  f"{type(exc).__name__}: {exc}",
                  file=sys.stderr, flush=True)


def iter_attributions() -> List[dict]:
    """Rescue overrides, oldest first (by `before`). Each claims every ledger
    row with no project_id whose timestamp precedes its `before`."""
    if _file_ledger_path() is not None:
        return []          # file mode keeps no attributions

    from foleyflow.storage import get_backend, get_bytes_many

    backend = get_backend()
    keys = list(backend.list_keys(ATTR_PREFIX))
    if not keys:
        return []
    blobs = get_bytes_many(backend, keys, skip_missing=True)
    out: List[dict] = []
    for key in keys:
        raw = blobs.get(key)
        if raw is None:
            continue
        try:
            data = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            continue
        if (isinstance(data, dict) and data.get("project_id")
                and isinstance(data.get("before"), (int, float))):
            out.append(data)
    return sorted(out, key=lambda a: float(a["before"]))


def _attribution_owner(row: dict, attributions: List[dict]) -> Optional[str]:
    """Which rescue override claims this unattributed row, if any.

    Overrides are applied in `before` order (first-come): the earliest one
    whose window covers the row's timestamp claims it, so a row can never land
    in two projects. No timestamp means unclaimable.
    """
    ts = row.get("timestamp")
    if not isinstance(ts, (int, float)):
        return None
    for attribution in attributions:
        if ts < float(attribution["before"]):
            return attribution["project_id"]
    return None


def record_attribution(project_id: str,
                       before: Optional[float] = None) -> int:
    """Rescue unattributed ledger rows into `project_id`.

    Writes one immutable override object claiming every row with no
    `project_id` written before `before` (default: now). Rows are never
    rewritten — this is an adoption record, not an edit. Returns how many
    unattributed rows the rescue actually adopts: rows an earlier override
    already owns are not counted, and a row is only counted when this rescue
    is its first-come owner once the override is in place.

    Storage mode only: the local-JSONL override keeps no attributions, and a
    rescue attempt in that mode is refused rather than split-brained between
    two stores.
    """
    from foleyflow.ids import new_ulid
    from foleyflow.storage import PreconditionFailed, StorageError, get_backend

    if _file_ledger_path() is not None:
        raise ValueError(
            "attribution rescues are storage-mode only; the local-JSONL "
            "ledger keeps no attributions")

    now = time.time()
    if before is not None and before > now:
        # A future `before` (e.g. a unix-ms timestamp mistaken for seconds)
        # would claim every row written from now on, forever — first-come
        # means a later honest rescue could never take them back.
        raise ValueError(
            f"cannot rescue rows written in the future: "
            f"before={before} > now={now}")
    before = before if before is not None else now

    # Count rows whose first-come owner resolves to this project once the new
    # override is in place — rows an earlier override owns must not be
    # reported as adopted by this rescue.
    attributions = iter_attributions()
    attributions.append({"project_id": project_id, "before": before})
    attributions.sort(key=lambda a: float(a["before"]))
    claimed = sum(
        1 for row in iter_transactions()
        if row.get("project_id") is None
        and _attribution_owner(row, attributions) == project_id)

    body = json.dumps({"project_id": project_id, "before": before,
                       "created_at": now},
                      separators=(",", ":")).encode("utf-8")
    # ULID collision retry, like _append_storage.
    last_err: Optional[Exception] = None
    for _ in range(3):
        key = f"{ATTR_PREFIX}{new_ulid()}.json"
        try:
            get_backend().put_if_absent(key, body, content_type="application/json")
            # Adopted rows were never seen by the running total; drop it so
            # the next alert check re-seeds from the full scan.
            invalidate_project_spend_cache(project_id)
            return claimed
        except PreconditionFailed as exc:
            last_err = exc
            continue
    raise StorageError(
        f"could not allocate a unique attribution key after retries: "
        f"{last_err}")
