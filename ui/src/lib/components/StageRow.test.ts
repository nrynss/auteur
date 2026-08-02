import { describe, expect, it } from "vitest";
import { render } from "svelte/server";

import StageRow from "./StageRow.svelte";

// control-room.md §7: an unknown stage state must never be presented as a
// confident success. Only succeeded earns the ok/"Done" chip; anything else
// falls to pending.

const KNOWN = {
  succeeded: { chip: "ok", word: "Done" },
  failed: { chip: "blocked", word: "Failed" },
  running: { chip: "pending", word: "Running" },
};

describe("StageRow", () => {
  for (const [status, expected] of Object.entries(KNOWN)) {
    it(`renders ${status} as ${expected.word}`, () => {
      const { body } = render(StageRow, { props: { name: "mix", status } });
      expect(body).toContain(expected.word);
    });
  }

  it("an unrecognised status reads as pending, never Done", () => {
    // A stage record that arrives without a status — or with a status the
    // API gains later — must not show green and say Done.
    for (const status of ["", "queued", "starting", "unstarted", "???"]) {
      const { body } = render(StageRow, { props: { name: "mix", status } });
      expect(body).toContain("Pending");
      expect(body).not.toContain("Done");
    }
  });

  it("shows elapsed and retries when present", () => {
    const { body } = render(StageRow, {
      props: { name: "stems", status: "failed", elapsed_s: 12.6, retries: 2 },
    });
    expect(body).toContain("00:12.600");
    expect(body).toContain("×2");
  });
});
