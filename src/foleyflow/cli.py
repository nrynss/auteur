"""`foleyflow` — a command-line client for the HTTP API.

Talks to the API over HTTP rather than importing the domain packages. That is
the point: a second client written only against `docs/api.md` is the test of
whether the contract is actually complete, and anything the CLI cannot do is a
gap in the API rather than a gap in the CLI.

Distinct from `foley`, which is the ported media core's local renderer and
speaks to no server at all.

    foleyflow health
    foleyflow create clip.mp4 --title "Western standoff" --analyze --wait
    foleyflow show proj_01K…
    foleyflow timeline proj_01K… --json

## Exit codes

    0  success
    1  the API returned an error
    2  usage error, or the API is unreachable
    3  quota exhausted — distinct so a scheduled job can back off until the
       reset rather than retrying into a wall
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
import time
from typing import Any, Optional

import httpx

DEFAULT_BASE_URL = os.environ.get("FOLEYFLOW_API", "http://127.0.0.1:8000")
API_PREFIX = "/api/v1"

EXIT_OK, EXIT_API_ERROR, EXIT_USAGE, EXIT_QUOTA = 0, 1, 2, 3

# Long enough for a 26 MB upload on a slow link.
UPLOAD_TIMEOUT_S = 600.0
POLL_INTERVAL_S = 2.0


class ApiError(Exception):
    """A structured error from the API. One shape, so one place unwraps it."""

    def __init__(self, code: str, message: str, detail: Optional[dict] = None):
        super().__init__(message)
        self.code, self.message, self.detail = code, message, detail or {}


class Client:
    def __init__(self, base_url: str = DEFAULT_BASE_URL,
                 http: Optional[httpx.Client] = None):
        self.base_url = base_url.rstrip("/")
        # Injectable so tests can drive an in-process ASGI app rather than
        # standing up a server.
        self._http = http or httpx.Client(timeout=30.0)

    def request(self, method: str, path: str, **kwargs) -> Any:
        url = f"{self.base_url}{API_PREFIX}{path}"
        try:
            response = self._http.request(method, url, **kwargs)
        except httpx.RequestError as e:
            raise SystemExit(_fail(
                f"cannot reach the API at {self.base_url} ({type(e).__name__}). "
                "Is it running? Set FOLEYFLOW_API to point elsewhere.",
                EXIT_USAGE))

        if response.status_code == 204:
            return None
        try:
            body = response.json()
        except ValueError:
            body = None

        if response.is_success:
            return body
        if isinstance(body, dict) and "error" in body:
            err = body["error"]
            raise ApiError(err.get("code", "unknown"),
                           err.get("message", ""), err.get("detail"))
        raise ApiError("http_error", f"HTTP {response.status_code}")

    def get(self, path: str, **kw):
        return self.request("GET", path, **kw)

    def post(self, path: str, **kw):
        return self.request("POST", path, **kw)


# -- output -----------------------------------------------------------------

def _fail(message: str, code: int = EXIT_API_ERROR) -> int:
    print(f"error: {message}", file=sys.stderr)
    return code


def _emit(data: Any, as_json: bool, render) -> None:
    """Machine output or human output — never both, never human output that a
    script might try to parse."""
    if as_json:
        print(json.dumps(data, indent=2))
    else:
        render(data)


def _state_marker(state: str) -> str:
    return {"ok": "OK  ", "warn": "WARN", "blocked": "STOP", "pending": "--  "}.get(
        state, "?   ")


# -- run polling ------------------------------------------------------------

def _poll_run(client: Client, project_id: str, run_id: str,
              quiet: bool = False) -> dict:
    """Block until a run reaches a terminal state.

    Every mutation returns 202 plus a run id; without this the CLI would be
    unusable for anything that actually does work.
    """
    spinner, i = "|/-\\", 0
    while True:
        run = client.get(f"/projects/{project_id}/runs/{run_id}")
        if run["status"] != "running":
            if not quiet:
                print(f"\r  run {run_id} {run['status']}    ")
            return run
        if not quiet and sys.stderr.isatty():
            print(f"\r  {spinner[i % 4]} {run['kind']} running…", end="", file=sys.stderr)
            i += 1
        time.sleep(POLL_INTERVAL_S)


def _report_run(run: dict) -> int:
    if run["status"] == "completed":
        return EXIT_OK
    # error is a classification, not the exception text — see docs/api.md §3.6.
    return _fail(f"run {run['id']} failed: "
                 f"{run.get('error_code') or 'unknown'} ({run.get('error') or ''})")


# -- commands ---------------------------------------------------------------

def cmd_health(client: Client, args) -> int:
    data = client.get("/health")
    _emit(data, args.json, lambda d: print(
        f"{d['status']}  storage={d['storage']}  version={d['version']}"))
    return EXIT_OK if data["status"] == "ok" else EXIT_API_ERROR


def cmd_quota(client: Client, args) -> int:
    data = client.get("/quota")

    def render(d):
        if not d.get("ledger_writable", True):
            print("WARNING: the ledger is not writable — these figures are not "
                  "merely imprecise, they are fiction.\n", file=sys.stderr)
        for m in d["models"]:
            day = m["per_day"]
            print(f"{_state_marker(m['status'])} {m['model_id']:<28} "
                  f"{day['used']}/{day['limit']} today")
        print(f"\nEstimates counted from our own ledger, not reported by the "
              f"provider.")

    _emit(data, args.json, render)
    return EXIT_OK


def cmd_list(client: Client, args) -> int:
    data = client.get("/projects", params={"limit": args.limit})

    def render(d):
        if not d["projects"]:
            print("no projects")
            return
        for p in d["projects"]:
            dur = f"{p['duration_s']:.1f}s" if p.get("duration_s") else "—"
            print(f"{p['id']}  {p['status']:<16} {dur:>7}  {p['title']}")

    _emit(data, args.json, render)
    return EXIT_OK


def cmd_show(client: Client, args) -> int:
    data = client.get(f"/projects/{args.project_id}")

    def render(d):
        print(f"{d['title']}  ({d['id']})")
        print(f"status: {d['status']}   readiness: {d['readiness_percent']}%")
        print()
        for item in d["readiness"]:
            detail = f"  — {item['detail']}" if item.get("detail") else ""
            print(f"  {_state_marker(item['state'])} {item['label']}{detail}")
        print()
        print(f"scenes: {d['scene_count']}   timeline: "
              f"v{d['timeline_version'] or '—'}   events: {d['event_count']}")
        if d.get("blocks_export"):
            print("\nexport blocked")

    _emit(data, args.json, render)
    return EXIT_OK


def cmd_create(client: Client, args) -> int:
    """Create a project, upload the file, confirm it, and optionally analyze.

    The whole intake flow in one command. It exercises the presigned-PUT path,
    which is the part no browser-free client could otherwise reach.
    """
    path = args.video
    if not os.path.isfile(path):
        return _fail(f"no such file: {path}", EXIT_USAGE)

    filename = os.path.basename(path)
    content_type = mimetypes.guess_type(filename)[0] or "video/mp4"
    title = args.title or os.path.splitext(filename)[0]

    created = client.post("/projects", json={
        "title": title, "filename": filename, "content_type": content_type})
    project_id = created["project"]["id"]
    upload = created["upload"]
    print(f"project {project_id}")

    size = os.path.getsize(path)
    print(f"uploading {filename} ({size / 1e6:.1f} MB) direct to B2…")
    with open(path, "rb") as f:
        put = httpx.put(upload["url"], content=f, timeout=UPLOAD_TIMEOUT_S,
                        headers=upload.get("headers") or {})
    if put.status_code >= 400:
        # The project record survives deliberately: it plus its event log
        # explain exactly how far the intake got.
        return _fail(f"upload failed (HTTP {put.status_code}). The project "
                     f"record remains at {project_id}.")

    confirmed = client.post(f"/projects/{project_id}/uploaded")
    run = _poll_run(client, project_id, confirmed["run_id"], quiet=args.json)
    if run["status"] != "completed":
        return _report_run(run)

    if args.analyze:
        return _analyze(client, project_id, model=args.model, force=False,
                        wait=args.wait, as_json=args.json)

    print(f"\nnext: foleyflow analyze {project_id} --wait")
    return EXIT_OK


def _analyze(client: Client, project_id: str, *, model: Optional[str],
             force: bool, wait: bool, as_json: bool) -> int:
    body = {"force": force}
    if model:
        body["model"] = model
    accepted = client.post(f"/projects/{project_id}/analyze", json=body)

    if as_json and not wait:
        print(json.dumps(accepted, indent=2))
        return EXIT_OK

    est = accepted.get("estimated_cost_usd")
    print(f"analysis run {accepted['run_id']}"
          + (f"  (est. ${est:.4f})" if est else ""))
    if not wait:
        print(f"poll: foleyflow run {project_id} {accepted['run_id']}")
        return EXIT_OK

    return _report_run(_poll_run(client, project_id, accepted["run_id"],
                                 quiet=as_json))


def cmd_analyze(client: Client, args) -> int:
    return _analyze(client, args.project_id, model=args.model, force=args.force,
                    wait=args.wait, as_json=args.json)


def cmd_timeline(client: Client, args) -> int:
    params = {"version": args.version} if args.version else None
    data = client.get(f"/projects/{args.project_id}/timeline", params=params)

    def render(d):
        print(f"timeline v{d['version']}  ({d['duration']:.2f}s)")
        print(f"ambience: {d['ambience']}\n")
        print(f"{'#':>3}  {'start':>7}  {'scene':<10} {'kind':<11} {'int':<5} event")
        for e in d["events"]:
            print(f"{e['index']:>3}  {e['start_time']:>6.2f}s  "
                  f"{e.get('scene_id') or '—':<10} {e['kind']:<11} "
                  f"{e['intensity']:<5} {e['name']}")

    _emit(data, args.json, render)
    return EXIT_OK


def cmd_scenes(client: Client, args) -> int:
    data = client.get(f"/projects/{args.project_id}/scenes")

    def render(d):
        if not d["scenes"]:
            print("no scenes — run analysis first")
            return
        print(f"{'id':<10} {'start':>7} {'end':>8} {'dur':>7} {'luma':>7}")
        for s in d["scenes"]:
            luma = f"{s['stats']['mean_luma']:.0f}" if s.get("stats") else "—"
            print(f"{s['id']:<10} {s['start_s']:>6.2f}s {s['end_s']:>7.2f}s "
                  f"{s['duration_s']:>6.2f}s {luma:>7}")

    _emit(data, args.json, render)
    return EXIT_OK


def cmd_events(client: Client, args) -> int:
    data = client.get(f"/projects/{args.project_id}/events")

    def render(d):
        for e in d["events"]:
            run = f"  {e['run_id'][:16]}" if e.get("run_id") else ""
            print(f"{e['created_at'][11:19]}  "
                  f"{e['type'].replace('postproduction.', ''):<34} "
                  f"{e['actor']:<8}{run}")

    _emit(data, args.json, render)
    return EXIT_OK


def cmd_runs(client: Client, args) -> int:
    data = client.get(f"/projects/{args.project_id}/runs")

    def render(d):
        if not d["runs"]:
            print("no completed runs")
            return
        for r in d["runs"]:
            extra = f"  {r['error_code']}" if r.get("error_code") else ""
            print(f"{r['id']}  {r['kind']:<10} {r['status']:<10}{extra}")

    _emit(data, args.json, render)
    return EXIT_OK


def cmd_run(client: Client, args) -> int:
    run = (_poll_run(client, args.project_id, args.run_id, quiet=args.json)
           if args.wait
           else client.get(f"/projects/{args.project_id}/runs/{args.run_id}"))
    _emit(run, args.json, lambda r: print(
        f"{r['id']}  {r['kind']}  {r['status']}"
        + (f"  {r.get('error_code')}" if r.get("error_code") else "")))
    return _report_run(run) if run["status"] != "running" else EXIT_OK


# -- parser -----------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="foleyflow",
        description="Command-line client for the FoleyFlow API. "
                    "Use `foley` instead for local rendering.")
    parser.add_argument("--api", default=DEFAULT_BASE_URL,
                        help=f"API base URL (default {DEFAULT_BASE_URL}, "
                             "or $FOLEYFLOW_API)")
    parser.add_argument("--json", action="store_true",
                        help="emit raw JSON instead of a human-readable table")
    sub = parser.add_subparsers(dest="command", required=True)

    def project_arg(p):
        p.add_argument("project_id")
        return p

    sub.add_parser("health", help="API and storage reachability").set_defaults(
        func=cmd_health)
    sub.add_parser("quota", help="provider budget (estimated)").set_defaults(
        func=cmd_quota)

    p = sub.add_parser("list", help="list projects")
    p.add_argument("--limit", type=int, default=25)
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("create", help="create a project and upload a video")
    p.add_argument("video")
    p.add_argument("--title")
    p.add_argument("--analyze", action="store_true",
                   help="start analysis once the upload is confirmed")
    p.add_argument("--model", help="override the analysis model")
    p.add_argument("--wait", action="store_true",
                   help="block until analysis finishes")
    p.set_defaults(func=cmd_create)

    project_arg(sub.add_parser("show", help="project detail and readiness")
                ).set_defaults(func=cmd_show)

    p = project_arg(sub.add_parser("analyze", help="start analysis"))
    p.add_argument("--model")
    p.add_argument("--force", action="store_true",
                   help="bypass the timeline cache and spend a request")
    p.add_argument("--wait", action="store_true")
    p.set_defaults(func=cmd_analyze)

    p = project_arg(sub.add_parser("timeline", help="show the event timeline"))
    p.add_argument("--version", type=int)
    p.set_defaults(func=cmd_timeline)

    project_arg(sub.add_parser("scenes", help="scene breakdown")).set_defaults(
        func=cmd_scenes)
    project_arg(sub.add_parser("events", help="the project's audit trail")
                ).set_defaults(func=cmd_events)
    project_arg(sub.add_parser("runs", help="completed runs")).set_defaults(
        func=cmd_runs)

    p = project_arg(sub.add_parser("run", help="one run's status"))
    p.add_argument("run_id")
    p.add_argument("--wait", action="store_true")
    p.set_defaults(func=cmd_run)

    return parser


def main(argv: Optional[list] = None, client: Optional[Client] = None) -> int:
    args = build_parser().parse_args(argv)
    client = client or Client(args.api)
    try:
        return args.func(client, args)
    except ApiError as e:
        if e.code == "quota_exhausted":
            resets = e.detail.get("resets_at")
            return _fail(f"{e.message}"
                         + (f" — resets at {resets}" if resets else ""),
                         EXIT_QUOTA)
        return _fail(f"{e.code}: {e.message}")
    except KeyboardInterrupt:
        return _fail("interrupted", EXIT_USAGE)


if __name__ == "__main__":
    sys.exit(main())
