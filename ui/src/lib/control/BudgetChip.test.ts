/** BudgetChip (SPEC §9.4 / docs/api.md §3.11) — the chrome's spend figure.
 * Every number is an estimate and is tagged so; an unwritable ledger turns
 * the figures into fiction and must say so. Absent budget renders nothing —
 * never a fake "$0.00". */
import { expect, it } from "vitest";
import { render } from "svelte/server";

import BudgetChip from "./BudgetChip.svelte";

const BUDGET = {
  project_id: "proj_TEST",
  budget_usd: 5,
  spent: 3.7,
  remaining: 1.3,
  estimate: true,
  ledger_writable: true,
};

it("shows spent against the budget with the estimate tag", () => {
  const { body } = render(BudgetChip, {
    props: { budget: BUDGET as never },
  });
  expect(body).toContain("$3.70");
  expect(body).toContain("of");
  expect(body).toContain("$5.00");
  expect(body).toContain("estimate");
  expect(body).not.toContain("costs not recorded");
});

it("an unwritable ledger says the figures are fiction — never a silent number", () => {
  const { body } = render(BudgetChip, {
    props: { budget: { ...BUDGET, ledger_writable: false } as never },
  });
  expect(body).toContain("costs not recorded");
  // The warning has role=status, so it is announced — not a colour-only icon.
  expect(body).toContain('role="status"');
});

it("absent budget renders nothing, not a fake zero", () => {
  const { body } = render(BudgetChip, { props: { budget: null } });
  // (server render leaves comment markers; assert on real content)
  expect(body).not.toContain("$");
  expect(body).not.toContain("budget");
});

it("a budget with no cap shows nothing — the chip is spent-vs-cap, and a capless figure would imply a comparison that does not exist", () => {
  const { body } = render(BudgetChip, {
    props: { budget: { ...BUDGET, budget_usd: null } as never },
  });
  expect(body).not.toContain("$");
});
