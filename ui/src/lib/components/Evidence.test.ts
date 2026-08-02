import { describe, expect, it } from "vitest";
import { render } from "svelte/server";

import Evidence from "./Evidence.svelte";

// The structural honesty rule (control-room.md §7): a QA number cannot
// render without its source. `source` is a required prop, so a screen that
// shows a measurement without naming the artifact it was measured from does
// not compile (svelte-check). These tests pin the render contract.

describe("Evidence", () => {
  it("renders the measured number with its source", () => {
    const { body } = render(Evidence, {
      props: {
        label: "Integrated loudness",
        value: "−21.4 LUFS",
        source: "runs/run_01KZ0JYN9M6Y4Q2WQTV3X8ZHD0/mix.wav",
      },
    });
    expect(body).toContain("Integrated loudness");
    expect(body).toContain("−21.4 LUFS");
    expect(body).toContain("measured from");
    expect(body).toContain("runs/run_01KZ0JYN9M6Y4Q2WQTV3X8ZHD0/mix.wav");
  });

  it("allows a different source phrase for non-measurements", () => {
    const { body } = render(Evidence, {
      props: {
        label: "Project spend",
        value: "$2.40",
        source: "ledger",
        sourcePhrase: "summed from",
      },
    });
    expect(body).toContain("summed from");
    expect(body).toContain("ledger");
    expect(body).not.toContain("measured from");
  });

  it("warn is a note with a glyph — colour never alone", () => {
    const { body } = render(Evidence, {
      props: {
        label: "True peak",
        value: "−2.1 dBTP",
        source: "runs/run_01KZ0JYN9M6Y4Q2WQTV3X8ZHD0/mix.wav",
        warn: "7.4 LU under target",
      },
    });
    // The warn colour, the glyph, and the word all arrive together.
    expect(body).toMatch(/class="val[^"]*\swarn(\s|")/);
    expect(body).toContain("7.4 LU under target");
  });

  it("no warn tone without a warn note", () => {
    const { body } = render(Evidence, {
      props: {
        label: "True peak",
        value: "−2.1 dBTP",
        source: "runs/run_01KZ0JYN9M6Y4Q2WQTV3X8ZHD0/mix.wav",
      },
    });
    expect(body).not.toMatch(/class="val[^"]*\swarn(\s|")/);
    expect(body).not.toContain("under target");
  });
});
