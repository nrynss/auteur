/** ChecklistRow — one readiness row. The state never travels as colour
 * alone: warn/blocked rows render the WORD beside the glyph. The detail is
 * the server's own sentence, and a row without one renders without a line. */
import { expect, it } from "vitest";
import { render } from "svelte/server";

import ChecklistRow from "./ChecklistRow.svelte";

function row(state: string, label: string, detail: string | null = null) {
  return render(ChecklistRow, {
    props: { item: { key: label, label, state, detail } },
  }).body;
}

it("warn and blocked rows carry the word — colour is never alone", () => {
  expect(row("warn", "QA measured", "true peak over target")).toContain("Warning");
  expect(row("blocked", "Export exists")).toContain("Blocked");
});

it("ok and pending rows do not print a verdict word", () => {
  const ok = row("ok", "Timeline has events");
  expect(ok).not.toContain("OK");
  const pending = row("pending", "Published");
  expect(pending).not.toContain("Pending");
});

it("renders the label and the server's detail sentence", () => {
  const body = row("warn", "QA measured", "integrated loudness 7.4 LU under target");
  expect(body).toContain("QA measured");
  expect(body).toContain("integrated loudness 7.4 LU under target");
});

it("a row without detail renders without a detail line", () => {
  const body = row("ok", "Timeline has events");
  expect(body).toContain("Timeline has events");
  expect(body).not.toContain("detail");
});
