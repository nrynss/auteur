#!/usr/bin/env python3
"""Live HTTP latency probe for Auteur control-room hot paths.

Hits a real API base URL (deployed Cloud Run or local uvicorn) and times the
same endpoints measured in handoff-task7.md §7.18.

Usage::

    uv run python scripts/latency_live.py \\
        --base-url https://foleyflow-….run.app \\
        --project-id proj_01KYJD1RKETS3KTX5HA3H5JPDQ

    # Discover a project with a timeline if --project-id is omitted:
    uv run python scripts/latency_live.py --base-url "$URL"

    # Multiple rounds (cold-ish first, then warm polls):
    uv run python scripts/latency_live.py --base-url "$URL" --rounds 3 --json out.json

Does not require provider credentials. Read-only GETs only.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

import httpx

API_PREFIX = "/api/v1"

# Same set as handoff-task7.md §7.18 pre-fix live table.
DEFAULT_PATHS = [
    ("GET /projects/{id}", "project"),
    ("GET /quota", "quota"),
    ("GET /projects/{id}/budget", "budget"),
    ("GET /projects/{id}/qa", "qa"),
    ("GET /projects/{id}/dossier", "dossier"),
    ("GET /projects/{id}/timeline", "timeline"),
    ("GET /projects/{id}/scenes", "scenes"),
]


@dataclass
class Sample:
    name: str
    path: str
    status: int
    wall_ms: float
    ok: bool
    error: Optional[str] = None
    bytes: int = 0


@dataclass
class RoundResult:
    round: int
    samples: List[Sample] = field(default_factory=list)


def _url(base: str, path: str) -> str:
    return urljoin(base.rstrip("/") + "/", path.lstrip("/"))


def _time_get(client: httpx.Client, url: str) -> Sample:
    name_path = url  # filled by caller
    t0 = time.perf_counter()
    try:
        r = client.get(url)
        wall = (time.perf_counter() - t0) * 1000.0
        ok = 200 <= r.status_code < 300
        return Sample(
            name="",
            path=url,
            status=r.status_code,
            wall_ms=wall,
            ok=ok,
            error=None if ok else r.text[:200],
            bytes=len(r.content),
        )
    except Exception as exc:
        wall = (time.perf_counter() - t0) * 1000.0
        return Sample(
            name="",
            path=url,
            status=0,
            wall_ms=wall,
            ok=False,
            error=f"{type(exc).__name__}: {exc}",
        )


def pick_project(client: httpx.Client, base: str,
                 preferred: Optional[str]) -> str:
    if preferred:
        return preferred
    r = client.get(_url(base, f"{API_PREFIX}/projects"))
    r.raise_for_status()
    payload = r.json()
    projects = payload if isinstance(payload, list) else payload.get(
        "projects") or payload.get("items") or []
    if not projects:
        raise SystemExit("No projects on this API; pass --project-id")
    # Prefer a project that looks fully built (has duration / not awaiting).
    for p in projects:
        status = (p.get("status") or "").lower()
        if status in ("needs_review", "ready", "analyzing"):
            return p["id"]
    return projects[0]["id"]


def resolve_paths(project_id: str) -> List[tuple]:
    """Return (display_name, relative_path) for each measured endpoint."""
    pid = project_id
    return [
        ("GET /projects/{id}", f"{API_PREFIX}/projects/{pid}"),
        ("GET /quota", f"{API_PREFIX}/quota"),
        ("GET /projects/{id}/budget", f"{API_PREFIX}/projects/{pid}/budget"),
        ("GET /projects/{id}/qa", f"{API_PREFIX}/projects/{pid}/qa"),
        ("GET /projects/{id}/dossier", f"{API_PREFIX}/projects/{pid}/dossier"),
        ("GET /projects/{id}/timeline",
         f"{API_PREFIX}/projects/{pid}/timeline"),
        ("GET /projects/{id}/scenes", f"{API_PREFIX}/projects/{pid}/scenes"),
    ]


def run_round(client: httpx.Client, base: str, project_id: str,
              round_i: int, paths_filter: Optional[List[str]]) -> RoundResult:
    result = RoundResult(round=round_i)
    for name, rel in resolve_paths(project_id):
        if paths_filter and name not in paths_filter:
            continue
        url = _url(base, rel)
        sample = _time_get(client, url)
        sample.name = name
        sample.path = rel
        result.samples.append(sample)
        tag = "OK" if sample.ok else "FAIL"
        print(f"  [{tag}] {name:32s}  {sample.wall_ms:8.0f} ms  "
              f"HTTP {sample.status}  ({sample.bytes} B)", flush=True)
    return result


def summarize(rounds: List[RoundResult]) -> Dict[str, Any]:
    by_name: Dict[str, List[float]] = {}
    for rnd in rounds:
        for s in rnd.samples:
            if s.ok:
                by_name.setdefault(s.name, []).append(s.wall_ms)
    table = []
    for name, xs in by_name.items():
        table.append({
            "endpoint": name,
            "n": len(xs),
            "first_ms": round(xs[0], 1),
            "min_ms": round(min(xs), 1),
            "median_ms": round(statistics.median(xs), 1),
            "max_ms": round(max(xs), 1),
            "mean_ms": round(statistics.fmean(xs), 1),
        })
    return {"by_endpoint": table}


# Pre-fix live numbers from handoff-task7.md §7.18 (2026-08-03).
BASELINE_LIVE_S = {
    "GET /projects/{id}": 8.4,
    "GET /quota": 11.1,
    "GET /projects/{id}/budget": 25.1,
    "GET /projects/{id}/qa": 7.2,
    "GET /projects/{id}/dossier": 56.0,
    "GET /projects/{id}/timeline": 0.7,
    "GET /projects/{id}/scenes": 0.7,
}


def print_comparison(summary: Dict[str, Any]) -> None:
    print()
    print("Comparison to handoff §7.18 live baseline (2026-08-03):")
    print(f"{'endpoint':32s}  {'baseline':>10s}  {'now (med)':>10s}  "
          f"{'first':>10s}  {'speedup':>8s}")
    print("-" * 80)
    rows = {r["endpoint"]: r for r in summary["by_endpoint"]}
    for name, base_s in BASELINE_LIVE_S.items():
        row = rows.get(name)
        if not row:
            print(f"{name:32s}  {base_s:8.1f} s  {'—':>10s}")
            continue
        med_s = row["median_ms"] / 1000.0
        first_s = row["first_ms"] / 1000.0
        speedup = base_s / med_s if med_s > 0 else float("inf")
        print(f"{name:32s}  {base_s:8.1f} s  {med_s:8.2f} s  "
              f"{first_s:8.2f} s  {speedup:7.1f}×")


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--base-url",
        default=None,
        help="API origin (default: FOLEYFLOW_API env or Cloud Run URL)",
    )
    p.add_argument("--project-id", default=None,
                   help="Project to time (default: auto-pick from list)")
    p.add_argument("--rounds", type=int, default=3,
                   help="How many full passes (default 3: cold-ish + warm)")
    p.add_argument("--timeout", type=float, default=120.0,
                   help="Per-request timeout seconds")
    p.add_argument("--json", default=None, help="Write full results JSON here")
    p.add_argument("--paths", nargs="*", default=None,
                   help="Optional subset of display names to time")
    args = p.parse_args(argv)

    import os
    base = (
        args.base_url
        or os.environ.get("FOLEYFLOW_API")
        or "https://foleyflow-142385844755.us-east4.run.app"
    )

    print(f"Live latency probe → {base}")
    print(f"rounds={args.rounds} timeout={args.timeout}s")

    with httpx.Client(timeout=args.timeout, follow_redirects=True) as client:
        # Health + build identity first.
        t0 = time.perf_counter()
        health = client.get(_url(base, f"{API_PREFIX}/health"))
        health_ms = (time.perf_counter() - t0) * 1000.0
        health.raise_for_status()
        h = health.json()
        print(f"health  {health_ms:.0f} ms  status={h.get('status')}  "
              f"git_sha={h.get('git_sha')}  build_time={h.get('build_time')}")

        project_id = pick_project(client, base, args.project_id)
        print(f"project_id={project_id}")
        print()

        rounds: List[RoundResult] = []
        for i in range(1, args.rounds + 1):
            print(f"--- round {i}/{args.rounds} ---")
            rounds.append(run_round(
                client, base, project_id, i, args.paths))
            print()

    summary = summarize(rounds)
    print_comparison(summary)

    payload = {
        "base_url": base,
        "project_id": project_id,
        "health": h,
        "rounds": [
            {
                "round": r.round,
                "samples": [asdict(s) for s in r.samples],
            }
            for r in rounds
        ],
        "summary": summary,
        "baseline_live_s": BASELINE_LIVE_S,
    }
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
            f.write("\n")
        print(f"\nWrote {args.json}")

    failed = any(not s.ok for r in rounds for s in r.samples)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
