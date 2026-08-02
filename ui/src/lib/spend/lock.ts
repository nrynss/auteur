/** Run locking and stale-lease detection (unit 7.11).

 * A non-stale `running` run locks spend and editing. After `RUN_LEASE_S` a
 * start event with no terminal record is stuck — not an infinite spinner.
 * ProjectDetail does not yet expose `stale_run_ids` on the wire, so the client
 * mirrors the server lease against the live run's `created_at` (docs/api.md
 * §3.6, projects.RUN_LEASE_S). */

import type { RunOut, StageOut } from "../api/projects";

/** Matches `foleyflow.projects.RUN_LEASE_S` (15 minutes). */
export const RUN_LEASE_S = 15 * 60;

/** Poll cadence while a run is in flight. No SSE — this is the only progress
 * path (docs/api.md §3.6). */
export const POLL_INTERVAL_MS = 2000;

export type StageSnapshot = Pick<
  StageOut,
  "name" | "status" | "elapsed_s" | "retries" | "provider"
>;

export type LockState =
  | { kind: "idle" }
  | {
      kind: "locked";
      runId: string;
      runKind: string;
      stages: StageSnapshot[];
      currentStage: string | null;
    }
  | {
      kind: "stale";
      runIds: string[];
      runKind: string | null;
    };

/** Age of a run start in seconds. Non-finite / unparseable / empty → null
 * (unknown — do not treat as stale; placeholders lack server created_at). */
export function runAgeSeconds(
  createdAt: string,
  nowMs: number = Date.now(),
): number | null {
  if (!createdAt) return null;
  const t = Date.parse(createdAt);
  if (!Number.isFinite(t)) return null;
  return Math.max(0, (nowMs - t) / 1000);
}

/** True when the run is lease-expired or the API already labelled it stale.
 * Missing created_at never expires the lease (wait for a real getRun). */
export function isRunStale(
  run: Pick<RunOut, "status" | "created_at">,
  nowMs: number = Date.now(),
): boolean {
  if (run.status === "stale") return true;
  if (run.status !== "running") return false;
  const age = runAgeSeconds(run.created_at, nowMs);
  if (age == null) return false;
  return age > RUN_LEASE_S;
}

/** True when the run is in flight and still within the lease. */
export function isRunLive(
  run: Pick<RunOut, "status" | "created_at">,
  nowMs: number = Date.now(),
): boolean {
  return run.status === "running" && !isRunStale(run, nowMs);
}

/** Classify a polled run into idle / locked / stale for the lock banner. */
export function classifyRun(
  run: RunOut | null | undefined,
  nowMs: number = Date.now(),
): LockState {
  if (!run) return { kind: "idle" };
  if (isRunStale(run, nowMs)) {
    return {
      kind: "stale",
      runIds: [run.id],
      runKind: run.kind ?? null,
    };
  }
  if (run.status === "running") {
    const stages = (run.stages ?? []).map((s) => ({
      name: s.name,
      status: s.status,
      elapsed_s: s.elapsed_s ?? 0,
      retries: s.retries ?? 0,
      provider: s.provider ?? null,
    }));
    return {
      kind: "locked",
      runId: run.id,
      runKind: run.kind,
      stages,
      currentStage: run.current_stage ?? stages.at(-1)?.name ?? null,
    };
  }
  return { kind: "idle" };
}

/** Honest stuck-run copy — what went wrong and what it means (control-room.md §8). */
export function staleRunMessage(state: Extract<LockState, { kind: "stale" }>): string {
  const id = state.runIds[0] ?? "unknown";
  const kind = state.runKind ? `${state.runKind} ` : "";
  return (
    `Stuck ${kind}run ${id}: the process died without a terminal record ` +
    `(lease expired after ${RUN_LEASE_S / 60} min). Spend is unlocked; ` +
    `inspect the event log before starting another run.`
  );
}

/** Merge a live mid-flight run into the terminal list so ReadinessPanel can
 * render stages as they arrive. The live snapshot wins on id collision
 * (fresher stages / status). */
export function mergeLiveRun(
  terminal: RunOut[],
  live: RunOut | null | undefined,
): RunOut[] {
  if (!live) return terminal;
  const without = terminal.filter((r) => r.id !== live.id);
  return [...without, live];
}

/** Project statuses that imply a run may be in flight even when this tab
 * did not start it (server `_derive_status`). */
export function projectMayHaveLiveRun(status: string | null | undefined): boolean {
  if (!status) return false;
  return (
    status === "analyzing" ||
    status === "needs_review" ||
    status === "awaiting_upload" ||
    status === "ingesting"
  );
}
