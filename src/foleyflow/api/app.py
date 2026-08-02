"""The FastAPI application.

Route handlers are deliberately thin: validate, call a domain function, shape a
response. Anything with real logic belongs in `foleyflow.*` so it stays testable
without HTTP.

Handlers are declared `def`, not `async def`. Every call below blocks — B2
round-trips, ffmpeg, provider requests — and a blocking call in an `async def`
handler stalls the event loop for every other request. Starlette runs sync
handlers in a threadpool, which is what this workload wants.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import List, Optional

from fastapi import Body, FastAPI, Query

from foleyflow.api import errors as err
from foleyflow.api.jobs import JobRunner
from foleyflow.api.schemas import (
    AnalyzeRequest,
    ArtifactOut,
    BudgetModelOut,
    BudgetResponse,
    CreateProjectRequest,
    CreateProjectResponse,
    DossierResponse,
    EventListResponse,
    EventOut,
    ExportRequest,
    HealthResponse,
    ModelQuotaOut,
    ProjectDetail,
    ProjectListResponse,
    ProjectSummary,
    QaReportResponse,
    QuotaResponse,
    ReadinessItem,
    RegenerateRequest,
    RemixRequest,
    RunAccepted,
    RunListResponse,
    RunOut,
    SceneListResponse,
    SceneOut,
    TimelineEventOut,
    TimelineResponse,
    TimelineVersionsResponse,
    UploadTarget,
    WorkerHealth,
)
from foleyflow.api.uploads import create_upload_target, source_key
from foleyflow.ids import is_valid
from foleyflow.projects import ProjectStore
from foleyflow.qa import build_report
from foleyflow.stages import StageRecord
from foleyflow.storage import get_backend
from foleyflow.storage.base import ObjectNotFound, StorageError

API_PREFIX = "/api/v1"
VERSION = "0.1.0"

# Signed artifact URLs. Long enough to browse a dossier, short enough that a
# copied link is not a durable public handle to project media.
ARTIFACT_URL_TTL_S = 3600


def _check_project_id(project_id: str) -> str:
    """Reject a malformed id before it reaches storage.

    A project id becomes part of an object key, so this is an input-validation
    boundary, not a politeness — and it turns a 500 from deep in the store into
    a 400 that says what is wrong.
    """
    if not is_valid(project_id, "proj"):
        raise err.InvalidId(f"Not a valid project id: {project_id}",
                            detail={"project_id": project_id})
    return project_id


def create_app(store: Optional[ProjectStore] = None,
               runner: Optional[JobRunner] = None) -> FastAPI:
    """Build the app.

    Dependencies are injected rather than constructed at import time so tests
    can supply a LocalBackend-backed store, and so importing this module never
    reaches for credentials.
    """
    if store is None:
        store = ProjectStore(get_backend())
    if runner is None:
        runner = JobRunner(store)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        """Drain in-flight work on shutdown.

        Cloud Run SIGTERMs the container on every deploy. Without this a run in
        flight simply vanishes: its start event is in the log, no terminal
        record is ever written, and the project reads `analyzing` until the
        lease in projects.RUN_LEASE_S expires.
        """
        yield
        runner.shutdown(wait=True)

    app = FastAPI(
        title="FoleyFlow",
        version=VERSION,
        description="AI post-production copilot. See docs/api.md for the contract.",
        docs_url="/docs",
        openapi_url=f"{API_PREFIX}/openapi.json",
        lifespan=lifespan,
    )
    err.install_handlers(app)

    app.state.store = store
    app.state.runner = runner

    # -- health -----------------------------------------------------------

    @app.get(f"{API_PREFIX}/health", response_model=HealthResponse, tags=["meta"])
    def health(worker: bool = False) -> HealthResponse:
        """Reports storage reachability too: an API that is up while B2 is not
        will serve empty reads and accept writes that fail later.

        `?worker=true` additionally probes the SFX worker behind the active
        tier. Off by default and deliberately so: this endpoint is what a
        platform liveness check calls, and a liveness check that depends on a
        third party can restart a perfectly healthy service because someone
        else's GPU box is off. Operators ask for it explicitly — `foleyflow
        health` always does.
        """
        try:
            # exists() raises rather than returning False when the answer is
            # unknown, so a rotated or wrongly-scoped credential surfaces here
            # instead of reporting a healthy service that silently misses every
            # cache lookup.
            store.backend.exists("health/probe")
            storage = "ok"
        except Exception:
            storage = "unavailable"
        return HealthResponse(status="ok" if storage == "ok" else "degraded",
                              storage=storage, version=VERSION,
                              worker=_worker_health() if worker else None)

    # -- projects ---------------------------------------------------------

    @app.post(f"{API_PREFIX}/projects", response_model=CreateProjectResponse,
              status_code=201, tags=["projects"])
    def create_project(body: CreateProjectRequest) -> CreateProjectResponse:
        """Create the record and hand back a direct-to-B2 upload URL.

        The project exists before its bytes do, so an abandoned or failed upload
        leaves a record and an event log describing exactly that, rather than
        nothing.
        """
        from foleyflow.ids import new_project_id
        from foleyflow.providers.config import get_budget_default

        project_id = new_project_id()
        key = source_key(project_id, body.filename)
        # Budget is fixed at creation: explicit body value, else the [budgets]
        # config default, else no cap. The resolved value is recorded on the
        # immutable project record, so later config edits don't re-price it.
        budget_usd = (body.budget_usd
                      if body.budget_usd is not None else get_budget_default())

        try:
            url, expires_at, headers = create_upload_target(
                store.backend, key, body.content_type)
        except StorageError as e:
            # Not str(e): StorageError messages are built from keys and boto3
            # detail, and this one reaches the client verbatim.
            raise err.StorageUnavailable(
                "Could not create an upload target for this project") from e

        project = store.create_project(
            title=body.title, input_key=key, source_sha256="",
            source_filename=body.filename, budget_usd=budget_usd,
            project_id=project_id, actor="user")

        return CreateProjectResponse(
            project=_summary(project, "awaiting_upload"),
            upload=UploadTarget(url=url, expires_at=expires_at, headers=headers),
        )

    @app.get(f"{API_PREFIX}/projects", response_model=ProjectListResponse,
             tags=["projects"])
    def list_projects(limit: int = Query(25, ge=1, le=100),
                      cursor: Optional[str] = None) -> ProjectListResponse:
        """Newest first, cursor-paginated on the id.

        Cursor rather than offset because ULIDs sort by creation time, so a
        cursor is a single comparison — an offset would re-list the whole
        prefix on every page.
        """
        try:
            projects = store.list_projects()
        except StorageError as e:
            # Never render a list of plausible-looking projects with invented
            # statuses during an outage; say the storage is down.
            raise err.StorageUnavailable("Project storage is unreachable") from e

        if cursor:
            projects = [p for p in projects if p.id < cursor]
        page = projects[:limit]
        next_cursor = page[-1].id if len(projects) > limit else None
        return ProjectListResponse(
            projects=[_summary(p, _status_of(store, p.id)) for p in page],
            next_cursor=next_cursor,
        )

    @app.get(f"{API_PREFIX}/projects/{{project_id}}", response_model=ProjectDetail,
             tags=["projects"])
    def get_project(project_id: str) -> ProjectDetail:
        _check_project_id(project_id)
        state = _load_state(store, project_id)
        scenes = store.load_scenes(project_id)
        readiness = _readiness(state, scenes, store.load_qa_report(project_id))
        return ProjectDetail(
            **_summary(state.project, state.status).model_dump(),
            source_sha256=state.source_sha256 or state.project.source_sha256 or None,
            latest_run_id=state.latest_run_id,
            timeline_version=state.timeline_version,
            scene_count=len(scenes),
            event_count=state.event_count,
            has_export=state.has_export,
            mix_run_id=(state.latest_mix or {}).get("run_id"),
            # Signed, not a bare key: the point of the readiness screen is that
            # the finished video is one click away, and B2 objects are private.
            export_url=_sign(store, (state.export or {}).get("export_key")),
            manifest_url=_sign(store, (state.export or {}).get("manifest_key")),
            readiness=readiness,
            readiness_percent=_readiness_percent(readiness),
            blocks_export=any(r.state == "blocked" for r in readiness),
        )

    # -- analysis ---------------------------------------------------------

    @app.post(f"{API_PREFIX}/projects/{{project_id}}/analyze",
              response_model=RunAccepted, status_code=202, tags=["analysis"])
    def analyze(project_id: str,
                body: AnalyzeRequest = Body(default=AnalyzeRequest())) -> RunAccepted:
        """Start analysis. Returns immediately with a run id to poll."""
        from foleyflow.api.jobs import SlotTaken
        from foleyflow.providers.config import get_analysis_config
        from foleyflow.quota import get_quota

        _check_project_id(project_id)
        state = _load_state(store, project_id)          # 404s before doing any work
        if not (state.source_sha256 or state.project.source_sha256):
            raise err.PreconditionFailed(
                "The source video has not been uploaded and confirmed yet",
                detail={"project_id": project_id, "required": "POST /uploaded"})

        model = body.model or get_analysis_config()["video_model"]

        quota = get_quota(model)
        # limit is None means "no declared budget", which is not the same as
        # "unlimited" — an undeclared model would otherwise sail past this gate
        # and die inside the worker with a KeyError, after the 202 already
        # promised a cost estimate.
        if quota.per_day.limit is None:
            raise err.InvalidRequest(
                f"Model {model!r} declares no request budget and cannot be used "
                "for analysis", detail={"model": model})
        if quota.status == "blocked":
            raise err.QuotaExhausted(model, resets_at=quota.resets_at,
                                     used=quota.per_day.used,
                                     limit=quota.per_day.limit)

        estimated = _estimate(model)
        _budget_gate(store, project_id, estimated)

        # The slot is claimed inside submit, under its lock. Checking here and
        # submitting afterwards would let two simultaneous requests through.
        try:
            run = runner.submit(project_id, "analyze",
                                _analysis_work(store, project_id, model, body.force),
                                metadata={"model": model, "force": body.force},
                                actor="user")
        except SlotTaken as e:
            raise err.AlreadyRunning(project_id, e.existing.run_id,
                                     e.existing.kind) from e

        return RunAccepted(run_id=run.id, kind="analyze",
                           estimated_cost_usd=estimated)
    @app.post(f"{API_PREFIX}/projects/{{project_id}}/uploaded",
              response_model=RunAccepted, status_code=202, tags=["projects"])
    def confirm_upload(project_id: str) -> RunAccepted:
        """Confirm the client's direct-to-B2 PUT completed.

        Verifies the object is really there, hashes it, reads its true duration
        with ffprobe, and records both in the event log. Until this runs a
        project has no `source_sha256`, so readiness stays pending and analysis
        is refused — which is correct: analysing a project whose upload was
        abandoned fails deep inside a worker instead of at the front door.
        """
        from foleyflow.api.jobs import SlotTaken

        _check_project_id(project_id)
        project = _load_state(store, project_id).project

        if not store.backend.exists(project.input_key):
            raise err.PreconditionFailed(
                "No uploaded object found for this project",
                detail={"project_id": project_id})

        try:
            run = runner.submit(project_id, "ingest",
                                _ingest_work(store, project_id), actor="user")
        except SlotTaken as e:
            raise err.AlreadyRunning(project_id, e.existing.run_id,
                                     e.existing.kind) from e
        return RunAccepted(run_id=run.id, kind="ingest")

    # -- mix and export ---------------------------------------------------

    @app.post(f"{API_PREFIX}/projects/{{project_id}}/mixes",
              response_model=RunAccepted, status_code=202, tags=["mix"])
    def create_mix(project_id: str,
                   body: RemixRequest = Body(default=RemixRequest())) -> RunAccepted:
        """Render stems, mix, mux, and measure. Returns a run id to poll.

        Not billed per call the way analysis is: every stem the shared cache
        already holds is free, so a re-mix of an unchanged timeline costs
        nothing and the quota gate that guards `/analyze` would be noise here.
        """
        from foleyflow.api.jobs import SlotTaken

        _check_project_id(project_id)
        state = _load_state(store, project_id)
        if not state.timeline_version:
            raise err.PreconditionFailed(
                "The project has no timeline to mix",
                detail={"project_id": project_id,
                        "required": "POST /analyze"})

        version = body.timeline_version or state.timeline_version
        if version not in store.list_timeline_versions(project_id):
            raise err.TimelineNotFound(project_id, version)

        try:
            run = runner.submit(project_id, "mix",
                                _mix_work(store, project_id, body.tier, version),
                                metadata={"tier": body.tier,
                                          "timeline_version": version},
                                actor="user")
        except SlotTaken as e:
            raise err.AlreadyRunning(project_id, e.existing.run_id,
                                     e.existing.kind) from e
        return RunAccepted(run_id=run.id, kind="mix")

    @app.post(f"{API_PREFIX}/projects/{{project_id}}/exports",
              response_model=RunAccepted, status_code=202, tags=["export"])
    def create_export(project_id: str,
                      body: ExportRequest = Body(default=ExportRequest())) -> RunAccepted:
        """Publish a rendered mix: manifest, then the `exports/latest.json` pointer.

        The QA gate is checked here as well as inside the run so a blocked
        export fails at the front door with the blockers listed, rather than
        202-ing and failing in a worker where the client has to poll to find
        out why.
        """
        from foleyflow.api.jobs import SlotTaken
        from foleyflow.render import blocking_qa_codes

        _check_project_id(project_id)
        state = _load_state(store, project_id)

        mix = (store.get_mix(project_id, body.run_id) if body.run_id
               else state.latest_mix)
        if not mix:
            raise err.PreconditionFailed(
                "The project has no rendered mix to export",
                detail={"project_id": project_id, "required": "POST /mixes"})

        blockers = blocking_qa_codes(store.load_qa_report(project_id))
        if blockers and not body.override_blocking:
            raise err.PreconditionFailed(
                "QA blocks this export",
                detail={"project_id": project_id, "blockers": blockers,
                        "override": "set override_blocking to publish anyway"})

        try:
            run = runner.submit(
                project_id, "export",
                _export_work(store, project_id, mix["run_id"],
                             body.override_blocking),
                parent_run_id=mix["run_id"],
                metadata={"mix_run_id": mix["run_id"],
                          "override_blocking": body.override_blocking},
                actor="user")
        except SlotTaken as e:
            raise err.AlreadyRunning(project_id, e.existing.run_id,
                                     e.existing.kind) from e
        return RunAccepted(run_id=run.id, kind="export")

    @app.post(f"{API_PREFIX}/projects/{{project_id}}/events/{{event_index}}/regenerate",
              response_model=RunAccepted, status_code=202, tags=["repair"])
    def regenerate_event(project_id: str, event_index: int,
                         body: RegenerateRequest = Body(default=RegenerateRequest())) -> RunAccepted:
        """Regenerate one event's stem and re-mix; everything else from cache
        (SPEC §9.3.4, docs/api.md §3.7).

        Addressed by event index, never name — names repeat within a timeline
        ("holster creak" twice), so a name is not an identifier. The repair
        allocates a new timeline version beside the old one and re-mixes it
        with a variant salt on exactly one stem's cache address, so one stem's
        bytes change and every other stem is still served from cache.

        The stem is identified by its routed request (endpoint, prompt,
        duration), not by the timeline row: the SFX cache is content-addressed
        on exactly that tuple, so two events that route to the same request
        already share one clip, and regenerating either one regenerates the
        shared stem — the lineage records the requested event index either
        way.

        A billable regeneration must be confirmed: the estimate is shown to
        the client, and `confirm_cost_usd` must come back equal to it (SPEC
        §9.4) — the request is refused otherwise, so a client cannot spend
        money it never displayed.
        """
        from foleyflow.api.jobs import SlotTaken
        from foleyflow.ledger import estimate_cost
        from foleyflow.routing import SfxRequest, clamp_duration, route_event
        from foleyflow.sfx import build_arguments
        from foleyflow.tiers import TierError, get_tier

        _check_project_id(project_id)
        state = _load_state(store, project_id)
        if not state.timeline_version:
            raise err.PreconditionFailed(
                "The project has no timeline to repair",
                detail={"project_id": project_id, "required": "POST /analyze"})

        # The timeline is immutable; capturing the version here means the
        # worker loads exactly the plan the estimate was shown against.
        version = state.timeline_version
        timeline = store.load_timeline(project_id, version)
        if not (0 <= event_index < len(timeline.events)):
            raise err.InvalidRequest(
                f"event index {event_index} is out of range for timeline "
                f"v{version} ({len(timeline.events)} events)",
                detail={"project_id": project_id, "event_index": event_index,
                        "event_count": len(timeline.events),
                        "timeline_version": version})

        event = timeline.events[event_index]
        request = route_event(event)

        if body.provider:
            # "Try an approved alternative" (SPEC §9.3.4): a declared SFX tier
            # re-aims this one request, and the duration is re-clamped to that
            # tier's endpoint so the request is exactly what it accepts.
            try:
                tier = get_tier(body.provider)
            except TierError as exc:
                raise err.InvalidRequest(str(exc)) from exc
            request = SfxRequest(
                endpoint=tier.endpoint, prompt=request.prompt,
                duration_s=clamp_duration(tier.endpoint, request.duration_s),
                category=request.category, tier=tier.name)

        estimated = estimate_cost(request.endpoint, build_arguments(request))
        if estimated > 0 and body.confirm_cost_usd is None:
            raise err.PreconditionFailed(
                "Regeneration is billable; confirm the estimated cost shown "
                "before proceeding (SPEC §9.4)",
                detail={"project_id": project_id, "event_index": event_index,
                        "estimated_cost_usd": estimated,
                        "require": "confirm_cost_usd"})
        if body.confirm_cost_usd is not None and \
                abs(body.confirm_cost_usd - estimated) > 1e-9:
            raise err.PreconditionFailed(
                "confirm_cost_usd must match the estimate shown",
                detail={"estimated_cost_usd": estimated,
                        "confirm_cost_usd": body.confirm_cost_usd})

        _budget_gate(store, project_id, estimated)

        try:
            run = runner.submit(
                project_id, "repair",
                _repair_work(store, project_id, version, event_index, request,
                             body.note, body.provider),
                metadata={"event_index": event_index,
                          "event_name": event.name,
                          "endpoint": request.endpoint,
                          "tier": request.tier,
                          "estimated_cost_usd": estimated},
                actor="user")
        except SlotTaken as e:
            raise err.AlreadyRunning(project_id, e.existing.run_id,
                                     e.existing.kind) from e
        return RunAccepted(run_id=run.id, kind="repair",
                           estimated_cost_usd=estimated)

    @app.get(f"{API_PREFIX}/projects/{{project_id}}/dossier",
             response_model=DossierResponse, tags=["projects"])
    def get_dossier(project_id: str) -> DossierResponse:
        """Every artifact the project has produced, in one call (docs/api.md §3.9).

        Composition only — nothing here writes, and nothing is downloaded to
        build the listing. Size comes from one cheap backend HEAD per object;
        sha256 comes from provenance where the system records one (the source
        video, each stem) and from the backend where it can produce one
        locally. Keys appear for provenance, exactly as docs/api.md §1
        allows; every `url` is the signed, time-limited way to actually read
        the object, so a dossier is browsable but never a durable public
        handle.
        """
        _check_project_id(project_id)
        state = _load_state(store, project_id)   # 404 before doing any work
        try:
            artifacts = _dossier_artifacts(store, project_id, state)
        except StorageError as e:
            # A listing assembled during an outage would be a confident
            # half-truth; say storage is down instead (same as every other
            # read that cannot proceed).
            raise err.StorageUnavailable("Project storage is unreachable") from e
        return DossierResponse(
            project_id=project_id,
            artifact_count=len(artifacts),
            total_bytes=sum(a.size_bytes or 0 for a in artifacts),
            artifacts=artifacts,
            budget=_budget_response(store, project_id),
        )

    # -- timeline ---------------------------------------------------------

    @app.get(f"{API_PREFIX}/projects/{{project_id}}/timeline",
             response_model=TimelineResponse, tags=["timeline"])
    def get_timeline(project_id: str, version: Optional[int] = None) -> TimelineResponse:
        _check_project_id(project_id)
        versions = store.list_timeline_versions(project_id)
        if not versions:
            raise err.TimelineNotFound(project_id)
        if version is not None and version not in versions:
            raise err.TimelineNotFound(project_id, version)

        resolved = version or versions[-1]
        timeline = store.load_timeline(project_id, resolved)
        scene_ids = _scene_ids_for(store, project_id, timeline)

        return TimelineResponse(
            version=resolved, duration=timeline.duration, ambience=timeline.ambience,
            events=[
                TimelineEventOut(
                    index=i, name=e.name, kind=e.kind, intensity=e.intensity,
                    start_time=e.start_time, duration=e.duration,
                    scene_id=scene_ids[i] if i < len(scene_ids) else None)
                for i, e in enumerate(timeline.events)
            ],
        )

    @app.get(f"{API_PREFIX}/projects/{{project_id}}/timeline/versions",
             response_model=TimelineVersionsResponse, tags=["timeline"])
    def timeline_versions(project_id: str) -> TimelineVersionsResponse:
        _check_project_id(project_id)
        versions = store.list_timeline_versions(project_id)
        return TimelineVersionsResponse(versions=versions,
                                        latest=versions[-1] if versions else None)

    # -- scenes -----------------------------------------------------------

    @app.get(f"{API_PREFIX}/projects/{{project_id}}/scenes",
             response_model=SceneListResponse, tags=["scenes"])
    def get_scenes(project_id: str) -> SceneListResponse:
        _check_project_id(project_id)
        out = []
        for raw in store.load_scenes(project_id):
            thumb = raw.get("thumbnail_key")
            out.append(SceneOut(
                id=raw["id"], index=raw["index"],
                start_s=raw["start_s"], end_s=raw["end_s"],
                duration_s=raw.get("duration_s", raw["end_s"] - raw["start_s"]),
                stats=raw.get("stats"),
                thumbnail_url=_sign(store, thumb) if thumb else None,
            ))
        return SceneListResponse(scenes=out)

    # -- QA ---------------------------------------------------------------

    @app.get(f"{API_PREFIX}/projects/{{project_id}}/qa",
             response_model=QaReportResponse, tags=["qa"])
    def get_qa(project_id: str) -> QaReportResponse:
        """Latest QA report. Continuity is written at analysis; audio lands
        once a mix is measured."""
        _check_project_id(project_id)
        _load_state(store, project_id)
        raw = store.load_qa_report(project_id)
        if raw is None:
            raise err.QaNotFound(project_id)
        return QaReportResponse(**raw)

    # -- runs and events --------------------------------------------------

    @app.get(f"{API_PREFIX}/projects/{{project_id}}/runs",
             response_model=RunListResponse, tags=["runs"])
    def list_runs(project_id: str) -> RunListResponse:
        _check_project_id(project_id)
        stages_by_run = _stages_by_run(store, project_id)
        return RunListResponse(runs=[_run_out(r, stages_by_run) for r in
                                     store.list_runs(project_id)])

    @app.get(f"{API_PREFIX}/projects/{{project_id}}/runs/{{run_id}}",
             response_model=RunOut, tags=["runs"])
    def get_run(project_id: str, run_id: str) -> RunOut:
        """Answered from storage for a finished run, and from the event log for
        one still in flight — a terminal record is only written at the end, so
        a running job has no run.json to read.

        The log is also consulted for a *terminal* event, not just a start.
        Assuming "no record means still running" made a run whose record could
        not be written poll as running forever, which is what a `--wait` client
        does until it is killed. If the log says it ended, it ended.
        """
        from foleyflow.projects import (EVENT_RUN_COMPLETED, EVENT_RUN_FAILED,
                                        EVENT_RUN_FAILED_AFTER_COMPLETION,
                                        EVENT_RUN_STARTED, EVENT_STAGE_RECORDED)

        _check_project_id(project_id)
        try:
            record = store.get_run(project_id, run_id)
        except StorageError:
            record = None

        seen = None
        status = "running"
        error = error_code = None
        stages: List[dict] = []
        genblaze_run_id = None
        for event in store.list_events(project_id):
            if event.run_id != run_id:
                continue
            if seen is None or event.type == EVENT_RUN_STARTED:
                seen = event
            if event.type == EVENT_STAGE_RECORDED:
                stages.append(event.payload)
                genblaze_run_id = (event.payload.get("genblaze_run_id")
                                   or genblaze_run_id)
            elif event.type == EVENT_RUN_COMPLETED:
                status = "completed"
            elif event.type in (EVENT_RUN_FAILED,
                                EVENT_RUN_FAILED_AFTER_COMPLETION):
                status = "failed"
                error = event.payload.get("error") or "run failed"
                error_code = event.payload.get("error_code") or "run_failed"

        # A finished run's own record wins — it is immutable and complete. The
        # event log is consulted regardless, because a run still in flight has
        # no record at all and its stage events are the only progress there is.
        if record is not None:
            return _run_out(record, {run_id: stages})

        if seen is None:
            raise err.RunNotFound(run_id)
        return RunOut(id=run_id, project_id=project_id,
                      kind=seen.payload.get("kind", "unknown"),
                      status=status, created_at=seen.created_at,
                      error=error, error_code=error_code,
                      genblaze_run_id=genblaze_run_id,
                      stages=stages,
                      current_stage=stages[-1]["name"] if stages else None)

    @app.get(f"{API_PREFIX}/projects/{{project_id}}/events",
             response_model=EventListResponse, tags=["runs"])
    def list_events(project_id: str) -> EventListResponse:
        _check_project_id(project_id)
        return EventListResponse(events=[
            EventOut(id=e.id, type=e.type, created_at=e.created_at, actor=e.actor,
                     run_id=e.run_id, payload=e.payload)
            for e in store.list_events(project_id)
        ])

    # -- quota ------------------------------------------------------------

    @app.get(f"{API_PREFIX}/quota", response_model=QuotaResponse, tags=["meta"])
    def quota() -> QuotaResponse:
        from foleyflow.quota import get_all_quotas, ledger_writable
        return QuotaResponse(
            estimate=True,
            ledger_writable=ledger_writable(),
            models=[ModelQuotaOut(**q.to_dict()) for q in get_all_quotas()])

    @app.get(f"{API_PREFIX}/projects/{{project_id}}/budget",
             response_model=BudgetResponse, tags=["projects"])
    def get_budget(project_id: str) -> BudgetResponse:
        """What this project has spent against its cap (docs/api.md §3.11).

        Counted from the cost ledger — never a provider balance. Estimates,
        like `/quota`; `ledger_writable: false` means fiction and the UI must
        stop presenting the numbers.
        """
        _check_project_id(project_id)
        _load_state(store, project_id)   # 404 before doing any work
        return _budget_response(store, project_id)

    return app


# -- helpers ----------------------------------------------------------------

def _worker_health() -> WorkerHealth:
    """Probe the SFX worker behind the active tier.

    Deliberately non-fatal: a render whose stems are all cached needs no worker
    at all, so a worker that is down must not make the API report itself
    unhealthy. It must, however, be *visible* — the failure this exists to catch
    is a demo where `/health` says ok, the first uncached stem is requested, and
    only then does anyone discover the GPU box is off or the tunnel is down.

    Only the self-hosted tier has something to probe; the paid and fallback
    tiers are hosted APIs whose reachability is not ours to assert.
    """
    from foleyflow.providers import get_adapter
    from foleyflow.tiers import active_tier_name, get_tier

    tier_name = None
    try:
        tier_name = active_tier_name()
        tier = get_tier(tier_name) if tier_name else None
        if tier is None or tier.adapter != "local":
            return WorkerHealth(status="not_applicable", tier=tier_name,
                                endpoint=tier.endpoint if tier else None)
        adapter = get_adapter("local")
        payload = adapter.health()
    except Exception as exc:
        # Configuration errors land here too — an unresolvable tier is just as
        # much "you cannot generate" as an unplugged GPU.
        return WorkerHealth(status="unreachable", tier=tier_name,
                            detail=f"{type(exc).__name__}: {exc}")

    return WorkerHealth(
        status=payload.get("status") or "ok",
        tier=tier_name,
        endpoint=tier.endpoint,
        model=payload.get("model"),
        device=payload.get("device"),
        auth_required=payload.get("auth_required"),
        auth_configured=payload.get("auth_configured"),
    )


def _stages_by_run(store: ProjectStore, project_id: str) -> dict:
    """Stage records from the event log, grouped by run id.

    Read once for a whole listing rather than per run: the log is a single
    object, and re-reading it for each of twenty runs would turn one storage
    round trip into twenty.
    """
    from foleyflow.projects import EVENT_STAGE_RECORDED

    out: dict = {}
    try:
        events = store.list_events(project_id)
    except StorageError:
        return out
    for event in events:
        if event.type == EVENT_STAGE_RECORDED and event.run_id:
            out.setdefault(event.run_id, []).append(event.payload)
    return out


def _run_out(record, stages_by_run: dict) -> RunOut:
    """A stored `Run` as the API's shape, with its §8.2 job history attached.

    The run's own `metadata["stages"]` is authoritative — it is the summary the
    work function froze onto an immutable record. The event log is the fallback
    for runs written before stages were recorded, and for anything a work
    function did not summarise.
    """
    payload = record.to_dict()
    stages = payload.get("metadata", {}).get("stages") or \
        stages_by_run.get(record.id) or []
    return RunOut(**payload, stages=stages,
                  current_stage=stages[-1]["name"] if stages else None)


def _status_of(store: ProjectStore, project_id: str) -> str:
    """Status for a list row. An unreadable individual project is reported as
    such rather than guessed at — the list route already fails loudly if the
    whole backend is down."""
    try:
        return store.get_state(project_id).status
    except StorageError:
        return "unknown"


def _summary(project, status: str) -> ProjectSummary:
    return ProjectSummary(
        id=project.id, title=project.title, status=status,
        created_at=project.created_at, source_filename=project.source_filename,
        duration_s=project.duration_s, budget_usd=project.budget_usd,
    )


def _load_state(store: ProjectStore, project_id: str):
    """Absent and unreachable are different failures.

    Mapping every StorageError to 404 turned a B2 outage into a confident
    "this project does not exist" — the client stops retrying and the user is
    told their work is gone. Only ObjectNotFound means absent.
    """
    try:
        return store.get_state(project_id)
    except ObjectNotFound as e:
        raise err.ProjectNotFound(project_id) from e
    except StorageError as e:
        raise err.StorageUnavailable("Project storage is unreachable") from e


def _sign(store: ProjectStore, key: Optional[str]) -> Optional[str]:
    """An artifact that cannot be signed is omitted rather than fatal —
    but only when the object is genuinely missing."""
    if not key:
        return None
    try:
        return store.backend.signed_url(key, ARTIFACT_URL_TTL_S)
    except ObjectNotFound:
        return None
    except StorageError:
        return None


def _head(store: ProjectStore, key: str):
    """Metadata for one artifact, or None when it is genuinely absent.

    Mirrors `_sign`'s contract: absent -> None, so the dossier omits the
    object rather than listing a hole. A backend that cannot answer raises,
    because dropping artifacts silently during a B2 outage would present a
    partial dossier as the whole truth.
    """
    try:
        return store.backend.head(key)
    except ObjectNotFound:
        return None


def _sign_strict(store: ProjectStore, key: str) -> str:
    """Signed URL for an artifact the dossier is about to list.

    `_sign` swallows StorageError so an unsigned link is omitted elsewhere
    (a missing thumbnail on the scenes rail is fine). For the dossier, a head
    that succeeded and a sign that failed is a storage fault, not an absent
    object — listing an artifact with no way to read it would contradict the
    "every artifact has a signed URL" contract. So this raises, and the route
    reports 503 like any other storage fault. An ObjectNotFound here is a
    vanished-between-head-and-sign race, which is the same fault class.
    """
    try:
        return store.backend.signed_url(key, ARTIFACT_URL_TTL_S)
    except ObjectNotFound as e:
        raise StorageError(f"object vanished while signing {key!r}") from e


def _dossier_artifacts(store: ProjectStore, project_id: str, state) -> List[ArtifactOut]:
    """The artifact listing behind `GET /dossier`. Composition only.

    Raises StorageError when storage cannot answer; the route maps that to the
    same 503 every other read uses. Absent objects are skipped, not listed as
    holes, and the same content-addressed stem referenced by several runs is
    listed once — the dossier counts physical objects, not references.
    """
    artifacts: List[ArtifactOut] = []
    seen: set = set()

    def _add(kind: str, key: str, *, sha256: Optional[str] = None) -> None:
        if key in seen:
            return
        seen.add(key)
        head = _head(store, key)
        if head is None:
            return
        artifacts.append(ArtifactOut(
            kind=kind, key=key, url=_sign_strict(store, key),
            size_bytes=head.size_bytes, sha256=sha256 or head.sha256))

    # Source first, then the derived record sets, then per-run artifacts —
    # creation order throughout, so the listing is deterministic.
    _add("source", state.project.input_key,
         sha256=state.source_sha256 or state.project.source_sha256 or None)

    for version in store.list_timeline_versions(project_id):
        _add("timeline", store.timeline_key(project_id, version))

    scenes = store.load_scenes(project_id)
    if scenes:
        _add("scene_index", store.scenes_key(project_id))
        for scene in scenes:
            thumb = scene.get("thumbnail_key")
            if thumb:
                _add("scene_thumbnail", thumb)

    if store.load_qa_report(project_id) is not None:
        _add("qa_report", store.qa_report_key(project_id))

    for event in store.list_events(project_id):
        _add("event", f"{store.events_prefix(project_id)}{event.id}.json")

    for run in store.list_runs(project_id):
        _add("run_record", store.run_key(project_id, run.id))
        stems = store.load_stem_inventory(project_id, run.id)
        if stems:
            _add("stems", store.run_artifact_key(project_id, run.id,
                                                  "stems.json"))
            for stem in stems:
                b2_key = stem.get("b2_key")
                if b2_key:
                    _add("stem", b2_key, sha256=stem.get("sha256"))
        _add("mix", store.mix_key(project_id, run.id))
        _add("final_export", store.mix_video_key(project_id, run.id))
        if store.load_manifest(project_id, run.id) is not None:
            _add("manifest", store.manifest_key(project_id, run.id))

    if store.get_latest_export(project_id) is not None:
        _add("export_pointer", store.latest_export_key(project_id))

    return artifacts


def _scene_ids_for(store: ProjectStore, project_id: str, timeline) -> List[Optional[str]]:
    from foleyflow.scenes import Scene, assign_scene_ids
    raw = store.load_scenes(project_id)
    if not raw:
        return [None] * len(timeline.events)
    scenes = [Scene(id=s["id"], index=s["index"], start_s=s["start_s"],
                    end_s=s["end_s"]) for s in raw]
    return assign_scene_ids(timeline, scenes)


def _estimate(model: str) -> float:
    from foleyflow.ledger import estimate_cost
    return estimate_cost(model, {})


def _budget_response(store: ProjectStore, project_id: str) -> BudgetResponse:
    """The §3.11 budget payload for one project, shared by GET /budget and the
    dossier's compact block."""
    from foleyflow.ledger import ledger_writable, project_spend

    state = store.get_state(project_id)
    spend = project_spend(project_id)
    budget = state.project.budget_usd
    remaining = None if budget is None else max(budget - spend["total"], 0.0)
    return BudgetResponse(
        project_id=project_id,
        budget_usd=budget,
        spent=round(spend["total"], 4),
        remaining=None if remaining is None else round(remaining, 4),
        ledger_writable=ledger_writable(),
        by_model=[BudgetModelOut(endpoint=endpoint, spent=round(value, 4))
                  for endpoint, value in sorted(spend["by_endpoint"].items())],
    )


def _budget_gate(store: ProjectStore, project_id: str, estimate: float) -> None:
    """Refuse a billable call that would take the project past its cap (§3.11).

    Called only by routes with a known estimate (analyze, regenerate). Free
    calls (estimate $0.00) are never refused, and a project with no configured
    budget is never gated. No reservation: two concurrent calls can both pass
    and overspend; the window is accepted for a single-instance deployment.
    """
    from foleyflow.ledger import project_spend

    if estimate <= 0:
        return
    budget = store.get_project(project_id).budget_usd
    if budget is None:
        return
    remaining = budget - project_spend(project_id)["total"]
    if remaining < estimate:
        raise err.PreconditionFailed(
            "This call would take the project past its budget",
            detail={"project_id": project_id, "budget_usd": budget,
                    "remaining": round(remaining, 4),
                    "estimated_cost_usd": estimate})


def _readiness(state, scenes, qa_report=None) -> List[ReadinessItem]:
    """The §6.1 checklist. `blocked` states are what §13 makes gate export."""
    items = [
        ReadinessItem(
            key="source", label="Source video stored in B2",
            # From the event log: the project record is written before the
            # upload exists, so the frozen Project never carries a hash.
            state="ok" if (state.source_sha256 or state.project.source_sha256)
            else "blocked",
            detail=state.project.source_filename),
        ReadinessItem(
            key="scenes", label="Scene analysis complete",
            state="ok" if scenes else "blocked",
            detail=f"{len(scenes)} scenes detected" if scenes else "required before export"),
        ReadinessItem(
            key="timeline", label="Foley plan proposed",
            state="ok" if state.timeline_version else "blocked",
            detail=f"timeline v{state.timeline_version}" if state.timeline_version
            else "required before export"),
        _mix_readiness(state),
        _qa_readiness(qa_report),
        ReadinessItem(
            key="export", label="Final export approved",
            state="ok" if state.has_export else "pending"),
    ]
    return items


def _mix_readiness(state) -> ReadinessItem:
    """A mix is required before export, and stale once the plan moves past it.

    A mix rendered from v1 is not evidence that v2 works — after a repair adds
    a version, the checklist has to say so rather than reporting the old render
    as satisfying the step.
    """
    mix = state.latest_mix
    if not mix:
        return ReadinessItem(
            key="mix", label="Foley mix rendered", state="blocked",
            detail="required before export")
    version = mix.get("timeline_version")
    if state.timeline_version and version and version != state.timeline_version:
        return ReadinessItem(
            key="mix", label="Foley mix rendered", state="warn",
            detail=f"mixed from v{version}; timeline is now "
                   f"v{state.timeline_version}")
    stems = mix.get("stem_count") or 0
    return ReadinessItem(
        key="mix", label="Foley mix rendered", state="ok",
        detail=f"{mix.get('tier')} tier, {stems} stems")


def _qa_readiness(qa_report) -> ReadinessItem:
    """Map a stored QA report onto one checklist row.

    True-peak overage blocks export (§13). Continuity and loudness drift only
    warn. No report yet is pending — analysis writes continuity, mix writes audio.
    """
    if not qa_report:
        return ReadinessItem(
            key="qa", label="QA checks", state="pending",
            detail="run analysis to produce the first report")
    status = qa_report.get("overall_status") or "ok"
    audio = qa_report.get("audio") or {}
    visual = qa_report.get("visual") or {}
    issues = list(audio.get("issues") or []) + list(visual.get("issues") or [])
    codes = sorted({i.get("code") for i in issues if i.get("code")})
    if status == "blocked" or any(i.get("severity") == "blocking" for i in issues):
        return ReadinessItem(
            key="qa", label="QA checks", state="blocked",
            detail=", ".join(codes) if codes else "blocking issue")
    if status == "needs_review" or issues:
        return ReadinessItem(
            key="qa", label="QA checks", state="warn",
            detail=", ".join(codes) if codes else "needs review")
    detail = "passed"
    if not audio.get("measured"):
        detail = "continuity ok — mix not measured yet"
    return ReadinessItem(key="qa", label="QA checks", state="ok", detail=detail)


def _readiness_percent(items: List[ReadinessItem]) -> int:
    """A transparent count of satisfied checks, not a quality score.

    SPEC.md §13 calls the figure "a transparent convenience, not a black-box
    quality claim", so it is exactly `ok / total` and nothing cleverer.
    """
    if not items:
        return 0
    return round(100 * sum(1 for i in items if i.state == "ok") / len(items))


def _fetch_source(store: ProjectStore, project_id: str) -> str:
    """Download the project's source video to a stable local path.

    Existence alone is not proof of a complete file: a download killed part-way
    leaves a truncated video that every later run reuses, and the timeline cache
    then hashes the fragment — producing a key that corresponds to no real
    asset, so the analysis runs and is cached under a lie. The Backend downloads
    to a `.part` name and renames on success, so anything present here is whole;
    a zero-length file is treated as absent.
    """
    from foleyflow.workflow import stage_for_ingest

    project = store.get_project(project_id)
    _, ext = os.path.splitext(project.input_key)
    local = os.path.join(store.work_dir, f"{project_id}_source{ext or '.mp4'}")
    if not os.path.exists(local) or os.path.getsize(local) == 0:
        store.backend.get(project.input_key, local)
    return stage_for_ingest(local, subdir=project_id)


def _ingest_sink(store: ProjectStore):
    """Genblaze's artifact sink, but only when B2 is where this app stores things.

    An instance running on LocalBackend (every test, and local development) gets
    `None`, which Genblaze supports: the run and the hash-verified manifest are
    still produced, the upload is skipped. Deriving it from the store rather
    than from an env var means the ingest publishes to exactly the bucket the
    rest of the project lives in, or to nothing — never to a third place.
    """
    from foleyflow.storage.b2 import B2Backend

    if not isinstance(store.backend, B2Backend):
        return None
    from foleyflow.workflow import get_sink

    return get_sink()


def _ingest_work(store: ProjectStore, project_id: str):
    """Verify and record the uploaded source: hash, size, true duration.

    Runs the source through Genblaze's ingest stage (SPEC §7, §8.2) rather than
    beside it, so the project's first stage produces the same artefact every
    later stage does: a canonical-hashed provenance manifest a judge can check
    with `genblaze verify`. The Genblaze run id lands on the FoleyFlow `Run`,
    which is what makes §8.2's "show the workflow run ID" answerable.

    The ffprobe/sha256 facts are recorded either way. A manifest is provenance;
    the duration is what every later stage's arithmetic depends on, so losing
    the former must not lose the latter.
    """
    def work(run):
        from foleyflow.analyze import get_video_duration
        from foleyflow.timeline_cache import file_sha256
        from foleyflow.workflow import ingest_source_video

        project = store.get_project(project_id)
        staged = _fetch_source(store, project_id)
        # ffprobe, not the model's self-reported duration. Every other module is
        # emphatic that ffprobe is the sole duration authority; this is where
        # that value enters the system.
        duration = get_video_duration(staged)
        sha256 = file_sha256(staged)
        store.record_input(project_id, sha256=sha256, duration_s=duration,
                           size_bytes=os.path.getsize(staged), actor="user")

        result = ingest_source_video(staged, project_id=project_id,
                                     title=project.title,
                                     sink=_ingest_sink(store))
        stage = StageRecord(
            name="ingest", status="succeeded", provider="genblaze",
            genblaze_run_id=result.run.run_id,
            detail=f"manifest {result.manifest.canonical_hash[:12]}")
        store.record_stage(project_id, run_id=run.id, stage=stage)

        store.complete_run(
            run, genblaze_run_id=result.run.run_id,
            metadata={"duration_s": duration,
                      "source_sha256": sha256,
                      "canonical_hash": result.manifest.canonical_hash,
                      "stages": [stage.to_dict()]})
    return work


def _analysis_work(store: ProjectStore, project_id: str, model: str, force: bool):
    """The analysis job body. Returned as a closure so the route stays thin and
    this stays unit-testable without HTTP."""
    def work(run):
        from foleyflow.analyze import TIMELINE_PROMPT, analyze_video_direct
        from foleyflow.scenes import detect_scenes
        from foleyflow.timeline_cache import get_or_analyze

        staged = _fetch_source(store, project_id)
        state = store.get_state(project_id)

        timeline, cached = get_or_analyze(
            staged, lambda: analyze_video_direct(staged, model=model),
            backend=store.backend, model_id=model, prompt=TIMELINE_PROMPT,
            force=force)

        # ffprobe's duration, recorded at ingest, in preference to the model's.
        duration = state.duration_s or state.project.duration_s or timeline.duration
        scenes = detect_scenes(staged, duration)
        store.save_scenes(project_id, scenes, video_path=staged, run_id=run.id)

        version = store.save_timeline(project_id, timeline, run_id=run.id)

        # After the timeline: a crash before this point leaves no QA flag that
        # could mask a failed analysis as needs_review. Continuity-only; prior
        # mix measurements are preserved inside save_qa_report.
        store.save_qa_report(
            project_id, build_report(scenes, run_id=run.id), run_id=run.id)

        store.complete_run(run, timeline_version=version,
                           metadata={"cached": cached, "model": model,
                                     "scene_count": len(scenes)})
    return work


def _mix_work(store: ProjectStore, project_id: str, tier: str,
              timeline_version: int):
    """The mix job body: stems, mix, mux, upload, QA."""
    def work(run):
        from foleyflow.render import render_mix

        staged = _fetch_source(store, project_id)
        result = render_mix(store, project_id, run=run, video_path=staged,
                            tier=tier, timeline_version=timeline_version)
        store.complete_run(
            run, timeline_version=result.timeline_version,
            genblaze_run_id=result.genblaze_run_id,
            metadata={"tier": result.tier, "mix_key": result.mix_key,
                      "video_key": result.video_key,
                      "stem_count": len(result.stems),
                      "generated_count": result.generated_count,
                      "ledger_delta_usd": round(result.ledger_delta, 4),
                      "qa_status": (result.qa or {}).get("overall_status"),
                      # The §8.2 job history, frozen onto the run record. The
                      # per-stage events are also in the log, but the log is
                      # append-only and unbounded; this is the run's own summary.
                      "stages": result.stages})
    return work


def _export_work(store: ProjectStore, project_id: str, mix_run_id: str,
                 override_blocking: bool):
    """The export job body: manifest, then the pointer.

    Re-checks the QA gate rather than trusting the route's check. The two are
    separated by however long the mix run took to be selected, and a QA refresh
    in between must not be able to slip a blocked mix past a stale decision.
    """
    def work(run):
        from foleyflow.render import publish_export

        pointer = publish_export(store, project_id, run=run,
                                 mix_run_id=mix_run_id,
                                 override_blocking=override_blocking,
                                 actor="user")
        store.complete_run(
            run,
            metadata={"export_key": pointer["export_key"],
                      "manifest_key": pointer["manifest_key"],
                      "overridden_blockers": pointer["overridden_blockers"]})
    return work


def _repair_work(store: ProjectStore, project_id: str, version: int,
                 event_index: int, request, note: Optional[str],
                 provider: Optional[str]):
    """The repair job body: a new timeline version, one regenerated stem, re-mix.

    ``request`` is the stem to regenerate, resolved (and possibly re-aimed at
    an approved alternative tier) at route time, so the estimate shown is the
    one actually spent. ``version`` is the immutable timeline the plan was
    shown against — loading it here cannot race a newer plan.

    The plan bytes are unchanged; the new version number is the lineage
    marker. v2 is the generation the regenerated take belongs to, and the
    readiness checklist says "mixed from v1; timeline is now v2" until this
    run's mix lands.
    """
    def work(run):
        from foleyflow.projects import EVENT_REPAIR_APPROVED
        from foleyflow.render import render_mix

        timeline = store.load_timeline(project_id, version)
        event = timeline.events[event_index]
        new_version = store.save_timeline(project_id, timeline, run_id=run.id)

        # The salt is unique per repair run, so repairing the same event twice
        # is a genuinely new take rather than a cache hit on the first one's.
        # cache_key_raw appends the salt only when non-empty (sfx_cache.cache_key
        # documents this), so every other stem keeps its existing address.
        variant = f"r{run.id[-8:]}"
        target = (request.endpoint, request.prompt,
                  round(request.duration_s, 2))

        def variant_fn(req):
            return variant if (req.endpoint, req.prompt,
                               round(req.duration_s, 2)) == target else ""

        # The superseded take is the address the *previous* mix served for this
        # request — captured before render_mix records this run's mix, so the
        # lineage names a fact about the bucket, not about this process.
        superseded = None
        latest = store.get_latest_mix(project_id)
        if latest:
            for stem in store.load_stem_inventory(project_id, latest["run_id"]):
                if (stem.get("endpoint"), stem.get("prompt"),
                        round(stem.get("duration_s") or 0.0, 2)) == target:
                    superseded = {
                        "cache_key": stem.get("cache_key"),
                        "b2_key": stem.get("b2_key"),
                        "variant": stem.get("variant") or "",
                    }
                    break

        staged = _fetch_source(store, project_id)
        result = render_mix(store, project_id, run=run, video_path=staged,
                            tier="full", timeline_version=new_version,
                            variant_fn=variant_fn)

        store.append_event(
            project_id, EVENT_REPAIR_APPROVED, actor="user", run_id=run.id,
            payload={"event_index": event_index, "event_name": event.name,
                     "target": {"endpoint": request.endpoint,
                                "prompt": request.prompt,
                                "duration_s": request.duration_s,
                                "tier": request.tier},
                     "superseded": superseded, "variant": variant,
                     "timeline_version": new_version,
                     "provider": provider, "note": note})

        store.complete_run(
            run, timeline_version=new_version,
            genblaze_run_id=result.genblaze_run_id,
            metadata={"event_index": event_index, "event_name": event.name,
                      "variant": variant, "superseded": superseded,
                      "provider": provider, "note": note,
                      "ledger_delta_usd": round(result.ledger_delta, 4),
                      "stem_count": len(result.stems),
                      "generated_count": result.generated_count,
                      "stages": result.stages})
    return work


app = None  # created by the ASGI entrypoint; see foleyflow.api.main
