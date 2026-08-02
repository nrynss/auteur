import { describe, expect, it } from "vitest";
import { render } from "svelte/server";

import Chip from "./Chip.svelte";

// The a11y contract from control-room.md §2: colour never carries meaning
// alone — every state must pair a glyph and a word, and the accessible name
// must be the word plus any detail.

const WORDS: Record<string, string> = {
  ok: "OK",
  warn: "Warning",
  blocked: "Blocked",
  pending: "Pending",
};

describe("Chip", () => {
  for (const state of ["ok", "warn", "blocked", "pending"] as const) {
    it(`renders the word for state ${state} — never colour alone`, () => {
      const { body } = render(Chip, { props: { state } });
      expect(body).toContain(WORDS[state]);
      // The state is exposed to CSS via a class AND to AT via the label —
      // but the visible word is what carries the meaning. (Svelte scoped CSS
      // inserts a hash between the component class and the state class.)
      expect(body).toMatch(
        new RegExp('class="chip[^"]*\\s' + state + '(\\s|")'),
      );
    });
  }

  it("puts word and detail in the accessible name", () => {
    const { body } = render(Chip, {
      props: { state: "warn", detail: "7.4 LU under target" },
    });
    expect(body).toContain("Warning — 7.4 LU under target");
  });

  it("honours an explicit label override", () => {
    const { body } = render(Chip, {
      props: { state: "ok", label: "Running", live: true },
    });
    expect(body).toContain("Running");
    expect(body).not.toContain(">OK<");
  });

  it("renders a real button when used as a toggle — keyboard support", () => {
    const { body } = render(Chip, {
      props: { state: "ok", pressed: true },
    });
    expect(body).toMatch(/<button[^>]*type="button"/);
    expect(body).toContain('aria-pressed="true"');
    // Never a span pretending to be a button.
    expect(body).not.toMatch(/<span[^>]*role="button"/);
  });
});
