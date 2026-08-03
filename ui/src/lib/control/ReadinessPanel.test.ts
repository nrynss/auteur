/** ReadinessPanel's state matrix: the §6.1 checklist (project data), the
 * latest run's stages (or the live run's stages as they land), the locked
 * in-flight label, and the failure branch — a failed run fetch must not
 * render as "nothing ever ran". */
import { expect, it } from "vitest";
import { render } from "svelte/server";

import ReadinessPanel from "./ReadinessPanel.svelte";

const PROJECT = {
  id: "proj_TEST",
  title: "Western standoff (silent)",
  status: "ready",
  readiness_percent: 83,
  readiness: [
    { key: "timeline", label: "Timeline has events", state: "ok" },
    { key: "qa", label: "QA measured", state: "warn", detail: "true peak over target" },
    { key: "export", label: "Export exists", state: "blocked" },
    { key: "publish", label: "Published", state: "pending" },
  ],
} as never;

const RUNS = [
  {
    id: "run_1",
    project_id: "proj_TEST",
    kind: "mix",
    status: "completed",
    created_at: "2026-08-02T10:00:00Z",
    timeline_version: 1,
    stages: [
      { name: "render", status: "succeeded", elapsed_s: 12, retries: 0, provider: "b2" },
      { name: "mix", status: "failed", elapsed_s: 3, retries: 2, provider: null },
    ],
  },
] as never[];

it("renders every readiness row with label, detail, and the word for warn/blocked", () => {
  const { body } = render(ReadinessPanel, {
    props: { project: PROJECT, runs: [] },
  });
  expect(body).toContain("Timeline has events");
  expect(body).toContain("QA measured");
  expect(body).toContain("true peak over target");
  // Warn/blocked carry the WORD (colour is never alone); ok/pending do not.
  expect(body).toContain("Warning");
  expect(body).toContain("Blocked");
  expect(body).not.toContain("OK");
  expect(body).not.toContain("Pending");
});

it("empty readiness rows name what would fill them", () => {
  const { body } = render(ReadinessPanel, {
    props: {
      project: { ...(PROJECT as object), readiness: [] } as never,
      runs: [],
    },
  });
  expect(body).toContain("No readiness rows yet.");
});

it("renders the latest run's stages with their statuses", () => {
  const { body } = render(ReadinessPanel, {
    props: { project: PROJECT, runs: RUNS },
  });
  expect(body).toContain("Last run · mix");
  expect(body).toContain("run_1");
  expect(body).toContain("render");
  expect(body).toContain("Done");
  expect(body).toContain("mix");
  expect(body).toContain("Failed");
  expect(body).toContain("×2"); // retries
});

it("a locked live run reads 'In flight' and spins, and pending stages say so", () => {
  const live = {
    id: "run_9",
    project_id: "proj_TEST",
    kind: "mix",
    status: "running",
    created_at: "2026-08-02T11:00:00Z",
    stages: [],
  } as never;
  const { body } = render(ReadinessPanel, {
    props: { project: PROJECT, runs: [], liveRun: live, locked: true },
  });
  expect(body).toContain("In flight · mix");
  expect(body).toContain("Run in flight");
  expect(body).toContain("Stages will appear as they complete.");
});

it("a failed run fetch never reads as 'nothing ever ran'", () => {
  const { body } = render(ReadinessPanel, {
    props: { project: PROJECT, runs: [], failed: true },
  });
  expect(body).toContain("Run status could not be loaded.");
  // The readiness checklist is project shell data — still shown.
  expect(body).toContain("Timeline has events");
});

it("failure never hides a run that did load", () => {
  const { body } = render(ReadinessPanel, {
    props: { project: PROJECT, runs: RUNS, failed: true },
  });
  expect(body).toContain("Last run · mix");
  expect(body).toContain("render");
  expect(body).not.toContain("Run status could not be loaded.");
});
