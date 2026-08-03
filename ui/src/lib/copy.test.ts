/** Rendered-copy invariants (the no-AI-slop rule): no em dash (U+2014) in
 * any rendered string, in any .svelte template or rendered copy module.
 * Comments are documentation, not UI — they are exempt. A rendered em dash
 * fails the suite so the register rule cannot decay by hand. */
import { expect, it } from "vitest";
import { readFileSync, readdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const lib = join(dirname(fileURLToPath(import.meta.url)), "..", "lib");

function stripComments(src: string): string {
  return src
    .replace(/\/\*.*?\*\//gs, "")
    .replace(/\/\/[^\n]*/g, "")
    .replace(/<!--.*?-->/gs, "");
}

const RENDERED_TS = [
  "format.ts",
  "help/glossary.ts",
  "help/bindings.ts",
  "brand.ts",
  "control/helpers.ts",
];

it("no rendered string contains an em dash", () => {
  const offenders: string[] = [];

  for (const f of readdirSync(lib, { recursive: true })) {
    if (typeof f !== "string" || !f.endsWith(".svelte")) continue;
    const src = readFileSync(join(lib, f), "utf-8");
    const cleaned = stripComments(src);
    const idx = cleaned.indexOf("\u2014");
    if (idx !== -1) {
      const line = cleaned.slice(0, idx).split("\n").length;
      offenders.push(`${f}:${line}`);
    }
  }

  for (const f of RENDERED_TS) {
    const src = readFileSync(join(lib, f), "utf-8");
    const cleaned = stripComments(src);
    const idx = cleaned.indexOf("\u2014");
    if (idx !== -1) {
      const line = cleaned.slice(0, idx).split("\n").length;
      offenders.push(`${f}:${line}`);
    }
  }

  expect(offenders, `rendered em dashes in:\n${offenders.join("\n")}`).toEqual([]);
});
