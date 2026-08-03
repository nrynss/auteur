/** Contrast gate (handoff.md task 22) — pins the token pairs a component
 * actually produces to WCAG AA (4.5:1), in both themes, by reading the
 * values straight out of theme.css so a token regression fails the suite.
 * The pairs tested are the ones the components produce: text, dim and faint
 * on surface, raised and ground. (axe cannot measure colour in jsdom — this
 * is the mechanical half the CI a11y check covers.) */
import { expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const css = readFileSync(join(here, "theme.css"), "utf-8");

/** Parse `--token: #rrggbb;` values from a CSS block: the :root block (light
 * defaults) and each theme redefinition. */
function tokens(block: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const m of block.matchAll(/--([a-z0-9-]+):\s*(#[0-9a-fA-F]{3,8});/g)) {
    out[m[1]] = m[2];
  }
  return out;
}

function block(name: string): string {
  // The named block's own declarations, up to its closing brace.
  const start = css.indexOf(name);
  expect(start, `${name} block in theme.css`).toBeGreaterThan(-1);
  const brace = css.indexOf("{", start);
  const end = css.indexOf("}", brace);
  return css.slice(brace + 1, end);
}

function rgb(hex: string): [number, number, number] {
  const h = hex.replace("#", "");
  const full =
    h.length === 3
      ? h.split("").map((c) => c + c).join("")
      : h.slice(0, 6);
  return [
    parseInt(full.slice(0, 2), 16),
    parseInt(full.slice(2, 4), 16),
    parseInt(full.slice(4, 6), 16),
  ];
}

function lum(hex: string): number {
  const [r, g, b] = rgb(hex).map((c) => c / 255);
  const f = (c: number) =>
    c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
}

function ratio(a: string, b: string): number {
  const la = lum(a);
  const lb = lum(b);
  const hi = Math.max(la, lb);
  const lo = Math.min(la, lb);
  return (hi + 0.05) / (lo + 0.05);
}

const LIGHT = tokens(block(":root"));
// The light redefinition block under `[data-theme="light"]` must match the
// defaults; the dark one is the second `@media (prefers-color-scheme: dark)`
// … simpler: take the last redefinition of each token in the file.
function finalValue(token: string): string {
  // The dark palette is defined in the `@media (prefers-color-scheme: dark)`
  // block AND the `[data-theme="dark"]` block; the light values come after
  // them in the file, so "last match" would grab the wrong theme.
  // The header comment also mentions the media query — take the LAST
  // occurrence, which is the real block.
  const darkStart = css.lastIndexOf("@media (prefers-color-scheme: dark)");
  const darkEnd = css.indexOf(':root[data-theme="dark"]', darkStart);
  const darkBlock = css.slice(darkStart, darkEnd);
  const m = darkBlock.match(new RegExp(`--${token}:\\s*(#[0-9a-fA-F]+);`));
  expect(m, `${token} in the dark block`).toBeTruthy();
  return m![1];
}

// fg tokens (text colours) on the bg tokens the components actually pair
// them with. `faint` is the small-metadata colour, so it must meet 4.5:1 on
// every background it is rendered on — including the app `ground`.
const CASES: [string, string][] = [
  ["text", "surface"], ["text", "raised"], ["text", "ground"],
  ["dim", "surface"], ["dim", "raised"], ["dim", "ground"],
  ["faint", "surface"], ["faint", "raised"], ["faint", "ground"],
  ["accent", "surface"],
  ["ok", "surface"], ["warn", "surface"], ["stop", "surface"],
];

for (const theme of ["light", "dark"]) {
  const fg = (t: string) => (theme === "light" ? LIGHT[t] : finalValue(t));
  
it(`${theme} theme meets AA for every produced pair`, () => {
    for (const [f, b] of CASES) {
      const fv = fg(f);
      const bv = fg(b);
      expect(fv, `--${f} defined`).toBeTruthy();
      expect(bv, `--${b} defined`).toBeTruthy();
      const r = ratio(fv, bv);
      expect(r, `${theme}: --${f} (${fv}) on --${b} (${bv})`).toBeGreaterThanOrEqual(4.5);
    }
  });
}

// -- chip fills -------------------------------------------------------------
// The state chips render the state COLOUR (glyph + border) on a tinted fill
// of the same hue (--{state}-q = state at 8% over the surface). The tint
// must be subtle enough that the coloured glyph meets non-text contrast
// (3:1) and the ink word/detail on the fill meet text contrast (4.5:1).
// The fills are color-mix() in the token table — recomputed here.

function mix(fg: string, pct: number, bg: string): string {
  const f = rgb(fg);
  const b = rgb(bg);
  const m = f.map((v, i) => Math.round(v * pct + b[i] * (1 - pct)));
  return m.map((v) => v.toString(16).padStart(2, "0")).join("");
}

const CHIP_STATES = ["ok", "warn", "stop"] as const;

/** Resolve a token for a theme — light from the :root defaults, dark from
 * the prefers-color-scheme block. */
function themeToken(theme: "light" | "dark", t: string): string {
  return theme === "light" ? LIGHT[t] : finalValue(t);
}

for (const theme of ["light", "dark"] as const) {
  const surface = theme === "light" ? "FFFFFF" : "1F2427";
  const ink = theme === "light" ? "121618" : "E8EDEE";
  const dimInk = theme === "light" ? "556064" : "8C989C";
  it(`${theme} chip fills keep the glyph and ink readable`, () => {
    for (const state of CHIP_STATES) {
      const stateColor = themeToken(theme, state);
      const fill = mix(stateColor, 0.08, surface);
      // Non-text contrast for the coloured glyph/border (WCAG 1.4.11).
      expect(ratio(stateColor, fill), `${theme} ${state} glyph on fill`)
        .toBeGreaterThanOrEqual(3.0);
      // The word and detail are high-contrast ink on the tint.
      expect(ratio(ink, fill), `${theme} ${state} word on fill`)
        .toBeGreaterThanOrEqual(4.5);
      expect(ratio(dimInk, fill), `${theme} ${state} detail on fill`)
        .toBeGreaterThanOrEqual(4.5);
    }
    // The pressed chip: ink text on the accent fill.
    const accentFill = theme === "light" ? "D7E8E5" : "1B3330";
    expect(ratio(ink, accentFill), `${theme} pressed chip text on accent-q`)
      .toBeGreaterThanOrEqual(4.5);
  });
}


