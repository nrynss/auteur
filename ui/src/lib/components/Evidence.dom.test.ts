/** Evidence's source row must never break mid-phrase: the whole payload of
 * the row is "summed from <artifact>" (or "measured from …"), and when the
 * artifact name is long the phrase used to shrink and wrap across two lines
 * while the artifact held its width.
 *
 * jsdom cannot measure layout and does not inject Svelte's scoped CSS (the
 * component's stylesheet is compiled out of the mount), so the pin lives in
 * the source CSS contract here, and the real geometry — phrase and artifact
 * on one line — is asserted in the browser gate (playwright/layout.spec.ts,
 * "the Evidence source row never breaks mid-phrase").
 */
import { expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const SOURCE = readFileSync(
  join(dirname(fileURLToPath(import.meta.url)), "Evidence.svelte"),
  "utf-8",
);

function cssBlock(selector: string): string {
  const m = SOURCE.match(new RegExp(`${selector} \\{([^}]*)\\}`));
  expect(m, `css block for ${selector}`).not.toBeNull();
  return m![1];
}

it("the phrase (.k) holds max-content and cannot shrink or wrap", () => {
  const k = cssBlock("\\.src \\.k");
  expect(k).toMatch(/flex:\s*0\s+0\s+auto/);
  expect(k).toMatch(/white-space:\s*nowrap/);
});

it("the artifact (.v) truncates instead of wrapping or pushing the row wider", () => {
  const v = cssBlock("\\.src \\.v");
  expect(v).toMatch(/flex:\s*1\s+1\s+auto/);
  expect(v).toMatch(/min-width:\s*0/);
  expect(v).toMatch(/text-overflow:\s*ellipsis/);
  expect(v).toMatch(/white-space:\s*nowrap/);
  expect(v).toMatch(/overflow:\s*hidden/);
});
