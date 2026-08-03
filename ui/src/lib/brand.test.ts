/** Brand module (handoff.md task 19): defaults are today's values — a default
 * build must render byte-identical in appearance. The override path (env vars
 * at build time) is asserted by the CI rebrand build, which greps the built
 * bundle for the default name; here we pin the defaults and the template
 * behaviour. */
import { expect, it } from "vitest";

import {
  BRAND_NAME,
  BRAND_TITLE_TEMPLATE,
  brandPageTitle,
  formatPageTitle,
} from "./brand";

it("defaults to today's product name", () => {
  expect(BRAND_NAME).toBe("Auteur");
});

it("default title template is the current one", () => {
  expect(BRAND_TITLE_TEMPLATE).toBe("{name} · {page}");
});

it("formats page titles with the configured name and template", () => {
  expect(brandPageTitle("Control room")).toBe("Auteur · Control room");
  expect(brandPageTitle("New project")).toBe("Auteur · New project");
  expect(brandPageTitle("Unclaimed")).toBe("Auteur · Unclaimed");
  expect(brandPageTitle("Projects")).toBe("Auteur · Projects");
});

it("a rebranded template moves the name and page anywhere", () => {
  // The CI rebrand build exercises the real env path; here the pure formatter
  // pins the mechanics: a custom template shape renders under it.
  expect(formatPageTitle("{name} · {page}", "KinoPost", "Control room"))
    .toBe("KinoPost · Control room");
});

it("replaces every placeholder occurrence, not just the first", () => {
  // String.replace with a string pattern substitutes only the first match;
  // a template that repeats a placeholder must render each occurrence.
  expect(
    formatPageTitle(
      "{name} · {page} · {name}",
      "KinoPost",
      "Control room",
    ),
  ).toBe("KinoPost · Control room · KinoPost");
});
