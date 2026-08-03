/** SpendPanel's one rule with teeth.
 *
 * docs/api.md §3.10: `ledger_writable: false` means the figures are fiction,
 * not merely imprecise — nothing is being recorded, so usage reads zero
 * however much was spent. "A UI seeing this must stop presenting the numbers
 * at all." A warning banner above the numbers does not satisfy that: the
 * numbers are still on screen to be read.
 */
import { expect, it } from "vitest";
import { render } from "svelte/server";

import SpendPanel from "./SpendPanel.svelte";

const BUDGET = {
  project_id: "proj_TEST",
  budget_usd: 5,
  spent: 3.7,
  remaining: 1.3,
  estimate: true,
  ledger_writable: true,
  spent_estimated: 3.4,
  spent_actual: 0.3,
  thresholds: [
    { threshold: 0.5, crossed: true },
    { threshold: 0.8, crossed: false },
  ],
  by_model: [
    { endpoint: "gemini-2.5-flash", spent: 0.42 },
    { endpoint: "eleven_text_to_sound_v2", spent: 3.28 },
  ],
};

const QUOTA = {
  estimate: true,
  ledger_writable: true,
  models: [
    {
      model_id: "gemini-2.5-flash",
      status: "warn",
      per_minute: { used: 1, exhausted: false },
      per_day: { used: 18, limit: 20, remaining: 2, exhausted: false },
      resets_at: "2026-08-04T00:00:00Z",
    },
  ],
};

it("shows the server's own figures — split, thresholds, per-model", () => {
  const { body } = render(SpendPanel, {
    props: { budget: BUDGET, quota: QUOTA } as never,
  });
  expect(body).toContain("$3.70");
  // The estimate/actual split is the point — both halves must be named.
  expect(body).toContain("$0.30");
  expect(body).toContain("$3.40");
  expect(body).toContain("eleven_text_to_sound_v2");
  // Per-day is the binding constraint, not per-minute.
  expect(body).toContain("18");
  expect(body).toContain("2");
  // Spend is evidence: it names where it was summed from.
  expect(body).toContain("proj_TEST");
});

it("withholds every figure when the ledger is unwritable", () => {
  const { body } = render(SpendPanel, {
    props: {
      budget: { ...BUDGET, ledger_writable: false },
      quota: QUOTA,
    } as never,
  });
  expect(body).toContain("figures withheld");
  // Not one number may survive — a warning over readable figures is exactly
  // what §3.10 forbids.
  expect(body).not.toContain("$3.70");
  expect(body).not.toContain("$0.30");
  expect(body).not.toContain("eleven_text_to_sound_v2");
});

it("an unwritable ledger reported by quota alone also withholds", () => {
  const { body } = render(SpendPanel, {
    props: { budget: BUDGET, quota: { ...QUOTA, ledger_writable: false } } as never,
  });
  expect(body).toContain("figures withheld");
  expect(body).not.toContain("$3.70");
});

it("distinguishes a failed fetch from no spend", () => {
  const failed = render(SpendPanel, {
    props: { budget: null, quota: null, failed: true } as never,
  });
  expect(failed.body).toContain("could not be loaded");

  const empty = render(SpendPanel, {
    props: { budget: null, quota: null } as never,
  });
  expect(empty.body).toContain("No spend recorded yet");
});
