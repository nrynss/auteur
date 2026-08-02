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
export type TimelineEventOut = Schema["TimelineEventOut"];
export type TimelineVersionsResponse = Schema["TimelineVersionsResponse"];
export type EventOut = Schema["EventOut"];
export type EventListResponse = Schema["EventListResponse"];
export type RunOut = Schema["RunOut"];
export type RunListResponse = Schema["RunListResponse"];
export type QaReportResponse = Schema["QaReportResponse"];
export type DossierResponse = Schema["DossierResponse"];
export type BudgetResponse = Schema["BudgetResponse"];
export type PeaksResponse = Schema["PeaksResponse"];

const P = "/api/v1/projects";

export function getProject(id: string): Promise<ProjectDetail> {
  return api<ProjectDetail>(`${P}/${id}`);
}

export function getScenes(id: string): Promise<SceneListResponse> {
  return api<SceneListResponse>(`${P}/${id}/scenes`);
}

export function getTimeline(id: string): Promise<TimelineResponse> {
  return api<TimelineResponse>(`${P}/${id}/timeline`);
}

export function getTimelineVersions(id: string): Promise<TimelineVersionsResponse> {
  return api<TimelineVersionsResponse>(`${P}/${id}/timeline/versions`);
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
