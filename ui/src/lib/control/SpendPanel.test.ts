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

// -- ledger rows (unit 7.16) ------------------------------------------------

const LEDGER = {
  project_id: "proj_TEST",
  total_count: 3,
  rows: [
    {
      timestamp: 1753305781.234,
      endpoint: "gemini-2.5-flash",
      cost_estimate: 0.005,
      cost_source: "estimate",
      success: true,
      error: null,
      project_id: "proj_TEST",
      params_hash: "a1b2c3d4",
    },
    {
      timestamp: 1753305780.0,
      endpoint: "eleven_text_to_sound_v2",
      cost_estimate: 0.3,
      cost_source: "actual",
      success: false,
      error: "provider auth failed",
      project_id: "proj_TEST",
      params_hash: null,
    },
  ],
  next_cursor: 2,
  ledger_writable: true,
};

it("renders ledger rows with their own source and result — failed included", () => {
  const { body } = render(SpendPanel, {
    props: {
      budget: BUDGET,
      quota: QUOTA,
      ledger: LEDGER,
    } as never,
  });
  expect(body).toContain("Ledger — individual transactions");
  // The audit trail names the endpoint, the money, and how it was priced.
  expect(body).toContain("eleven_text_to_sound_v2");
  expect(body).toContain("$0.30");
  expect(body).toContain("actual");
  // A failed billable call is visible as a failure — never skipped, never
  // substituted by a success.
  expect(body).toContain("Failed");
  expect(body).toContain("provider auth failed");
  // Pagination controls exist and say how many rows there are.
  expect(body).toContain("2 of 3");
  expect(body).toContain("older");
});

it("withholds ledger rows too when the ledger is unwritable", () => {
  const { body } = render(SpendPanel, {
    props: {
      budget: { ...BUDGET, ledger_writable: false },
      quota: QUOTA,
      ledger: LEDGER,
    } as never,
  });
  expect(body).toContain("figures withheld");
  expect(body).not.toContain("individual transactions");
  expect(body).not.toContain("eleven_text_to_sound_v2");
});

it("navigation states are explicit props, not inferred from next_cursor", () => {
  // next_cursor names the OLDER page, so a page payload cannot answer "can I
  // go newer" — that lives with the cursor owner. The last page (next_cursor
  // null) must leave "newer" enabled when a previous page exists, and the
  // first page must disable it. The old derivation inverted both.
  const last = render(SpendPanel, {
    props: {
      budget: BUDGET,
      quota: QUOTA,
      ledger: { ...LEDGER, next_cursor: null, rows: [LEDGER.rows[0]] },
      canGoNewer: true,
      canGoOlder: false,
    } as never,
  });
  const lastBody = last.body;
  expect(lastBody).toContain("1 of 3");
  const newerBtn = (body: string) =>
    body.match(/<button class="mini[^"]*"[^>]*>newer<\/button>/)?.[0] ?? "";
  const olderBtn = (body: string) =>
    body.match(/<button class="mini[^"]*"[^>]*>older<\/button>/)?.[0] ?? "";
  // "newer" must be alive on the last page — the trap this regression pins;
  // "older" is correctly dead there.
  expect(newerBtn(lastBody)).not.toContain("disabled");
  expect(olderBtn(lastBody)).toContain("disabled");

  const first = render(SpendPanel, {
    props: {
      budget: BUDGET,
      quota: QUOTA,
      ledger: LEDGER,
      canGoNewer: false,
      canGoOlder: true,
    } as never,
  });
  // First page: newer disabled (no previous page), older enabled.
  expect(newerBtn(first.body)).toContain("disabled");
  expect(olderBtn(first.body)).not.toContain("disabled");
});
