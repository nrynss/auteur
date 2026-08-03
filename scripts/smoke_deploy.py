#!/usr/bin/env python3
"""Post-deploy smoke check for an Auteur API base URL.

The same checks operators run by hand after every deploy: health + worker
probe, build identity, storage, ledger_writable, and one read endpoint with
real content. Exits non-zero on any failure so it can be the last step of a
deploy script.

Catches two failures already seen in production:
  1. Stale revision (reported git_sha does not match the commit just deployed)
  2. FOLEYFLOW_LEDGER_PATH forcing an unwritable / ephemeral local ledger
     (ledger_writable false)

Also catches the structural one the status code alone cannot: the "UI not
built" page is deliberately a 200 (decision 8, local dev), so a bundle-less
image — one whose Node stage silently produced nothing — passes any check
that only looks at the status of `GET /`. The `spa` check asserts the built
SPA specifically.

Usage:
  uv run python scripts/smoke_deploy.py --base-url https://… \\
      [--expect-sha SHA] [--timeout S]

Default --expect-sha is `git rev-parse HEAD` when run inside a git checkout;
otherwise --expect-sha is required.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from typing import Any, List, Optional, Sequence
from urllib.parse import urljoin

import httpx

API_PREFIX = "/api/v1"


@dataclass
class CheckResult:
    name: str
    ok: bool
    message: str

    def line(self) -> str:
        tag = "OK" if self.ok else "FAIL"
        return f"{tag}  {self.name}: {self.message}"


def _git_head() -> Optional[str]:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
        )
        sha = out.strip()
        return sha or None
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return None


def _sha_matches(reported: Optional[str], expected: str) -> bool:
    """Match short/full SHAs either way, with minimum prefix length.

    - Both sides full-length (>= 40 hex chars): require exact equality.
    - Otherwise: prefix match either way, but the shorter side must be at
      least 7 characters so a tiny fragment cannot accidentally match.
    """
    if not reported:
        return False
    a, b = reported.lower(), expected.lower()
    if len(a) >= 40 and len(b) >= 40:
        return a == b
    if a == b:
        return True
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    if len(shorter) < 7:
        return False
    return longer.startswith(shorter)


def _url(base: str, path: str) -> str:
    base = base.rstrip("/") + "/"
    return urljoin(base, path.lstrip("/"))


def _get_json(
    client: httpx.Client,
    url: str,
    *,
    params: Optional[dict] = None,
) -> tuple[int, Any, Optional[str]]:
    """Return (status_code, body_or_None, error_message)."""
    try:
        r = client.get(url, params=params)
    except httpx.RequestError as e:
        return 0, None, f"request failed: {e}"
    try:
        body = r.json()
    except (json.JSONDecodeError, ValueError):
        body = None
    return r.status_code, body, None


def check_git_sha(
    client: httpx.Client,
    base: str,
    expect_sha: str,
    *,
    health_body: Optional[dict] = None,
) -> CheckResult:
    body = health_body
    if body is None:
        status, body, err = _get_json(
            client, _url(base, f"{API_PREFIX}/health"), params={"worker": "true"},
        )
        if err:
            return CheckResult("git_sha", False, err)
        if status != 200 or not isinstance(body, dict):
            return CheckResult(
                "git_sha", False,
                f"HTTP {status}, cannot read git_sha",
            )

    reported = body.get("git_sha") if isinstance(body, dict) else None
    if reported is None:
        return CheckResult(
            "git_sha", False,
            f"git_sha is null (unstamped deploy); expected {expect_sha}",
        )
    if not _sha_matches(reported, expect_sha):
        return CheckResult(
            "git_sha", False,
            f"reported={reported!r} does not match expected={expect_sha!r} "
            f"(production may be stale)",
        )
    return CheckResult(
        "git_sha", True,
        f"reported={reported!r} matches expected={expect_sha!r}",
    )


def check_ledger_writable(client: httpx.Client, base: str) -> CheckResult:
    status, body, err = _get_json(client, _url(base, f"{API_PREFIX}/quota"))
    if err:
        return CheckResult("ledger_writable", False, err)
    if status != 200 or not isinstance(body, dict):
        return CheckResult(
            "ledger_writable", False,
            f"HTTP {status}, expected 200 JSON quota response",
        )
    writable = body.get("ledger_writable")
    if writable is not True:
        return CheckResult(
            "ledger_writable", False,
            f"ledger_writable={writable!r} (must be true; FOLEYFLOW_LEDGER_PATH "
            f"may force ephemeral local-file mode, or storage writes failed)",
        )
    return CheckResult("ledger_writable", True, "ledger_writable=true")


def check_projects_list(client: httpx.Client, base: str) -> CheckResult:
    status, body, err = _get_json(
        client, _url(base, f"{API_PREFIX}/projects"), params={"limit": "1"},
    )
    if err:
        return CheckResult("projects", False, err)
    if status != 200 or not isinstance(body, dict):
        return CheckResult(
            "projects", False,
            f"HTTP {status}, expected 200 JSON project list",
        )
    projects = body.get("projects")
    if not isinstance(projects, list):
        return CheckResult(
            "projects", False,
            f"response missing projects list: keys={list(body.keys())}",
        )
    n = len(projects)
    return CheckResult("projects", True, f"HTTP 200, {n} project(s) on page")


def check_read_content(client: httpx.Client, base: str) -> CheckResult:
    """Dossier for a real project when one exists; else OpenAPI document."""
    status, body, err = _get_json(
        client, _url(base, f"{API_PREFIX}/projects"), params={"limit": "1"},
    )
    if err:
        return CheckResult("read_content", False, err)
    if status != 200 or not isinstance(body, dict):
        return CheckResult(
            "read_content", False,
            f"projects list HTTP {status}; cannot probe dossier",
        )
    projects = body.get("projects") or []
    if projects:
        pid = projects[0].get("id") if isinstance(projects[0], dict) else None
        if not pid:
            return CheckResult(
                "read_content", False,
                "project list entry has no id",
            )
        d_status, d_body, d_err = _get_json(
            client, _url(base, f"{API_PREFIX}/projects/{pid}/dossier"),
        )
        if d_err:
            return CheckResult("read_content", False, d_err)
        if d_status != 200 or not isinstance(d_body, dict):
            return CheckResult(
                "read_content", False,
                f"dossier HTTP {d_status} for {pid}",
            )
        # Real content: project_id echo + artifact listing fields present.
        if d_body.get("project_id") != pid:
            return CheckResult(
                "read_content", False,
                f"dossier body project_id={d_body.get('project_id')!r} "
                f"!= requested {pid!r}",
            )
        if "artifacts" not in d_body and "artifact_count" not in d_body:
            return CheckResult(
                "read_content", False,
                "dossier 200 but body lacks artifacts / artifact_count",
            )
        count = d_body.get("artifact_count", len(d_body.get("artifacts") or []))
        return CheckResult(
            "read_content", True,
            f"dossier {pid} HTTP 200, artifact_count={count}",
        )

    # No projects yet — fall back to OpenAPI so we still prove a content body.
    o_status, o_body, o_err = _get_json(
        client, _url(base, f"{API_PREFIX}/openapi.json"),
    )
    if o_err:
        return CheckResult("read_content", False, o_err)
    if o_status != 200 or not isinstance(o_body, dict):
        return CheckResult(
            "read_content", False,
            f"no projects; openapi HTTP {o_status}",
        )
    if "openapi" not in o_body and "paths" not in o_body:
        return CheckResult(
            "read_content", False,
            "no projects; openapi 200 but body not an OpenAPI document",
        )
    return CheckResult(
        "read_content", True,
        "no projects yet; openapi.json HTTP 200 with document body",
    )


# Marker that the *built* SPA was served — the bundle's mount point. The
# "UI not built" page never contains it.
_BUILT_SPA_MARKER = '<div id="app">'
_UNBUILT_MARKER = "not built"


def check_spa(client: httpx.Client, base: str) -> CheckResult:
    """GET / must serve the built SPA, not the "UI not built" page.

    The unbuilt page is deliberately a 200 (local-dev friendly, decision 8),
    so a status-code probe cannot tell a bundle-less image from a healthy
    one. A deployed image always has the bundle; if this check fails, the
    Node stage produced nothing and the deploy is broken even though /health
    is green.
    """
    try:
        r = client.get(_url(base, "/"))
    except httpx.RequestError as e:
        return CheckResult("spa", False, f"request failed: {e}")
    if r.status_code != 200:
        return CheckResult("spa", False, f"HTTP {r.status_code} on GET /")
    content_type = r.headers.get("content-type", "")
    if not content_type.startswith("text/html"):
        return CheckResult(
            "spa", False,
            f"GET / returned {content_type!r}, expected text/html",
        )
    if _UNBUILT_MARKER in r.text:
        return CheckResult(
            "spa", False,
            "GET / returned the 'UI not built' page — the image has no bundle",
        )
    if _BUILT_SPA_MARKER not in r.text:
        return CheckResult(
            "spa", False,
            "GET / returned HTML but without the built SPA mount point",
        )
    return CheckResult("spa", True, "GET / serves the built SPA")


def run_checks(
    base_url: str,
    expect_sha: str,
    *,
    timeout: float = 30.0,
    client: Optional[httpx.Client] = None,
) -> List[CheckResult]:
    """Run all smoke checks. Order matches the post-deploy checklist."""
    own_client = client is None
    if client is None:
        client = httpx.Client(timeout=timeout, follow_redirects=True)
    try:
        # Single health fetch shared by health/storage/worker and git_sha.
        status, health_body, err = _get_json(
            client,
            _url(base_url, f"{API_PREFIX}/health"),
            params={"worker": "true"},
        )
        results: List[CheckResult] = []

        if err:
            results.append(CheckResult("health", False, err))
            results.append(CheckResult("storage", False, "skipped (health unreachable)"))
            results.append(CheckResult("worker", False, "skipped (health unreachable)"))
            results.append(CheckResult("git_sha", False, "skipped (health unreachable)"))
        elif status != 200 or not isinstance(health_body, dict):
            results.append(CheckResult(
                "health", False,
                f"HTTP {status}, expected 200 JSON",
            ))
            results.append(CheckResult("storage", False, "skipped (health not 200)"))
            results.append(CheckResult("worker", False, "skipped (health not 200)"))
            results.append(CheckResult("git_sha", False, "skipped (health not 200)"))
        else:
            top = health_body.get("status")
            results.append(CheckResult(
                "health",
                top in ("ok", "degraded"),
                f"status={top!r} version={health_body.get('version')!r}",
            ))
            storage = health_body.get("storage")
            results.append(CheckResult(
                "storage",
                storage == "ok",
                f"storage={storage!r}" + (
                    "" if storage == "ok"
                    else " (must be ok; writes will fail later)"
                ),
            ))
            worker = health_body.get("worker")
            if worker is None or not isinstance(worker, dict):
                results.append(CheckResult(
                    "worker", False,
                    "worker probe missing (requested worker=true; "
                    "field absent or null)",
                ))
            elif "status" not in worker:
                results.append(CheckResult(
                    "worker", False,
                    f"worker present but no status field: {worker!r}",
                ))
            else:
                wstatus = worker.get("status")
                detail = worker.get("detail")
                extra = (
                    f" detail={detail!r}"
                    if detail and wstatus != "ok" else ""
                )
                results.append(CheckResult(
                    "worker", True,
                    f"status={wstatus!r} tier={worker.get('tier')!r}{extra}",
                ))
            results.append(check_git_sha(
                client, base_url, expect_sha, health_body=health_body,
            ))

        results.append(check_ledger_writable(client, base_url))
        results.append(check_projects_list(client, base_url))
        results.append(check_read_content(client, base_url))
        results.append(check_spa(client, base_url))
        return results
    finally:
        if own_client:
            client.close()


def all_passed(results: Sequence[CheckResult]) -> bool:
    return all(r.ok for r in results)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="smoke_deploy.py",
        description=(
            "Post-deploy smoke check: health, storage, worker, git_sha, "
            "ledger_writable, a read endpoint, and the built SPA at /."
        ),
    )
    p.add_argument(
        "--base-url", required=True,
        help="API base URL (e.g. https://foleyflow-….run.app)",
    )
    p.add_argument(
        "--expect-sha", default=None,
        help=(
            "Expected git SHA reported by /health (prefix match ok). "
            "Default: git rev-parse HEAD when available."
        ),
    )
    p.add_argument(
        "--timeout", type=float, default=30.0, metavar="S",
        help="HTTP timeout in seconds (default: 30)",
    )
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    expect = args.expect_sha or _git_head()
    if not expect:
        print(
            "FAIL  setup: --expect-sha required (not in a git repo and no "
            "default SHA)",
            file=sys.stderr,
        )
        return 2

    results = run_checks(args.base_url, expect, timeout=args.timeout)
    for r in results:
        print(r.line())
    if all_passed(results):
        print(f"smoke ok  base={args.base_url}  expect_sha={expect}")
        return 0
    failed = [r.name for r in results if not r.ok]
    print(f"smoke FAILED  checks={','.join(failed)}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
