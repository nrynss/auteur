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

    Mix responses also carry a pre-flight *projection* (task 9c): estimated
    cost of cache misses only, plus the project's current spend and advisory
    budget so the client can say "projected $2.40, taking this project to
    $6.10 against a $5.00 budget." Label it a projection — not a bill.
    """

    run_id: str
    kind: str
    status: str = "running"
    estimated_cost_usd: Optional[float] = None
    # Mix pre-flight projection (optional; absent on analyze/repair/export).
    projected_cost_usd: Optional[float] = None
    spent_usd: Optional[float] = None
    budget_usd: Optional[float] = None
    projected_total_usd: Optional[float] = None  # spent + projected
    cache_hits: Optional[int] = None
    cache_misses: Optional[int] = None


# -- projects ---------------------------------------------------------------

class CreateProjectRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    filename: str = Field(min_length=1, max_length=255)
    content_type: str = "video/mp4"
    # Optional advisory budget (docs/api.md §3.11). Seeds the first
    # budget.set event; None means the [budgets] default applies.
    budget_usd: Optional[float] = None

    @field_validator("budget_usd")
    @classmethod
    def _positive_budget(cls, v: Optional[float]) -> Optional[float]:
        if v is not None and v <= 0:
            raise ValueError("budget_usd must be > 0 when set")
        return v

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


class SetBudgetRequest(BaseModel):
    """Set or clear the project's advisory budget (docs/api.md §3.11).

    ``budget_usd: null`` clears it. Positive values set/raise/lower it.
    """

    budget_usd: Optional[float] = None

    @field_validator("budget_usd")
    @classmethod
    def _positive_budget(cls, v: Optional[float]) -> Optional[float]:
        if v is not None and v <= 0:
            raise ValueError("budget_usd must be > 0 when set")
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
    budget_usd: Optional[float] = None


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
    # Pre-export A/B: source video + latest mix's foley.mp4. export_url is
    # post-publish only; these fill the player before that.
    source_url: Optional[str] = None
    mix_url: Optional[str] = None
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

class TimelineEventIn(BaseModel):
    """One event in a timeline commit body. No index — order is the index."""

    name: str = Field(min_length=1)
    start_time: float
    duration: float
    intensity: str
    kind: str
    # Writable mix-variation freeze (editor may set/clear on nudge).
    seed: Optional[int] = None
    # Cost/cache freeze. Commit rejects silent changes; see docs/api.md §3.4.
    source_duration_s: Optional[float] = None

    @field_validator("intensity")
    @classmethod
    def _intensity(cls, v: str) -> str:
        if v not in ("low", "med", "high"):
            raise ValueError("intensity must be one of low, med, high")
        return v

    @field_validator("kind")
    @classmethod
    def _kind(cls, v: str) -> str:
        if v not in ("discrete", "rhythmic", "continuous"):
            raise ValueError("kind must be one of discrete, rhythmic, continuous")
        return v

    @field_validator("duration", "start_time")
    @classmethod
    def _finite_time(cls, v: float) -> float:
        import math
        if not math.isfinite(v):
            raise ValueError("must be a finite number")
        return v

    @field_validator("duration")
    @classmethod
    def _nonneg_duration(cls, v: float) -> float:
        if v < 0:
            raise ValueError("duration must be >= 0")
        return v

    @field_validator("start_time")
    @classmethod
    def _nonneg_start(cls, v: float) -> float:
        if v < 0:
            raise ValueError("start_time must be >= 0")
        return v

    @field_validator("source_duration_s")
    @classmethod
    def _source_duration(cls, v: Optional[float]) -> Optional[float]:
        import math
        if v is None:
            return None
        if not math.isfinite(v) or v < 0:
            raise ValueError("source_duration_s must be a non-negative finite number")
        return v


class TimelineCommitRequest(BaseModel):
    """Whole timeline document for PUT /timeline (not a patch list)."""

    duration: float
    ambience: str
    events: List[TimelineEventIn]

    @field_validator("duration")
    @classmethod
    def _finite_duration(cls, v: float) -> float:
        import math
        if not math.isfinite(v) or v < 0:
            raise ValueError("duration must be a non-negative finite number")
        return v


class TimelineEventOut(BaseModel):
    index: int          # the stable handle for repair — names repeat
    name: str
    kind: str
    intensity: str
    start_time: float
    duration: float
    seed: Optional[int] = None
    source_duration_s: Optional[float] = None
    # The stem's storage key, stamped at mix time (unit 7.9) — the honest
    # event→audio link for the browser preview. Names repeat and stems dedupe,
    # so this is the only address the client should trust. None = never mixed.
    stem_key: Optional[str] = None
    scene_id: Optional[str] = None


class TimelineResponse(BaseModel):
    version: int
    duration: float
    ambience: str
    events: List[TimelineEventOut]


class TimelineVersionsResponse(BaseModel):
    versions: List[int]
    latest: Optional[int] = None


# -- director mode (unit 7.12, docs/api.md §3.16) ---------------------------

class DirectorRequest(BaseModel):
    """Chat proposes, never applies: the whole current document plus a
    plain-language instruction. Same document shape as a commit (order is
    index); `timeline_version` is that document's version, carried for
    display so the diff can name what it was proposed against."""

    instruction: str
    timeline_version: Optional[int] = None
    duration: float
    ambience: str
    events: List[TimelineEventIn]

    @field_validator("instruction")
    @classmethod
    def _instruction(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("instruction must not be empty")
        if len(stripped) > 2000:
            raise ValueError("instruction must be at most 2000 characters")
        return stripped

    @field_validator("duration")
    @classmethod
    def _finite_duration(cls, v: float) -> float:
        import math
        if not math.isfinite(v) or v < 0:
            raise ValueError("duration must be a non-negative finite number")
        return v


class DirectorPlacement(BaseModel):
    """One event's placement, old or new, for the per-index diff."""

    start_time: float
    duration: float


class DirectorEditOut(BaseModel):
    """One changed event: the ghosted-old → solid-new pair a UI renders."""

    index: int
    old: DirectorPlacement
    new: DirectorPlacement


class DirectorEventOut(BaseModel):
    """An event in the proposed document. Placement may differ from the base;
    seed/source_duration_s are copied through untouched (the model never
    invents or moves a freeze). No stem_key/scene_id — those are stamped by
    the server onto committed versions, and the proposal is not committed."""

    index: int
    name: str
    kind: str
    intensity: str
    start_time: float
    duration: float
    seed: Optional[int] = None
    source_duration_s: Optional[float] = None


class DirectorTimelineOut(BaseModel):
    """The proposed document, diffed against the request document."""

    duration: float
    ambience: str
    events: List[DirectorEventOut]


class DirectorResponse(BaseModel):
    """The outcome of one director turn. `status` is the turn's domain
    outcome (proposed / no_changes / invalid_output), never an HTTP error —
    an invalid model output still billed the call and counted against quota.
    `cost_usd` is what this call is estimated (or actually) recorded as;
    `ledger_writable` false means the row did not land and the UI must fail
    closed (§3.10)."""

    project_id: str
    base_version: int
    model: str
    instruction: str
    status: str
    message: str
    cost_usd: float
    cost_source: str
    ledger_writable: bool
    timeline: DirectorTimelineOut
    edits: List[DirectorEditOut]


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
    # Compact §3.11 budget block: configured, spent, remaining, estimate flag.
    budget: Optional[BudgetResponse] = None


# -- quota ------------------------------------------------------------------

class AttributionRequest(BaseModel):
    """Rescue unattributed ledger rows into a project (docs/api.md §3.11).
    `before` is a unix timestamp; default is now."""

    before: Optional[float] = None


class AttributionOut(BaseModel):
    project_id: str
    before: float
    claimed: int          # unattributed rows the rescue adopted at write time


class BudgetModelOut(BaseModel):
    endpoint: str
    spent: float


class BudgetThresholdOut(BaseModel):
    """One advisory threshold fraction and whether it has been crossed."""

    threshold: float          # e.g. 0.8
    crossed: bool


class BudgetResponse(BaseModel):
    """Per-project spend against its advisory budget, from the cost ledger
    (docs/api.md §3.11). Estimates, like /quota; `ledger_writable: false`
    means the figures are fiction and alerts are unreliable."""

    project_id: str
    budget_usd: Optional[float] = None   # None = no advisory budget
    spent: float
    remaining: Optional[float] = None    # may be negative when overspent
    estimate: bool = True
    ledger_writable: bool = True
    # Portion of spent whose cost_source is estimate vs actual.
    spent_estimated: float = 0.0
    spent_actual: float = 0.0
    thresholds: List[BudgetThresholdOut] = Field(default_factory=list)
    by_model: List[BudgetModelOut] = Field(default_factory=list)


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


# -- ledger rows ------------------------------------------------------------

class LedgerRowOut(BaseModel):
    """One cost transaction for the per-project audit trail (3.14).

    Failed rows are included -- a request that failed may still have consumed
    provider quota, and excluding it would produce a subset that does not
    reconcile with /budget.
    """

    timestamp: float
    endpoint: str
    cost_estimate: float
    cost_source: str          # "estimate" | "actual"
    success: bool
    error: Optional[str] = None
    project_id: Optional[str] = None
    params_hash: Optional[str] = None


class LedgerRowsResponse(BaseModel):
    """Paginated ledger rows for one project (3.14)."""

    project_id: str
    total_count: int
    rows: List[LedgerRowOut]
    next_cursor: Optional[int] = None
    # No `estimate` flag here: unlike /quota and /budget (totals priced from
    # providers.toml), every row names its own cost_source — an aggregate
    # "these are estimates" claim would be a lie the moment any row is actual.
    ledger_writable: bool = True


# -- unclaimed (3.15) -------------------------------------------------------

class UnclaimedLedgerRowOut(LedgerRowOut):
    """One orphan ledger row — no project_id, no rescue override — plus a
    signed URL to its immutable record so the spend is inspectable before it
    is claimed (3.15)."""

    url: Optional[str] = None


class UnclaimedLedgerResponse(BaseModel):
    """Paginated orphan ledger rows (3.15)."""

    total_count: int
    rows: List[UnclaimedLedgerRowOut]
    next_cursor: Optional[int] = None
    ledger_writable: bool = True


class OrphanProjectOut(BaseModel):
    """A `projects/{id}/` prefix with no readable project record (3.15).
    Object count and bytes come from the LIST (unit 7.19) — the material still
    costs storage while the whole product cannot see it."""

    project_id: str
    object_count: int
    total_bytes: int
    unreadable: bool = False    # a record object exists but does not parse


class UnclaimedProjectsResponse(BaseModel):
    projects: List[OrphanProjectOut]


class UnclaimedCacheRow(BaseModel):
    """One shared cache stem — an address, not provenance, so it is never
    claimable; the signed URL is for inspection only (3.15)."""

    key: str
    size_bytes: int
    url: Optional[str] = None


class UnclaimedCacheResponse(BaseModel):
    total_count: int
    rows: List[UnclaimedCacheRow]
    next_cursor: Optional[int] = None


class AdoptProjectRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class AdoptProjectResponse(BaseModel):
    """The orphan prefix now has a live project record (3.15)."""

    project: ProjectSummary
    repaired: bool = False    # an unreadable record was replaced


class ClaimLedgerRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    budget_usd: Optional[float] = None

    @field_validator("budget_usd")
    @classmethod
    def _positive_budget(cls, v: Optional[float]) -> Optional[float]:
        if v is not None and v <= 0:
            raise ValueError("budget_usd must be > 0 when set")
        return v


class ClaimLedgerResponse(BaseModel):
    """A new spend-holding project plus how many orphan rows it adopted (3.15)."""

    project: ProjectSummary
    claimed: int

# -- waveform peaks ---------------------------------------------------------

class PeaksResponse(BaseModel):
    """Two-sided peak envelope for a stem or mix (docs/api.md §3.12).

    ``min``/``max`` are parallel arrays of raw float samples (typically in
    ``[-1, 1]``) at ``peaks_per_second`` resolution. Length is
    ``ceil(duration_s * peaks_per_second)`` for non-empty audio.
    """

    peaks_per_second: int
    channels: int
    sample_rate: int
    duration_s: float
    min: List[float]
    max: List[float]


class StemPeaksEntry(BaseModel):
    """One stem's envelope in the batch response, or why it has none.

    Exactly one of ``peaks`` / ``error`` is set. A stem that cannot be
    summarised is reported rather than dropped: silently omitting it would
    leave the client unable to tell "no such stem" from "we did not look",
    which is the synthesised-verdict failure §A.3 exists to prevent. The
    ``error`` codes are the same ones the per-key route raises
    (``audio_not_found``, ``invalid_request``).
    """

    b2_key: str
    peaks: Optional[PeaksResponse] = None
    error: Optional[str] = None
    message: Optional[str] = None


class StemPeaksBatchResponse(BaseModel):
    """Every stem envelope for one mix run, keyed by ``b2_key``."""

    run_id: str
    stems: Dict[str, StemPeaksEntry]


# -- health -----------------------------------------------------------------

class WorkerHealth(BaseModel):
    """Reachability of the SFX worker behind the active tier.

    `status` is one of:

    * `ok` — reachable and loaded;
    * `loading` — reachable, model still building (the worker reports this for
      its first ~13s and refuses generation with 503 meanwhile);
    * `unreachable` — the probe failed. `detail` says how;
    * `not_applicable` — the active tier is served by a hosted API, so there is
      no worker to probe.

    `auth_configured` is false when the worker demands a bearer token and this
    process holds none. That combination reads as healthy from the outside and
    fails every generation, so it is called out rather than folded into `ok`.
    """

    status: str
    tier: Optional[str] = None
    endpoint: Optional[str] = None
    model: Optional[str] = None
    device: Optional[str] = None
    auth_required: Optional[bool] = None
    auth_configured: Optional[bool] = None
    detail: Optional[str] = None


class HealthResponse(BaseModel):
    status: str
    storage: str
    version: str
    # Baked into the image at build time (FOLEYFLOW_GIT_SHA /
    # FOLEYFLOW_BUILD_TIME). Null when absent — a local `uv run uvicorn` has
    # no stamp and must not invent one.
    git_sha: Optional[str] = None
    build_time: Optional[str] = None  # ISO8601 UTC
    worker: Optional[WorkerHealth] = None


# -- read-only deployment configuration (task 20, docs/api.md §3.17) --------

class IntegrationOut(BaseModel):
    """One integration's presence. `env` carries variable NAMES only, never
    values; `missing` names what is absent; `unresolved` names vars whose
    value is a literal Secret Manager reference (a real misconfiguration,
    reported by name, never echoed). `source` is the deployment contract
    (secret-manager via deploy.sh --set-secrets, or plain env)."""

    key: str
    label: str
    source: str            # secret-manager | env
    configured: bool
    env: List[str]
    missing: List[str]
    unresolved: List[str]


class ConfigResponse(BaseModel):
    """The read-only deployment sheet: the same checks `foleyflow doctor`
    prints (same states, same fail-closed rules), the baked build identity,
    and integration presence. `ok` is true exactly when no check is blocked.
    `git_sha`/`build_time` are null on an unstamped (local) process; `brand`
    is the API's runtime product name (FOLEYFLOW_BRAND_NAME) — the screen
    compares it against the UI's build-time name and reports a disagreement
    as a blocked row (a deployment whose two halves disagree renders as two
    products). A name is not a credential."""

    ok: bool
    version: str
    brand: str
    git_sha: Optional[str] = None
    build_time: Optional[str] = None
    checks: List[ReadinessItem]
    integrations: List[IntegrationOut]
