/** waitForRun's transient-failure tolerance.
 *
 * Same class as the 7.11 lock finding one module over: a single failed
 * getRun must not collapse a flow whose run is progressing fine on the
 * server. Ingest hashes the whole source and can run for minutes, so one
 * network blip used to fail an otherwise healthy create. */
import { expect, it, vi } from "vitest";

import { ApiError } from "../api/client";
import { waitForRun, PollTimeoutError } from "./poll-run";

vi.mock("../api/projects", () => ({
  getRun: (...args: unknown[]) => mockGetRun(...args),
}));

let mockGetRun: (...args: unknown[]) => Promise<unknown> = async () => ({});

const RUNNING = { id: "run_1", status: "running", current_stage: "hash" };
const DONE = { id: "run_1", status: "completed", current_stage: "hash" };

it("survives transient failures and still resolves", async () => {
  const seen: number[] = [];
  let call = 0;
  mockGetRun = async () => {
    call += 1;
    if (call === 1) return RUNNING;
    if (call === 2) throw new ApiError("boom", "storage_unavailable", 503, {});
    if (call === 3) throw new TypeError("network");
    return DONE;
  };
  const run = await waitForRun("p", "run_1", {
    intervalMs: 0,
    onTransientError: (_e, n) => seen.push(n),
  });
  expect((run as { status: string }).status).toBe("completed");
  expect(seen).toEqual([1, 2]); // both blips surfaced, neither fatal
});

it("gives up after too many consecutive failures", async () => {
  mockGetRun = async () => {
    throw new ApiError("down", "storage_unavailable", 503, {});
  };
  await expect(
    waitForRun("p", "run_1", { intervalMs: 0, maxTransientErrors: 2 }),
  ).rejects.toBeInstanceOf(ApiError);
});

it("fails fast on 404 — the run id will never exist", async () => {
  let calls = 0;
  mockGetRun = async () => {
    calls += 1;
    throw new ApiError("gone", "not_found", 404, {});
  };
  await expect(
    waitForRun("p", "run_1", { intervalMs: 0, maxTransientErrors: 5 }),
  ).rejects.toBeInstanceOf(ApiError);
  expect(calls).toBe(1); // not retried
});

it("still times out while merely running", async () => {
  mockGetRun = async () => RUNNING;
  await expect(
    waitForRun("p", "run_1", { intervalMs: 0, timeoutMs: -1 }),
  ).rejects.toBeInstanceOf(PollTimeoutError);
});
