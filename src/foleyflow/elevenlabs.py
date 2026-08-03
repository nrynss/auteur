"""ElevenLabs workspace usage — the paid SFX tier's actual accounting.

The genblaze-elevenlabs connector reads ``ELEVENLABS_API_KEY`` itself and
returns audio with no billing metadata, so the cost ledger can only ever hold
estimates (providers.toml's flat per-call figure). This module asks the
workspace analytics API for what actually happened — requests, credits and
cost per day — which is the only source that knows the real bill.

Endpoint (elevenlabs.io/docs/api-reference/analytics/workspace/usage):
    POST /v1/workspace/analytics/query/usage-by-product-over-time

The response is tabular: ``columns`` / ``column_types`` / ``column_units`` /
``rows``. The workspace reports ``total_cost`` in its configured fiat
currency; per product decision we use the numeric value as USD and do not
convert — the cap, the quota row and the Spend tab all speak that same unit.

This is the billed-account seam for the offline suite: tests stand in for
``usage_rows`` / ``today_usage`` by name, and ``tests/conftest.py`` pins
``_http_post`` so the suite can never reach api.elevenlabs.io.
"""

from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import httpx

API_BASE = "https://api.elevenlabs.io"
USAGE_PATH = "/v1/workspace/analytics/query/usage-by-product-over-time"
API_KEY_ENV = "ELEVENLABS_API_KEY"
PAID_SFX_MODEL = "eleven_text_to_sound_v2"

# The workspace reports total_cost in its configured fiat; we treat the
# number as USD (product decision — no conversion layer).
CACHE_TTL_S = 60.0
LEDGER_FALLBACK_TTL_S = 300.0
TIMEOUT_S = 15.0


class ElevenLabsUsageError(RuntimeError):
    """The analytics endpoint could not be queried.

    Callers treat this as "usage unknown": the quota row falls back to the
    ledger, and the hard cap falls back to the ledger lower bound rather than
    pretending a number exists.
    """


_cache_lock = threading.Lock()
_cache: Dict[str, tuple] = {}  # key -> (expires_at_monotonic, payload)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _api_key() -> str:
    key = os.environ.get(API_KEY_ENV)
    if not key:
        raise ElevenLabsUsageError(f"{API_KEY_ENV} is not set")
    return key


def _http_post(path: str, payload: dict) -> dict:
    """The one HTTP call this module makes — the offline suite pins it."""
    with httpx.Client(timeout=TIMEOUT_S) as client:
        resp = client.post(
            f"{API_BASE}{path}",
            json=payload,
            headers={"xi-api-key": _api_key(),
                     "Content-Type": "application/json"},
        )
        resp.raise_for_status()
        return resp.json()


def invalidate_cache() -> None:
    """Forget every cached figure. Tests call this after stubbing."""
    with _cache_lock:
        _cache.clear()


def _cached(key: str, ttl: float):
    with _cache_lock:
        hit = _cache.get(key)
        if hit is not None and hit[0] > time.monotonic():
            return hit[1]
    return None


def _store(key: str, ttl: float, payload: Any) -> None:
    with _cache_lock:
        _cache[key] = (time.monotonic() + ttl, payload)


def fetch_usage(start_time_ms: int, end_time_ms: int, *,
                group_by: Optional[List[str]] = None,
                interval_seconds: int = 86400) -> dict:
    """Raw tabular response from the workspace usage endpoint."""
    payload: dict = {"start_time": start_time_ms, "end_time": end_time_ms,
                     "interval_seconds": interval_seconds}
    if group_by:
        payload["group_by"] = group_by
    return _http_post(USAGE_PATH, payload)


def usage_rows(start_time_ms: int, end_time_ms: int, *,
               use_cache: bool = True) -> List[dict]:
    """Parsed per-bucket rows for a window, oldest first.

    Each row: ``{date, model, product_type, requests, credits, cost_usd,
    minutes, api_key_name}``. TTL-cached so a route never pays for the same
    query twice within a minute.
    """
    key = f"rows:{start_time_ms}:{end_time_ms}"
    if use_cache:
        cached = _cached(key, CACHE_TTL_S)
        if cached is not None:
            return cached

    raw = fetch_usage(
        start_time_ms, end_time_ms,
        group_by=["model", "product_type", "hashed_xi_api_key"])
    cols = raw.get("columns") or []
    rows: List[dict] = []
    for raw_row in raw.get("rows") or []:
        m = dict(zip(cols, raw_row))
        ts = m.get("timestamp") or ""
        rows.append({
            "date": str(ts)[:10],
            "model": m.get("model"),
            "product_type": m.get("product_type"),
            "requests": int(m.get("usage_count") or 0),
            "credits": int(m.get("total_usage") or 0),
            "cost_usd": float(m.get("total_cost") or 0.0),
            "minutes": float(m.get("total_minutes") or 0.0),
            "api_key_name": m.get("api_key_name"),
        })
    if use_cache:
        _store(key, CACHE_TTL_S, rows)
    return rows


def _day_bounds(now: Optional[datetime] = None) -> tuple:
    """(start_ms, end_ms) of the UTC calendar day containing ``now``.

    ``now`` may be aware in any timezone (quota callers pass a
    Pacific-aware timestamp); the day is always the UTC day. End is the
    day's own end, not ``now``: the day is a stable cache key and the
    analytics endpoint reports the elapsed part of a future-ended window.
    """
    now = now or _now_utc()
    utc = now.astimezone(timezone.utc)
    start = datetime(utc.year, utc.month, utc.day, tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000)


def next_reset_utc(now: Optional[datetime] = None) -> datetime:
    """Next UTC midnight — when the daily cap and the quota row roll over."""
    now = now or _now_utc()
    _, end_ms = _day_bounds(now)
    return datetime.fromtimestamp(end_ms / 1000.0, tz=timezone.utc)


def today_usage(now: Optional[datetime] = None, *,
                use_cache: bool = True) -> dict:
    """Today's totals (UTC day) for the paid SFX model: requests / credits /
    cost_usd.

    Scoped to ``eleven_text_to_sound_v2``: the workspace analytics covers the
    whole workspace, which also carries unrelated models on the same key (TTS
    measured at $14.31 on 2026-08-03). The SFX cap and quota row must be
    driven by SFX spend, not by whatever else shares the account.

    ``available: False`` (with an ``error`` string) when the query failed —
    the caller decides what unavailable means; it never raises. Cached by day
    (a stable key), not by wall clock, so the TTL actually hits.
    """
    start_ms, end_ms = _day_bounds(now)
    key = f"today:{start_ms}"
    if use_cache:
        cached = _cached(key, CACHE_TTL_S)
        if cached is not None:
            return cached
    try:
        rows = usage_rows(start_ms, end_ms, use_cache=use_cache)
    except (httpx.HTTPError, ElevenLabsUsageError,
            KeyError, ValueError, TypeError) as exc:
        result = {"available": False, "requests": 0, "credits": 0,
                  "cost_usd": 0.0, "error": f"{type(exc).__name__}: {exc}"}
        if use_cache:
            _store(key, CACHE_TTL_S, result)
        return result
    sfx = [r for r in rows if r["model"] == PAID_SFX_MODEL]
    result = {
        "available": True,
        "requests": sum(r["requests"] for r in sfx),
        "credits": sum(r["credits"] for r in sfx),
        "cost_usd": round(sum(r["cost_usd"] for r in sfx), 4),
    }
    if use_cache:
        _store(key, CACHE_TTL_S, result)
    return result


def _ledger_estimate_today(now: Optional[datetime] = None,
                           *, use_cache: bool = True) -> float:
    """The ledger's own estimate of today's paid-SFX spend.

    Lower bound only (the flat per-call figure under-quotes long stems), and
    only reached when the analytics endpoint is unavailable — the cap falls
    back to it so a transient analytics outage cannot open the gate. A full
    ledger scan is the price of that degraded path; it is cached for a few
    minutes so it is paid once, not per request.
    """
    from foleyflow.ledger import iter_transactions

    key = "ledger-today"
    if use_cache:
        cached = _cached(key, LEDGER_FALLBACK_TTL_S)
        if cached is not None:
            return cached

    start_ms, end_ms = _day_bounds(now)
    total = 0.0
    for row in iter_transactions():
        if row.get("endpoint") != PAID_SFX_MODEL:
            continue
        ts = float(row.get("timestamp") or 0.0) * 1000.0
        if start_ms <= ts <= end_ms:
            total += float(row.get("cost_estimate") or 0.0)
    if use_cache:
        _store(key, LEDGER_FALLBACK_TTL_S, total)
    return total


def hard_cap_usd() -> Optional[float]:
    """The configured daily hard cap for the paid SFX model, or None (off).

    Missing or zero means "no cap" — a cap is an operator's explicit choice,
    not an ambient default.
    """
    from foleyflow.providers.config import get_model_spec

    try:
        spec = get_model_spec(PAID_SFX_MODEL)
    except KeyError:
        return None
    cap = spec.hard_cap_usd
    if cap is None or cap <= 0:
        return None
    return float(cap)


def cap_status(now: Optional[datetime] = None, *,
               use_cache: bool = True) -> dict:
    """Whether billable paid-tier work is currently refused.

    Effective spend is ``max(analytics actuals, ledger estimate)`` for the
    UTC day. Analytics is the truth; the ledger is a lower bound that keeps
    protecting when the analytics query fails. ``capped`` means a billable
    run must be refused until ``resets_at`` (next UTC midnight).

    Returns ``{"capped", "cap_usd", "spend_usd", "resets_at", "source"}``;
    ``source`` names which figure the decision used.
    """
    cap = hard_cap_usd()
    if cap is None:
        return {"capped": False, "cap_usd": None, "spend_usd": None,
                "resets_at": None, "source": "no_cap"}

    usage = today_usage(now=now, use_cache=use_cache)
    if usage["available"]:
        # Analytics is the workspace truth and is never scanned locally; the
        # ledger estimate is only an outage fallback, so the healthy path
        # costs exactly one cached API query.
        spend = usage["cost_usd"]
        source = "analytics"
    else:
        spend = _ledger_estimate_today(now=now, use_cache=use_cache)
        source = "ledger"
    now = now or _now_utc()
    return {"capped": spend >= cap, "cap_usd": cap,
            "spend_usd": round(spend, 4),
            "resets_at": next_reset_utc(now).isoformat(),
            "source": source}
