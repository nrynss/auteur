import { describe, expect, it } from "vitest";

import {
  classifyRun,
  isRunLive,
  isRunStale,
  mergeLiveRun,
  RUN_LEASE_S,
  runAgeSeconds,
  staleRunMessage,
} from "./lock";
import type { RunOut } from "../api/projects";

const base = (partial: Partial<RunOut> & Pick<RunOut, "id" | "status" | "created_at">): RunOut => ({
  project_id: "proj_01",
  kind: "mix",
  stages: [],
  metadata: {},
  ...partial,
});

describe("runAgeSeconds", () => {
  it("returns age from created_at", () => {
    const now = Date.parse("2026-08-03T12:00:00.000Z");
    const created = "2026-08-03T11:59:00.000Z";
    expect(runAgeSeconds(created, now)).toBe(60);
  });

  it("returns null for unparseable or empty timestamps (not stale)", () => {
    expect(runAgeSeconds("not-a-date", 1_000_000)).toBeNull();
    expect(runAgeSeconds("", 1_000_000)).toBeNull();
  });
});

describe("isRunStale / isRunLive", () => {
  const now = Date.parse("2026-08-03T12:00:00.000Z");

  it("treats a fresh running run as live, not stale", () => {
    const run = base({
      id: "run_1",
      status: "running",
      created_at: "2026-08-03T11:55:00.000Z", // 5 min ago
    });
    expect(isRunLive(run, now)).toBe(true);
    expect(isRunStale(run, now)).toBe(false);
  });

  it("treats a running run past RUN_LEASE_S as stale", () => {
    const started = new Date(now - (RUN_LEASE_S + 1) * 1000).toISOString();
    const run = base({ id: "run_stale", status: "running", created_at: started });
    expect(isRunStale(run, now)).toBe(true);
    expect(isRunLive(run, now)).toBe(false);
  });

  it("honours an explicit stale status from the API", () => {
    const run = base({
      id: "run_s",
      status: "stale",
      created_at: "2026-08-03T11:59:00.000Z",
    });
    expect(isRunStale(run, now)).toBe(true);
    expect(isRunLive(run, now)).toBe(false);
  });

  it("completed and failed runs are neither live nor stale-lease", () => {
    for (const status of ["completed", "failed"] as const) {
      const run = base({
        id: "run_x",
        status,
        created_at: "2026-08-03T10:00:00.000Z",
      });
      expect(isRunLive(run, now)).toBe(false);
      // status is not "running"/"stale" → isRunStale false (lock only cares about live)
      expect(isRunStale(run, now)).toBe(false);
    }
  });
});

describe("classifyRun", () => {
  const now = Date.parse("2026-08-03T12:00:00.000Z");

  it("returns idle for null / terminal runs", () => {
    expect(classifyRun(null, now)).toEqual({ kind: "idle" });
    expect(
      classifyRun(
        base({
          id: "run_done",
          status: "completed",
          created_at: "2026-08-03T11:00:00.000Z",
        }),
        now,
      ),
    ).toEqual({ kind: "idle" });
  });

  it("locks on a live running run and surfaces stages", () => {
    const run = base({
      id: "run_live",
      kind: "repair",
      status: "running",
      created_at: "2026-08-03T11:58:00.000Z",
      current_stage: "render_stems",
      stages: [
        {
          name: "render_stems",
          status: "running",
          elapsed_s: 12,
          retries: 0,
          provider: "genblaze",
        },
      ],
    });
    const lock = classifyRun(run, now);
    expect(lock.kind).toBe("locked");
    if (lock.kind === "locked") {
      expect(lock.runId).toBe("run_live");
      expect(lock.runKind).toBe("repair");
      expect(lock.currentStage).toBe("render_stems");
      expect(lock.stages).toHaveLength(1);
    }
  });

  it("surfaces stuck-run state past the lease", () => {
    const started = new Date(now - (RUN_LEASE_S + 30) * 1000).toISOString();
    const lock = classifyRun(
      base({
        id: "run_dead",
        kind: "mix",
        status: "running",
        created_at: started,
      }),
      now,
    );
    expect(lock.kind).toBe("stale");
    if (lock.kind === "stale") {
      expect(lock.runIds).toEqual(["run_dead"]);
      expect(lock.runKind).toBe("mix");
      const msg = staleRunMessage(lock);
      expect(msg).toContain("Stuck");
      expect(msg).toContain("run_dead");
      expect(msg).toContain("lease expired");
      // Must not read as a spinner / still-running message
      expect(msg.toLowerCase()).not.toContain("please wait");
      expect(msg.toLowerCase()).not.toContain("in progress");
    }
  });
});

describe("mergeLiveRun", () => {
  it("appends a live run not already in the terminal list", () => {
    const terminal = [
      base({
        id: "run_old",
        status: "completed",
        created_at: "2026-08-03T10:00:00.000Z",
      }),
    ];
    const live = base({
      id: "run_new",
      status: "running",
      created_at: "2026-08-03T11:00:00.000Z",
    });
    expect(mergeLiveRun(terminal, live).map((r) => r.id)).toEqual([
      "run_old",
      "run_new",
    ]);
  });

  it("replaces a terminal record with the live snapshot of the same id", () => {
    const terminal = [
      base({
        id: "run_1",
        status: "completed",
        created_at: "2026-08-03T10:00:00.000Z",
        stages: [],
      }),
    ];
    const live = base({
      id: "run_1",
      status: "running",
      created_at: "2026-08-03T11:00:00.000Z",
      stages: [
        { name: "mix", status: "running", elapsed_s: 1, retries: 0 },
      ],
    });
    const merged = mergeLiveRun(terminal, live);
    expect(merged).toHaveLength(1);
    expect(merged[0].status).toBe("running");
    expect(merged[0].stages).toHaveLength(1);
  });
});
