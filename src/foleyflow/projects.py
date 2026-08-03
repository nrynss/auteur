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
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from foleyflow.models import Timeline
from foleyflow.qa import QaReport, findings_signature, report_from_dict, with_preserved_audio
from foleyflow.timeline_io import dict_to_timeline, timeline_to_dict
from foleyflow.ids import is_valid, new_event_id, new_project_id, new_run_id
from foleyflow.storage.base import (
    Backend,
    ObjectNotFound,
    PreconditionFailed,
    StorageError,
    get_bytes_many,
)

# Process-local event-log checkpoint (unit 7.18 phase-2).
# Fingerprint = (process epoch, key count, last key). Same-process appends bump
# the epoch and clear the entry so a just-written event is never missing.
# Cross-process appends change list_keys fingerprint → miss. Multi-instance
# limitation matches the ledger row cache: each process holds its own view.
_event_log_lock = threading.Lock()
_event_log_epoch: Dict[str, int] = {}
# project_id -> (epoch, (n_keys, last_key), events)
_event_log_cache: Dict[str, Tuple[int, Tuple[int, str], List["Event"]]] = {}


def invalidate_event_log_cache(project_id: Optional[str] = None) -> None:
    """Drop cached event lists (one project, or all). Tests / backend rewire."""
    with _event_log_lock:
        if project_id is None:
            _event_log_cache.clear()
            _event_log_epoch.clear()
        else:
            _event_log_cache.pop(project_id, None)

# Lifecycle event types — SPEC.md §8.4's domain-neutral names. Deliberately not
# incident-domain terms inherited from Mosaic.
EVENT_PROJECT_CREATED = "postproduction.project.created"
EVENT_PROJECT_ADOPTED = "postproduction.project.adopted"
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
# Advisory budget: latest-wins figure and threshold crossings (task 9).
EVENT_BUDGET_SET = "postproduction.budget.set"
EVENT_BUDGET_ALERT = "postproduction.budget.alert"

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
    # Latest-wins advisory budget from EVENT_BUDGET_SET; falls back to the
    # creation-time seed on the frozen project record for pre-task-9 projects.
    budget_usd: Optional[float] = None
    # Event id of the latest budget.set — alert marker keys include it so a
    # clear→re-set of the same dollar amount can fire thresholds again.
    budget_generation: Optional[str] = None
    # Threshold fractions that have already fired an alert under the current
    # budget generation (from EVENT_BUDGET_ALERT events).
    budget_thresholds_crossed: List[float] = field(default_factory=list)

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
            "budget_generation": self.budget_generation,
            "budget_thresholds_crossed": self.budget_thresholds_crossed,
        }
        # The event log wins over the frozen identity record: sha256 and
        # duration are only known after the upload is confirmed, which happens
        # long after the project record is written. budget_usd is likewise
        # latest-wins from the log (task 9) with a legacy fallback.
        payload["source_sha256"] = self.source_sha256 or self.project.source_sha256
        payload["duration_s"] = self.duration_s or self.project.duration_s
        payload["budget_usd"] = self.budget_usd
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
        # Advisory budget is an event, not a frozen field (task 9). The value
        # on Project is a seed for legacy readers; the log is source of truth.
        # Always emit so a later clear (budget_usd=None) is distinguishable
        # from "never set" on projects that only carry the creation seed.
        self.append_event(project.id, EVENT_BUDGET_SET, actor=actor,
                          payload={"budget_usd": budget_usd})
        if source_sha256:
            self.record_input(project.id, sha256=source_sha256,
                              duration_s=duration_s, actor=actor)
        return project

    def adopt_project(self, project_id: str, *, title: str, input_key: str,
                      actor: str = "user") -> "tuple[Project, bool]":
        """Write the project record for an existing orphan prefix (unit 7.17).

        Recovery, not creation: the id is the prefix's own, and the record
        describes what is already there. Normally the record is absent and
        `put_if_absent` allocates it immutably — a racing adopt cannot
        double-create. When the prefix already holds a `project.json` that
        does not parse, adoption is the one deliberate overwrite: a record
        that cannot be read is not completed work, the repair is consented to
        by the caller (the route has already read the object and shown it
        unreadable), and the event log names it `postproduction.project.adopted`
        with `repaired: true` and the actor.

        Returns ``(project, repaired)``. Raises ``PreconditionFailed`` when a
        readable record already exists — the id is live and the normal project
        routes own it.
        """
        key = self.project_key(project_id)
        try:
            existing = self.backend.get_bytes(key)
        except ObjectNotFound:
            existing = None
        project = Project(
            id=project_id,
            title=title,
            created_at=utcnow(),
            input_key=input_key,
            source_sha256="",
            source_filename="",
            duration_s=None,
            budget_usd=None,
        )
        payload = json.dumps(project.to_dict(), indent=2, sort_keys=True)
        payload = payload.encode("utf-8")
        repaired = False
        if existing is None:
            self.backend.put_if_absent(key, payload,
                                       content_type="application/json")
        else:
            try:
                parsed = json.loads(existing.decode("utf-8"))
                if not (isinstance(parsed, dict)
                        and isinstance(parsed.get("id"), str)
                        and parsed["id"]):
                    raise ValueError("record has no id")
                Project.from_dict(parsed)
            except (ValueError, TypeError):
                # Unreadable record: explicit repair, recorded in the log.
                self.backend.put_bytes(key, payload,
                                       content_type="application/json")
                repaired = True
            else:
                raise PreconditionFailed(
                    f"project {project_id!r} already has a readable record")
        self.append_event(project_id, EVENT_PROJECT_ADOPTED, actor=actor,
                          payload={"title": title, "repaired": repaired})
        self.append_event(project_id, EVENT_BUDGET_SET, actor=actor,
                          payload={"budget_usd": None})
        return project, repaired

    def set_budget(self, project_id: str, budget_usd: Optional[float], *,
                   actor: str = "user") -> Optional[Event]:
        """Set or clear the project's advisory budget (latest-wins event).

        ``None`` clears the budget — no alerts fire and remaining is null.
        The project record is never rewritten (SPEC.md §10).

        Unchanged re-PUTs are a no-op (no new event, no re-alerts): a UI that
        saves an unedited form must not spam the log. When the figure does
        change to a positive value, thresholds are evaluated against a full
        ``project_spend`` so lowering the budget beneath existing spend
        alerts immediately (task 17) — the running-total cache is process-
        local and the API process may never have written a row for this
        project.
        """
        if budget_usd is not None and budget_usd <= 0:
            raise ValueError("budget_usd must be > 0 when set")

        # Same-value no-op: generation is the event id, so an unchanged re-PUT
        # would mint a new generation and re-fire every crossed threshold.
        current = self.get_state(project_id).budget_usd
        if budget_usd is None and current is None:
            return None
        if (budget_usd is not None and current is not None
                and abs(float(budget_usd) - float(current)) < 1e-9):
            return None

        event = self.append_event(
            project_id, EVENT_BUDGET_SET, actor=actor,
            payload={"budget_usd": budget_usd})
        # Drop the ledger's short-lived budget cache so the next evaluation
        # sees the new figure and generation (alert marker keys include both).
        try:
            from foleyflow.ledger import (
                evaluate_budget_alerts,
                invalidate_budget_cache,
            )
            invalidate_budget_cache(project_id)
            # Clearing removes the figure — nothing to cross. Raising above
            # spend is silent inside evaluate (no threshold met). Lowering
            # beneath spend is the headline case task 17 covers.
            if budget_usd is not None:
                evaluate_budget_alerts(project_id, reason="budget_set")
        except Exception:
            # Never let an alerting bug fail a durable budget.set.
            pass
        return event

    def budget_alert_key(self, project_id: str, budget_usd: float,
                         threshold: float, *,
                         generation: str = "none") -> str:
        """Idempotency key for one (project, generation, threshold) alert.

        ``put_if_absent`` on this key is what makes concurrent stem fan-out
        emit exactly one alert when several rows cross the same threshold.
        ``generation`` is the latest budget.set event id, so clear→re-set
        the same dollar amount (or a raise) can alert again under a new set.
        """
        # Sanitize generation for a path segment; cents/bp avoid float noise.
        gen = "".join(c for c in generation if c.isalnum() or c in ("_", "-"))
        gen = gen or "none"
        cents = int(round(float(budget_usd) * 100))
        bp = int(round(float(threshold) * 1000))
        return f"{self._root(project_id)}/alerts/{gen}_{cents}_{bp:04d}.json"

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
        if not keys:
            return []
        # Concurrent GETs: listing every project.json serially is one B2 RTT
        # per project (same pattern as list_events).
        blobs = get_bytes_many(self.backend, keys)
        projects = [
            Project.from_dict(json.loads(blobs[k].decode("utf-8")))
            for k in keys if k in blobs
        ]
        return sorted(projects, key=lambda p: p.id, reverse=True)

    # -- events -----------------------------------------------------------

    def append_event(self, project_id: str, event_type: str, *,
                     actor: str = "system", run_id: Optional[str] = None,
                     payload: Optional[dict] = None) -> Event:
        project_id = self._check_id(project_id, "proj")
        event = Event(
            id=new_event_id(),
            project_id=project_id,
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
        # Never serve state missing this event in the same process.
        with _event_log_lock:
            _event_log_epoch[project_id] = _event_log_epoch.get(project_id, 0) + 1
            _event_log_cache.pop(project_id, None)
        return event

    def list_events(self, project_id: str) -> List[Event]:
        """Chronological. ULID event ids sort in creation order, so the sorted
        key listing is already the ordered log.

        Events are fetched concurrently: against B2 each GET is tens–hundreds
        of ms, and a busy project has dozens of events — serial GETs were the
        dominant cost of GET /projects/{id} (unit 7.18).

        Process-local checkpoint: after the first multi-get for a stable key
        set, subsequent folds reuse the event list (still re-lists keys so a
        cross-process append is detected). Same-process appends bump an epoch
        and clear the cache immediately.
        """
        project_id = self._check_id(project_id, "proj")
        keys = sorted(self.backend.list_keys(self.events_prefix(project_id)))
        fingerprint: Tuple[int, str] = (
            len(keys), keys[-1] if keys else "")
        with _event_log_lock:
            epoch = _event_log_epoch.get(project_id, 0)
            cached = _event_log_cache.get(project_id)
            if (cached is not None
                    and cached[0] == epoch
                    and cached[1] == fingerprint):
                # Return a shallow copy so callers cannot mutate the cache.
                return list(cached[2])

        if not keys:
            with _event_log_lock:
                if _event_log_epoch.get(project_id, 0) == epoch:
                    _event_log_cache[project_id] = (epoch, fingerprint, [])
            return []

        # skip_missing=False: a listed event object must exist; a miss or a
        # storage fault fails the whole log rather than silently dropping rows.
        blobs = get_bytes_many(self.backend, keys, skip_missing=False)
        events = [
            Event.from_dict(json.loads(blobs[k].decode("utf-8")))
            for k in keys
        ]
        with _event_log_lock:
            # Discard if an append landed while we multi-got — caller still
            # receives this snapshot (same race as an uncached mid-write list);
            # the next call re-fetches.
            if _event_log_epoch.get(project_id, 0) == epoch:
                _event_log_cache[project_id] = (epoch, fingerprint, events)
        return events

    @staticmethod
    def invalidate_state_cache(project_id: Optional[str] = None) -> None:
        """Alias for :func:`invalidate_event_log_cache` (bench / tests)."""
        invalidate_event_log_cache(project_id)

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
        """Oldest first. Concurrent GETs — same B2 RTT tax as list_events."""
        keys = sorted(
            k for k in self.backend.list_keys(f"{self._root(project_id)}/runs/")
            if k.endswith("/run.json")
        )
        if not keys:
            return []
        blobs = get_bytes_many(self.backend, keys, skip_missing=False)
        return [
            Run.from_dict(json.loads(blobs[k].decode("utf-8")))
            for k in keys
        ]

    # -- timelines --------------------------------------------------------

    def save_timeline(self, project_id: str, timeline: Timeline, *,
                      run_id: Optional[str] = None, actor: str = "system",
                      source: Optional[str] = None) -> int:
        """Store the next timeline version and return its number.

        Allocation retries on collision. Computing `latest + 1` and then writing
        is not atomic: two writers both read v1, both write v2, and one plan is
        destroyed with no error while the log records two `plan.proposed` events
        claiming the same version. The conditional write makes the loser lose
        loudly, so it simply takes the next number.

        Versions are never reused: a repair produces v2 alongside v1 rather than
        replacing it, which is what makes the history in §6.3 real and what lets
        a user go back to an earlier plan.

        ``source`` is optional provenance for the plan.proposed payload
        (e.g. ``"commit"`` for a direct editor PUT, omitted for analysis/repair).
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

        event_payload: Dict[str, Any] = {
            "timeline_version": version,
            "event_count": len(timeline.events),
        }
        if source is not None:
            event_payload["source"] = source
        self.append_event(project_id, EVENT_PLAN_PROPOSED, actor=actor,
                          run_id=run_id, payload=event_payload)
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
                  now: Optional[datetime] = None,
                  events: Optional[List[Event]] = None) -> ProjectState:
        """Fold the event log into the project's current state.

        Folded per run, then reduced — not by letting whichever event happened
        to be written last set the status. Under concurrent runs the last event
        is not the last outcome, and treating every `run.started` as
        "analyzing" made a published export regress to `analyzing` the moment a
        user clicked Regenerate, with no path back to `ready`.

        `events` lets a caller that has already read the log pass it in rather
        than have it read again. `list_events` is one object GET per event, so
        on a busy project a path that folds the log twice costs hundreds of
        round trips against B2 — see the timeline commit, which needs both the
        state and the raw log and now reads it exactly once.
        """
        project = self.get_project(project_id)
        if events is None:
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
        # Latest-wins budget; seed from the frozen record for projects created
        # before budget.set events existed.
        budget_usd = project.budget_usd
        seen_budget_set = False
        budget_generation: Optional[str] = None
        # Thresholds crossed under the *current* budget generation only.
        thresholds_crossed: List[float] = []

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
            elif event.type == EVENT_BUDGET_SET:
                seen_budget_set = True
                raw = event.payload.get("budget_usd")
                budget_usd = float(raw) if raw is not None else None
                budget_generation = event.id
                thresholds_crossed = []
            elif event.type == EVENT_BUDGET_ALERT:
                # Only count alerts under the current budget generation.
                threshold = event.payload.get("threshold")
                alert_gen = event.payload.get("generation")
                if threshold is not None and (
                        alert_gen is None or alert_gen == budget_generation
                        or (budget_generation is None and alert_gen is None)):
                    # Also accept alerts that match budget figure when older
                    # events predate generation stamping.
                    t = float(threshold)
                    if t not in thresholds_crossed:
                        if alert_gen == budget_generation or (
                                alert_gen is None
                                and event.payload.get("budget_usd") == budget_usd):
                            thresholds_crossed.append(t)

        if not seen_budget_set:
            # Pre-task-9 project: only the frozen seed exists.
            budget_usd = project.budget_usd

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
            budget_usd=budget_usd,
            budget_generation=budget_generation,
            budget_thresholds_crossed=sorted(thresholds_crossed),
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
