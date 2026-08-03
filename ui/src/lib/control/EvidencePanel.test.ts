/** EvidencePanel regression pins (task 22 review): the QA report's audio and
 * visual blocks are null when a project has not been measured — the real
 * no-QA case — and the panel must render its empty state, not throw. The
 * browser gate caught these crashes; this pins them at the unit level so a
 * regression fails fast in vitest too. */
import { expect, it } from "vitest";
import { render } from "svelte/server";

import EvidencePanel from "./EvidencePanel.svelte";

const NO_QA = {
  run_id: null,
  overall_status: "pending",
  audio: null,
  visual: null,
};

it("renders the empty state when a project has no QA measurement (audio null)", () => {
  const { body } = render(EvidencePanel, {
    props: { qa: NO_QA as never, scenes: [] },
  });
  expect(body).toContain("No measurements yet");
});

it("renders the empty state when the visual block is null", () => {
  const { body } = render(EvidencePanel, {
    props: {
      qa: {
        run_id: "run_1",
        overall_status: "ok",
        audio: {
          integrated_lufs: -21.4,
          true_peak_dbtp: -1.0,
          target_lufs: -14.0,
          target_true_peak_dbtp: -1.0,
          measured_run_id: "run_1",
          measured: true,
        },
        visual: null,
      } as never,
      scenes: [],
    },
  });
  // Measurements render; the visual block being null must not throw and
  // renders no continuity cards.
  expect(body).toContain("-21.4 LUFS");
  expect(body).not.toContain("Visual");
});

it("renders continuity issues when the visual block exists", () => {
  const { body } = render(EvidencePanel, {
    props: {
      qa: {
        run_id: "run_1",
        overall_status: "ok",
        audio: null,
        visual: {
          luma_delta_threshold: 20.0,
          issues: [
            { code: "luma_jump", scene_id: "scene_04", compared_with: "scene_05", mean_luma_delta: 31.8, confidence: 0.79 },
          ],
        },
      } as never,
      scenes: [],
    },
  });
  expect(body).toContain("Shot-to-shot comparison");
});

it("a failed QA fetch says failure, not 'not measured' — the empty state's claim would be false", () => {
  // "No measurements yet — the mix has not been QA-measured" is a checkable
  // claim and is false when the request simply failed. The failure branch
  // names what failed instead.
  const { body } = render(EvidencePanel, {
    props: { qa: null, scenes: [], failed: true },
  });
  expect(body).toContain("QA measurements could not be loaded.");
  expect(body).not.toContain("has not been QA-measured");
  expect(body).not.toContain("has not been QA-measured");
});

it("failure never hides measurements that did load", () => {
  const { body } = render(EvidencePanel, {
    props: {
      qa: {
        run_id: "run_1",
        overall_status: "ok",
        audio: {
          integrated_lufs: -21.4,
          true_peak_dbtp: -1.0,
          target_lufs: -14.0,
          target_true_peak_dbtp: -1.0,
          measured_run_id: "run_1",
          measured: true,
        },
        visual: null,
      } as never,
      scenes: [],
      failed: true,
    },
  });
  expect(body).toContain("-21.4 LUFS");
  expect(body).not.toContain("could not be loaded");
});
