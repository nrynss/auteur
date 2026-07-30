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
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from foleyflow.assemble import assemble_timeline, write_mix
from foleyflow.ledger import summarize_ledger
from foleyflow.mux import extract_audio, mux as mux_video
from foleyflow.projects import ProjectStore, Run, utcnow
from foleyflow.qa import build_report
from foleyflow.routing import SfxRequest
from foleyflow.sfx_cache import backed_sfx_fn, cache_key as cache_digest, cache_object_key
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

    def _observe(payload: dict) -> None:
        cached_by_key[payload["key"]] = payload["cached"]
        _notify(progress_cb, {"phase": "stem", **payload})

    inner = backed_sfx_fn(store.backend, stems_dir, variant_fn, on_event=_observe)

    def _sfx_fn(request: SfxRequest) -> str:
        path = inner(request)
        variant = (variant_fn(request) if variant_fn else "") or ""
        args = (request.endpoint, request.prompt, request.duration_s, variant)
        b2_key = cache_object_key(*args)
        cached = cached_by_key.get(b2_key, True)
        # Several timeline events can share one request, so the same stem is
        # asked for more than once per render. Record the address once.
        if not any(s["b2_key"] == b2_key for s in stems):
            stems.append({"cache_key": cache_digest(*args), "b2_key": b2_key,
                          "endpoint": request.endpoint,
                          "prompt": request.prompt,
                          "duration_s": request.duration_s,
                          "variant": variant, "cached": cached,
                          "local_path": path})
            if not cached:
                store.record_stem(project_id, run_id=run.id,
                                  cache_key=cache_digest(*args),
                                  endpoint=request.endpoint,
                                  prompt=request.prompt,
                                  duration_s=request.duration_s,
                                  variant=variant)
        return path

    _notify(progress_cb, {"phase": "stage", "stage": "assembling"})
    mix = assemble_timeline(timeline, duration, tier=tier, sfx_fn=_sfx_fn,
                            orig_audio_path=orig_wav if has_orig else None)

    mix_path = os.path.join(work, "mix.wav")
    write_mix(mix, mix_path)

    _notify(progress_cb, {"phase": "stage", "stage": "muxing"})
    video_out = os.path.join(work, "foley.mp4")
    mux_video(video_path, mix_path, video_out)

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

    # QA against the mix that was actually written, not the array in memory —
    # the WAV on disk is what a listener gets, and what ebur128 can measure.
    _notify(progress_cb, {"phase": "stage", "stage": "qa"})
    report = store.save_qa_report(
        project_id,
        build_report(store.load_scenes(project_id), audio_path=mix_path,
                     run_id=run.id),
        run_id=run.id)

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


def _notify(cb: Optional[Callable[[dict], None]], payload: dict) -> None:
    if cb is None:
        return
    try:
        cb(payload)
    except Exception:
        # An observer must never break a render that has already spent money.
        pass


