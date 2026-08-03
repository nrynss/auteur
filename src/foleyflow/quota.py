"""Rate-limit accounting for the UI.

Google's API does not tell you how much of a free-tier quota remains — there is
no "requests left today" field to read. So this counts what *we* did, from the
cost ledger, which already records every provider call with its endpoint,
timestamp, and success flag. Limits come from providers.toml.

## This is an estimate, and the UI must say so

Local counting can drift from Google's accounting in at least three ways:

  * another process, machine, or app sharing the same API key spends quota this
    ledger never sees;
  * a request that fails client-side after the server counted it is recorded
    here as a failure but consumed quota upstream;
  * the daily window is assumed to reset at midnight in `RESET_TZ` (Pacific,
    Google's convention for AI Studio free-tier quotas). A different reset
    boundary shifts every daily figure;
  * the ledger may not be writable at all. `log_transaction` does not raise on
    write failure (a successful generation must not be retried for a cost-
    record fault), but marks the process unwritable so `ledger_writable()`
    returns False and the API stops presenting the figures as real. Against
    the storage backend a failed write is never a silent no-op.

Treat the numbers as a guard rail — "you have roughly 6 analysis runs left
today" — never as an authoritative balance. Label them as estimates wherever
they are displayed.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import List, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from foleyflow.ledger import endpoint_call_counts, iter_transactions, ledger_writable
from foleyflow.providers.config import load_config

DEFAULT_RESET_TZ = "America/Los_Angeles"

# Re-export so existing `from foleyflow.quota import ledger_writable` keeps
# working — the API and CLI import it from here.
__all__ = [
    "QuotaWindow",
    "ModelQuota",
    "ledger_writable",
    "get_quota",
    "get_all_quotas",
    "can_call",
    "RESET_TZ",
]


def _resolve_reset_tz():
    """Google resets AI Studio free-tier daily quotas at midnight Pacific.

    Falls back to UTC rather than raising: zoneinfo needs a system tz database,
    which Windows does not ship and slim container images often omit. A quota
    panel showing a window offset by a few hours is a far better failure than
    an import error that takes the whole app down.
    """
    name = os.environ.get("FOLEYFLOW_QUOTA_TZ", DEFAULT_RESET_TZ)
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ModuleNotFoundError):
        print(f"[quota] WARNING: timezone {name!r} unavailable (install `tzdata`) "
              "— daily windows will use UTC, so the reset boundary may be off.")
        return timezone.utc


RESET_TZ = _resolve_reset_tz()


@dataclass
class QuotaWindow:
    """Usage against one limit. `limit` is None when none is declared."""

    used: int
    limit: Optional[int]

    @property
    def remaining(self) -> Optional[int]:
        return None if self.limit is None else max(self.limit - self.used, 0)

    @property
    def fraction(self) -> Optional[float]:
        if not self.limit:
            return None
        return min(self.used / self.limit, 1.0)

    @property
    def exhausted(self) -> bool:
        return self.limit is not None and self.used >= self.limit


@dataclass
class ModelQuota:
    model_id: str
    per_minute: QuotaWindow
    per_day: QuotaWindow
    resets_at: Optional[str]        # ISO8601, when the daily window rolls over

    @property
    def status(self) -> str:
        """`ok` | `warn` | `blocked` — what a UI colours on."""
        if self.per_day.exhausted or self.per_minute.exhausted:
            return "blocked"
        worst = max(
            (w.fraction or 0.0) for w in (self.per_minute, self.per_day)
        )
        return "warn" if worst >= 0.8 else "ok"

    def to_dict(self) -> dict:
        payload = asdict(self)
        for window, name in ((self.per_minute, "per_minute"), (self.per_day, "per_day")):
            payload[name]["remaining"] = window.remaining
            payload[name]["fraction"] = window.fraction
            payload[name]["exhausted"] = window.exhausted
        payload["status"] = self.status
        return payload


def get_quota(model_id: str, *, now: Optional[datetime] = None,
              ledger_path: Optional[str] = None,
              rows: Optional[List[dict]] = None) -> ModelQuota:
    """Current usage for one model.

    Failed calls are counted. A request rejected by the provider generally
    still consumed quota, and under-reporting is the dangerous direction for a
    limit whose daily budget is 20.

    ``ledger_path`` forces a local JSONL file (unit tests). ``None`` reads the
    configured default — storage backend, or the file named by
    ``FOLEYFLOW_LEDGER_PATH`` when that is set. ``rows`` is a pre-loaded
    snapshot for callers that already paid for one full scan (``get_all_quotas``).
    """
    now = now or datetime.now(RESET_TZ)
    config = load_config()
    raw = config.get("models", {}).get(model_id, {})

    minute_ago = now.timestamp() - 60
    day_start = datetime.combine(
        now.astimezone(RESET_TZ).date(), datetime.min.time(), tzinfo=RESET_TZ
    ).timestamp()

    # Storage mode uses write-through timestamp aggregates (exact; failed
    # calls counted). ``rows`` / ``ledger_path`` keep the scan path for
    # unit tests and the get_all_quotas shared snapshot.
    per_minute, per_day = endpoint_call_counts(
        model_id, minute_ago=minute_ago, day_start=day_start,
        rows=rows, ledger_path=ledger_path)

    return ModelQuota(
        model_id=model_id,
        per_minute=QuotaWindow(per_minute, raw.get("requests_per_minute")),
        per_day=QuotaWindow(per_day, raw.get("requests_per_day")),
        resets_at=_next_daily_reset(now).isoformat(),
    )


def _next_daily_reset(now: datetime) -> datetime:
    local = now.astimezone(RESET_TZ)
    tomorrow = (local + timedelta(days=1)).date()
    return datetime.combine(tomorrow, datetime.min.time(), tzinfo=RESET_TZ)


def get_all_quotas(*, now: Optional[datetime] = None,
                   ledger_path: Optional[str] = None) -> List[ModelQuota]:
    """Every model that declares a limit, worst status first so a UI can show
    the problem without sorting.

    Storage mode answers from process-local exact timestamp aggregates after
    one seed scan (or write-through after that). File-mode / explicit
    ``ledger_path`` still reads the ledger once and shares the snapshot
    across models.
    """
    config = load_config()
    # File / explicit path: one shared scan. Storage mode: leave rows=None so
    # each get_quota hits the aggregate (seeded once inside endpoint_call_counts).
    from foleyflow.ledger import uses_storage_ledger
    rows: Optional[List[dict]] = None
    if ledger_path is not None or not uses_storage_ledger():
        rows = iter_transactions(ledger_path)
    quotas = [
        get_quota(model_id, now=now, ledger_path=ledger_path, rows=rows)
        for model_id, raw in config.get("models", {}).items()
        if raw.get("requests_per_minute") or raw.get("requests_per_day")
    ]
    order = {"blocked": 0, "warn": 1, "ok": 2}
    return sorted(quotas, key=lambda q: (order[q.status], q.model_id))


def can_call(model_id: str, *, now: Optional[datetime] = None,
             ledger_path: Optional[str] = None) -> bool:
    """Whether a call would fit within the estimated remaining budget.

    A guard for the UI to disable an Analyze button, not a hard gate — this
    estimate can be wrong in both directions, so it must never be the only
    thing standing between the user and a legitimate request.
    """
    return get_quota(model_id, now=now, ledger_path=ledger_path).status != "blocked"
