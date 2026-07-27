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
      exports/latest.json           the single mutable pointer (§10)
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from foley.models import Timeline
from foley.timeline_io import dict_to_timeline, timeline_to_dict
from foleyflow.ids import is_valid, new_event_id, new_project_id, new_run_id
from foleyflow.storage.base import Backend, StorageError

# Lifecycle event types — SPEC.md §8.4's domain-neutral names. Deliberately not
# incident-domain terms inherited from Mosaic.
EVENT_PROJECT_CREATED = "postproduction.project.created"
EVENT_ANALYSIS_COMPLETED = "postproduction.analysis.completed"
EVENT_PLAN_PROPOSED = "postproduction.foley.plan.proposed"
EVENT_REPAIR_APPROVED = "postproduction.repair.approved"
EVENT_STEM_GENERATED = "postproduction.stem.generated"
EVENT_QA_FLAGGED = "postproduction.qa.flagged"
EVENT_EXPORT_APPROVED = "postproduction.export.approved"
EVENT_RUN_STARTED = "postproduction.run.started"
EVENT_RUN_COMPLETED = "postproduction.run.completed"
EVENT_RUN_FAILED = "postproduction.run.failed"

STATUS_NEW = "new"
STATUS_ANALYZING = "analyzing"
STATUS_NEEDS_REVIEW = "needs_review"
STATUS_READY = "ready"
STATUS_FAILED = "failed"


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


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

    def to_dict(self) -> dict:
        return {
            **self.project.to_dict(),
            "status": self.status,
            "latest_run_id": self.latest_run_id,
            "timeline_version": self.timeline_version,
            "event_count": self.event_count,
            "has_export": self.has_export,
        }


class ProjectStore:
    """Reads and writes the project record set against any storage Backend."""

    def __init__(self, backend: Backend, work_dir: Optional[str] = None):
        self.backend = backend
        self.work_dir = work_dir or os.path.join(
            tempfile.gettempdir(), "foleyflow", "store")
        os.makedirs(self.work_dir, exist_ok=True)

    # -- key helpers ------------------------------------------------------

    @staticmethod
    def _check_id(project_id: str) -> str:
        """A project id becomes part of an object key, so a malformed one is a
        traversal risk, not merely bad data."""
        if not is_valid(project_id, "proj"):
            raise ValueError(f"not a valid project id: {project_id!r}")
        return project_id

    def _root(self, project_id: str) -> str:
        return f"projects/{self._check_id(project_id)}"

    def project_key(self, project_id: str) -> str:
        return f"{self._root(project_id)}/project.json"

    def events_prefix(self, project_id: str) -> str:
        return f"{self._root(project_id)}/events/"

    def timeline_key(self, project_id: str, version: int) -> str:
        return f"{self._root(project_id)}/timelines/timeline.v{version}.json"

    def run_key(self, project_id: str, run_id: str) -> str:
        return f"{self._root(project_id)}/runs/{run_id}/run.json"

    def latest_export_key(self, project_id: str) -> str:
        return f"{self._root(project_id)}/exports/latest.json"

    # -- JSON helpers -----------------------------------------------------

    def _put_json(self, key: str, payload: dict) -> str:
        path = os.path.join(self.work_dir, key.replace("/", "_"))
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, sort_keys=True)
        self.backend.put(key, path, content_type="application/json")
        return key

    def _get_json(self, key: str) -> dict:
        path = os.path.join(self.work_dir, key.replace("/", "_"))
        self.backend.get(key, path)
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _put_json_immutable(self, key: str, payload: dict) -> str:
        """Write only if absent. The guard is best-effort — object storage
        offers no atomic create — but it turns the common accident (a retry
        rewriting a completed artifact) into a loud error instead of silent
        history loss."""
        if self.backend.exists(key):
            raise StorageError(
                f"refusing to overwrite immutable object {key!r} — "
                "completed artifacts are never rewritten (SPEC.md §10)"
            )
        return self._put_json(key, payload)

    # -- projects ---------------------------------------------------------

    def create_project(self, *, title: str, input_key: str, source_sha256: str,
                       source_filename: str, duration_s: Optional[float] = None,
                       project_id: Optional[str] = None, actor: str = "system") -> Project:
        project = Project(
            id=project_id or new_project_id(),
            title=title,
            created_at=_utcnow(),
            input_key=input_key,
            source_sha256=source_sha256,
            source_filename=source_filename,
            duration_s=duration_s,
        )
        self._put_json_immutable(self.project_key(project.id), project.to_dict())
        self.append_event(project.id, EVENT_PROJECT_CREATED, actor=actor,
                          payload={"title": title, "source_sha256": source_sha256})
        return project

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
            project_id=self._check_id(project_id),
            type=event_type,
            created_at=_utcnow(),
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
        started event is in the log immediately, so nothing is lost."""
        run = Run(
            id=new_run_id(),
            project_id=self._check_id(project_id),
            kind=kind,
            created_at=_utcnow(),
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

    def fail_run(self, run: Run, error: str, *, actor: str = "system") -> Run:
        failed = Run(
            id=run.id, project_id=run.project_id, kind=run.kind,
            created_at=run.created_at, status="failed",
            timeline_version=run.timeline_version,
            parent_run_id=run.parent_run_id,
            genblaze_run_id=run.genblaze_run_id,
            error=error, metadata=run.metadata,
        )
        self._put_json_immutable(self.run_key(run.project_id, run.id),
                                 failed.to_dict())
        self.append_event(run.project_id, EVENT_RUN_FAILED, actor=actor,
                          run_id=run.id, payload={"error": error})
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

        Versions are never reused: a repair produces v2 alongside v1 rather
        than replacing it, which is what makes the history in §6.3 real and
        what lets a user go back to an earlier plan.
        """
        version = (self.latest_timeline_version(project_id) or 0) + 1
        self._put_json_immutable(self.timeline_key(project_id, version),
                                 timeline_to_dict(timeline))
        self.append_event(project_id, EVENT_PLAN_PROPOSED, actor=actor,
                          run_id=run_id,
                          payload={"timeline_version": version,
                                   "event_count": len(timeline.events)})
        return version

    def load_timeline(self, project_id: str, version: Optional[int] = None) -> Timeline:
        version = version or self.latest_timeline_version(project_id)
        if not version:
            raise StorageError(f"project {project_id} has no timeline yet")
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

    # -- exports ----------------------------------------------------------

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
            "updated_at": _utcnow(),
        }
        self._put_json(self.latest_export_key(project_id), payload)
        self.append_event(project_id, EVENT_EXPORT_APPROVED, actor=actor,
                          run_id=run_id, payload={"export_key": export_key})
        return payload

    def get_latest_export(self, project_id: str) -> Optional[dict]:
        key = self.latest_export_key(project_id)
        if not self.backend.exists(key):
            return None
        return self._get_json(key)

    # -- derived state ----------------------------------------------------

    def get_state(self, project_id: str) -> ProjectState:
        """Fold the event log into the project's current state."""
        project = self.get_project(project_id)
        events = self.list_events(project_id)

        status = STATUS_NEW
        latest_run_id = None
        timeline_version = None
        has_export = False

        for event in events:
            if event.run_id:
                latest_run_id = event.run_id
            if event.type == EVENT_RUN_STARTED:
                status = STATUS_ANALYZING
            elif event.type == EVENT_RUN_FAILED:
                status = STATUS_FAILED
            elif event.type == EVENT_PLAN_PROPOSED:
                timeline_version = event.payload.get("timeline_version", timeline_version)
                status = STATUS_NEEDS_REVIEW
            elif event.type == EVENT_QA_FLAGGED:
                status = STATUS_NEEDS_REVIEW
            elif event.type == EVENT_RUN_COMPLETED and status == STATUS_ANALYZING:
                status = STATUS_NEEDS_REVIEW
            elif event.type == EVENT_EXPORT_APPROVED:
                status = STATUS_READY
                has_export = True

        return ProjectState(
            project=project,
            status=status,
            latest_run_id=latest_run_id,
            timeline_version=timeline_version,
            event_count=len(events),
            has_export=has_export,
        )
