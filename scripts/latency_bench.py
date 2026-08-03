#!/usr/bin/env python3
"""Deterministic offline latency harness for Auteur hot read paths.

Wraps LocalBackend with a fixed per-op delay so serial vs concurrent vs
aggregate paths show clear, reproducible differences without live B2.

Usage::

    uv run python scripts/latency_bench.py
    uv run python scripts/latency_bench.py --delay-ms 50 --json scripts/latency_bench_results.json

Does not require credentials. Never writes outside the temp root it creates
(or ``--root`` when given).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

# Ensure `src/` is importable when run as a script.
_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


# ---------------------------------------------------------------------------
# Delayed backend
# ---------------------------------------------------------------------------


class DelayedBackend:
    """LocalBackend wrapper that injects a fixed sleep per storage op.

    Simulates B2 RTT without network. Each ``get_bytes`` / ``head`` /
    ``list_keys`` / ``exists`` / ``put*`` pays ``delay_s`` once.
    """

    def __init__(self, inner, delay_s: float = 0.05):
        self._inner = inner
        self.delay_s = float(delay_s)
        self.op_counts: Dict[str, int] = {
            "get_bytes": 0,
            "head": 0,
            "list_keys": 0,
            "exists": 0,
            "put_bytes": 0,
            "put_if_absent": 0,
            "put": 0,
            "get": 0,
            "signed_url": 0,
        }

    @property
    def root(self) -> str:
        return getattr(self._inner, "root", "")

    def _sleep(self, op: str) -> None:
        self.op_counts[op] = self.op_counts.get(op, 0) + 1
        if self.delay_s > 0:
            time.sleep(self.delay_s)

    def exists(self, key: str) -> bool:
        self._sleep("exists")
        return self._inner.exists(key)

    def head(self, key: str):
        self._sleep("head")
        return self._inner.head(key)

    def get(self, key: str, dest_path: str) -> str:
        self._sleep("get")
        return self._inner.get(key, dest_path)

    def get_bytes(self, key: str) -> bytes:
        self._sleep("get_bytes")
        return self._inner.get_bytes(key)

    def put(self, key: str, src_path: str, content_type: Optional[str] = None) -> str:
        self._sleep("put")
        return self._inner.put(key, src_path, content_type=content_type)

    def put_bytes(self, key: str, data: bytes,
                  content_type: Optional[str] = None) -> str:
        self._sleep("put_bytes")
        return self._inner.put_bytes(key, data, content_type=content_type)

    def put_if_absent(self, key: str, data: bytes,
                      content_type: Optional[str] = None) -> str:
        self._sleep("put_if_absent")
        return self._inner.put_if_absent(key, data, content_type=content_type)

    def signed_url(self, key: str, expires_s: int = 3600) -> str:
        self._sleep("signed_url")
        return self._inner.signed_url(key, expires_s=expires_s)

    def list_keys(self, prefix: str) -> List[str]:
        self._sleep("list_keys")
        return self._inner.list_keys(prefix)

    def reset_counts(self) -> None:
        for k in self.op_counts:
            self.op_counts[k] = 0


# ---------------------------------------------------------------------------
# Seed
# ---------------------------------------------------------------------------


@dataclass
class SeedStats:
    projects: int = 0
    events: int = 0
    ledger_rows: int = 0
    runs: int = 0
    primary_project_id: str = ""
    project_ids: List[str] = field(default_factory=list)


def _seed(
    backend,
    *,
    n_events: int = 60,
    n_ledger: int = 200,
    n_projects: int = 3,
) -> SeedStats:
    """Write realistic-ish project + ledger objects (no artificial delay)."""
    from foleyflow import ledger
    from foleyflow.projects import (
        EVENT_ANALYSIS_COMPLETED,
        EVENT_MIX_COMPLETED,
        EVENT_PLAN_PROPOSED,
        EVENT_QA_CLEARED,
        EVENT_RUN_COMPLETED,
        EVENT_RUN_STARTED,
        EVENT_STAGE_RECORDED,
        ProjectStore,
    )
    from foleyflow.storage.local import LocalBackend

    # Seed against the *inner* local backend so setup is not delayed.
    inner = backend._inner if isinstance(backend, DelayedBackend) else backend
    store = ProjectStore(inner, work_dir=tempfile.mkdtemp(prefix="ff-bench-w-"))

    stats = SeedStats()
    project_ids: List[str] = []

    for pi in range(n_projects):
        p = store.create_project(
            title=f"Bench project {pi}",
            input_key=f"assets/bench/source_{pi}.mp4",
            source_sha256=("ab" * 32),
            source_filename=f"bench_{pi}.mp4",
            duration_s=12.5 + pi,
            budget_usd=10.0 + pi,
        )
        project_ids.append(p.id)
        # Source blob + mix artifact for HEAD/exists paths.
        inner.put_bytes(p.input_key, b"fake-mp4-bytes", content_type="video/mp4")
        # Scenes + QA as raw JSON (avoid Scene/QaReport constructors in the harness).
        scenes_payload = {
            "scenes": [
                {"id": "scene_0", "index": 0, "start_s": 0.0, "end_s": 5.0,
                 "duration_s": 5.0, "stats": {}},
                {"id": "scene_1", "index": 1, "start_s": 5.0, "end_s": 12.5,
                 "duration_s": 7.5, "stats": {}},
            ]
        }
        store._put_json(store.scenes_key(p.id), scenes_payload)
        qa_payload = {
            "run_id": "run_seed",
            "overall_status": "ok",
            "audio": {
                "integrated_lufs": -16.0,
                "true_peak_dbtp": -2.0,
                "target_lufs": -14.0,
                "target_true_peak_dbtp": -1.0,
                "measured": True,
                "issues": [],
            },
            "visual": {"issues": [], "luma_delta_threshold": 20.0},
            "findings": [],
        }
        store._put_json(store.qa_report_key(p.id), qa_payload)

        run = store.start_run(p.id, "analyze")
        store.append_event(p.id, EVENT_ANALYSIS_COMPLETED, run_id=run.id,
                           payload={"model": "gemini-2.5-flash"})
        store.append_event(p.id, EVENT_PLAN_PROPOSED, run_id=run.id,
                           payload={"timeline_version": 1})
        store.complete_run(run, timeline_version=1)
        mix_run = store.start_run(p.id, "mix")
        mix_key = store.mix_video_key(p.id, mix_run.id)
        inner.put_bytes(mix_key, b"fake-mix-mp4", content_type="video/mp4")
        store.append_event(
            p.id, EVENT_MIX_COMPLETED, run_id=mix_run.id,
            payload={"video_key": mix_key, "audio_key": store.mix_key(p.id, mix_run.id)},
        )
        store.append_event(p.id, EVENT_QA_CLEARED, run_id=mix_run.id, payload={})
        store.complete_run(mix_run)
        # Pad the primary project to the target event count.
        if pi == 0:
            while len(store.list_events(p.id)) < n_events:
                store.append_event(
                    p.id, EVENT_STAGE_RECORDED,
                    payload={"stage": "pad", "i": len(store.list_events(p.id))},
                )
        stats.events += len(store.list_events(p.id))
        stats.runs += 2

    stats.projects = n_projects
    stats.project_ids = project_ids
    stats.primary_project_id = project_ids[0]

    # Ledger rows across projects (storage mode).
    endpoints = [
        "gemini-2.5-flash",
        "gemini-2.0-flash",
        "eleven_multilingual_v2",
        "fal-ai/mmaudio-v2",
    ]
    for i in range(n_ledger):
        pid = project_ids[i % n_projects]
        with ledger.set_project_context(pid):
            ledger.log_transaction(
                endpoints[i % len(endpoints)],
                f"bench-{i:04d}",
                cost=0.01 + (i % 7) * 0.001,
                success=(i % 11 != 0),  # some failures; still count for spend/quota
                cost_source="estimate" if i % 3 else "actual",
            )
    stats.ledger_rows = n_ledger
    return stats


# ---------------------------------------------------------------------------
# Measurements
# ---------------------------------------------------------------------------


@dataclass
class Measure:
    name: str
    wall_ms: float
    ops: Dict[str, int] = field(default_factory=dict)
    note: str = ""


def _snapshot_ops(backend: DelayedBackend) -> Dict[str, int]:
    return dict(backend.op_counts)


def _diff_ops(before: Dict[str, int], after: Dict[str, int]) -> Dict[str, int]:
    return {k: after.get(k, 0) - before.get(k, 0)
            for k in set(before) | set(after)
            if after.get(k, 0) - before.get(k, 0)}


def _time_call(backend: DelayedBackend, name: str, fn: Callable[[], Any],
               note: str = "") -> Measure:
    before = _snapshot_ops(backend)
    t0 = time.perf_counter()
    fn()
    elapsed = (time.perf_counter() - t0) * 1000.0
    after = _snapshot_ops(backend)
    return Measure(name=name, wall_ms=elapsed, ops=_diff_ops(before, after),
                   note=note)


def run_bench(
    *,
    delay_ms: float = 50.0,
    n_events: int = 60,
    n_ledger: int = 200,
    n_projects: int = 3,
    root: Optional[str] = None,
    warm_passes: int = 1,
) -> Dict[str, Any]:
    from foleyflow import ledger
    from foleyflow.projects import ProjectStore
    from foleyflow.quota import get_all_quotas, ledger_writable
    from foleyflow.storage.local import LocalBackend

    delay_s = delay_ms / 1000.0
    tmp_owned = root is None
    root = root or tempfile.mkdtemp(prefix="ff-latency-bench-")
    inner = LocalBackend(root)
    backend = DelayedBackend(inner, delay_s=delay_s)

    # Point ledger + storage at our delayed backend.
    ledger.LEDGER_PATH = None
    ledger._set_write_ok(True)
    ledger._invalidate_row_cache()
    ledger._invalidate_writable_cache()
    ledger.invalidate_project_spend_cache()
    if hasattr(ledger, "invalidate_ledger_aggregates"):
        ledger.invalidate_ledger_aggregates()

    import foleyflow.storage as storage_pkg
    import foleyflow.storage.factory as factory

    def _get_backend(kind=None):
        return backend

    storage_pkg.get_backend = _get_backend  # type: ignore[assignment]
    factory.get_backend = _get_backend  # type: ignore[assignment]

    # Seed with delay disabled so setup wall time is not part of the story.
    backend.delay_s = 0.0
    stats = _seed(backend, n_events=n_events, n_ledger=n_ledger,
                  n_projects=n_projects)
    backend.delay_s = delay_s
    backend.reset_counts()

    # Drop process caches so first measurements are cold.
    ledger._invalidate_row_cache()
    ledger._invalidate_writable_cache()
    ledger.invalidate_project_spend_cache()
    if hasattr(ledger, "invalidate_ledger_aggregates"):
        ledger.invalidate_ledger_aggregates()
    if hasattr(ProjectStore, "invalidate_state_cache"):
        ProjectStore.invalidate_state_cache()

    store = ProjectStore(backend, work_dir=tempfile.mkdtemp(prefix="ff-bench-w-"))
    pid = stats.primary_project_id

    results: List[Measure] = []

    def project_fold():
        """GET /projects/{id} core: state + scenes + qa + source/mix existence."""
        state = store.get_state(pid)
        mix_video_key = (state.latest_mix or {}).get("video_key")

        def scenes():
            return store.load_scenes(pid)

        def qa():
            return store.load_qa_report(pid)

        def head_source():
            try:
                backend.head(state.project.input_key)
            except Exception:
                pass

        def head_mix():
            if not mix_video_key:
                return
            try:
                backend.head(mix_video_key)
            except Exception:
                pass

        from foleyflow.storage.base import get_io_pool
        pool = get_io_pool()
        fs = [pool.submit(scenes), pool.submit(qa),
              pool.submit(head_source), pool.submit(head_mix)]
        for f in fs:
            f.result()

    def quota_path():
        get_all_quotas()
        ledger_writable()

    def budget_path():
        from foleyflow.storage.base import get_io_pool
        pool = get_io_pool()
        sf = pool.submit(store.get_state, pid)
        pf = pool.submit(ledger.project_spend, pid)
        sf.result()
        pf.result()
        ledger_writable()

    def qa_path():
        store.get_project(pid)
        store.load_qa_report(pid)

    def dossier_heads():
        """Minimal dossier-like concurrent HEADs on a few keys."""
        from foleyflow.storage.base import get_io_pool
        state = store.get_state(pid)
        keys = [state.project.input_key]
        if state.latest_mix and state.latest_mix.get("video_key"):
            keys.append(state.latest_mix["video_key"])
        keys.append(store.scenes_key(pid))
        keys.append(store.qa_report_key(pid))

        def one(k):
            try:
                backend.head(k)
            except Exception:
                pass

        pool = get_io_pool()
        list(pool.map(one, keys))

    cold_names = [
        ("GET /projects/{id} fold", project_fold),
        ("GET /quota", quota_path),
        ("GET /budget", budget_path),
        ("GET /qa", qa_path),
        ("dossier heads (sample)", dossier_heads),
    ]

    for name, fn in cold_names:
        # Force cold caches between independent paths where money aggregates
        # would otherwise make later paths free (we still want warm numbers).
        results.append(_time_call(backend, f"{name} [cold]", fn))

    # Warm: re-run each path without invalidating caches.
    for name, fn in cold_names:
        for i in range(warm_passes):
            label = f"{name} [warm]" if warm_passes == 1 else f"{name} [warm#{i+1}]"
            results.append(_time_call(backend, label, fn))

    table = {
        "delay_ms": delay_ms,
        "seed": asdict(stats),
        "measurements": [asdict(m) for m in results],
        "root": root if not tmp_owned else "(temp)",
    }
    return table


def _print_table(table: Dict[str, Any]) -> None:
    seed = table["seed"]
    print()
    print("Auteur latency bench (simulated RTT)")
    print(f"  delay_ms={table['delay_ms']}  "
          f"events={seed['events']}  ledger_rows={seed['ledger_rows']}  "
          f"projects={seed['projects']}")
    print(f"  primary={seed['primary_project_id']}")
    print()
    print(f"{'path':<36} {'ms':>10}  ops")
    print("-" * 72)
    for m in table["measurements"]:
        ops = m.get("ops") or {}
        ops_s = " ".join(f"{k}={v}" for k, v in sorted(ops.items()) if v)
        print(f"{m['name']:<36} {m['wall_ms']:>10.1f}  {ops_s}")
    print()


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--delay-ms", type=float, default=50.0,
                        help="Fixed per-op storage latency (default 50)")
    parser.add_argument("--events", type=int, default=60,
                        help="Target event count on primary project")
    parser.add_argument("--ledger-rows", type=int, default=200,
                        help="Number of ledger transaction objects")
    parser.add_argument("--projects", type=int, default=3)
    parser.add_argument("--json", type=str, default="",
                        help="Write full results JSON to this path")
    parser.add_argument("--root", type=str, default="",
                        help="Reuse a storage root (default: temp dir)")
    parser.add_argument("--label", type=str, default="",
                        help="Optional tag stored in JSON (baseline|after)")
    args = parser.parse_args(argv)

    table = run_bench(
        delay_ms=args.delay_ms,
        n_events=args.events,
        n_ledger=args.ledger_rows,
        n_projects=args.projects,
        root=args.root or None,
    )
    if args.label:
        table["label"] = args.label
    _print_table(table)

    if args.json:
        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(table, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
