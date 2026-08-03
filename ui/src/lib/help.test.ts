/** Help system (handoff.md task 22) — three invariants:
 *
 * 1. The glossary is the single source: every Term usage in the UI resolves
 *    to an entry, and every entry is actually used (a defined term never
 *    rendered is dead content; a rendered dense term with no entry is a
 *    bug — "no rendered term is undefined").
 * 2. The keyboard map matches the bindings: the map's keys and the step
 *    constants the handlers actually use cannot drift by hand.
 * 3. The tooltip/term/overlay behaviours: focus-open, aria-describedby,
 *    Escape-close, and the overlay rendering every binding and term.
 *
 * @vitest-environment jsdom
 */
import { afterEach, expect, it } from "vitest";
import { mount, unmount } from "svelte";
import { readFileSync, readdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

import { BINDINGS } from "./help/bindings";
import { GLOSSARY, termDef } from "./help/glossary";
import HelpOverlay from "./help/HelpOverlay.svelte";
import Term from "./help/Term.svelte";

const here = join(dirname(fileURLToPath(import.meta.url)), "..", "lib");

// -- glossary coverage -------------------------------------------------------

it("every glossary entry is used by a Term or rendered somewhere", () => {
  // Scan only the TEMPLATE region of each component (`</script>` → `<style>`)
  // — a term that appears only in a script or a comment is not rendered, and
  // must not count. Craft terms (stem, mix, transient, envelope) are exempt:
  // they are completeness entries in the `?` overlay for vocabulary the
  // audience knows, whether or not the word is drawn on screen.
  const sources = readdirSync(here, { recursive: true })
    .filter((f): f is string => typeof f === "string" && f.endsWith(".svelte"))
    .map((f) => {
      const src = readFileSync(join(here, f), "utf-8");
      const scriptEnd = src.indexOf("</script>");
      const styleStart = src.indexOf("<style");
      const start = scriptEnd === -1 ? 0 : scriptEnd + 9;
      const end = styleStart === -1 ? src.length : styleStart;
      return src.slice(start, end);
    })
    .join("\n");
  for (const [id, t] of Object.entries(GLOSSARY)) {
    if (t.craft) continue;
    const usedAsTerm = sources.includes(`term="${id}"`);
    const labelRendered = new RegExp(`\\b${t.label}\\b`, "i").test(sources);
    expect(
      usedAsTerm || labelRendered,
      `glossary entry ${id} ("${t.label}") is never rendered`,
    ).toBe(true);
  }
});

it("every Term usage resolves to a glossary entry", () => {
  const sources = readdirSync(here, { recursive: true })
    .filter((f): f is string => typeof f === "string" && f.endsWith(".svelte"))
    .map((f) => readFileSync(join(here, f), "utf-8"))
    .join("\n");
  for (const m of sources.matchAll(/<Term\s+term="([^"]+)"/g)) {
    expect(termDef(m[1]), `Term "${m[1]}" is undefined in the glossary`).not.toBeNull();
  }
});

// -- bindings parity ---------------------------------------------------------

it("the map matches the step constants and keys the handlers use", () => {
  const draft = readFileSync(join(here, "control", "draft.ts"), "utf-8");
  expect(draft).toContain("NUDGE_S = 0.01");
  expect(draft).toContain("NUDGE_COARSE_S = 0.1");
  expect(draft).toContain("NUDGE_FINE_S = 0.001");

  const timeline = readFileSync(join(here, "control", "Timeline.svelte"), "utf-8");
  const player = readFileSync(join(here, "control", "Player.svelte"), "utf-8");
  const dialog = readFileSync(join(here, "components", "ConfirmDialog.svelte"), "utf-8");

  // The map's claims, checked against the implementations.
  const map = BINDINGS.map((b) => b.id).join(",");
  expect(map).toContain("timeline-nudge");
  expect(timeline).toContain('e.key !== "ArrowLeft"');
  expect(timeline).toContain('e.key !== "ArrowRight"');
  expect(map).toContain("timeline-grip");
  expect(timeline).toContain('e.key === "ArrowUp"');
  expect(map).toContain("rename-enter");
  expect(timeline).toContain('e.key === "Enter"');
  expect(map).toContain("rename-escape");
  expect(timeline).toContain('e.key === "Escape"');
  expect(map).toContain("dialog-escape");
  expect(dialog).toContain('e.key === "Escape"');
  expect(map).toContain("player-toggle");
  expect(player).toContain('e.key === "k"');
  expect(map).toContain("player-seek");
  expect(player).toContain('e.key === "ArrowRight"');
  expect(map).toContain("help-overlay");

  // `?` must be wired in App.
  const app = readFileSync(join(here, "..", "App.svelte"), "utf-8");
  expect(app).toContain('e.key !== "?"');
  expect(app).toContain("openHelp");
});

// -- behaviours --------------------------------------------------------------

afterEach(() => {
  document.body.innerHTML = "";
});

function mountTo(component: unknown, props: Record<string, unknown> = {}) {
  const target = document.createElement("div");
  document.body.appendChild(target);
  const app = mount(component as never, { target, props: props as never });
  return { target, app };
}

it("Tooltip opens on focus, wires aria-describedby, closes on Escape", async () => {
  // Through a real usage: Term renders a focusable glossary button wrapped in
  // the tooltip, so this exercises the action's aria wiring end to end.
  const { target, app } = mountTo(Term, { term: "transient" });
  // Let the mount effect wire the tooltip action before exercising it.
  await new Promise((r) => setTimeout(r, 0));
  const btn = target.querySelector("button.term") as HTMLButtonElement;
  btn.focus();
  // jsdom's focus() fires focusin on the element but does not bubble it to
  // ancestors (real browsers do); dispatch on the anchor to exercise the
  // action's wiring.
  const anchor = target.querySelector(".tt-anchor") as HTMLElement;
  anchor.dispatchEvent(new FocusEvent("focusin", { bubbles: true }));
  await new Promise((r) => setTimeout(r, 0));
  // The tip is PORTALED to <body> — it must never render inside the anchor's
  // container, where an overflow or transformed ancestor could clip it
  // (the clipped-help-tip class of bugs).
  const tip = document.body.querySelector('[role="tooltip"]');
  expect(tip).toBeTruthy();
  expect(target.querySelector('[role="tooltip"]')).toBeNull();
  expect(tip?.textContent).toContain("onset of a one-shot sound");
  expect(btn.getAttribute("aria-describedby")).toBe(tip?.id);
  // Escape dismisses.
  btn.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
  await new Promise((r) => setTimeout(r, 0));
  expect(document.body.querySelector('[role="tooltip"]')).toBeNull();
  unmount(app);
});

it("Term renders a defined term as a labelled button", () => {
  const { target, app } = mountTo(Term, { term: "transient" });
  const btn = target.querySelector("button.term");
  expect(btn).toBeTruthy();
  expect(btn?.textContent).toBe("transient");
  expect(btn?.getAttribute("aria-label")).toContain("the onset of a one-shot sound");
  unmount(app);
});

it("HelpOverlay moves focus inside on open (the Escape bug's pin)", async () => {
  const { target, app } = mountTo(HelpOverlay, { open: true, onclose: () => {} });
  // The dialog focuses itself via a setTimeout(0) after mount — two ticks.
  await new Promise((r) => setTimeout(r, 0));
  await new Promise((r) => setTimeout(r, 0));
  const close = target.querySelector("button.close") as HTMLButtonElement;
  expect(close).toBeTruthy();
  // Focus must land inside the dialog, so Escape (a dialog key) actually
  // reaches the dialog's handler — the browser gate caught this when the
  // overlay opened but never autofocused.
  expect(document.activeElement).toBe(close);
  unmount(app);
});

it("an undefined Term renders plainly without crashing", () => {
  const { target, app } = mountTo(Term, { term: "nonexistent-term" });
  expect(target.textContent).toBe("nonexistent-term");
  unmount(app);
});

it("HelpOverlay renders every binding and every glossary term", () => {
  const { target, app } = mountTo(HelpOverlay, { open: true, onclose: () => {} });
  for (const b of BINDINGS) {
    expect(target.textContent).toContain(b.action);
  }
  for (const t of Object.values(GLOSSARY)) {
    expect(target.textContent).toContain(t.label);
  }
  // The map and the glossary share the surface.
  expect(target.textContent).toContain("Keyboard map");
  expect(target.textContent).toContain("Glossary");
  unmount(app);
});
