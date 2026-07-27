"""Offline tests for quota accounting.

Every test writes its own ledger file and passes an explicit `now`, so results
do not depend on the wall clock, the machine's timezone, or whatever the real
ledger happens to contain.
"""

import json
import os
from datetime import datetime, timedelta

import pytest

from foleyflow import quota
from foleyflow.quota import RESET_TZ, can_call, get_all_quotas, get_quota

MODEL = "gemini-2.5-flash"          # declared: 5 rpm, 20 rpd
LITE = "gemini-3.5-flash-lite"      # declared: 15 rpm, 500 rpd


def write_ledger(path, entries):
    with open(path, "w", encoding="utf-8") as f:
        for endpoint, ts in entries:
            f.write(json.dumps({
                "endpoint": endpoint, "params_hash": "x", "cost_estimate": 0.005,
                "success": True, "error": None, "timestamp": ts,
            }) + "\n")
    return str(path)


@pytest.fixture
def noon():
    return datetime(2026, 7, 27, 12, 0, 0, tzinfo=RESET_TZ)


def test_empty_ledger_reports_full_budget(tmp_path, noon):
    q = get_quota(MODEL, now=noon, ledger_path=str(tmp_path / "absent.jsonl"))
    assert q.per_day.used == 0
    assert q.per_day.limit == 20
    assert q.per_day.remaining == 20
    assert q.status == "ok"


def test_counts_only_the_requested_model(tmp_path, noon):
    path = write_ledger(tmp_path / "l.jsonl", [
        (MODEL, noon.timestamp() - 100),
        (LITE, noon.timestamp() - 100),
        ("cassetteai/sound-effects-generator", noon.timestamp() - 100),
    ])
    assert get_quota(MODEL, now=noon, ledger_path=path).per_day.used == 1


def test_minute_window_excludes_older_calls(tmp_path, noon):
    path = write_ledger(tmp_path / "l.jsonl", [
        (MODEL, noon.timestamp() - 30),    # inside
        (MODEL, noon.timestamp() - 59),    # inside
        (MODEL, noon.timestamp() - 61),    # outside
        (MODEL, noon.timestamp() - 600),   # outside
    ])
    q = get_quota(MODEL, now=noon, ledger_path=path)
    assert q.per_minute.used == 2
    assert q.per_day.used == 4, "all four are still today"


def test_yesterday_does_not_count_against_today(tmp_path, noon):
    """The daily budget is 20; leaking yesterday's calls in would make a fresh
    day look exhausted."""
    yesterday = noon - timedelta(days=1)
    path = write_ledger(tmp_path / "l.jsonl",
                        [(MODEL, yesterday.timestamp())] * 20 +
                        [(MODEL, noon.timestamp() - 10)])
    q = get_quota(MODEL, now=noon, ledger_path=path)
    assert q.per_day.used == 1
    assert q.status == "ok"


def test_day_boundary_is_midnight_in_the_reset_timezone(tmp_path):
    just_after = datetime(2026, 7, 27, 0, 5, tzinfo=RESET_TZ)
    just_before = just_after - timedelta(minutes=10)     # 23:55 previous day
    path = write_ledger(tmp_path / "l.jsonl", [(MODEL, just_before.timestamp())])
    assert get_quota(MODEL, now=just_after, ledger_path=path).per_day.used == 0


def test_exhausted_daily_budget_blocks(tmp_path, noon):
    path = write_ledger(tmp_path / "l.jsonl",
                        [(MODEL, noon.timestamp() - 3600)] * 20)
    q = get_quota(MODEL, now=noon, ledger_path=path)
    assert q.per_day.exhausted
    assert q.status == "blocked"
    assert q.per_day.remaining == 0
    assert can_call(MODEL, now=noon, ledger_path=path) is False


def test_warns_before_exhaustion(tmp_path, noon):
    path = write_ledger(tmp_path / "l.jsonl",
                        [(MODEL, noon.timestamp() - 3600)] * 16)   # 80%
    assert get_quota(MODEL, now=noon, ledger_path=path).status == "warn"


def test_failed_calls_still_count(tmp_path, noon):
    """A rejected request generally still consumed quota upstream, and
    under-counting is the dangerous direction on a 20/day budget."""
    p = tmp_path / "l.jsonl"
    with open(p, "w", encoding="utf-8") as f:
        for _ in range(3):
            f.write(json.dumps({
                "endpoint": MODEL, "params_hash": "x", "cost_estimate": 0.005,
                "success": False, "error": "429", "timestamp": noon.timestamp() - 10,
            }) + "\n")
    assert get_quota(MODEL, now=noon, ledger_path=str(p)).per_day.used == 3


def test_truncated_final_line_is_skipped(tmp_path, noon):
    """The ledger is append-only and may be read mid-write."""
    p = tmp_path / "l.jsonl"
    with open(p, "w", encoding="utf-8") as f:
        f.write(json.dumps({"endpoint": MODEL, "success": True,
                            "timestamp": noon.timestamp() - 10}) + "\n")
        f.write('{"endpoint": "gemini-2.5-fla')
    assert get_quota(MODEL, now=noon, ledger_path=str(p)).per_day.used == 1


def test_lite_has_a_far_larger_daily_budget():
    """The reason Flash Lite is the development default."""
    assert get_quota(LITE).per_day.limit > get_quota(MODEL).per_day.limit * 10


def test_all_quotas_sorted_worst_first(tmp_path, noon):
    path = write_ledger(tmp_path / "l.jsonl",
                        [(MODEL, noon.timestamp() - 3600)] * 20)
    quotas = get_all_quotas(now=noon, ledger_path=path)
    assert quotas, "models declaring limits must be reported"
    assert quotas[0].status == "blocked"
    assert all(q.per_minute.limit or q.per_day.limit for q in quotas)


def test_to_dict_is_json_serializable(tmp_path, noon):
    """The UI receives this over the wire."""
    q = get_quota(MODEL, now=noon, ledger_path=str(tmp_path / "absent.jsonl"))
    payload = q.to_dict()
    json.dumps(payload)
    assert payload["status"] == "ok"
    assert payload["per_day"]["remaining"] == 20
    assert payload["resets_at"]


def test_ledger_attributes_usage_to_the_model_actually_called(tmp_path, monkeypatch):
    """Regression: analyze_video_direct logged the configured default endpoint
    while invoking whatever `model` the caller passed. Every Flash-Lite run was
    counted against the 20/day Flash budget — the quota panel would report the
    wrong model as exhausted while the real one silently drained.
    """
    from foley import analyze

    captured = {}

    def fake_execute(endpoint, arguments, **kwargs):
        captured["endpoint"] = endpoint
        captured["model_arg"] = arguments.get("model")
        return {"output": '{"duration": 15.0, "ambience": "wind", "events": []}'}

    monkeypatch.setattr(analyze, "execute_with_ledger", fake_execute)
    monkeypatch.setattr(analyze, "upload_video", lambda p, model_id=None: "files/fake")
    monkeypatch.setattr(analyze, "get_video_duration", lambda p: 15.0)

    analyze.analyze_video_direct("clip.mp4", model="gemini-3.5-flash-lite")

    assert captured["endpoint"] == "gemini-3.5-flash-lite", \
        "the ledger endpoint must be the model actually invoked"
    assert captured["model_arg"] == "gemini-3.5-flash-lite"
