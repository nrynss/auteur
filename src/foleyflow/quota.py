"""Rate-limit accounting for the UI.

Google's API does not tell you how much of a free-tier quota remains — there is
no "requests left today" field to read. So this counts what *we* did, from
`ledger.jsonl`, which already records every provider call with its endpoint,
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
  * the ledger may not be writable at all. `log_transaction` swallows write
    failures, and LEDGER_PATH is derived from __file__ — in a container with a
    read-only layer, every row is lost and this module then reports a full
    budget while the real one drains. That failure is systematic rather than
    noisy, so `ledger_writable()` exposes it and the API surfaces it.

Treat the numbers as a guard rail — "you have roughly 6 analysis runs left
today" — never as an authoritative balance. Label them as estimates wherever
they are displayed.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import List, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from foley.ledger import LEDGER_PATH
from foleyflow.providers.config import load_config

DEFAULT_RESET_TZ = "America/Los_Angeles"


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


def ledger_writable(path: str = LEDGER_PATH) -> bool:
    """Whether quota counting can actually observe anything.

    False means every figure here is fiction: no rows are being recorded, so
    usage always reads zero no matter how much budget is spent.
    """
    try:
        directory = os.path.dirname(path) or "."
        os.makedirs(directory, exist_ok=True)
        with open(path, "a", encoding="utf-8"):
            return True
    except OSError:
        return False


def _next_daily_reset(now: datetime) -> datetime:
    local = now.astimezone(RESET_TZ)
    tomorrow = (local + timedelta(days=1)).date()
    return datetime.combine(tomorrow, datetime.min.time(), tzinfo=RESET_TZ)


def _read_calls(path: str) -> List[dict]:
    """Ledger rows, oldest first. A truncated final line is skipped rather than
    raising — the ledger is append-only and may be read mid-write."""
    if not os.path.exists(path):
        return []
    rows = []
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


def get_quota(model_id: str, *, now: Optional[datetime] = None,
              ledger_path: str = LEDGER_PATH) -> ModelQuota:
    """Current usage for one model.

    Failed calls are counted. A request rejected by the provider generally
    still consumed quota, and under-reporting is the dangerous direction for a
    limit whose daily budget is 20.
    """
    now = now or datetime.now(RESET_TZ)
    config = load_config()
    raw = config.get("models", {}).get(model_id, {})

    minute_ago = now.timestamp() - 60
    day_start = datetime.combine(
        now.astimezone(RESET_TZ).date(), datetime.min.time(), tzinfo=RESET_TZ
    ).timestamp()

    per_minute = per_day = 0
    for row in _read_calls(ledger_path):
        if row.get("endpoint") != model_id:
            continue
        ts = row.get("timestamp")
        if not isinstance(ts, (int, float)):
            continue
        if ts >= day_start:
            per_day += 1
        if ts >= minute_ago:
            per_minute += 1

    return ModelQuota(
        model_id=model_id,
        per_minute=QuotaWindow(per_minute, raw.get("requests_per_minute")),
        per_day=QuotaWindow(per_day, raw.get("requests_per_day")),
        resets_at=_next_daily_reset(now).isoformat(),
    )


def get_all_quotas(*, now: Optional[datetime] = None,
                   ledger_path: str = LEDGER_PATH) -> List[ModelQuota]:
    """Every model that declares a limit, worst status first so a UI can show
    the problem without sorting."""
    config = load_config()
    quotas = [
        get_quota(model_id, now=now, ledger_path=ledger_path)
        for model_id, raw in config.get("models", {}).items()
        if raw.get("requests_per_minute") or raw.get("requests_per_day")
    ]
    order = {"blocked": 0, "warn": 1, "ok": 2}
    return sorted(quotas, key=lambda q: (order[q.status], q.model_id))


def can_call(model_id: str, *, now: Optional[datetime] = None,
             ledger_path: str = LEDGER_PATH) -> bool:
    """Whether a call would fit within the estimated remaining budget.

    A guard for the UI to disable an Analyze button, not a hard gate — this
    estimate can be wrong in both directions, so it must never be the only
    thing standing between the user and a legitimate request.
    """
    return get_quota(model_id, now=now, ledger_path=ledger_path).status != "blocked"
