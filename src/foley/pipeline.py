"""run() -- the single library orchestrator -- T4.

analyze -> route (for the printout) -> assemble (routes + sfx internally) ->
mux. CLI, demo scripts, and the conditional web shell are all thin callers
of `run()`; none of them should re-implement this sequence.

Printing is deliberately plain (print + f-strings, no formatting libs) so it
reads well live in a terminal during the demo: timeline table -> per-event
routing/sourcing -> assembly progress -> footer with the ledger delta.
"""

import os
from dataclasses import dataclass, field
from typing import Callable, List, Optional

from foley.analyze import DEFAULT_MODEL, analyze_video, get_video_duration
from foley.assemble import assemble_timeline, write_mix
from foley.ledger import summarize_ledger
from foley.mux import extract_audio, mux as mux_video
from foley.routing import route_ambience, route_event
from foley.sfx import DEFAULT_CACHE_DIR, _resolve_cache_dir, cache_key, cache_path, get_sfx
from foley.timeline_io import load_timeline, save_timeline


@dataclass
class FoleyResult:
    out_path: str
    timeline_path: str
    mix_path: str
    events: List[dict] = field(default_factory=list)
    tier: str = "full"
    ledger_delta: float = 0.0
    duration_s: float = 0.0


def _default_out_path(clip_path: str) -> str:
    stem, _ = os.path.splitext(clip_path)
    return f"{stem}.foley.mp4"


def _default_timeline_path(out_path: str) -> str:
    stem, _ = os.path.splitext(out_path)
    return f"{stem}.timeline.json"


def _default_mix_path(out_path: str) -> str:
    stem, _ = os.path.splitext(out_path)
    return f"{stem}.mix.wav"


def _is_cached(cache_dir: str, endpoint: str, prompt: str, duration_s: float) -> bool:
    resolved = _resolve_cache_dir(cache_dir)
    return os.path.exists(cache_path(resolved, endpoint, prompt, duration_s))


def run(clip_path: str, out_path: Optional[str] = None, tier: str = "full",
        model: str = DEFAULT_MODEL, timeline_json: Optional[str] = None,
        save_timeline_to: Optional[str] = None,
        cache_dir: str = DEFAULT_CACHE_DIR,
        progress_cb: Optional[Callable[[dict], None]] = None) -> FoleyResult:
    """Analyze (or load) -> route -> assemble -> mux one clip. Returns a
    FoleyResult describing what happened.

    timeline_json: path to a saved timeline JSON. When given, analysis is
    skipped entirely (no VLM call, $0) -- this is the demo/deterministic path
    (`--from-timeline`). When omitted, analyze_video(strategy="direct") runs
    (paid; fails loud, no silent fallback -- see build_spec.md T0).

    progress_cb: optional callable receiving progress dicts so a caller (the
    web UI) can show live per-event status. Payloads:
      {"phase": "plan", "events": [events_report rows]}   after routing
      {"phase": "sfx_start", "indices": [...], "cached": bool}
      {"phase": "sfx_done",  "indices": [...], "cached": bool}
      {"phase": "stage", "stage": "assembling"|"muxing"|"done"}
    indices refer to events_report rows (several events can share one cache
    key and therefore one SFX request). Callback errors are swallowed.
    """
    def _notify(payload: dict) -> None:
        if progress_cb is None:
            return
        try:
            progress_cb(payload)
        except Exception:
            pass
    out_path = out_path or _default_out_path(clip_path)
    timeline_path = save_timeline_to or _default_timeline_path(out_path)

    # Ledger delta bookkeeping for the WHOLE run — snapshot before the
    # timeline step, or a paid analyze_video call would be excluded from
    # the footer's "this run" figure.
    ledger_before = summarize_ledger()["total"]

    # a. ffprobe is the sole duration authority.
    duration = get_video_duration(clip_path)
    print(f"[pipeline] clip duration (ffprobe): {duration:.2f}s", flush=True)

    # b. Timeline: load (no analysis call) or analyze (paid, fail loud).
    if timeline_json:
        print(f"[pipeline] loading timeline from {timeline_json} (no analysis call, $0)", flush=True)
        timeline = load_timeline(timeline_json)
    else:
        print(f"[pipeline] analyzing {clip_path} with model={model} (PAID VLM call)...", flush=True)
        timeline = analyze_video(clip_path, strategy="direct", model=model)

    # c. Announce timeline source provenance (T5b follow-up item 2)
    history_len = len(timeline.history) if hasattr(timeline, "history") and timeline.history else 0
    if history_len > 0:
        print(f"[pipeline] timeline source: DIRECTED — {history_len} edit{'s' if history_len != 1 else ''} applied over VLM original", flush=True)
    else:
        print(f"[pipeline] timeline source: VLM, unedited", flush=True)

    # d. Save the timeline (default alongside out_path, .timeline.json).
    save_timeline(timeline, timeline_path)
    print(f"[pipeline] timeline saved -> {timeline_path}", flush=True)

    # -- timeline table --
    print(flush=True)
    print(f"{'start':>7}  {'kind':<10}  {'intensity':<9}  name", flush=True)
    for ev in timeline.events:
        print(f"{ev.start_time:7.2f}  {ev.kind:<10}  {ev.intensity:<9}  {ev.name}", flush=True)

    # -- per-event routing / sourcing --
    print(flush=True)
    events_report: List[dict] = []

    # Helper to check if event is active for tier
    def _is_active(k):
        if tier == "ambience":
            return False
        if tier == "discrete":
            return k == "discrete"
        return True

    from foley.ledger import estimate_cost as _est_cost

    amb_req = route_ambience(timeline.ambience, duration)
    amb_cached = _is_cached(cache_dir, amb_req.endpoint, amb_req.prompt, amb_req.duration_s)
    amb_cost_est = _est_cost(amb_req.endpoint, {})
    
    if amb_cached:
        amb_status = "cached · $0"
    else:
        amb_status = f"will generate (~${amb_cost_est:.2f})"

    print(f"[route] ambience: {amb_req.category} -> {amb_req.endpoint} "
          f"({amb_status})", flush=True)

    # cache-key -> events_report row indices, for live per-event progress:
    # assemble only hands _sfx_fn the SfxRequest, so the key is the join.
    request_key_to_indices: dict = {}

    events_report.append({
        "name": timeline.ambience,
        "kind": "ambience",
        "intensity": "",
        "start_time": 0.0,
        "category": amb_req.category,
        "endpoint": amb_req.endpoint,
        "cached": amb_cached,
        "skipped": False,
        "cost_est": amb_cost_est,
        "status": amb_status,
        "state": "cached" if amb_cached else "pending",
    })
    request_key_to_indices.setdefault(
        cache_key(amb_req.endpoint, amb_req.prompt, amb_req.duration_s), []).append(0)

    for ev in timeline.events:
        req = route_event(ev)
        cached = _is_cached(cache_dir, req.endpoint, req.prompt, req.duration_s)
        cost_est = _est_cost(req.endpoint, {})
        
        active = _is_active(ev.kind)
        if not active:
            status = "skipped by tier"
        elif cached:
            status = "cached · $0"
        else:
            status = f"will generate (~${cost_est:.2f})"
            
        print(f"[route] {ev.name!r} ({ev.kind}): {req.category} -> {req.endpoint} "
              f"({status})", flush=True)
              
        events_report.append({
            "name": ev.name,
            "kind": ev.kind,
            "intensity": ev.intensity,
            "start_time": ev.start_time,
            "category": req.category,
            "endpoint": req.endpoint,
            "cached": cached,
            "skipped": not active,
            "cost_est": cost_est,
            "status": status,
            "state": "skipped" if not active else ("cached" if cached else "pending"),
        })
        request_key_to_indices.setdefault(
            cache_key(req.endpoint, req.prompt, req.duration_s), []).append(len(events_report) - 1)

    _notify({"phase": "plan", "events": events_report})

    # e. Assemble, write mix, mux.
    print(flush=True)

    # Extract original video audio bed if present
    orig_wav_path = f"{out_path}.orig.wav"
    has_orig_audio = extract_audio(clip_path, orig_wav_path)
    orig_audio_arg = orig_wav_path if has_orig_audio else None

    print(f"[pipeline] assembling tier={tier} ...", flush=True)
    _notify({"phase": "stage", "stage": "assembling"})

    def _sfx_fn(request):
        key = cache_key(request.endpoint, request.prompt, request.duration_s)
        indices = request_key_to_indices.get(key, [])
        # Re-check at call time: an earlier duplicate request in this same run
        # may have populated the cache since the routing printout.
        hit = _is_cached(cache_dir, request.endpoint, request.prompt, request.duration_s)
        _notify({"phase": "sfx_start", "indices": indices, "cached": hit})
        path = get_sfx(request, cache_dir=cache_dir)
        _notify({"phase": "sfx_done", "indices": indices, "cached": hit})
        return path

    mix = assemble_timeline(timeline, duration, tier=tier, sfx_fn=_sfx_fn, orig_audio_path=orig_audio_arg)
    print("[pipeline] assembly done.", flush=True)

    mix_path = _default_mix_path(out_path)
    write_mix(mix, mix_path)
    print(f"[pipeline] mix written -> {mix_path}", flush=True)

    # Clean up temporary original audio file
    if has_orig_audio and os.path.exists(orig_wav_path):
        try:
            os.remove(orig_wav_path)
        except Exception:
            pass

    print(f"[pipeline] muxing -> {out_path}", flush=True)
    _notify({"phase": "stage", "stage": "muxing"})
    mux_video(clip_path, mix_path, out_path)
    print(f"[pipeline] done -> {out_path}", flush=True)
    _notify({"phase": "stage", "stage": "done"})

    ledger_after_summary = summarize_ledger()
    ledger_after = ledger_after_summary["total"]
    ledger_delta = ledger_after - ledger_before

    # -- footer --
    print(flush=True)
    print("-" * 60, flush=True)
    print(f"out:          {out_path}", flush=True)
    print(f"tier:         {tier}", flush=True)
    print(f"duration:     {duration:.2f}s", flush=True)
    print(f"ledger delta: ${ledger_delta:.4f} (this run)", flush=True)
    print(f"ledger total: ${ledger_after:.4f} (cumulative)", flush=True)
    print("-" * 60, flush=True)

    return FoleyResult(
        out_path=out_path,
        timeline_path=timeline_path,
        mix_path=mix_path,
        events=events_report,
        tier=tier,
        ledger_delta=ledger_delta,
        duration_s=duration,
    )
