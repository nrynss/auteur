"""`foleyflow` — the single FoleyFlow command.

Project verbs talk to the HTTP API (the contract test against `docs/api.md`).
File verbs (`render`, `direct`) call the media core directly — no server, no
`Client`.

    foleyflow health
    foleyflow create clip.mp4 --title "Western standoff" --analyze --wait
    foleyflow show proj_01K…
    foleyflow timeline proj_01K… --json
    foleyflow render clip.mp4 --from-timeline samples/timelines/clip_1.json
    foleyflow direct timeline.json --notes "…"

## Exit codes

    0  success
    1  the API returned an error (or a file-verb failure)
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
from dotenv import load_dotenv

from foleyflow.analyze import DEFAULT_MODEL, get_video_duration
from foleyflow.direct import apply_notes, default_output_path, diff_timelines
from foleyflow.pipeline import run
from foleyflow.sfx import DEFAULT_CACHE_DIR
from foleyflow.timeline_io import load_timeline, save_timeline

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
    # Always ask for the worker probe: the API leaves it off so a platform
    # liveness check does not depend on someone else's GPU, but an operator
    # running `foleyflow health` is asking exactly that question.
    data = client.get("/health", params={"worker": "true"})

    def render(d):
        print(f"{d['status']}  storage={d['storage']}  version={d['version']}")
        worker = d.get("worker")
        if not worker:
            return
        status = worker.get("status")
        if status == "not_applicable":
            print(f"worker  n/a  (tier {worker.get('tier')} is a hosted API)")
            return
        line = f"worker  {status}"
        if worker.get("device"):
            line += f"  {worker['device']}"
        if worker.get("tier"):
            line += f"  tier={worker['tier']}"
        print(line)
        # A worker demanding a token we do not hold looks healthy from here and
        # fails every generation, so say so rather than leaving it in the JSON.
        if worker.get("auth_required") and worker.get("auth_configured") is False:
            print("WARNING: the worker requires a bearer token and none is "
                  "configured — set FOLEYFLOW_SFX_WORKER_TOKEN.", file=sys.stderr)
        if status == "unreachable":
            print(f"WARNING: {worker.get('detail') or 'worker unreachable'}. "
                  "Cached renders still work; anything needing a fresh stem "
                  "will fail.", file=sys.stderr)

    _emit(data, args.json, render)
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


def cmd_mix(client: Client, args) -> int:
    """Render stems, mix, and mux — the project-scoped counterpart to `render`."""
    body = {"tier": args.tier}
    if args.timeline_version:
        body["timeline_version"] = args.timeline_version
    accepted = client.post(f"/projects/{args.project_id}/mixes", json=body)

    if args.json and not args.wait:
        print(json.dumps(accepted, indent=2))
        return EXIT_OK

    print(f"mix run {accepted['run_id']}  (tier {args.tier})")
    if not args.wait:
        print(f"poll: foleyflow run {args.project_id} {accepted['run_id']}")
        return EXIT_OK

    run = _poll_run(client, args.project_id, accepted["run_id"], quiet=args.json)
    meta = run.get("metadata") or {}
    if run["status"] == "completed" and not args.json:
        # Spend first: it is the number a user is deciding on, and every stem
        # the shared cache already held cost nothing.
        print(f"  {meta.get('generated_count', 0)} of "
              f"{meta.get('stem_count', 0)} stems generated  "
              f"(${meta.get('ledger_delta_usd', 0):.4f} this run)")
        print(f"  QA: {meta.get('qa_status') or 'unknown'}")
        print(f"\nnext: foleyflow export {args.project_id} --wait")
    return _report_run(run)


def cmd_export(client: Client, args) -> int:
    """Publish a rendered mix: manifest, then the exports/latest.json pointer."""
    accepted = client.post(f"/projects/{args.project_id}/exports",
                           json={"override_blocking": args.override_blocking})

    if args.json and not args.wait:
        print(json.dumps(accepted, indent=2))
        return EXIT_OK

    print(f"export run {accepted['run_id']}")
    if not args.wait:
        print(f"poll: foleyflow run {args.project_id} {accepted['run_id']}")
        return EXIT_OK

    run = _poll_run(client, args.project_id, accepted["run_id"], quiet=args.json)
    if run["status"] != "completed":
        return _report_run(run)

    if not args.json:
        meta = run.get("metadata") or {}
        overridden = meta.get("overridden_blockers") or []
        if overridden:
            print(f"  published over QA blockers: {', '.join(overridden)}")
        detail = client.get(f"/projects/{args.project_id}")
        print(f"  manifest: {meta.get('manifest_key')}")
        if detail.get("export_url"):
            print(f"\n{detail['export_url']}")
    return EXIT_OK


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


def cmd_qa(client: Client, args) -> int:
    data = client.get(f"/projects/{args.project_id}/qa")

    def render(d):
        print(f"status: {d['overall_status']}")
        audio = d.get("audio") or {}
        if audio.get("measured"):
            print(f"audio:  {audio['integrated_lufs']:.1f} LUFS  "
                  f"true peak {audio['true_peak_dbtp']:.1f} dBTP  "
                  f"(targets {audio['target_lufs']:.0f} / "
                  f"{audio['target_true_peak_dbtp']:.1f})")
        else:
            print("audio:  not measured yet (needs a mix)")
        for issue in audio.get("issues") or []:
            print(f"  [{issue['severity']}] {issue['code']}"
                  + (f" — {issue['detail']}" if issue.get("detail") else ""))
        visual = d.get("visual") or {}
        v_issues = visual.get("issues") or []
        if not v_issues:
            print("visual: no continuity warnings")
        else:
            print(f"visual: {len(v_issues)} continuity warning(s)")
            for issue in v_issues:
                conf = (f"  conf={issue['confidence']:.2f}"
                        if issue.get("confidence") is not None else "")
                pair = ""
                if issue.get("scene_id") and issue.get("compared_with"):
                    pair = f" {issue['compared_with']}→{issue['scene_id']}"
                print(f"  [{issue['severity']}] {issue['code']}{pair}{conf}"
                      + (f" — {issue['detail']}" if issue.get("detail") else ""))

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


def run_render(args: argparse.Namespace) -> int:
    load_dotenv()
    try:
        run(
            args.clip,
            out_path=args.out,
            tier=args.tier,
            model=args.model,
            timeline_json=args.from_timeline,
            save_timeline_to=args.timeline_out,
            cache_dir=args.cache_dir,
        )
    except Exception as e:
        print(f"foleyflow render: error: {e}", file=sys.stderr)
        return 1
    return 0


def run_direct(args: argparse.Namespace) -> int:
    load_dotenv()
    out_path = args.out or default_output_path(args.timeline)
    if os.path.normcase(os.path.abspath(out_path)) == os.path.normcase(
            os.path.abspath(args.timeline)):
        print(f"foleyflow direct: error: refusing to overwrite the input "
              f"timeline ({args.timeline!r}); pass a different -o.",
              file=sys.stderr)
        return 1

    try:
        old_timeline = load_timeline(args.timeline)
        clip_duration = get_video_duration(args.clip) if args.clip else None
        new_timeline = apply_notes(
            old_timeline, args.notes, model=args.model,
            clip_duration=clip_duration,
        )
        save_timeline(new_timeline, out_path)
    except Exception as e:
        print(f"foleyflow direct: error: {e}", file=sys.stderr)
        return 1

    print(f"[direct] notes applied via {args.model}")
    print(diff_timelines(old_timeline, new_timeline))
    print(f"\nAPPLIED — saved to {out_path}")
    return 0


def cmd_prewarm(args) -> int:
    """Fill the shared SFX cache for a timeline without rendering it.

    Switching the active SFX tier changes every cache key by design — a stem
    generated by MOSS must not be served as if ElevenLabs produced it — so a
    tier switch starts cold, and until now the only way to warm one was to
    perform a full render on it. That is a poor thing to discover during a demo.
    """
    import os

    from foleyflow.assemble import plan_requests
    from foleyflow.sfx_cache import cache_object_key, prewarm
    from foleyflow.storage import StorageError, get_backend
    from foleyflow.tiers import ENV_TIER, TierError, active_tier_name
    from foleyflow.timeline_io import load_timeline
    from foleyflow.workflow import work_dir

    # Routing resolves the SFX endpoint from $FOLEYFLOW_SFX_TIER during
    # plan_requests (via tiers.resolve_endpoint). The mix tier (`--tier`) is a
    # different axis — which events are included — and must not be conflated.
    # Restore the prior env value so a long-lived process (or the test suite)
    # is not left on a different tier after this verb returns.
    previous_tier = os.environ.get(ENV_TIER)
    if args.sfx_tier:
        os.environ[ENV_TIER] = args.sfx_tier

    try:
        try:
            timeline = load_timeline(args.timeline)
        except (OSError, ValueError) as exc:
            return _fail(f"cannot read timeline {args.timeline}: {exc}",
                         EXIT_USAGE)

        duration = args.duration or timeline.duration
        if not duration:
            return _fail("timeline carries no duration; pass --duration",
                         EXIT_USAGE)

        try:
            # `tier=` here is the degradation ladder (ambience|discrete|full),
            # not the SFX provider tier.
            requests = plan_requests(timeline, duration, tier=args.tier)
            active = active_tier_name() or "(none)"
        except TierError as exc:
            # Bad --sfx-tier / $FOLEYFLOW_SFX_TIER is a usage error, same as a
            # missing timeline: the operator typed something the config does
            # not know about.
            return _fail(str(exc), EXIT_USAGE)
        except Exception as exc:
            return _fail(f"cannot plan requests: {type(exc).__name__}: {exc}")

        try:
            backend = get_backend()
        except StorageError as exc:
            return _fail(f"storage unavailable: {exc}")

        # Match sfx_cache.prewarm's hit rule: a non-empty local work-dir copy
        # counts as cached even when B2 was never written (stale work dir from
        # an interrupted render). Without this the count line can overstate
        # "to generate" relative to what prewarm actually bills.
        work = work_dir()
        hits, misses = [], []
        try:
            for request in requests:
                key = cache_object_key(request.endpoint, request.prompt,
                                       request.duration_s, "")
                local = os.path.join(work, os.path.basename(key))
                cached = (
                    (os.path.exists(local) and os.path.getsize(local) > 0)
                    or backend.exists(key)
                )
                (hits if cached else misses).append((request, key))
        except StorageError as exc:
            return _fail(f"cannot probe SFX cache: {exc}")

        print(f"tier {active}  ->  "
              f"{requests[0].endpoint if requests else '(no requests)'}")
        print(f"{len(requests)} stems planned: {len(hits)} cached, "
              f"{len(misses)} to generate")

        if args.verbose:
            for request, key in misses:
                print(f"  MISS {key.split('/')[-1]:<24} "
                      f"{request.duration_s:>5.1f}s  {request.prompt[:60]}")

        if not misses:
            return EXIT_OK
        if args.dry_run:
            print("\n--dry-run: nothing generated.")
            return EXIT_OK

        results = prewarm(backend, work, [r for r, _ in misses],
                          max_concurrency=args.concurrency)
        failed = [r for r in results if not r.ok]
        print(f"generated {len(results) - len(failed)} of {len(results)}")
        if failed:
            for result in failed:
                print(f"  FAILED {result.request.prompt[:60]}: {result.error}",
                      file=sys.stderr)
            return EXIT_API_ERROR
        return EXIT_OK
    finally:
        if args.sfx_tier:
            if previous_tier is None:
                os.environ.pop(ENV_TIER, None)
            else:
                os.environ[ENV_TIER] = previous_tier


def cmd_render(args) -> int:
    return run_render(args)


def cmd_direct(args) -> int:
    return run_direct(args)


# -- parser -----------------------------------------------------------------

def _build_render_parser(*, add_help: bool = True) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="foleyflow render",
        description="AI Foley: silent AI video -> VLM event timeline -> "
                     "per-event generated SFX -> synced soundtrack.",
        add_help=add_help,
    )
    parser.add_argument("clip", help="Path to a silent input video clip (e.g. clip.mp4).")
    parser.add_argument(
        "-o", "--out", default=None,
        help="Output mp4 path (default: <clip stem>.foley.mp4 next to the clip).",
    )
    parser.add_argument(
        "--tier", choices=["ambience", "discrete", "full"], default="full",
        help="Degradation-ladder render tier (default: full).",
    )
    parser.add_argument(
        "--model", default=DEFAULT_MODEL,
        help="VLM model id for analysis. Ignored when --from-timeline is "
             "given, since analysis is skipped.",
    )
    parser.add_argument(
        "--timeline", dest="timeline_out", default=None,
        help="Save the resulting timeline JSON to this path "
             "(default: alongside the output, <out stem>.timeline.json).",
    )
    parser.add_argument(
        "--from-timeline", dest="from_timeline", default=None,
        help="Skip analysis (no paid VLM call) and load the timeline from "
             "this JSON file instead.",
    )
    parser.add_argument(
        "--cache-dir", default=DEFAULT_CACHE_DIR,
        help=f"SFX cache directory (default: {DEFAULT_CACHE_DIR}).",
    )
    return parser


def _build_direct_parser(*, add_help: bool = True) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="foleyflow direct",
        description="Apply plain-language director's notes to an existing "
                     "timeline JSON via a text-only LLM edit.",
        add_help=add_help,
    )
    parser.add_argument(
        "timeline",
        help="Path to an existing timeline JSON (e.g. samples/timelines/clip_1.json).",
    )
    parser.add_argument(
        "--notes", required=True,
        help='Director\'s notes in plain language, e.g. '
             '"move the match strike 200ms later and make it high intensity".',
    )
    parser.add_argument(
        "-o", "--out", default=None,
        help="Output timeline JSON path (default: <in stem>.directed.json "
             "next to the input; the input is NEVER overwritten).",
    )
    parser.add_argument(
        "--model", default=DEFAULT_MODEL,
        help="LLM model id for the edit.",
    )
    parser.add_argument(
        "--clip", default=None,
        help="Optional source video — supplies ffprobe duration so the "
             "edited timeline is clamped against it.",
    )
    return parser


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="foleyflow",
        description="FoleyFlow CLI: project verbs talk to the API; "
                    "render and direct run locally with no server.")
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

    p = project_arg(sub.add_parser("mix", help="render stems, mix, and mux"))
    p.add_argument("--tier", default="full",
                   choices=("ambience", "discrete", "full"))
    p.add_argument("--timeline-version", type=int,
                   help="mix an earlier plan instead of the latest")
    p.add_argument("--wait", action="store_true")
    p.set_defaults(func=cmd_mix)

    p = project_arg(sub.add_parser("export", help="publish the rendered mix"))
    p.add_argument("--override-blocking", action="store_true",
                   help="publish despite blocking QA findings (§13); recorded "
                        "in the event log")
    p.add_argument("--wait", action="store_true")
    p.set_defaults(func=cmd_export)

    p = project_arg(sub.add_parser("timeline", help="show the event timeline"))
    p.add_argument("--version", type=int)
    p.set_defaults(func=cmd_timeline)

    project_arg(sub.add_parser("scenes", help="scene breakdown")).set_defaults(
        func=cmd_scenes)
    project_arg(sub.add_parser("qa", help="latest QA report")).set_defaults(
        func=cmd_qa)
    project_arg(sub.add_parser("events", help="the project's audit trail")
                ).set_defaults(func=cmd_events)
    project_arg(sub.add_parser("runs", help="completed runs")).set_defaults(
        func=cmd_runs)

    p = project_arg(sub.add_parser("run", help="one run's status"))
    p.add_argument("run_id")
    p.add_argument("--wait", action="store_true")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser(
        "prewarm", help="fill the SFX cache for a timeline (no server)",
        description="Generate every stem a timeline needs that is not already "
                    "cached, without rendering. Use after switching SFX tier: "
                    "the cache is keyed on the endpoint, so a tier switch "
                    "starts cold.")
    p.add_argument("timeline", help="Path to a timeline JSON.")
    p.add_argument("--sfx-tier", default=None,
                   help="Warm this SFX tier (paid|fallback|test) instead of "
                        "the configured one. Sets $FOLEYFLOW_SFX_TIER.")
    p.add_argument("--tier", default="full",
                   choices=("ambience", "discrete", "full"),
                   help="Degradation-ladder tier deciding which events are "
                        "included (default: full). Not the SFX tier.")
    p.add_argument("--duration", type=float, default=None,
                   help="Clip duration in seconds. Defaults to the timeline's.")
    p.add_argument("--concurrency", type=int, default=None,
                   help="Parallel generations (default: the fan-out default).")
    p.add_argument("--dry-run", action="store_true",
                   help="Report hits and misses, generate nothing.")
    p.add_argument("-v", "--verbose", action="store_true",
                   help="List each miss.")
    p.set_defaults(func=cmd_prewarm, needs_client=False)

    p = sub.add_parser(
        "render",
        parents=[_build_render_parser(add_help=False)],
        help="render a clip locally (no server)",
    )
    p.set_defaults(func=cmd_render, needs_client=False)

    p = sub.add_parser(
        "direct",
        parents=[_build_direct_parser(add_help=False)],
        help="apply director's notes to a timeline JSON (no server)",
    )
    p.set_defaults(func=cmd_direct, needs_client=False)

    return parser


def main(argv: Optional[list] = None, client: Optional[Client] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if getattr(args, "needs_client", True):
            client = client or Client(args.api)
            return args.func(client, args)
        return args.func(args)
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
