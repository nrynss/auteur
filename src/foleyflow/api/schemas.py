"""Request and response models.

These are the contract's types. They exist separately from the domain
dataclasses in `foleyflow.projects` on purpose: the wire format should be able
to stay stable while internal records change shape, and a Pydantic model that
is also the storage record ends up leaking storage concerns (raw B2 keys,
internal status vocabulary) into responses by default rather than by decision.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator


# -- shared -----------------------------------------------------------------

class ErrorBody(BaseModel):
    code: str
    message: str
    detail: Dict[str, Any] = Field(default_factory=dict)


class ErrorResponse(BaseModel):
    error: ErrorBody


class RunAccepted(BaseModel):
    """The 202 body for every mutation.

    Deliberately carries no `cached` field. Whether a request hits the timeline
    cache is not knowable until the worker runs, so a value here could only
    ever be a guess — the client learns it from the completed run's metadata.
    """

    run_id: str
    kind: str
    status: str = "running"
    estimated_cost_usd: Optional[float] = None


# -- projects ---------------------------------------------------------------

class CreateProjectRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    filename: str = Field(min_length=1, max_length=255)
    content_type: str = "video/mp4"

    @field_validator("filename")
    @classmethod
    def _safe_filename(cls, v: str) -> str:
        """The filename is recorded and used to build a storage key suffix, so
        a path separator in it would escape the project's prefix."""
        if "/" in v or "\\" in v or v.strip() in ("", ".", ".."):
            raise ValueError("filename must not contain a path separator")
        return v

    @field_validator("content_type")
    @classmethod
    def _supported_type(cls, v: str) -> str:
        allowed = {"video/mp4", "video/quicktime", "video/x-matroska", "video/webm"}
        if v not in allowed:
            raise ValueError(f"content_type must be one of {sorted(allowed)}")
        return v


class UploadTarget(BaseModel):
    url: str
    method: str = "PUT"
    expires_at: str
    headers: Dict[str, str] = Field(default_factory=dict)


class ProjectSummary(BaseModel):
    id: str
    title: str
    status: str
    created_at: str
    source_filename: str
    duration_s: Optional[float] = None


class ReadinessItem(BaseModel):
    """One row of the §6.1 checklist."""

    key: str
    label: str
    state: str          # ok | warn | blocked | pending
    detail: Optional[str] = None


class ProjectDetail(ProjectSummary):
    source_sha256: Optional[str] = None
    latest_run_id: Optional[str] = None
    timeline_version: Optional[int] = None
    scene_count: int = 0
    event_count: int = 0
    has_export: bool = False
    mix_run_id: Optional[str] = None
    export_url: Optional[str] = None
    manifest_url: Optional[str] = None
    readiness: List[ReadinessItem] = Field(default_factory=list)
    readiness_percent: Optional[int] = None
    blocks_export: bool = False


class CreateProjectResponse(BaseModel):
    project: ProjectSummary
    upload: UploadTarget


class ProjectListResponse(BaseModel):
    projects: List[ProjectSummary]
    next_cursor: Optional[str] = None


# -- analysis ---------------------------------------------------------------

class AnalyzeRequest(BaseModel):
    model: Optional[str] = None
    force: bool = False


# -- timeline ---------------------------------------------------------------

class TimelineEventOut(BaseModel):
    index: int          # the stable handle for repair — names repeat
    name: str
    kind: str
    intensity: str
    start_time: float
    duration: float
    scene_id: Optional[str] = None


class TimelineResponse(BaseModel):
    version: int
    duration: float
    ambience: str
    events: List[TimelineEventOut]


class TimelineVersionsResponse(BaseModel):
    versions: List[int]
    latest: Optional[int] = None


# -- scenes -----------------------------------------------------------------

class SceneStatsOut(BaseModel):
    mean_luma: float
    mean_rgb: List[float]
    luma_stddev: float
    sample_count: int


class SceneOut(BaseModel):
    id: str
    index: int
    start_s: float
    end_s: float
    duration_s: float
    stats: Optional[SceneStatsOut] = None
    thumbnail_url: Optional[str] = None


class SceneListResponse(BaseModel):
    scenes: List[SceneOut]


# -- QA ---------------------------------------------------------------------

class QaIssueOut(BaseModel):
    severity: str
    code: str
    scene_id: Optional[str] = None
    compared_with: Optional[str] = None
    confidence: Optional[float] = None
    detail: Optional[str] = None


class AudioQaOut(BaseModel):
    integrated_lufs: Optional[float] = None
    true_peak_dbtp: Optional[float] = None
    target_lufs: float
    target_true_peak_dbtp: float
    loudness_range_lu: Optional[float] = None
    measured: bool = False
    measured_run_id: Optional[str] = None
    issues: List[QaIssueOut] = Field(default_factory=list)


class VisualQaOut(BaseModel):
    luma_delta_threshold: float
    issues: List[QaIssueOut] = Field(default_factory=list)


class QaReportResponse(BaseModel):
    run_id: Optional[str] = None
    overall_status: str
    audio: AudioQaOut
    visual: VisualQaOut


# -- runs and events --------------------------------------------------------

class StageOut(BaseModel):
    """One workflow stage's outcome — SPEC §8.2's job-history row.

    Written by our own stage recorder, never from provider or user input, so
    `error` here is safe to serve verbatim (unlike `RunOut.error`): it is
    Genblaze's own step error, not an arbitrary exception message.
    """

    name: str
    status: str
    elapsed_s: float = 0.0
    provider: Optional[str] = None
    retries: int = 0
    error: Optional[str] = None
    genblaze_run_id: Optional[str] = None
    started_at: Optional[str] = None
    detail: Optional[str] = None


class RunOut(BaseModel):
    id: str
    project_id: str
    kind: str
    status: str
    created_at: str
    timeline_version: Optional[int] = None
    parent_run_id: Optional[str] = None
    genblaze_run_id: Optional[str] = None
    # SPEC §8.2: "the workflow run ID, current stage, and stage-level outcome
    # in its job history". `stages` is the whole history; `current_stage` is the
    # last one to have been recorded, which for a run still in flight is the one
    # a UI should be showing as active.
    stages: List[StageOut] = Field(default_factory=list)
    current_stage: Optional[str] = None
    # A classification, never the exception text — see docs/api.md §3.6.
    error: Optional[str] = None
    error_code: Optional[str] = None
    # What the run produced: artifact keys, counts, spend. Written by our own
    # work functions, never from provider or user input, which is why it is
    # safe to serve verbatim where `error` is not.
    metadata: Dict[str, Any] = Field(default_factory=dict)


class RunListResponse(BaseModel):
    runs: List[RunOut]


class EventOut(BaseModel):
    id: str
    type: str
    created_at: str
    actor: str
    run_id: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)


class EventListResponse(BaseModel):
    events: List[EventOut]


# -- repair -----------------------------------------------------------------

class RegenerateRequest(BaseModel):
    provider: Optional[str] = None
    confirm_cost_usd: Optional[float] = None
    note: Optional[str] = Field(default=None, max_length=500)


class RemixRequest(BaseModel):
    timeline_version: Optional[int] = None
    tier: str = "full"

    @field_validator("tier")
    @classmethod
    def _known_tier(cls, v: str) -> str:
        if v not in ("ambience", "discrete", "full"):
            raise ValueError("tier must be ambience, discrete, or full")
        return v


# -- export -----------------------------------------------------------------

class ExportRequest(BaseModel):
    run_id: Optional[str] = None
    override_blocking: bool = False


class ArtifactOut(BaseModel):
    kind: str
    key: str
    url: Optional[str] = None
    size_bytes: Optional[int] = None
    sha256: Optional[str] = None


class DossierResponse(BaseModel):
    project_id: str
    artifact_count: int
    total_bytes: int
    artifacts: List[ArtifactOut]


# -- quota ------------------------------------------------------------------

class QuotaWindowOut(BaseModel):
    used: int
    limit: Optional[int] = None
    remaining: Optional[int] = None
    fraction: Optional[float] = None
    exhausted: bool = False


class ModelQuotaOut(BaseModel):
    model_id: str
    status: str
    per_minute: QuotaWindowOut
    per_day: QuotaWindowOut
    resets_at: Optional[str] = None


class QuotaResponse(BaseModel):
    # Google exposes no remaining-balance field; these are counted from our own
    # ledger and can drift. The flag is in the payload so a UI cannot present
    # them as authoritative without ignoring an explicit signal.
    estimate: bool = True
    # False means the figures are fiction — nothing is being recorded, so usage
    # reads zero regardless of what is actually spent.
    ledger_writable: bool = True
    models: List[ModelQuotaOut]


# -- health -----------------------------------------------------------------

class HealthResponse(BaseModel):
    status: str
    storage: str
    version: str
