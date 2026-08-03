/** Typed API functions for the control room (unit 7.8). Thin wrappers over
 * the generated schema types — the contract, not the UI, is the source of
 * truth (decision 5: drift fails the build). */
import { api } from "./client";
import type { components } from "./schema";

type Schema = components["schemas"];

export type ProjectDetail = Schema["ProjectDetail"];
export type ProjectSummary = Schema["ProjectSummary"];
export type ProjectListResponse = Schema["ProjectListResponse"];
export type ReadinessItem = Schema["ReadinessItem"];
export type SceneOut = Schema["SceneOut"];
export type SceneListResponse = Schema["SceneListResponse"];
export type TimelineResponse = Schema["TimelineResponse"];
export type TimelineEventIn = Schema["TimelineEventIn"];
export type TimelineEventOut = Schema["TimelineEventOut"];
export type TimelineVersionsResponse = Schema["TimelineVersionsResponse"];
export type EventOut = Schema["EventOut"];
export type EventListResponse = Schema["EventListResponse"];
export type RunOut = Schema["RunOut"];
export type StageOut = Schema["StageOut"];
export type RunListResponse = Schema["RunListResponse"];
export type QaReportResponse = Schema["QaReportResponse"];
export type DossierResponse = Schema["DossierResponse"];
export type BudgetResponse = Schema["BudgetResponse"];
export type PeaksResponse = Schema["PeaksResponse"];
export type QuotaResponse = Schema["QuotaResponse"];
export type LedgerRowOut = Schema["LedgerRowOut"];
export type LedgerRowsResponse = Schema["LedgerRowsResponse"];
export type AttributionOut = Schema["AttributionOut"];
export type UnclaimedLedgerResponse = Schema["UnclaimedLedgerResponse"];
export type UnclaimedProjectsResponse = Schema["UnclaimedProjectsResponse"];
export type OrphanProjectOut = Schema["OrphanProjectOut"];
export type UnclaimedCacheResponse = Schema["UnclaimedCacheResponse"];
export type AdoptProjectResponse = Schema["AdoptProjectResponse"];
export type ClaimLedgerResponse = Schema["ClaimLedgerResponse"];

const P = "/api/v1/projects";

export function getProject(id: string): Promise<ProjectDetail> {
  return api<ProjectDetail>(`${P}/${id}`);
}

export function getScenes(id: string): Promise<SceneListResponse> {
  return api<SceneListResponse>(`${P}/${id}/scenes`);
}

export function getTimeline(id: string, version?: number): Promise<TimelineResponse> {
  const q = version != null ? `?version=${version}` : "";
  return api<TimelineResponse>(`${P}/${id}/timeline${q}`);
}

export function getTimelineVersions(id: string): Promise<TimelineVersionsResponse> {
  return api<TimelineVersionsResponse>(`${P}/${id}/timeline/versions`);
}

// -- timeline commit (unit 7.10) ------------------------------------------
// A commit submits the WHOLE timeline document and the server allocates one
// new immutable version (docs/api.md §3.4). Synchronous storage write — free,
// no run slot; 409 while a run is in flight, 400 with `detail.violations` on
// a freeze-conservation violation.

export type TimelineCommitRequest = Schema["TimelineCommitRequest"];

/** Commit a draft as the next immutable version. */
export function putTimeline(
  id: string,
  body: TimelineCommitRequest,
): Promise<TimelineResponse> {
  return api<TimelineResponse>(`${P}/${id}/timeline`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

// -- director mode (unit 7.12, docs/api.md §3.16) --------------------------
// Chat proposes, never applies: the client sends the WHOLE current document
// plus an instruction; the server returns a schema-validated proposal (status
// proposed / no_changes / invalid_output), never writing a version. Approval
// is the client committing the proposal through the normal putTimeline path.

export type DirectorRequest = Schema["DirectorRequest"];
export type DirectorResponse = Schema["DirectorResponse"];
export type DirectorEditOut = Schema["DirectorEditOut"];

/** One director turn — a provider call, billed and quota-counted. */
export function proposeDirector(
  id: string,
  body: DirectorRequest,
): Promise<DirectorResponse> {
  return api<DirectorResponse>(`${P}/${id}/director`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export function getEvents(id: string): Promise<EventListResponse> {
  return api<EventListResponse>(`${P}/${id}/events`);
}

export function getRuns(id: string): Promise<RunListResponse> {
  return api<RunListResponse>(`${P}/${id}/runs`);
}

export function getQa(id: string): Promise<QaReportResponse> {
  return api<QaReportResponse>(`${P}/${id}/qa`);
}

export function getDossier(id: string): Promise<DossierResponse> {
  return api<DossierResponse>(`${P}/${id}/dossier`);
}

/** Model quota is global, not per project (docs/api.md §3.10). */
export function getQuota(): Promise<QuotaResponse> {
  return api<QuotaResponse>("/api/v1/quota");
}

/** Individual cost transactions for this project, newest first (docs/api.md
 * §3.14). Failed rows are included — they may still have consumed quota. */
export function getLedgerRows(
  id: string,
  cursor = 0,
  limit = 50,
): Promise<LedgerRowsResponse> {
  const q = new URLSearchParams({ cursor: String(cursor), limit: String(limit) });
  return api<LedgerRowsResponse>(`${P}/${id}/ledger?${q}`);
}

// -- unclaimed (unit 7.17, docs/api.md §3.15) ------------------------------
// The material no project claims: orphan ledger rows, orphan project prefixes,
// and shared cache stems (shown, never claimable).

const U = "/api/v1/unclaimed";

export function getUnclaimedLedger(
  cursor = 0,
  limit = 50,
): Promise<UnclaimedLedgerResponse> {
  const q = new URLSearchParams({ cursor: String(cursor), limit: String(limit) });
  return api<UnclaimedLedgerResponse>(`${U}/ledger?${q}`);
}

export function getUnclaimedProjects(): Promise<UnclaimedProjectsResponse> {
  return api<UnclaimedProjectsResponse>(`${U}/projects`);
}

export function getUnclaimedCache(
  cursor = 0,
  limit = 50,
): Promise<UnclaimedCacheResponse> {
  const q = new URLSearchParams({ cursor: String(cursor), limit: String(limit) });
  return api<UnclaimedCacheResponse>(`${U}/cache?${q}`);
}

export function adoptOrphanProject(
  projectId: string,
  title: string,
): Promise<AdoptProjectResponse> {
  return api<AdoptProjectResponse>(`${U}/projects/${encodeURIComponent(projectId)}/adopt`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title }),
  });
}

export function claimOrphanLedger(
  title: string,
  budgetUsd?: number | null,
): Promise<ClaimLedgerResponse> {
  return api<ClaimLedgerResponse>(`${U}/ledger/claim`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title, budget_usd: budgetUsd ?? null }),
  });
}

/** Claim orphan rows into an existing project (docs/api.md §3.11). */
export function attributeOrphanRows(
  id: string,
  before?: number,
): Promise<AttributionOut> {
  return api<AttributionOut>(`${P}/${id}/attributions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(before != null ? { before } : {}),
  });
}

export function getBudget(id: string): Promise<BudgetResponse> {
  return api<BudgetResponse>(`${P}/${id}/budget`);
}

/** Latest mix's peaks when run_id is omitted (docs/api.md §3.12). */
export function getMixPeaks(
  id: string,
  run_id?: string,
): Promise<PeaksResponse> {
  const q = run_id ? `?run_id=${encodeURIComponent(run_id)}` : "";
  return api<PeaksResponse>(`${P}/${id}/peaks/mix${q}`);
}

// -- shared run poll target (7.11 spend lock + 7.13 create flow) -----------

export type RunAccepted = Schema["RunAccepted"];

/** Single run — finished from storage, or mid-flight from the event log
 * (docs/api.md §3.6). Stages arrive here while a run is live; list_runs only
 * sees terminal records. */
export function getRun(id: string, runId: string): Promise<RunOut> {
  return api<RunOut>(`${P}/${id}/runs/${encodeURIComponent(runId)}`);
}

// -- mutations (unit 7.11) -------------------------------------------------
// Billable spend paths: regenerate, mix, export.

export type RegenerateRequest = Schema["RegenerateRequest"];
export type RemixRequest = Schema["RemixRequest"];
export type ExportRequest = Schema["ExportRequest"];

/** Repair one event by index (never name). Billable regenerations require
 * `confirm_cost_usd` echoing the estimate the client was shown — probe without
 * it returns 422 precondition_failed with `detail.estimated_cost_usd`. */
export function regenerateEvent(
  id: string,
  eventIndex: number,
  body: RegenerateRequest = {},
): Promise<RunAccepted> {
  return api<RunAccepted>(
    `${P}/${id}/events/${eventIndex}/regenerate`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    },
  );
}

/** Start a mix. Projection (estimated/projected cost, cache hits/misses,
 * budget) arrives on the 202 — label it a projection, not a bill. */
export function createMix(
  id: string,
  body: Partial<RemixRequest> = {},
): Promise<RunAccepted> {
  const payload: RemixRequest = {
    tier: body.tier ?? "full",
    timeline_version: body.timeline_version ?? null,
  };
  return api<RunAccepted>(`${P}/${id}/mixes`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

/** Publish a rendered mix. 422 precondition_failed lists QA blockers under
 * `detail.blockers`; override only with `override_blocking: true`. */
export function createExport(
  id: string,
  body: Partial<ExportRequest> = {},
): Promise<RunAccepted> {
  const payload: ExportRequest = {
    override_blocking: body.override_blocking ?? false,
    run_id: body.run_id ?? null,
  };
  return api<RunAccepted>(`${P}/${id}/exports`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

// -- project creation / upload / analysis (unit 7.13) ----------------------

export type CreateProjectRequest = Schema["CreateProjectRequest"];
export type CreateProjectResponse = Schema["CreateProjectResponse"];
export type UploadTarget = Schema["UploadTarget"];
export type AnalyzeRequest = Schema["AnalyzeRequest"];

/** Create the project record and receive a direct-to-storage upload target
 * (docs/api.md §3.2). Video bytes never pass through the API. */
export function createProject(
  body: CreateProjectRequest,
  init?: { signal?: AbortSignal },
): Promise<CreateProjectResponse> {
  return api<CreateProjectResponse>(P, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal: init?.signal,
  });
}

/** Confirm the client's direct PUT completed; starts async ingest (202). */
export function confirmUpload(
  id: string,
  init?: { signal?: AbortSignal },
): Promise<RunAccepted> {
  return api<RunAccepted>(`${P}/${id}/uploaded`, {
    method: "POST",
    signal: init?.signal,
  });
}

/** Kick off analysis after ingest has produced a source hash (docs/api.md §3.3). */
export function analyzeProject(
  id: string,
  body: Partial<AnalyzeRequest> = {},
  init?: { signal?: AbortSignal },
): Promise<RunAccepted> {
  return api<RunAccepted>(`${P}/${id}/analyze`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ force: false, ...body }),
    signal: init?.signal,
  });
}

/** List projects, newest first (docs/api.md §3.2). */
export function listProjects(
  limit = 25,
  cursor?: string,
): Promise<ProjectListResponse> {
  const params = new URLSearchParams({ limit: String(limit) });
  if (cursor) params.set("cursor", cursor);
  return api<ProjectListResponse>(`${P}?${params}`);
}
