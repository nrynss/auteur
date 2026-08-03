"""Unit tests for the ElevenLabs usage adapter (foleyflow.elevenlabs).

The adapter is the billed-account seam: every fetch here is stubbed by name
(conftest pins ``_http_post`` so the suite can never reach the network), and
these tests exercise parsing, caching, the ledger fallback, and the cap
decision math against canned responses.
"""

import json
import time
from datetime import datetime, timezone

import pytest

from foleyflow import elevenlabs
from foleyflow.elevenlabs import ElevenLabsUsageError

TODAY = "2026-08-03T00:00:00Z"
DAY_START_MS = int(datetime(2026, 8, 3, tzinfo=timezone.utc).timestamp() * 1000)
DAY_END_MS = int(datetime(2026, 8, 3, 12, tzinfo=timezone.utc).timestamp() * 1000)


def _canned_response(rows):
    return {
        "columns": ["model", "product_type", "hashed_xi_api_key", "timestamp",
                    "total_usage", "total_minutes", "total_cost",
                    "usage_count", "total_charge_count", "api_key_name"],
        "column_types": ["String", "String", "String", "DateTime", "Int",
                         "Float", "Float", "Int", "Float", "String"],
        "column_units": [None, None, None, None, "credits", "min", "inr",
                         None, None, None],
        "rows": rows,
    }


@pytest.fixture(autouse=True)
def _fresh_cache():
    elevenlabs.invalidate_cache()
    yield
    elevenlabs.invalidate_cache()


def _row(ts=TODAY, count=2, cost=1.2, usage=60):
    return ["eleven_text_to_sound_v2", "Sound Effects", "k1", ts,
            usage, 0.0, cost, count, 0.0, "FoleyFlow"]


# -- parsing ----------------------------------------------------------------

def test_usage_rows_parses_the_tabular_response(monkeypatch):
    calls = []
    monkeypatch.setattr(
        elevenlabs, "_http_post",
        lambda path, payload: (calls.append((path, payload))
                               or _canned_response([_row()])))
    rows = elevenlabs.usage_rows(DAY_START_MS, DAY_END_MS)
    assert len(rows) == 1
    row = rows[0]
    assert row["model"] == "eleven_text_to_sound_v2"
    assert row["product_type"] == "Sound Effects"
    assert row["date"] == "2026-08-03"
    assert row["requests"] == 2
    assert row["credits"] == 60
    assert row["cost_usd"] == pytest.approx(1.2)
    assert calls[0][0] == elevenlabs.USAGE_PATH
    payload = calls[0][1]
    assert payload["start_time"] == DAY_START_MS
    assert payload["group_by"] == ["model", "product_type", "hashed_xi_api_key"]


def test_usage_rows_is_ttl_cached(monkeypatch):
    calls = []

    def _fake(path, payload):
        calls.append(1)
        return _canned_response([_row()])

    monkeypatch.setattr(elevenlabs, "_http_post", _fake)
    elevenlabs.usage_rows(DAY_START_MS, DAY_END_MS)
    elevenlabs.usage_rows(DAY_START_MS, DAY_END_MS)
    assert len(calls) == 1  # second read served from cache


def test_usage_rows_cache_expires(monkeypatch):
    calls = []

    def _fake(path, payload):
        calls.append(1)
        return _canned_response([_row()])

    monkeypatch.setattr(elevenlabs, "_http_post", _fake)
    elevenlabs.usage_rows(DAY_START_MS, DAY_END_MS)
    elevenlabs.invalidate_cache()
    elevenlabs.usage_rows(DAY_START_MS, DAY_END_MS)
    assert len(calls) == 2


def test_today_usage_sums_rows(monkeypatch):
    monkeypatch.setattr(
        elevenlabs, "_http_post",
        lambda path, payload: _canned_response(
            [_row(count=2, cost=1.2, usage=60), _row(count=5, cost=3.0, usage=80)]))
    usage = elevenlabs.today_usage(
        now=datetime(2026, 8, 3, 12, tzinfo=timezone.utc))
    assert usage["available"] is True
    assert usage["requests"] == 7
    assert usage["credits"] == 140
    assert usage["cost_usd"] == pytest.approx(4.2)


def test_today_usage_fails_soft_when_the_endpoint_is_unreachable(monkeypatch):
    def _boom(path, payload):
        raise ElevenLabsUsageError("offline pin")

    monkeypatch.setattr(elevenlabs, "_http_post", _boom)
    usage = elevenlabs.today_usage(
        now=datetime(2026, 8, 3, 12, tzinfo=timezone.utc))
    assert usage["available"] is False
    assert usage["cost_usd"] == 0.0
    assert "offline pin" in usage["error"]


def test_the_conftest_pin_keeps_today_usage_offline():
    """With no test override, the suite's autouse pin makes today_usage
    report unavailable rather than reach api.elevenlabs.io."""
    usage = elevenlabs.today_usage(
        now=datetime(2026, 8, 3, 12, tzinfo=timezone.utc))
    assert usage["available"] is False


# -- the ledger fallback ----------------------------------------------------

def test_ledger_estimate_today_sums_only_today_s_paid_rows(monkeypatch,
                                                           tmp_path):
    path = tmp_path / "ledger.jsonl"
    now = datetime(2026, 8, 3, 12, tzinfo=timezone.utc)
    day = datetime(2026, 8, 3, tzinfo=timezone.utc).timestamp()
    rows = [
        {"endpoint": "eleven_text_to_sound_v2", "cost_estimate": 0.3,
         "timestamp": day + 100},
        {"endpoint": "eleven_text_to_sound_v2", "cost_estimate": 0.3,
         "timestamp": day + 200},
        {"endpoint": "gemini-2.5-flash", "cost_estimate": 9.9,
         "timestamp": day + 150},  # not SFX: ignored
        {"endpoint": "eleven_text_to_sound_v2", "cost_estimate": 0.3,
         "timestamp": day - 86400},  # yesterday: ignored
    ]
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    monkeypatch.setattr("foleyflow.ledger.LEDGER_PATH", str(path))

    assert elevenlabs._ledger_estimate_today(now=now) == pytest.approx(0.6)


def test_cap_status_trusts_analytics_when_it_is_available(monkeypatch):
    """The healthy path is exactly one cached analytics query: the ledger
    fallback is only for outages, never scanned alongside the truth."""
    monkeypatch.setattr(elevenlabs, "hard_cap_usd", lambda: 5.0)
    monkeypatch.setattr(
        elevenlabs, "today_usage",
        lambda now=None, use_cache=True: {
            "available": True, "requests": 3, "credits": 0,
            "cost_usd": 4.0, "error": None})
    scanned = []
    monkeypatch.setattr(elevenlabs, "_ledger_estimate_today",
                        lambda **k: scanned.append(1) or 4.5)
    status = elevenlabs.cap_status(
        now=datetime(2026, 8, 3, 12, tzinfo=timezone.utc))
    assert status["capped"] is False
    assert status["spend_usd"] == pytest.approx(4.0)  # analytics won
    assert status["source"] == "analytics"
    assert scanned == []  # the ledger was never consulted


def test_today_usage_is_cached_by_day_not_by_wall_clock(monkeypatch):
    """The first version keyed the cache on (start, now-ms), so the TTL never
    hit — the day is the stable key now."""
    calls = []

    def _fake(path, payload):
        calls.append(1)
        return _canned_response([_row()])

    monkeypatch.setattr(elevenlabs, "_http_post", _fake)
    now = datetime(2026, 8, 3, 12, tzinfo=timezone.utc)
    elevenlabs.today_usage(now=now)
    elevenlabs.today_usage(now=now.replace(minute=1))
    assert len(calls) == 1  # same UTC day, one fetch


def test_cap_status_refuses_past_the_cap(monkeypatch):
    monkeypatch.setattr(elevenlabs, "hard_cap_usd", lambda: 5.0)
    monkeypatch.setattr(
        elevenlabs, "today_usage",
        lambda now=None, use_cache=True: {
            "available": True, "requests": 9, "credits": 0,
            "cost_usd": 7.0, "error": None})
    monkeypatch.setattr(elevenlabs, "_ledger_estimate_today", lambda **k: 2.1)
    status = elevenlabs.cap_status(
        now=datetime(2026, 8, 3, 12, tzinfo=timezone.utc))
    assert status["capped"] is True
    assert status["cap_usd"] == pytest.approx(5.0)
    assert status["spend_usd"] == pytest.approx(7.0)
    assert status["source"] == "analytics"
    assert status["resets_at"].startswith("2026-08-04")


def test_cap_status_falls_back_to_the_ledger_when_analytics_is_down(monkeypatch):
    monkeypatch.setattr(elevenlabs, "hard_cap_usd", lambda: 5.0)
    monkeypatch.setattr(
        elevenlabs, "today_usage",
        lambda now=None, use_cache=True: {
            "available": False, "requests": 0, "credits": 0,
            "cost_usd": 0.0, "error": "offline"})
    monkeypatch.setattr(elevenlabs, "_ledger_estimate_today", lambda **k: 6.0)
    status = elevenlabs.cap_status(
        now=datetime(2026, 8, 3, 12, tzinfo=timezone.utc))
    assert status["capped"] is True
    assert status["spend_usd"] == pytest.approx(6.0)
    assert status["source"] == "ledger"


def test_cap_status_is_off_without_a_cap(monkeypatch):
    monkeypatch.setattr(elevenlabs, "hard_cap_usd", lambda: None)
    status = elevenlabs.cap_status(
        now=datetime(2026, 8, 3, 12, tzinfo=timezone.utc))
    assert status["capped"] is False
    assert status["source"] == "no_cap"
    assert status["cap_usd"] is None


def test_hard_cap_usd_reads_the_declared_config():
    """The real providers.toml declares a demo guard; missing/zero is off."""
    cap = elevenlabs.hard_cap_usd()
    assert cap == 15.0
