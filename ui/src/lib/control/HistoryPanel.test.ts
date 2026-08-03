/** HistoryPanel's state matrix: each of the three sections (timeline
 * versions, runs & mixes, event log) has an empty state that names what
 * would fill it, and a failure state that must never be confused with it.
 * "No timeline versions yet — analysis creates the first" is a checkable
 * claim and is FALSE when the request failed; the failure branch replaces
 * exactly the empty branch, never real data. */
import { expect, it } from "vitest";
import { render } from "svelte/server";

import HistoryPanel from "./HistoryPanel.svelte";

const VERSIONS = { versions: [1, 2], latest: 2 };

const RUNS = [
  {
    id: "run_1",
    project_id: "proj_TEST",
    kind: "analyze",
    status: "completed",
    created_at: "2026-08-02T10:00:00Z",
    timeline_version: 1,
    stages: [],
  },
  {
    id: "run_2",
    project_id: "proj_TEST",
    kind: "mix",
    status: "failed",
    created_at: "2026-08-02T10:05:00Z",
    timeline_version: 2,
    error_code: "storage_unavailable",
    stages: [],
  },
] as never[];

const EVENTS = [
  { id: "ev_1", type: "analysis", actor: "system", created_at: "2026-08-02T10:00:00Z" },
  { id: "ev_2", type: "repair", actor: "director", created_at: "2026-08-02T10:06:00Z" },
] as never[];

const PEAKS = {
  peaks_per_second: 10,
  channels: 1,
  sample_rate: 100,
  duration_s: 15,
  min: [-0.2, -0.1],
  max: [0.8, 0.9],
};

it("renders versions, runs and events with their own evidence", () => {
  const { body } = render(HistoryPanel, {
    props: {
      versions: VERSIONS as never,
      runs: RUNS,
      events: EVENTS,
      mixPeaks: null,
      mixRunId: null,
    },
  });
  // Version chips: v1 pending, v2 live.
  expect(body).toContain("v1");
  expect(body).toContain("v2");
  // Runs: kind, version, clock time, status, and the error code when failed.
  expect(body).toContain("mix");
  expect(body).toContain("v2");
  expect(body).toContain("10:05:00");
  expect(body).toContain("failed");
  expect(body).toContain("storage_unavailable");
  // Events: friendly type + actor + time.
  expect(body).toContain("analysis");
  expect(body).toContain("director");
  expect(body).toContain("10:06:00");
});

it("the latest mix's real peaks render under the run id", () => {
  const { body } = render(HistoryPanel, {
    props: {
      versions: VERSIONS as never,
      runs: RUNS,
      events: EVENTS,
      mixPeaks: PEAKS as never,
      mixRunId: "run_2",
    },
  });
  expect(body).toContain("latest mix · run_2");
  expect(body).toContain("<canvas");
});

it("each empty section names what would fill it — and only when not failed", () => {
  const { body } = render(HistoryPanel, {
    props: {
      versions: null,
      runs: [],
      events: [],
      mixPeaks: null,
      mixRunId: null,
    },
  });
  expect(body).toContain("No timeline versions yet. Analysis creates the first.");
  expect(body).toContain("No runs yet. Mix, repair or export create one.");
  expect(body).toContain("No events yet. Analysis and repairs populate the log.");
  expect(body).not.toContain("could not be loaded");
});

it("a failed fetch replaces every empty claim with a failure — never the cause it cannot know", () => {
  const { body } = render(HistoryPanel, {
    props: {
      versions: null,
      runs: [],
      events: [],
      mixPeaks: null,
      mixRunId: null,
      failed: true,
    },
  });
  // The checkable claims are all absent:
  expect(body).not.toContain("No timeline versions yet");
  expect(body).not.toContain("No runs yet");
  expect(body).not.toContain("No events yet");
  // The failures name what failed:
  expect(body).toContain("Timeline versions could not be loaded.");
  expect(body).toContain("Runs could not be loaded.");
  expect(body).toContain("The event log could not be loaded.");
});

it("failure never hides real data — a loaded section still renders beside a failed one", () => {
  // The global failure flag covers every ancillary fetch; a section that
  // actually has data must keep showing it (data beats suspicion).
  const { body } = render(HistoryPanel, {
    props: {
      versions: VERSIONS as never,
      runs: [],
      events: EVENTS,
      mixPeaks: null,
      mixRunId: null,
      failed: true,
    },
  });
  expect(body).toContain("v1");
  expect(body).toContain("v2");
  expect(body).toContain("director");
  // Only the empty section got the failure treatment.
  expect(body).toContain("Runs could not be loaded.");
  expect(body).not.toContain("No runs yet");
});
