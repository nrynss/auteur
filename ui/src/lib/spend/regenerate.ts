/** Billable regenerate probe (unit 7.11 / SPEC §9.4 / docs/api.md §3.7).

 * Pattern: POST without `confirm_cost_usd`. Free work (estimate 0) returns 202.
 * Billable work returns 422 `precondition_failed` with
 * `detail.estimated_cost_usd` — show that exact number, then re-POST echoing
 * it as `confirm_cost_usd`. Never invent a cost. */

import { ApiError } from "../api/client";
import {
  regenerateEvent,
  type RegenerateRequest,
  type RunAccepted,
} from "../api/projects";

export type RegenerateProbeResult =
  | { status: "accepted"; accepted: RunAccepted }
  | { status: "needs_confirm"; estimatedCostUsd: number }
  | { status: "error"; error: ApiError };

export interface RegenerateOpts {
  provider?: string | null;
  note?: string | null;
  /** Exact estimate previously shown — echoed as confirm_cost_usd. */
  confirmCostUsd?: number | null;
}

function bodyFrom(opts: RegenerateOpts): RegenerateRequest {
  const body: RegenerateRequest = {};
  if (opts.provider != null && opts.provider !== "") {
    body.provider = opts.provider;
  }
  if (opts.note != null && opts.note !== "") {
    body.note = opts.note;
  }
  if (opts.confirmCostUsd != null) {
    body.confirm_cost_usd = opts.confirmCostUsd;
  }
  return body;
}

/** Probe or confirm a regenerate. Branch on `ApiError.code`, never message. */
export async function probeRegenerate(
  projectId: string,
  eventIndex: number,
  opts: RegenerateOpts = {},
): Promise<RegenerateProbeResult> {
  try {
    const accepted = await regenerateEvent(
      projectId,
      eventIndex,
      bodyFrom(opts),
    );
    return { status: "accepted", accepted };
  } catch (e) {
    if (!(e instanceof ApiError)) throw e;
    if (
      e.code === "precondition_failed" &&
      opts.confirmCostUsd == null &&
      typeof e.detail.estimated_cost_usd === "number"
    ) {
      return {
        status: "needs_confirm",
        estimatedCostUsd: e.detail.estimated_cost_usd as number,
      };
    }
    return { status: "error", error: e };
  }
}

/** True when a detail blob carries a numeric estimated_cost_usd (probe shape). */
export function hasEstimatedCost(
  detail: Record<string, unknown>,
): detail is Record<string, unknown> & { estimated_cost_usd: number } {
  return typeof detail.estimated_cost_usd === "number";
}

/** Pull blockers list from an export 422 detail — always an array of strings. */
export function exportBlockers(detail: Record<string, unknown>): string[] {
  const raw = detail.blockers;
  if (!Array.isArray(raw)) return [];
  return raw.map((b) => String(b));
}
