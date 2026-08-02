import { describe, expect, it } from "vitest";
import { render } from "svelte/server";

import ContinuityCard from "./ContinuityCard.svelte";

// The honesty guarantee for SPEC §6.3's most sensitive display: the headline
// must be the QA report's OWN recorded detail — never re-derived from scene
// stats — and the source must name the artifact it was recorded in.

const issue = {
  scene_id: "scene_05",
  compared_with: "scene_04",
  confidence: 0.79,
  detail: "mean luma jumps 31.8 between scene_04 (106.7) and scene_05 (138.4)",
};

const scenes = [
  { id: "scene_04", index: 3, start_s: 7.833, end_s: 10.625, stats: { mean_luma: 106.66 } },
  { id: "scene_05", index: 4, start_s: 10.625, end_s: 15.042, stats: { mean_luma: 138.42 } },
] as never[];

function renderCard(runId: string | null) {
  return render(ContinuityCard, {
    props: { issue, threshold: 20, scenes, runId },
  });
}

describe("ContinuityCard", () => {
  it("shows the recorded detail verbatim as the measurement", () => {
    const { body } = renderCard("run_abc123");
    expect(body).toContain(
      "mean luma jumps 31.8 between scene_04 (106.7) and scene_05 (138.4)",
    );
  });

  it("names the artifact the measurement was recorded in", () => {
    const { body } = renderCard("run_abc123");
    expect(body).toContain("recorded at");
    expect(body).toContain("qa/qa-report.json");
    expect(body).toContain("run_abc123");
  });

  it("falls back to the report path when no run id is available", () => {
    const { body } = renderCard(null);
    expect(body).toContain("qa/qa-report.json");
    expect(body).not.toContain("run null");
  });

  it("says the comparison is not a fault and nothing was corrected", () => {
    const { body } = renderCard("run_abc123");
    expect(body).toContain("adjacent-shot mean-luma comparison");
    expect(body).toContain("Nothing has been corrected");
  });

  it("renders the comparison in Evidence's BODY slot, not the meter bar", () => {
    // Regression: the comparison was previously passed as default children,
    // which Evidence renders inside the 3px meter bar — thumbnails and note
    // colliding with the source row. Assert structural position, not just
    // substring presence. (Svelte scoped CSS inserts a hash into class
    // attributes, so match class tokens.)
    const { body } = renderCard("run_abc123");
    const iBody = body.search(/class="body[^"]*"/);
    const iBar = body.indexOf('class="bar"');
    const iCompare = body.search(/class="compare[^"]*"/);
    expect(iBody).toBeGreaterThan(-1); // the body slot rendered
    expect(iBar).toBe(-1); // nothing sits in the 3px meter slot
    expect(iCompare).toBeGreaterThan(iBody); // compare lives inside the body
  });

  it("renders the recorded sentence as a statement, not a mono metric", () => {
    const { body } = renderCard("run_abc123");
    // The headline is a full recorded sentence — UI font, wrapping layout,
    // not the tabular-mono −21.4 LUFS treatment.
    expect(body).toMatch(/class="val[^"]*\sstatement(\s|")/);
  });
});
