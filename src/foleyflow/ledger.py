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
import time
import hashlib
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Dict, List, Optional

from foleyflow.providers.config import get_defaults, get_model_spec

# Prefix under the shared storage backend. One object per transaction; the
# trailing slash keeps list_keys scoped to the ledger alone.
LEDGER_PREFIX = "ledger/"

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
_ROW_CACHE_TTL_S = 2.0
_row_cache: Optional[List[dict]] = None
_row_cache_at: float = 0.0


def _file_ledger_path(path: Optional[str] = None) -> Optional[str]:
    """Resolved local-file path, or None when the storage backend is in use."""
    if path is not None:
        return path
    return LEDGER_PATH


def uses_storage_ledger() -> bool:
    """True when rows go to the shared backend rather than a local JSONL file."""
    return _file_ledger_path() is None


def _set_write_ok(ok: bool) -> None:
    global _write_ok
    _write_ok = ok


def _invalidate_row_cache() -> None:
    global _row_cache, _row_cache_at
    _row_cache = None
    _row_cache_at = 0.0


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
    """
    record = {
        "endpoint": endpoint,
        "params_hash": params_hash,
        "cost_estimate": cost,
        "cost_source": cost_source,
        "success": success,
        "error": error,
        "timestamp": time.time(),
        # Present-but-None when unattributed (a direct render): project_spend
        # filters on this key, and old rows without it read as None too.
        "project_id": _current_project.get(),
    }
    try:
        path = _file_ledger_path()
        if path is not None:
            _append_file(path, record)
        else:
            _append_storage(record)
        _set_write_ok(True)
        # New row is on the backend; drop any snapshot so the next read sees it.
        _invalidate_row_cache()
    except Exception as e:
        _set_write_ok(False)
        print(f"[ledger] WARNING: failed to record transaction for "
              f"{endpoint}: {type(e).__name__}: {e}",
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
    """Skip probe objects and anything that is not a ledger row."""
    name = key.rsplit("/", 1)[-1]
    return name.endswith(".json") and not name.startswith("_")


def _read_storage_rows(*, force: bool = False) -> List[dict]:
    """Every stored transaction, oldest first (ULID keys sort by creation).

    A short process-local cache absorbs the repeated full scans that
    ``get_all_quotas`` and before/after ``summarize_ledger`` pairs would
    otherwise pay against object storage. Append-only semantics make the
    snapshot safe; successful writes invalidate it.
    """
    global _row_cache, _row_cache_at
    from foleyflow.storage import ObjectNotFound, StorageError, get_backend

    now = time.monotonic()
    if (not force and _row_cache is not None
            and (now - _row_cache_at) < _ROW_CACHE_TTL_S):
        return _row_cache

    backend = get_backend()
    try:
        keys = backend.list_keys(LEDGER_PREFIX)
    except StorageError:
        raise
    rows: List[dict] = []
    for key in keys:
        if not _is_transaction_key(key):
            continue
        try:
            raw = backend.get_bytes(key)
        except ObjectNotFound:
            continue
        try:
            rows.append(json.loads(raw))
        except (TypeError, json.JSONDecodeError):
            continue
    _row_cache = rows
    _row_cache_at = now
    return rows


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

    try:
        from foleyflow.storage import get_backend
        get_backend().put_bytes(
            _WRITABLE_PROBE_KEY, b"ok", content_type="text/plain")
        return True
    except Exception:
        return False


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


def project_spend(project_id: str) -> Dict[str, float]:
    """Estimated spend attributed to one project: total + per-endpoint totals.

    Failed calls are counted, the same rule quota uses — a request that failed
    may still have consumed provider quota. Rows written before attribution
    existed, and rows from a direct (project-less) render, carry no
    `project_id` and are never attributed to any project.
    """
    total = 0.0
    by_endpoint: Dict[str, float] = {}
    for row in iter_transactions():
        if row.get("project_id") != project_id:
            continue
        cost = float(row.get("cost_estimate") or 0.0)
        endpoint = row.get("endpoint", "unknown")
        total += cost
        by_endpoint[endpoint] = by_endpoint.get(endpoint, 0.0) + cost
    return {"total": total, "by_endpoint": by_endpoint}
