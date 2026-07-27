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
from typing import List, Optional

from fastapi import Body, FastAPI, Query

from foleyflow.api import errors as err
from foleyflow.api.jobs import JobRunner
from foleyflow.api.schemas import (
    AnalyzeRequest,
    CreateProjectRequest,
    CreateProjectResponse,
    EventListResponse,
    EventOut,
    HealthResponse,
    ModelQuotaOut,
    ProjectDetail,
    ProjectListResponse,
    ProjectSummary,
    QuotaResponse,
    ReadinessItem,
    RunAccepted,
    RunListResponse,
    RunOut,
    SceneListResponse,
    SceneOut,
    TimelineEventOut,
    TimelineResponse,
    TimelineVersionsResponse,
    UploadTarget,
)
from foleyflow.api.uploads import create_upload_target, source_key
from foleyflow.ids import is_valid
from foleyflow.projects import ProjectStore
from foleyflow.storage import get_backend
from foleyflow.storage.base import StorageError

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
    app = FastAPI(
        title="FoleyFlow",
        version=VERSION,
        description="AI post-production copilot. See docs/api.md for the contract.",
        docs_url="/docs",
        openapi_url=f"{API_PREFIX}/openapi.json",
    )
    err.install_handlers(app)

    if store is None:
        store = ProjectStore(get_backend())
    if runner is None:
        runner = JobRunner(store)

    app.state.store = store
    app.state.runner = runner

    # -- health -----------------------------------------------------------

    @app.get(f"{API_PREFIX}/health", response_model=HealthResponse, tags=["meta"])
    def health() -> HealthResponse:
        """Reports storage reachability too: an API that is up while B2 is not
        will serve empty reads and accept writes that fail later."""
        try:
            store.backend.exists("health/probe")
            storage = "ok"
        except Exception:
            storage = "unavailable"
        return HealthResponse(status="ok" if storage == "ok" else "degraded",
                              storage=storage, version=VERSION)

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

        project_id = new_project_id()
        key = source_key(project_id, body.filename)

        try:
            url, expires_at, headers = create_upload_target(
                store.backend, key, body.content_type)
        except StorageError as e:
            raise err.StorageUnavailable(str(e)) from e

        project = store.create_project(
            title=body.title, input_key=key, source_sha256="",
            source_filename=body.filename, project_id=project_id, actor="user")

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
        projects = store.list_projects()
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
        readiness = _readiness(state, scenes)
        return ProjectDetail(
            **_summary(state.project, state.status).model_dump(),
            source_sha256=state.project.source_sha256 or None,
            latest_run_id=state.latest_run_id,
            timeline_version=state.timeline_version,
            scene_count=len(scenes),
            event_count=state.event_count,
            has_export=state.has_export,
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
        from foleyflow.providers.config import get_analysis_config
        from foleyflow.quota import get_quota

        _check_project_id(project_id)
        _load_state(store, project_id)          # 404s before doing any work

        active = runner.active_for_project(project_id)
        if active:
            raise err.AlreadyRunning(project_id, active.run_id, active.kind)

        model = body.model or get_analysis_config()["video_model"]
        quota = get_quota(model)
        if quota.status == "blocked":
            raise err.QuotaExhausted(model, resets_at=quota.resets_at,
                                     used=quota.per_day.used,
                                     limit=quota.per_day.limit)

        run = runner.submit(project_id, "analyze",
                            _analysis_work(store, project_id, model, body.force),
                            metadata={"model": model, "force": body.force},
                            actor="user")
        return RunAccepted(run_id=run.id, kind="analyze",
                           estimated_cost_usd=_estimate(model))

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

    # -- runs and events --------------------------------------------------

    @app.get(f"{API_PREFIX}/projects/{{project_id}}/runs",
             response_model=RunListResponse, tags=["runs"])
    def list_runs(project_id: str) -> RunListResponse:
        _check_project_id(project_id)
        return RunListResponse(runs=[RunOut(**r.to_dict()) for r in
                                     store.list_runs(project_id)])

    @app.get(f"{API_PREFIX}/projects/{{project_id}}/runs/{{run_id}}",
             response_model=RunOut, tags=["runs"])
    def get_run(project_id: str, run_id: str) -> RunOut:
        """Answered from storage for a finished run, and from the event log for
        one still in flight — a terminal record is only written at the end, so
        a running job has no run.json to read."""
        _check_project_id(project_id)
        try:
            return RunOut(**store.get_run(project_id, run_id).to_dict())
        except StorageError:
            pass

        for event in store.list_events(project_id):
            if event.run_id == run_id:
                return RunOut(id=run_id, project_id=project_id,
                              kind=event.payload.get("kind", "unknown"),
                              status="running", created_at=event.created_at)
        raise err.RunNotFound(run_id)

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
        from foleyflow.quota import get_all_quotas
        return QuotaResponse(estimate=True, models=[
            ModelQuotaOut(**q.to_dict()) for q in get_all_quotas()
        ])

    return app


# -- helpers ----------------------------------------------------------------

def _summary(project, status: str) -> ProjectSummary:
    return ProjectSummary(
        id=project.id, title=project.title, status=status,
        created_at=project.created_at, source_filename=project.source_filename,
        duration_s=project.duration_s,
    )


def _load_state(store: ProjectStore, project_id: str):
    try:
        return store.get_state(project_id)
    except StorageError as e:
        raise err.ProjectNotFound(project_id) from e


def _status_of(store: ProjectStore, project_id: str) -> str:
    try:
        return store.get_state(project_id).status
    except StorageError:
        return "unknown"


def _sign(store: ProjectStore, key: str) -> Optional[str]:
    try:
        return store.backend.signed_url(key, ARTIFACT_URL_TTL_S)
    except StorageError:
        return None


def _scene_ids_for(store: ProjectStore, project_id: str, timeline) -> List[Optional[str]]:
    from foleyflow.scenes import Scene, assign_scene_ids
    raw = store.load_scenes(project_id)
    if not raw:
        return [None] * len(timeline.events)
    scenes = [Scene(id=s["id"], index=s["index"], start_s=s["start_s"],
                    end_s=s["end_s"]) for s in raw]
    return assign_scene_ids(timeline, scenes)


def _estimate(model: str) -> float:
    from foley.ledger import estimate_cost
    return estimate_cost(model, {})


def _readiness(state, scenes) -> List[ReadinessItem]:
    """The §6.1 checklist. `blocked` states are what §13 makes gate export."""
    items = [
        ReadinessItem(
            key="source", label="Source video stored in B2",
            state="ok" if state.project.source_sha256 else "pending",
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
        ReadinessItem(
            key="export", label="Final export approved",
            state="ok" if state.has_export else "pending"),
    ]
    return items


def _readiness_percent(items: List[ReadinessItem]) -> int:
    """A transparent count of satisfied checks, not a quality score.

    SPEC.md §13 calls the figure "a transparent convenience, not a black-box
    quality claim", so it is exactly `ok / total` and nothing cleverer.
    """
    if not items:
        return 0
    return round(100 * sum(1 for i in items if i.state == "ok") / len(items))


def _analysis_work(store: ProjectStore, project_id: str, model: str, force: bool):
    """The analysis job body. Returned as a closure so the route stays thin and
    this stays unit-testable without HTTP."""
    def work(run):
        from foley.analyze import TIMELINE_PROMPT, analyze_video_direct
        from foleyflow.scenes import detect_scenes
        from foleyflow.timeline_cache import get_or_analyze
        from foleyflow.workflow import stage_for_ingest

        project = store.get_project(project_id)
        local = os.path.join(store.work_dir, f"{project_id}_source.mp4")
        if not os.path.exists(local):
            store.backend.get(project.input_key, local)
        staged = stage_for_ingest(local, subdir=project_id)

        timeline, cached = get_or_analyze(
            staged, lambda: analyze_video_direct(staged, model=model),
            backend=store.backend, model_id=model, prompt=TIMELINE_PROMPT,
            force=force)

        duration = project.duration_s or timeline.duration
        scenes = detect_scenes(staged, duration)
        store.save_scenes(project_id, scenes, video_path=staged, run_id=run.id)

        version = store.save_timeline(project_id, timeline, run_id=run.id)
        store.complete_run(run, timeline_version=version,
                           metadata={"cached": cached, "model": model,
                                     "scene_count": len(scenes)})
    return work


app = None  # created by the ASGI entrypoint; see foleyflow.api.main
