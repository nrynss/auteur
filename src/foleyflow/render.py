"""Project-scoped rendering: stems -> mix -> muxed video -> QA -> export.

`foleyflow.pipeline.run` already does this for a *file*, and deliberately keeps
doing so — it is the offline tuning lane (§14.1) and owes nothing to storage.
This module is the *project* lane: the same mix math, driven from stored
records, with every artifact addressed in B2 and every decision written to the
event log.

The two lanes share the parts that matter — `assemble_timeline` for the mix and
`mux` for the video — so a project render and a `foleyflow render` of the same
timeline produce the same audio. What differs is only where bytes come from and
go: `sfx_cache.backed_sfx_fn` puts the shared, content-addressed SFX cache in
front of generation, so a second project needing "boot scuff on gravel" pays
nothing.

## Why the mix and the export are separate steps

Rendering is expensive and repeatable; publishing is cheap and consequential.
Keeping them apart means QA measures a real mix *before* anything is published,
and §13's export gate has something to gate. It also makes the repair loop
tractable: regenerate a stem, re-mix, look at the numbers, and only then move
the pointer.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field, replace as dataclass_replace
from typing import Any, Callable, Dict, List, Optional

from foleyflow.assemble import _event_seed, assemble_timeline, plan_requests, write_mix
from foleyflow.ledger import summarize_ledger
from foleyflow.mux import extract_audio, mux as mux_video
from foleyflow.projects import ProjectStore, Run, utcnow
from foleyflow.qa import build_report
from foleyflow.routing import SfxRequest, route_event
from foleyflow.sfx_cache import (
    backed_sfx_fn,
    cache_key as cache_digest,
    cache_object_key,
    prewarm,
)
from foleyflow.stages import StageLog, record_stem_stage, run_stages
from foleyflow.storage.base import ObjectNotFound

MANIFEST_VERSION = "1.0"

# Set by the export gate. §13 makes a true-peak overage block an export while a
# continuity warning only warns, so only blocking findings are gate material.
BLOCKING_SEVERITY = "blocking"


@dataclass
class MixResult:
    """What one mix run produced. Paths are local scratch; keys are the record."""

    run_id: str
    timeline_version: int
    tier: str
    duration_s: float
    mix_key: str
    video_key: str
    stems_key: str
    mix_path: str
    video_path: str
    stems: List[Dict[str, Any]] = field(default_factory=list)
    qa: Optional[Dict[str, Any]] = None
    ledger_delta: float = 0.0
    # The §8.2 job history for this run: one entry per stage, in order.
    stages: List[Dict[str, Any]] = field(default_factory=list)
    genblaze_run_id: Optional[str] = None

    @property
    def generated_count(self) -> int:
        return sum(1 for s in self.stems if not s.get("cached"))


class ExportBlocked(RuntimeError):
    """Readiness blocks the export and the caller did not override it."""

    def __init__(self, blockers: List[str]):
        super().__init__("export blocked: " + ", ".join(blockers))
        self.blockers = blockers


def blocking_qa_codes(qa_report: Optional[dict]) -> List[str]:
    """The §13 blockers in a stored QA report, as issue codes."""
    if not qa_report:
        return []
    issues = list((qa_report.get("audio") or {}).get("issues") or [])
    issues.extend((qa_report.get("visual") or {}).get("issues") or [])
    return sorted({i.get("code") or "unknown" for i in issues
                   if i.get("severity") == BLOCKING_SEVERITY})


def render_mix(store: ProjectStore, project_id: str, *, run: Run,
               video_path: str, tier: str = "full",
               timeline_version: Optional[int] = None,
               variant_fn: Optional[Callable[[SfxRequest], str]] = None,
               progress_cb: Optional[Callable[[dict], None]] = None) -> MixResult:
    """Render one mix for a project and store everything it produced.

    video_path must be the staged local source — the caller already has it
    downloaded for ffprobe and scene work, and re-fetching it here would double
    the egress on every render.

    variant_fn is passed straight through to `backed_sfx_fn`, which is how the
    repair loop forces one event to regenerate while every other stem still
    hits cache. It must be pure: it is consulted once inside the cache lookup
    and once here to record which address the stem landed at.
    """
    state = store.get_state(project_id)
    version = timeline_version or state.timeline_version
    if not version:
        raise ObjectNotFound(f"project {project_id} has no timeline to mix")
    timeline = store.load_timeline(project_id, version)

    # ffprobe's duration, recorded at ingest. Never the model's self-reported
    # one: a mix built to the wrong length drifts against picture, and the
    # timeline's duration is whatever the VLM claimed it saw.
    duration = state.duration_s or state.project.duration_s or timeline.duration

    work = os.path.join(store.work_dir, project_id, run.id)
    stems_dir = os.path.join(work, "stems")
    os.makedirs(stems_dir, exist_ok=True)

    ledger_before = summarize_ledger()["total"]

    # The source's own audio, kept as a bed. A silent clip yields nothing and
    # the mix is Foley alone, which is the normal case for the demo material.
    orig_wav = os.path.join(work, "source-audio.wav")
    has_orig = extract_audio(video_path, orig_wav)

    stems: List[Dict[str, Any]] = []
    cached_by_key: Dict[str, bool] = {}
    # Addresses this run generated during the warm-up. Assembly reads them from
    # the cache and would otherwise report them as hits — which would file a
    # freshly billed stem as free, and leave `stem.generated` (the spend log)
    # empty for a run that spent.
    generated_keys: set = set()

    # Which tier actually answered, keyed by cache address. Only populated when
    # a stem was generated AND the serving tier differs from the requested one,
    # i.e. a fallback fired. Without this the manifest would attribute a
    # fal-served clip to ElevenLabs, because the cache address — and therefore
    # `request.endpoint` — is the tier that was *asked*, not the one that
    # answered. See sfx_cache's second `_notify`.
    served_by_key: dict = {}

    def _observe(payload: dict) -> None:
        cached_by_key[payload["key"]] = payload["cached"]
        if payload.get("served_by") and payload["served_by"] != payload.get("tier"):
            served_by_key[payload["key"]] = {
                "served_by": payload["served_by"],
                "served_endpoint": payload.get("served_endpoint"),
            }
        _notify(progress_cb, {"phase": "stem", **payload})

    inner = backed_sfx_fn(store.backend, stems_dir, variant_fn, on_event=_observe)

    def _variant_of(request: SfxRequest) -> str:
        return (variant_fn(request) if variant_fn else "") or ""

    def _sfx_fn(request: SfxRequest) -> str:
        path = inner(request)
        variant = _variant_of(request)
        args = (request.endpoint, request.prompt, request.duration_s, variant)
        b2_key = cache_object_key(*args)
        cached = b2_key not in generated_keys and cached_by_key.get(b2_key, True)
        # Several timeline events can share one request, so the same stem is
        # asked for more than once per render. Record the address once.
        if not any(s["b2_key"] == b2_key for s in stems):
            fallback = served_by_key.get(b2_key) or {}
            stems.append({"cache_key": cache_digest(*args), "b2_key": b2_key,
                          "endpoint": request.endpoint,
                          "prompt": request.prompt,
                          "duration_s": request.duration_s,
                          "variant": variant, "cached": cached,
                          "local_path": path, **fallback})
            if not cached:
                store.record_stem(project_id, run_id=run.id,
                                  cache_key=cache_digest(*args),
                                  endpoint=request.endpoint,
                                  prompt=request.prompt,
                                  duration_s=request.duration_s,
                                  variant=variant,
                                  served_endpoint=fallback.get("served_endpoint"))
        return path

    # -- stems: one Genblaze run, every independent stem fanned out ----------
    #
    # Planned rather than pulled. `assemble_timeline` asks for stems one at a
    # time as it builds the mix, which serialises generation; warming the cache
    # first means the whole timeline's stems go out concurrently and assembly
    # afterwards reads from disk. `plan_requests` is shared with the mixer, so
    # the set warmed here is exactly the set the mix consumes.
    stage_log = StageLog(sink=lambda record: store.record_stage(
        project_id, run_id=run.id, stage=record))

    stems_started = time.monotonic()
    stems_started_at = utcnow()
    _notify(progress_cb, {"phase": "stage", "stage": "stems"})
    planned = plan_requests(timeline, duration, tier=tier)
    stem_results = prewarm(store.backend, stems_dir, planned, variant_fn,
                           on_event=lambda p: _notify(progress_cb,
                                                      {"phase": "stem", **p}))
    generated_keys.update(
        cache_object_key(r.request.endpoint, r.request.prompt,
                         r.request.duration_s, _variant_of(r.request))
        for r in stem_results if r.ok)
    # Fan-out is the primary generation path, and its escalations are the ones
    # most likely to happen in anger. `record_stem_stage` puts them in the stage
    # log; this is what carries them into the stem inventory, and from there
    # into the §11.6 provenance manifest.
    for result in stem_results:
        if not result.ok or result.served is None:
            continue
        if result.served.endpoint == result.request.endpoint:
            continue
        served_by_key[cache_object_key(
            result.request.endpoint, result.request.prompt,
            result.request.duration_s, _variant_of(result.request))] = {
                "served_by": getattr(result.served, "tier", None),
                "served_endpoint": result.served.endpoint,
        }
    record_stem_stage(stage_log, stem_results,
                      genblaze_run_id=_first_genblaze_run_id(stem_results),
                      elapsed_s=time.monotonic() - stems_started,
                      started_at=stems_started_at)

    # -- mix / mux / QA: local, deterministic, and orchestrated all the same --
    mix_path = os.path.join(work, "mix.wav")
    video_out = os.path.join(work, "foley.mp4")

    def _mix_stage(_params: dict) -> dict:
        _notify(progress_cb, {"phase": "stage", "stage": "assembling"})
        mix = assemble_timeline(timeline, duration, tier=tier, sfx_fn=_sfx_fn,
                                orig_audio_path=orig_wav if has_orig else None)
        write_mix(mix, mix_path)
        return {"output_path": mix_path, "media_type": "audio/wav"}

    def _mux_stage(_params: dict) -> dict:
        _notify(progress_cb, {"phase": "stage", "stage": "muxing"})
        mux_video(video_path, mix_path, video_out)
        return {"output_path": video_out, "media_type": "video/mp4"}

    def _qa_stage(_params: dict) -> dict:
        # Measured against the WAV that was actually written, not the array in
        # memory — the file on disk is what a listener gets, and what ebur128
        # can measure.
        _notify(progress_cb, {"phase": "stage", "stage": "qa"})
        return {"report": build_report(store.load_scenes(project_id),
                                       audio_path=mix_path, run_id=run.id)}

    stage_results = run_stages(
        f"foleyflow-render-{run.id}",
        [("mix", _mix_stage), ("mux", _mux_stage), ("qa", _qa_stage)],
        log=stage_log, project_id=project_id)

    mix_key = store.mix_key(project_id, run.id)
    video_key = store.mix_video_key(project_id, run.id)
    stems_key = store.run_artifact_key(project_id, run.id, "stems.json")

    store.backend.put(mix_key, mix_path, content_type="audio/wav")
    store.backend.put(video_key, video_out, content_type="video/mp4")

    # The extracted bed is the one large transient nothing reads again. The
    # mix and the video stay on disk: they are this run's outputs, and the
    # work dir is scratch that a single-instance deployment recycles.
    if has_orig and os.path.exists(orig_wav):
        try:
            os.remove(orig_wav)
        except OSError:
            pass

    inventory = [_stem_record(s) for s in stems]
    store.save_stem_inventory(project_id, run.id, inventory)

    report = store.save_qa_report(
        project_id, (stage_results.get("qa") or {}).get("report"), run_id=run.id)

    # Post-mix the document must tell the truth about its own cache addresses.
    # Until this runs, `source_duration_s` is unreachable in production: nothing
    # else can honestly set it (analysis is told not to invent one, and the
    # commit endpoint may only record an address that already exists), so
    # resize-is-free stayed theoretical. See `stamp_source_durations`.
    version = _freeze_generated_durations(
        store, project_id, timeline, inventory, variant_fn=variant_fn,
        run=run, version=version)

    store.record_mix(project_id, run_id=run.id, mix_key=mix_key,
                     video_key=video_key, tier=tier, timeline_version=version,
                     stem_count=len(stems),
                     generated_count=sum(1 for s in stems if not s["cached"]))

    _notify(progress_cb, {"phase": "stage", "stage": "done"})

    return MixResult(
        run_id=run.id, timeline_version=version, tier=tier, duration_s=duration,
        mix_key=mix_key, video_key=video_key, stems_key=stems_key,
        mix_path=mix_path, video_path=video_out, stems=inventory, qa=report,
        ledger_delta=summarize_ledger()["total"] - ledger_before,
        stages=stage_log.to_list(), genblaze_run_id=stage_log.genblaze_run_id,
    )


def publish_export(store: ProjectStore, project_id: str, *, run: Run,
                   mix_run_id: Optional[str] = None,
                   override_blocking: bool = False,
                   actor: str = "user") -> Dict[str, Any]:
    """Write the provenance manifest for a rendered mix and publish it.

    Publishing does not re-render: it names an already-stored, already-measured
    mix. Re-rendering here would mean the bytes a judge downloads are not the
    bytes QA passed.

    Raises ExportBlocked when QA has a blocking finding, unless the caller
    passes the override §13 allows — which is recorded in the event log with
    the actor who made the call, since an override is a human decision and the
    audit trail has to show whose.
    """
    mix = (store.get_mix(project_id, mix_run_id) if mix_run_id
           else store.get_latest_mix(project_id))
    if not mix:
        raise ObjectNotFound(
            f"project {project_id} has no rendered mix to export")

    qa_report = store.load_qa_report(project_id)
    blockers = blocking_qa_codes(qa_report)
    if blockers and not override_blocking:
        raise ExportBlocked(blockers)

    manifest = build_manifest(store, project_id, run=run, mix=mix,
                              qa_report=qa_report, overridden=bool(blockers))
    manifest_key = store.save_manifest(project_id, run.id, manifest)

    pointer = store.set_latest_export(
        project_id, run_id=mix["run_id"], export_key=mix["video_key"],
        manifest_key=manifest_key, actor=actor)

    return {**pointer, "manifest_key": manifest_key,
            "overridden_blockers": blockers if blockers else []}


def build_manifest(store: ProjectStore, project_id: str, *, run: Run,
                   mix: Dict[str, Any], qa_report: Optional[dict] = None,
                   overridden: bool = False) -> Dict[str, Any]:
    """The §11.6 provenance manifest for one export.

    Everything in it is read back from storage rather than carried in memory
    from the mix run, so the manifest describes what is actually in the bucket.
    A manifest that agrees with a process but not with the bucket is worse than
    none: it is a hashed, authoritative-looking claim about bytes that may not
    exist.
    """
    state = store.get_state(project_id)
    stems = store.load_stem_inventory(project_id, mix["run_id"])

    return {
        "manifest_version": MANIFEST_VERSION,
        "project_id": project_id,
        "run_id": run.id,
        "mix_run_id": mix["run_id"],
        "created_at": utcnow(),
        "workflow": {"engine": "foleyflow", "tier": mix.get("tier"),
                     "timeline_version": mix.get("timeline_version")},
        "source": {
            "b2_key": state.project.input_key,
            "sha256": state.source_sha256 or state.project.source_sha256,
            "duration_s": state.duration_s or state.project.duration_s,
        },
        "artifacts": (
            [{"type": "timeline",
              "b2_key": store.timeline_key(project_id, mix["timeline_version"]),
              "version": mix["timeline_version"]}]
            + [{"type": "audio_stem", **stem} for stem in stems]
            + [{"type": "mix", "b2_key": mix["mix_key"]},
               {"type": "final_export", "b2_key": mix["video_key"]}]
        ),
        "qa_report_key": store.qa_report_key(project_id),
        "qa_status": (qa_report or {}).get("overall_status"),
        "qa_override": overridden,
        "final_export_key": mix["video_key"],
    }


def stamp_source_durations(timeline, inventory: List[Dict[str, Any]], *,
                           variant_fn: Optional[Callable[[SfxRequest], str]] = None):
    """The timeline with `source_duration_s` recorded from real stems.

    Returns ``(timeline, changed)``; ``changed`` is False when there was
    nothing honest to stamp, and the caller then allocates no version.

    **Provenance, not arithmetic.** An event is only frozen when the address it
    routes to is actually present in this run's stem inventory — i.e. a clip
    was generated (or served from cache) and paid for at that address. Events
    whose stem is absent stay unfrozen: a freeze with no recorded provenance
    would be a fabricated number, which §A.3 forbids.

    **Why the inventory's `duration_s` round-trips exactly.** `route_event`
    applies `clamp_duration` *after* choosing the duration, and the inventory
    records the request as sent — i.e. post-clamp, which is also what the cache
    address is computed from. Feeding that value back in as `source_duration_s`
    clamps it a second time, but clamping is idempotent (the value is already
    inside the endpoint's bounds, and the endpoint is unchanged because it is
    keyword-matched from the event name), so the resulting `SfxRequest` is
    identical field for field. Stamping the *pre*-clamp value would be the bug:
    it is recorded nowhere, and for a clamped request it names an address that
    was never generated.

    **Why discrete events also get their placement span widened.** With a
    freeze present, `assemble` windows a one-shot to the placement `duration`
    (that is what makes resize free); without one it plays the clip out in full
    and `duration` is decorative. So stamping alone would silently shorten every
    discrete event by its headroom. Setting the span to the frozen source length
    is the one value that leaves the mix byte-identical, and it is also the
    truthful statement of how long that one-shot occupies the timeline.
    Shortening it afterwards is then a free edit, which is the whole point.

    **Stem keys are refreshed on every run, freezes are not.** Each event's
    `stem_key` is looked up every time (a repair re-rolls the variant, which
    moves the cache address — the stamped key must follow). `source_duration_s`
    is only *written* on the first freeze; once frozen it is preserved, as is
    the widened discrete placement span (a later nudge of it is a free edit the
    stamp must not undo). `changed` is True only when something actually
    moved, so an unchanged re-mix allocates no version.

    **Rhythmic events also have their mix `seed` stamped.** assemble draws the
    tile-gain jitter from it, so recording the value this run used is what
    makes a later nudge preserve the texture (7.10's acceptance). The stamped
    version therefore renders byte-identically even if an event is nudged
    afterwards — the seed is frozen, not recomputed from the new position.
    Non-rhythmic events never consult the seed, so they keep it untouched.
    """
    by_key = {s.get("b2_key"): s for s in inventory if s.get("b2_key")}
    events = []
    changed = False
    for event in timeline.events:
        request = route_event(event)
        variant = (variant_fn(request) if variant_fn else "") or ""
        stem = by_key.get(cache_object_key(
            request.endpoint, request.prompt, request.duration_s, variant))
        frozen = (stem or {}).get("duration_s")
        if stem is None or frozen is None:
            events.append(event)
            continue
        frozen = float(frozen)
        first_freeze = event.source_duration_s is None
        # The mix seed is part of how a *rhythmic* event renders (assemble
        # draws the tile-gain jitter from it), so the seed this run actually
        # used is recorded the same way the duration and address are. That is
        # what makes a later nudge translate instead of re-rolling the texture
        # (7.10). Non-rhythmic events never consult the seed, so stamping one
        # would be fabricated provenance (§A.3) — it stays as-is.
        seed_used = (event.seed if event.seed is not None
                     else _event_seed(event.name, event.start_time))
        updated = dataclass_replace(
            event,
            source_duration_s=frozen if first_freeze else event.source_duration_s,
            stem_key=stem["b2_key"],
            duration=(frozen if (first_freeze and event.kind == "discrete")
                      else event.duration),
            seed=(seed_used if event.kind == "rhythmic" else event.seed),
        )
        if updated != event:
            changed = True
        events.append(updated)

    if not changed:
        return timeline, False
    return dataclass_replace(timeline, events=events), True


def _freeze_generated_durations(store: ProjectStore, project_id: str, timeline,
                                inventory: List[Dict[str, Any]], *,
                                variant_fn, run: Run, version: int) -> int:
    """Persist the stamped timeline as a new version; return the mix's version.

    The mix is recorded against the *stamped* version rather than the one it was
    handed. The two render byte-identically by construction (see
    `stamp_source_durations`), and the stamped one is the document that actually
    names the addresses this mix used — so pointing the mix at the pre-stamp
    version would make `_mix_readiness` report every freshly rendered project as
    "mixed from v1; timeline is now v2", a staleness claim that is not true.

    `actor="system"` / `source="mix_freeze"` because this is not a director's
    edit; decision A.2.1 keeps history real by naming who wrote each version.
    Never fatal: a mix that produced audio must not be lost to a bookkeeping
    write, so a storage failure here leaves the project on the pre-stamp version
    (unfrozen — exactly today's behaviour) rather than failing the run.
    """
    stamped, changed = stamp_source_durations(timeline, inventory,
                                              variant_fn=variant_fn)
    if not changed:
        return version
    try:
        return store.save_timeline(project_id, stamped, run_id=run.id,
                                   actor="system", source="mix_freeze")
    except Exception as e:
        print(f"[render] WARNING: could not record generated durations: {e}")
        return version


# -- internals ---------------------------------------------------------------


def _stem_record(stem: Dict[str, Any]) -> Dict[str, Any]:
    """The storable half of a stem record — no local scratch paths.

    A local path is meaningless to anyone reading the manifest later, and on a
    container it names a directory that no longer exists.
    """
    record = {k: v for k, v in stem.items() if k != "local_path"}
    path = stem.get("local_path")
    if path and os.path.exists(path):
        from foleyflow.timeline_cache import file_sha256

        record["sha256"] = file_sha256(path)
    return record


def _first_genblaze_run_id(stem_results) -> Optional[str]:
    """The Genblaze run that generated this render's stems, if any ran.

    First rather than last: escalation rounds each get their own run, and the
    one worth surfacing is the round the render started with. The rest are on
    the per-stem attempt history.
    """
    for result in stem_results or []:
        for attempt in result.attempts:
            if attempt.genblaze_run_id:
                return attempt.genblaze_run_id
    return None


def _notify(cb: Optional[Callable[[dict], None]], payload: dict) -> None:
    if cb is None:
        return
    try:
        cb(payload)
    except Exception:
        # An observer must never break a render that has already spent money.
        pass


