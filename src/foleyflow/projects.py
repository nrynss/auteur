"""Projects, runs, and versioned artifacts — the record every other stage hangs off.

## Why the state is derived rather than stored

SPEC.md §10 is blunt: never overwrite a completed generation or manifest, and
`exports/latest.json` is the *only* mutable pointer. A `project.json` carrying a
mutable `status` and `latest_run_id` would violate that on the first status
change, and would also lose the history that §6.3 wants to show.

So the durable truth is an append-only event log — one immutable object per
event — and the project's current state is a fold over it. Nothing is ever
rewritten, the audit trail is the state rather than a byproduct of it, and the
lifecycle types match the domain-neutral names SPEC.md §8.4 reserves.

## Why one object per event rather than a JSONL file

Object storage has no append. Adding a line to `events.jsonl` means read,
concatenate, put — and two concurrent writers silently lose one of the events.
One immutable object per event has no read-modify-write, so concurrent writers
cannot clobber each other, and ULID names make a sorted listing an ordered log.

## Layout

    projects/{project_id}/
      project.json                  immutable identity, written once
      events/{evt_ulid}.json        append-only lifecycle log
      timelines/timeline.v{n}.json  immutable versions
      runs/{run_id}/run.json        immutable, written at terminal state
      runs/{run_id}/mix.wav         the rendered mix, and the bytes QA measured
      runs/{run_id}/foley.mp4       the muxed video that export publishes
      runs/{run_id}/stems.json      what the mix is made of, for the manifest
      runs/{run_id}/manifest.json   §11.6 provenance, written once at export
      scenes/scenes.json            deterministic, refreshable
      qa/qa-report.json             latest QA report; refreshable like scenes
      exports/latest.json           the single mutable pointer (§10)
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from foleyflow.models import Timeline
from foleyflow.qa import QaReport, findings_signature, report_from_dict, with_preserved_audio
from foleyflow.timeline_io import dict_to_timeline, timeline_to_dict
from foleyflow.ids import is_valid, new_event_id, new_project_id, new_run_id
from foleyflow.storage.base import (
    Backend,
    ObjectNotFound,
    PreconditionFailed,
    StorageError,
)

# Lifecycle event types — SPEC.md §8.4's domain-neutral names. Deliberately not
# incident-domain terms inherited from Mosaic.
EVENT_PROJECT_CREATED = "postproduction.project.created"
EVENT_INPUT_RECORDED = "postproduction.input.recorded"
EVENT_ANALYSIS_COMPLETED = "postproduction.analysis.completed"
EVENT_PLAN_PROPOSED = "postproduction.foley.plan.proposed"
EVENT_REPAIR_APPROVED = "postproduction.repair.approved"
EVENT_STEM_GENERATED = "postproduction.stem.generated"
EVENT_MIX_COMPLETED = "postproduction.mix.completed"
EVENT_QA_FLAGGED = "postproduction.qa.flagged"
EVENT_QA_CLEARED = "postproduction.qa.cleared"
EVENT_EXPORT_APPROVED = "postproduction.export.approved"
EVENT_STAGE_RECORDED = "postproduction.stage.recorded"
EVENT_RUN_STARTED = "postproduction.run.started"
EVENT_RUN_COMPLETED = "postproduction.run.completed"
EVENT_RUN_FAILED = "postproduction.run.failed"
EVENT_RUN_FAILED_AFTER_COMPLETION = "postproduction.run.failed_after_completion"

STATUS_NEW = "new"
STATUS_AWAITING_UPLOAD = "awaiting_upload"
STATUS_ANALYZING = "analyzing"
STATUS_NEEDS_REVIEW = "needs_review"
STATUS_READY = "ready"
STATUS_FAILED = "failed"

# A run whose start event is older than this, with no terminal record, is
# treated as dead. Cloud Run SIGTERMs containers on deploy, so a crashed run is
# an expected outcome rather than an exotic one — without this a redeploy
# strands every in-flight project in `analyzing` permanently.
RUN_LEASE_S = 15 * 60

# Version allocation retries when a concurrent writer claims the number first.
_VERSION_RACE_RETRIES = 25


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(value: str) -> Optional[datetime]:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class Project:
    """Immutable identity. Anything that can change lives in the event log."""

    id: str
    title: str
    created_at: str
    input_key: str
    source_sha256: str
    source_filename: str
    duration_s: Optional[float] = None
    budget_usd: Optional[float] = None

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "Project":
        return Project(**{k: d.get(k) for k in Project.__dataclass_fields__})


@dataclass(frozen=True)
class Event:
    id: str
    project_id: str
    type: str
    created_at: str
    actor: str = "system"
    run_id: Optional[str] = None
    payload: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "Event":
        return Event(**{k: d.get(k) for k in Event.__dataclass_fields__
                        if k != "payload"}, payload=d.get("payload") or {})


@dataclass(frozen=True)
class Run:
    id: str
    project_id: str
    kind: str                       # analyze | generate | repair | export
    created_at: str
    status: str                     # running | completed | failed
    timeline_version: Optional[int] = None
    parent_run_id: Optional[str] = None
    genblaze_run_id: Optional[str] = None
    error: Optional[str] = None
    error_code: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "Run":
        return Run(**{k: d.get(k) for k in Run.__dataclass_fields__
                      if k != "metadata"}, metadata=d.get("metadata") or {})


@dataclass
class ProjectState:
    """Derived by folding the event log. Never stored — recomputed on read, so
    it cannot drift from the history that produced it."""

    project: Project
    status: str
    latest_run_id: Optional[str]
    timeline_version: Optional[int]
    event_count: int
    has_export: bool
    source_sha256: Optional[str] = None
    duration_s: Optional[float] = None
    stale_run_ids: List[str] = field(default_factory=list)
    latest_mix: Optional[Dict[str, Any]] = None
    export: Optional[Dict[str, Any]] = None

    def to_dict(self) -> dict:
        payload = {
            **self.project.to_dict(),
            "status": self.status,
            "latest_run_id": self.latest_run_id,
            "timeline_version": self.timeline_version,
            "event_count": self.event_count,
            "has_export": self.has_export,
            "stale_run_ids": self.stale_run_ids,
            "latest_mix": self.latest_mix,
            "export": self.export,
        }
        # The event log wins over the frozen identity record: sha256 and
        # duration are only known after the upload is confirmed, which happens
        # long after the project record is written.
        payload["source_sha256"] = self.source_sha256 or self.project.source_sha256
        payload["duration_s"] = self.duration_s or self.project.duration_s
        return payload


class ProjectStore:
    """Reads and writes the project record set against any storage Backend."""

    def __init__(self, backend: Backend, work_dir: Optional[str] = None):
        self.backend = backend
        self.work_dir = work_dir or os.path.join(
            tempfile.gettempdir(), "foleyflow", "store")
        os.makedirs(self.work_dir, exist_ok=True)

    # -- key helpers ------------------------------------------------------

    @staticmethod
    def _check_id(identifier: str, prefix: str) -> str:
        """Validate any id that becomes part of an object key.

        Applied to run and scene ids too, not just project ids: they are
        interpolated into keys exactly the same way, and relying on one
        backend's path handling to catch a bad one made safety depend on which
        backend was configured.
        """
        if not is_valid(identifier, prefix):
            raise ValueError(f"not a valid {prefix} id: {identifier!r}")
        return identifier

    @staticmethod
    def _check_scene_id(scene_id: str) -> str:
        if not scene_id.startswith("scene_") or not scene_id[6:].isdigit():
            raise ValueError(f"not a valid scene id: {scene_id!r}")
        return scene_id

    def _root(self, project_id: str) -> str:
        return f"projects/{self._check_id(project_id, 'proj')}"

    def project_key(self, project_id: str) -> str:
        return f"{self._root(project_id)}/project.json"

    def events_prefix(self, project_id: str) -> str:
        return f"{self._root(project_id)}/events/"

    def timeline_key(self, project_id: str, version: int) -> str:
        if not isinstance(version, int) or version < 1:
            raise ValueError(f"timeline version must be a positive integer: {version!r}")
        return f"{self._root(project_id)}/timelines/timeline.v{version}.json"

    def run_key(self, project_id: str, run_id: str) -> str:
        return f"{self._root(project_id)}/runs/{self._check_id(run_id, 'run')}/run.json"

    def latest_export_key(self, project_id: str) -> str:
        return f"{self._root(project_id)}/exports/latest.json"

    def run_artifact_key(self, project_id: str, run_id: str, name: str) -> str:
        """Anything a run produces, addressed under that run.

        Mixes, muxed videos, and manifests all live beside the run record that
        made them, which is what keeps §10's "never overwrite a completed
        generation" free: a re-mix is a new run, so it cannot collide with an
        earlier one's artifacts, and `exports/latest.json` stays the only
        mutable pointer.
        """
        if not name or "/" in name:
            raise ValueError(f"not a valid run artifact name: {name!r}")
        return (f"{self._root(project_id)}/runs/"
                f"{self._check_id(run_id, 'run')}/{name}")

    def mix_key(self, project_id: str, run_id: str) -> str:
        return self.run_artifact_key(project_id, run_id, "mix.wav")

    def mix_video_key(self, project_id: str, run_id: str) -> str:
        return self.run_artifact_key(project_id, run_id, "foley.mp4")

    def manifest_key(self, project_id: str, run_id: str) -> str:
        return self.run_artifact_key(project_id, run_id, "manifest.json")

    def scenes_key(self, project_id: str) -> str:
        return f"{self._root(project_id)}/scenes/scenes.json"

    def scene_thumbnail_key(self, project_id: str, scene_id: str) -> str:
        return (f"{self._root(project_id)}/scenes/"
                f"{self._check_scene_id(scene_id)}/thumbnail.jpg")

    def qa_report_key(self, project_id: str) -> str:
        return f"{self._root(project_id)}/qa/qa-report.json"

    # -- JSON helpers -----------------------------------------------------

    def _put_json(self, key: str, payload: dict) -> str:
        """Serialise and upload directly.

        No scratch file: staging every record through one shared, key-derived
        temp path meant two threads touching the same key raced on the same
        file — producing JSONDecodeErrors at best, and at worst uploading one
        writer's bytes under another writer's key.
        """
        self.backend.put_bytes(
            key, json.dumps(payload, indent=2, sort_keys=True).encode("utf-8"),
            content_type="application/json")
        return key

    def _get_json(self, key: str) -> dict:
        return json.loads(self.backend.get_bytes(key).decode("utf-8"))

    def _put_json_immutable(self, key: str, payload: dict) -> str:
        """Write only if absent, atomically.

        `put_if_absent` is a single conditional operation. The previous
        exists()-then-put() was two, and two writers racing between them both
        believed they had won — one artifact silently destroyed the other.
        """
        try:
            self.backend.put_if_absent(
                key, json.dumps(payload, indent=2, sort_keys=True).encode("utf-8"),
                content_type="application/json")
        except PreconditionFailed as e:
            # Re-raised as PreconditionFailed, not a bare StorageError: it is a
            # StorageError subclass either way, so `except StorageError` callers
            # are unaffected, but "this key is already written" stays
            # distinguishable from "storage is broken". fail_run turns on that
            # difference, and flattening the two made a B2 outage look like a
            # run that had already completed.
            raise PreconditionFailed(
                f"refusing to overwrite immutable object {key!r} — "
                "completed artifacts are never rewritten (SPEC.md §10)") from e
        return key

    # -- projects ---------------------------------------------------------

    def create_project(self, *, title: str, input_key: str, source_sha256: str,
                       source_filename: str, duration_s: Optional[float] = None,
                       budget_usd: Optional[float] = None,
                       project_id: Optional[str] = None, actor: str = "system") -> Project:
        project = Project(
            id=project_id or new_project_id(),
            title=title,
            created_at=utcnow(),
            input_key=input_key,
            source_sha256=source_sha256,
            source_filename=source_filename,
            duration_s=duration_s,
            budget_usd=budget_usd,
        )
        self._put_json_immutable(self.project_key(project.id), project.to_dict())
        self.append_event(project.id, EVENT_PROJECT_CREATED, actor=actor,
                          payload={"title": title})
        if source_sha256:
            self.record_input(project.id, sha256=source_sha256,
                              duration_s=duration_s, actor=actor)
        return project

    def record_input(self, project_id: str, *, sha256: str,
                     duration_s: Optional[float] = None,
                     size_bytes: Optional[int] = None,
                     actor: str = "system") -> Event:
        """Record that the source bytes are present and verified.

        Separate from creation because the project record is written before the
        upload happens — the hash and duration are not knowable until after.
        `Project` is frozen, so this is where those values actually live, and
        `get_state` reads them from here.
        """
        return self.append_event(
            project_id, EVENT_INPUT_RECORDED, actor=actor,
            payload={"sha256": sha256, "duration_s": duration_s,
                     "size_bytes": size_bytes})

    def get_project(self, project_id: str) -> Project:
        return Project.from_dict(self._get_json(self.project_key(project_id)))

    def list_projects(self) -> List[Project]:
        """Newest first — ULID ids sort by creation time, so reversing the
        sorted key listing is enough."""
        keys = [k for k in self.backend.list_keys("projects/")
                if k.endswith("/project.json")]
        projects = [Project.from_dict(self._get_json(k)) for k in keys]
        return sorted(projects, key=lambda p: p.id, reverse=True)

    # -- events -----------------------------------------------------------

    def append_event(self, project_id: str, event_type: str, *,
                     actor: str = "system", run_id: Optional[str] = None,
                     payload: Optional[dict] = None) -> Event:
        event = Event(
            id=new_event_id(),
            project_id=self._check_id(project_id, "proj"),
            type=event_type,
            created_at=utcnow(),
            actor=actor,
            run_id=run_id,
            payload=payload or {},
        )
        # One object per event: no read-modify-write, so concurrent writers
        # cannot lose each other's events.
        self._put_json(f"{self.events_prefix(project_id)}{event.id}.json",
                       event.to_dict())
        return event

    def list_events(self, project_id: str) -> List[Event]:
        """Chronological. ULID event ids sort in creation order, so the sorted
        key listing is already the ordered log."""
        keys = self.backend.list_keys(self.events_prefix(project_id))
        return [Event.from_dict(self._get_json(k)) for k in sorted(keys)]

    # -- runs -------------------------------------------------------------

    def start_run(self, project_id: str, kind: str, *,
                  parent_run_id: Optional[str] = None,
                  metadata: Optional[dict] = None, actor: str = "system") -> Run:
        """Open a run. The record is not written yet — a run object appears in
        storage only once it reaches a terminal state, so storage never holds a
        run claiming to be 'running' after the process that owned it died. The
        started event is in the log immediately, so nothing is lost, and
        `RUN_LEASE_S` bounds how long such a run reads as live."""
        run = Run(
            id=new_run_id(),
            project_id=self._check_id(project_id, "proj"),
            kind=kind,
            created_at=utcnow(),
            status="running",
            parent_run_id=parent_run_id,
            metadata=metadata or {},
        )
        self.append_event(project_id, EVENT_RUN_STARTED, actor=actor,
                          run_id=run.id, payload={"kind": kind})
        return run

    def complete_run(self, run: Run, *, timeline_version: Optional[int] = None,
                     genblaze_run_id: Optional[str] = None,
                     metadata: Optional[dict] = None, actor: str = "system") -> Run:
        finished = Run(
            id=run.id, project_id=run.project_id, kind=run.kind,
            created_at=run.created_at, status="completed",
            timeline_version=timeline_version if timeline_version is not None
            else run.timeline_version,
            parent_run_id=run.parent_run_id,
            genblaze_run_id=genblaze_run_id or run.genblaze_run_id,
            metadata={**run.metadata, **(metadata or {})},
        )
        self._put_json_immutable(self.run_key(run.project_id, run.id),
                                 finished.to_dict())
        self.append_event(run.project_id, EVENT_RUN_COMPLETED, actor=actor,
                          run_id=run.id, payload={"kind": run.kind})
        return finished

    def fail_run(self, run: Run, error: str, *, error_code: str = "run_failed",
                 actor: str = "system") -> Run:
        """Record a run as failed.

        If the run already reached a terminal state — work raised *after*
        `complete_run` succeeded — the immutable record is left alone and a
        distinct event is appended instead. Attempting the write would raise
        from inside the error handler, and the run would stay recorded as
        `completed`: SPEC.md §12's "never a silently incomplete complete
        project", exactly inverted.

        Only `PreconditionFailed` means already-terminal. Catching every
        StorageError here filed genuine write failures as
        `failed_after_completion` and left no run.json at all — and since
        `GET /runs/{id}` falls back to the event log for a run with no record,
        the run then read as *running* forever, so a `--wait` client polled a
        dead run indefinitely. Anything else propagates to the job runner,
        which logs that the failure could not be recorded.
        """
        failed = Run(
            id=run.id, project_id=run.project_id, kind=run.kind,
            created_at=run.created_at, status="failed",
            timeline_version=run.timeline_version,
            parent_run_id=run.parent_run_id,
            genblaze_run_id=run.genblaze_run_id,
            error=error, error_code=error_code, metadata=run.metadata,
        )
        try:
            self._put_json_immutable(self.run_key(run.project_id, run.id),
                                     failed.to_dict())
        except PreconditionFailed:
            self.append_event(run.project_id, EVENT_RUN_FAILED_AFTER_COMPLETION,
                              actor=actor, run_id=run.id,
                              payload={"kind": run.kind, "error_code": error_code,
                                       "error": error})
            return failed
        self.append_event(run.project_id, EVENT_RUN_FAILED, actor=actor,
                          run_id=run.id,
                          payload={"error_code": error_code, "kind": run.kind})
        return failed

    def get_run(self, project_id: str, run_id: str) -> Run:
        return Run.from_dict(self._get_json(self.run_key(project_id, run_id)))

    def list_runs(self, project_id: str) -> List[Run]:
        """Oldest first."""
        keys = [k for k in self.backend.list_keys(f"{self._root(project_id)}/runs/")
                if k.endswith("/run.json")]
        return [Run.from_dict(self._get_json(k)) for k in sorted(keys)]

    # -- timelines --------------------------------------------------------

    def save_timeline(self, project_id: str, timeline: Timeline, *,
                      run_id: Optional[str] = None, actor: str = "system") -> int:
        """Store the next timeline version and return its number.

        Allocation retries on collision. Computing `latest + 1` and then writing
        is not atomic: two writers both read v1, both write v2, and one plan is
        destroyed with no error while the log records two `plan.proposed` events
        claiming the same version. The conditional write makes the loser lose
        loudly, so it simply takes the next number.

        Versions are never reused: a repair produces v2 alongside v1 rather than
        replacing it, which is what makes the history in §6.3 real and what lets
        a user go back to an earlier plan.
        """
        payload = json.dumps(timeline_to_dict(timeline), indent=2,
                             sort_keys=True).encode("utf-8")
        version = (self.latest_timeline_version(project_id) or 0) + 1

        for _ in range(_VERSION_RACE_RETRIES):
            try:
                self.backend.put_if_absent(
                    self.timeline_key(project_id, version), payload,
                    content_type="application/json")
                break
            except PreconditionFailed:
                version += 1
        else:
            raise StorageError(
                f"could not allocate a timeline version for {project_id} after "
                f"{_VERSION_RACE_RETRIES} attempts")

        self.append_event(project_id, EVENT_PLAN_PROPOSED, actor=actor,
                          run_id=run_id,
                          payload={"timeline_version": version,
                                   "event_count": len(timeline.events)})
        return version

    def load_timeline(self, project_id: str, version: Optional[int] = None) -> Timeline:
        version = version or self.latest_timeline_version(project_id)
        if not version:
            raise ObjectNotFound(f"project {project_id} has no timeline yet")
        return dict_to_timeline(self._get_json(self.timeline_key(project_id, version)))

    def latest_timeline_version(self, project_id: str) -> Optional[int]:
        versions = self.list_timeline_versions(project_id)
        return versions[-1] if versions else None

    def list_timeline_versions(self, project_id: str) -> List[int]:
        """Ascending. Parsed from the key rather than tracked in state, so the
        listing cannot disagree with what is actually stored."""
        prefix = f"{self._root(project_id)}/timelines/"
        versions = []
        for key in self.backend.list_keys(prefix):
            name = key.rsplit("/", 1)[-1]
            if name.startswith("timeline.v") and name.endswith(".json"):
                try:
                    versions.append(int(name[len("timeline.v"):-len(".json")]))
                except ValueError:
                    continue
        return sorted(versions)

    # -- scenes -----------------------------------------------------------

    def save_scenes(self, project_id: str, scenes: list, *,
                    video_path: Optional[str] = None,
                    run_id: Optional[str] = None, actor: str = "system") -> list:
        """Store the scene breakdown, and a thumbnail per scene when the video
        is available locally.

        Scene detection is deterministic, so re-running it on the same source
        yields the same breakdown — written with the ordinary (not immutable)
        put so a re-analysis can refresh it without tripping the overwrite guard
        on an artifact that carries no history.
        """
        from foleyflow.scenes import extract_thumbnail

        payload = [s.to_dict() for s in scenes]

        if video_path and os.path.exists(video_path):
            for scene in scenes:
                # Unique scratch name per thumbnail: a fixed one is shared by
                # every concurrent run on the box.
                fd, dest = tempfile.mkstemp(suffix=".jpg", dir=self.work_dir)
                os.close(fd)
                try:
                    extract_thumbnail(video_path, scene, dest)
                    key = self.scene_thumbnail_key(project_id, scene.id)
                    self.backend.put(key, dest, content_type="image/jpeg")
                except (RuntimeError, StorageError) as e:
                    # A missing thumbnail degrades the rail; it must not fail
                    # the analysis run that produced a valid breakdown.
                    print(f"[projects] WARNING: thumbnail for {scene.id}: {e}")
                    continue
                finally:
                    if os.path.exists(dest):
                        os.unlink(dest)
                for entry in payload:
                    if entry["id"] == scene.id:
                        entry["thumbnail_key"] = key

        self._put_json(self.scenes_key(project_id), {"scenes": payload})
        self.append_event(project_id, EVENT_ANALYSIS_COMPLETED, actor=actor,
                          run_id=run_id, payload={"scene_count": len(scenes)})
        return payload

    def load_scenes(self, project_id: str) -> list:
        try:
            return self._get_json(self.scenes_key(project_id)).get("scenes", [])
        except ObjectNotFound:
            return []

    # -- QA ---------------------------------------------------------------

    def save_qa_report(self, project_id: str, report, *,
                       run_id: Optional[str] = None,
                       actor: str = "system") -> dict:
        """Persist the latest QA report and keep the event log in sync.

        Refreshable like scenes.json: re-running QA after a re-mix must update
        the report without tripping the immutable-artifact guard. Not atomic
        against concurrent writers (load → merge → put); the API is single-
        worker today. Fail-closed audio preservation is deliberate — see
        `with_preserved_audio`.

        Continuity-only writes preserve any previously measured audio section
        so re-analysis cannot erase a true-peak block. Dirty reports append
        `postproduction.qa.flagged`; a clean report appends
        `postproduction.qa.cleared`. Identical findings skip a new event.
        """
        previous = self.load_qa_report(project_id)
        if isinstance(report, QaReport):
            merged = with_preserved_audio(report, previous)
        else:
            merged = with_preserved_audio(report_from_dict(dict(report)), previous)
        payload = merged.to_dict()
        if run_id and not payload.get("run_id"):
            payload["run_id"] = run_id
        self._put_json(self.qa_report_key(project_id), payload)

        if previous and findings_signature(previous) == findings_signature(payload):
            return payload

        issues = list((payload.get("audio") or {}).get("issues") or [])
        issues.extend((payload.get("visual") or {}).get("issues") or [])
        if issues:
            self.append_event(
                project_id, EVENT_QA_FLAGGED, actor=actor, run_id=run_id,
                payload={
                    "overall_status": payload.get("overall_status"),
                    "issue_count": len(issues),
                    "codes": sorted({i.get("code") for i in issues if i.get("code")}),
                })
        else:
            self.append_event(
                project_id, EVENT_QA_CLEARED, actor=actor, run_id=run_id,
                payload={"overall_status": payload.get("overall_status") or "ok"})
        return payload

    def load_qa_report(self, project_id: str) -> Optional[dict]:
        try:
            return self._get_json(self.qa_report_key(project_id))
        except ObjectNotFound:
            return None

    # -- mixes ------------------------------------------------------------

    def record_stem(self, project_id: str, *, run_id: str, cache_key: str,
                    endpoint: str, prompt: str, duration_s: float,
                    variant: str = "", served_endpoint: Optional[str] = None,
                    actor: str = "system") -> Event:
        """Record one *billed* stem generation.

        Cache hits are deliberately not logged. A full render touches every
        event in the timeline and the demo clip's stems are almost all cached,
        so logging hits would bury the handful of real generations — and the
        thing this log exists to answer is "what did this run actually spend".
        The manifest inventories every stem, hit or miss; this records the spend.

        `endpoint` is what was *asked for* — it is the cache address. When a
        fallback tier answered instead, `served_endpoint` names who actually
        produced the audio and therefore who actually charged for it. Omitted
        when the requested tier served it, so the common case stays quiet.
        """
        payload = {"cache_key": cache_key, "endpoint": endpoint,
                   "prompt": prompt, "duration_s": duration_s,
                   "variant": variant}
        if served_endpoint and served_endpoint != endpoint:
            payload["served_endpoint"] = served_endpoint
        return self.append_event(
            project_id, EVENT_STEM_GENERATED, actor=actor, run_id=run_id,
            payload=payload)

    def record_stage(self, project_id: str, *, run_id: str, stage,
                     actor: str = "system") -> Event:
        """Record one workflow stage's outcome as it happens.

        SPEC §8.2 wants the workflow run id, the current stage, and the
        stage-level outcome visible in job history. The finished `Run` carries
        the whole list, but it is only written when the job ends — so a client
        watching a render would see nothing until it was over. Appending each
        stage to the event log as it completes is what makes "current stage"
        answerable for a run still in flight.

        `stage` is a `foleyflow.stages.StageRecord`.
        """
        return self.append_event(
            project_id, EVENT_STAGE_RECORDED, actor=actor, run_id=run_id,
            payload=stage.to_dict())

    def record_mix(self, project_id: str, *, run_id: str, mix_key: str,
                   video_key: str, tier: str, timeline_version: int,
                   stem_count: int = 0, generated_count: int = 0,
                   actor: str = "system") -> Event:
        """Announce a finished mix. The artifacts are already in storage."""
        return self.append_event(
            project_id, EVENT_MIX_COMPLETED, actor=actor, run_id=run_id,
            payload={"mix_key": mix_key, "video_key": video_key, "tier": tier,
                     "timeline_version": timeline_version,
                     "stem_count": stem_count,
                     "generated_count": generated_count})

    def save_stem_inventory(self, project_id: str, run_id: str,
                            stems: List[Dict[str, Any]]) -> str:
        """Every stem a mix used, cache hits included.

        Separate from the `stem.generated` events, which record only spend.
        This is the inventory the provenance manifest is built from, so it has
        to list what the mix is *made of*, not what it paid for.
        """
        return self._put_json(
            self.run_artifact_key(project_id, run_id, "stems.json"),
            {"stems": stems})

    def load_stem_inventory(self, project_id: str, run_id: str) -> List[Dict[str, Any]]:
        try:
            key = self.run_artifact_key(project_id, run_id, "stems.json")
            return self._get_json(key).get("stems", [])
        except ObjectNotFound:
            return []

    def get_mix(self, project_id: str, run_id: str) -> Optional[dict]:
        """The recorded mix for one run, or None if that run produced none."""
        for event in self.list_events(project_id):
            if event.type == EVENT_MIX_COMPLETED and event.run_id == run_id:
                return {"run_id": run_id, **event.payload}
        return None

    def get_latest_mix(self, project_id: str) -> Optional[dict]:
        """The most recent mix, read from the log rather than a pointer.

        There is no `mixes/latest.json`: §10 allows exactly one mutable pointer
        and `exports/latest.json` is it. Event ULIDs already sort in creation
        order, so the newest mix is the last matching event.
        """
        latest = None
        for event in self.list_events(project_id):
            if event.type == EVENT_MIX_COMPLETED:
                latest = {"run_id": event.run_id, **event.payload}
        return latest

    # -- exports ----------------------------------------------------------

    def save_manifest(self, project_id: str, run_id: str, manifest: dict) -> str:
        """Write a run's provenance manifest (§11.6), once.

        Immutable: the manifest is the auditable claim about what a run
        produced, and a second export of the same run must not be able to
        rewrite history. Re-exporting the same mix is therefore a no-op here,
        which the caller treats as success rather than a conflict.
        """
        key = self.manifest_key(project_id, run_id)
        try:
            return self._put_json_immutable(key, manifest)
        except StorageError:
            if self.backend.exists(key):
                return key
            raise

    def load_manifest(self, project_id: str, run_id: str) -> Optional[dict]:
        try:
            return self._get_json(self.manifest_key(project_id, run_id))
        except ObjectNotFound:
            return None

    def set_latest_export(self, project_id: str, *, run_id: str, export_key: str,
                          manifest_key: Optional[str] = None,
                          actor: str = "user") -> dict:
        """Update the one sanctioned mutable pointer (SPEC.md §10).

        It names an immutable run, so repointing it publishes a new export
        without destroying the previous one.
        """
        payload = {
            "run_id": run_id,
            "export_key": export_key,
            "manifest_key": manifest_key,
            "updated_at": utcnow(),
        }
        self._put_json(self.latest_export_key(project_id), payload)
        self.append_event(project_id, EVENT_EXPORT_APPROVED, actor=actor,
                          run_id=run_id, payload={"export_key": export_key})
        return payload

    def get_latest_export(self, project_id: str) -> Optional[dict]:
        try:
            return self._get_json(self.latest_export_key(project_id))
        except ObjectNotFound:
            return None

    # -- derived state ----------------------------------------------------

    def get_state(self, project_id: str, *,
                  now: Optional[datetime] = None) -> ProjectState:
        """Fold the event log into the project's current state.

        Folded per run, then reduced — not by letting whichever event happened
        to be written last set the status. Under concurrent runs the last event
        is not the last outcome, and treating every `run.started` as
        "analyzing" made a published export regress to `analyzing` the moment a
        user clicked Regenerate, with no path back to `ready`.
        """
        project = self.get_project(project_id)
        events = self.list_events(project_id)
        now = now or datetime.now(timezone.utc)

        runs: Dict[str, Dict[str, Any]] = {}
        timeline_version = None
        has_export = False
        source_sha256 = None
        duration_s = None
        latest_run_id = None
        qa_flagged = False
        latest_mix = None
        export = None

        for event in events:
            if event.run_id:
                entry = runs.setdefault(event.run_id, {
                    "kind": "unknown", "status": "running",
                    "started_at": event.created_at})
                if event.type == EVENT_RUN_STARTED:
                    entry["kind"] = event.payload.get("kind", entry["kind"])
                    entry["started_at"] = event.created_at
                    latest_run_id = event.run_id
                elif event.type == EVENT_RUN_COMPLETED:
                    entry["status"] = "completed"
                elif event.type in (EVENT_RUN_FAILED,
                                    EVENT_RUN_FAILED_AFTER_COMPLETION):
                    entry["status"] = "failed"

            if event.type == EVENT_INPUT_RECORDED:
                source_sha256 = event.payload.get("sha256") or source_sha256
                duration_s = event.payload.get("duration_s") or duration_s
            elif event.type == EVENT_PLAN_PROPOSED:
                timeline_version = max(timeline_version or 0,
                                       event.payload.get("timeline_version") or 0)
            elif event.type == EVENT_QA_FLAGGED:
                qa_flagged = True
            elif event.type == EVENT_QA_CLEARED:
                qa_flagged = False
            elif event.type == EVENT_MIX_COMPLETED:
                latest_mix = {"run_id": event.run_id, **event.payload}
            elif event.type == EVENT_EXPORT_APPROVED:
                has_export = True
                export = {"run_id": event.run_id, **event.payload}

        # A run still marked running past its lease had its process die; nothing
        # will ever write its terminal record.
        stale = []
        for run_id, entry in runs.items():
            if entry["status"] != "running":
                continue
            started = _parse_iso(entry["started_at"])
            if started and now - started > timedelta(seconds=RUN_LEASE_S):
                entry["status"] = "stale"
                stale.append(run_id)

        status = self._derive_status(project, runs, timeline_version,
                                     has_export, qa_flagged, source_sha256)

        return ProjectState(
            project=project,
            status=status,
            latest_run_id=latest_run_id,
            timeline_version=timeline_version,
            event_count=len(events),
            has_export=has_export,
            source_sha256=source_sha256,
            duration_s=duration_s,
            stale_run_ids=sorted(stale),
            latest_mix=latest_mix,
            export=export,
        )

    @staticmethod
    def _derive_status(project, runs, timeline_version, has_export,
                       qa_flagged, source_sha256) -> str:
        """Project status from run outcomes and artifacts, in priority order.

        A live run outranks a finished one because it is what the user is
        waiting on; an approved export outranks everything else because it is
        the terminal state of the whole workflow. A timeline means there is a
        plan to review. Failed runs without a timeline outrank a QA flag so a
        crash mid-analysis cannot masquerade as needs_review.
        """
        live = [r for r in runs.values() if r["status"] == "running"]
        if live:
            return STATUS_ANALYZING if any(r["kind"] == "analyze" for r in live) \
                else STATUS_NEEDS_REVIEW if has_export or timeline_version \
                else STATUS_ANALYZING

        if has_export:
            return STATUS_READY
        if timeline_version:
            return STATUS_NEEDS_REVIEW

        # Failed/stale before qa_flagged: a mid-flight QA write must not mask an
        # analysis that never landed a timeline as "needs review".
        if any(r["status"] in ("failed", "stale") for r in runs.values()):
            return STATUS_FAILED
        if qa_flagged:
            return STATUS_NEEDS_REVIEW
        if not (source_sha256 or project.source_sha256):
            return STATUS_AWAITING_UPLOAD
        return STATUS_NEW
