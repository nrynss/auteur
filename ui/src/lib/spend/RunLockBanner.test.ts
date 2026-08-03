/** RunLockBanner (unit 7.11) — the live-run lock and the stale-lease error.
 * Colour is never alone: the locked banner carries the word "Locked" plus a
 * spinner label, the stale banner carries "Stuck run" plus the honest
 * message. An idle lock renders nothing at all. */
import { expect, it } from "vitest";
import { render } from "svelte/server";

import RunLockBanner from "./RunLockBanner.svelte";

it("an idle lock renders nothing", () => {
  const { body } = render(RunLockBanner, { props: { lock: { kind: "idle" } } });
  expect(body).not.toContain("Locked");
  expect(body).not.toContain("Stuck run");
  expect(body).not.toContain("banner");
});

it("a live run renders the lock with its id, kind and current stage", () => {
  const { body } = render(RunLockBanner, {
    props: {
      lock: {
        kind: "locked",
        runId: "run_42",
        runKind: "mix",
        stages: [{ name: "render", status: "running", elapsed_s: 4, retries: 0, provider: null }],
        currentStage: "render",
      } as never,
    },
  });
  expect(body).toContain("Locked");
  expect(body).toContain("mix run");
  expect(body).toContain("run_42");
  expect(body).toContain("stage");
  expect(body).toContain("render");
  // The word "Locked" is paired with a spinner label — never colour alone.
  expect(body).toContain("Run in flight");
  // And what the lock means:
  expect(body).toContain("spend and edits disabled until it finishes");
});

it("a run without a current stage names the run and the lock, not a ghost stage", () => {
  const { body } = render(RunLockBanner, {
    props: {
      lock: {
        kind: "locked",
        runId: "run_7",
        runKind: "analyze",
        stages: [],
        currentStage: null,
      } as never,
    },
  });
  expect(body).toContain("Locked");
  expect(body).toContain("analyze run");
  expect(body).toContain("run_7");
  expect(body).not.toContain("stage");
});

it("a stale lease renders the stuck-run error, not an infinite spinner", () => {
  const { body } = render(RunLockBanner, {
    props: {
      lock: {
        kind: "stale",
        runIds: ["run_99"],
        runKind: "mix",
      } as never,
    },
  });
  expect(body).toContain("Stuck run");
  expect(body).toContain("run_99");
  expect(body).toContain("the process died without a terminal record");
  // The honest consequence: spend is NOT locked forever.
  expect(body).toContain("Spend is unlocked");
  expect(body).toContain("inspect the event log before starting another run");
  // No spinner — a stale lease is a stopped thing, not a working one.
  expect(body).not.toContain("Run in flight");
});
