/** Export with blocker listing and explicit override (unit 7.11 / docs/api.md §3.9). */

import { ApiError } from "../api/client";
import {
  createExport,
  type ExportRequest,
  type RunAccepted,
} from "../api/projects";
import { exportBlockers } from "./regenerate";

export type ExportResult =
  | { status: "accepted"; accepted: RunAccepted }
  | { status: "blocked"; blockers: string[]; message: string }
  | { status: "error"; error: ApiError };

export async function tryExport(
  projectId: string,
  body: Partial<ExportRequest> = {},
): Promise<ExportResult> {
  try {
    const accepted = await createExport(projectId, body);
    return { status: "accepted", accepted };
  } catch (e) {
    if (!(e instanceof ApiError)) throw e;
    if (e.code === "precondition_failed" && !body.override_blocking) {
      // Always offer the override path on precondition_failed — blockers may
      // be missing if the detail shape drifts, but the gate is still real.
      const blockers = exportBlockers(e.detail);
      return {
        status: "blocked",
        blockers:
          blockers.length > 0
            ? blockers
            : [e.message || "Export blocked (no blocker list from API)"],
        message: e.message,
      };
    }
    return { status: "error", error: e };
  }
}
