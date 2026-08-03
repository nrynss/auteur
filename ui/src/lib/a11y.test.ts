/** Accessibility gate (handoff.md task 22) — the automated check CI runs so
 * the polish pass does not decay.
 *
 * Two mechanical rules, asserted against the rendered DOM of the real
 * screens (jsdom mount, like the other DOM tests):
 *
 * 1. axe-core finds no serious violations on any of the four routes —
 *    roles, labels, names, aria wiring, focus. (Colour contrast cannot be
 *    measured in jsdom; that is pinned by contrast.test.ts against the
 *    token table instead.)
 * 2. No native `title=` attribute survives in the rendered DOM. Task 22
 *    moved every substantive help string to the Tooltip component; a title
 *    that reappears is the decay this test exists to catch (the acceptance:
 *    help is available to a keyboard user without a pointer, and no title
 *    carries information available nowhere else).
 * 3. Every interactive control is focusable — nothing may be unreachable by
 *    keyboard.
 *
 * @vitest-environment jsdom
 */
import { afterEach, expect, it, vi } from "vitest";
import { mount, unmount } from "svelte";

vi.hoisted(() => {
  (globalThis as unknown as { matchMedia: unknown }).matchMedia = (
    query: string,
  ) => ({
    matches: false,
    media: query,
    addEventListener: () => {},
    removeEventListener: () => {},
  });
});

import axe from "axe-core";
import HelpOverlay from "./help/HelpOverlay.svelte";
import ConfigScreen from "./config/ConfigScreen.svelte";
import ControlRoom from "./control/ControlRoom.svelte";
import CreateProject from "./projects/CreateProject.svelte";
import ProjectsIndex from "./projects/ProjectsIndex.svelte";
import Unclaimed from "./projects/Unclaimed.svelte";

const PROJECT = {
  id: "proj_A11Y",
  title: "Western standoff (silent)",
  status: "ready",
  created_at: "2026-08-02T10:00:00Z",
  source_filename: "standoff_kling.mp4",
  duration_s: 15.041667,
  budget_usd: 5,
  scene_count: 5,
  event_count: 3,
  readiness_percent: 83,
  readiness: [],
  has_export: false,
  blocks_export: false,
  mix_run_id: null,
  source_url: null,
  mix_url: null,
};

const TIMELINE = {
  version: 1,
  duration: 15.0,
  ambience: "desert wind",
  events: [
    {
      index: 0,
      name: "rhythmic church bell tolling",
      kind: "rhythmic",
      intensity: "med",
      start_time: 6.65,
      duration: 2.0,
      seed: null,
      source_duration_s: null,
      stem_key: null,
      scene_id: "scene_03",
    },
    {
      index: 1,
      name: "close POV single loud revolver gunshot",
      kind: "discrete",
      intensity: "high",
      start_time: 9.88,
      duration: 0.9,
      seed: null,
      source_duration_s: null,
      stem_key: null,
      scene_id: "scene_05",
    },
  ],
};

const CONFIG = {
  ok: true,
  version: "0.1.0",
  brand: "Auteur",
  git_sha: "deadbeefcafebabe0123456789abcdef01234567",
  build_time: "2026-08-02T12:00:00Z",
  checks: [
    { key: "api", label: "API reachable", state: "ok", detail: "status=ok" },
    { key: "storage", label: "storage ok", state: "ok" },
    { key: "worker", label: "worker", state: "ok" },
    { key: "ledger", label: "ledger writable", state: "ok" },
    { key: "quota", label: "quota", state: "ok" },
  ],
  integrations: [
    {
      key: "b2",
      label: "Backblaze B2 storage",
      source: "secret-manager",
      configured: true,
      env: ["B2_APPLICATION_KEY"],
      missing: [],
      unresolved: [],
    },
  ],
};

function jsonResponse(body: unknown, status = 200) {
  return {
    ok: status < 400,
    status,
    json: async () => body,
    text: async () => JSON.stringify(body),
    headers: { get: () => "application/json" },
  } as unknown as Response;
}

afterEach(() => {
  vi.unstubAllGlobals();
  document.body.innerHTML = "";
});

async function mountScreen(component: unknown, props: Record<string, unknown> = {}) {
  const target = document.createElement("div");
  document.body.appendChild(target);
  const app = mount(component as never, {
    target,
    props: props as never,
  });
  // Let the fetch-driven screens settle.
  for (let i = 0; i < 12; i += 1) {
    await new Promise((r) => setTimeout(r, 0));
  }
  return { target, app };
}

function stubFetch() {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string | URL, init?: RequestInit) => {
      const url = String(input);
      const [path] = url.split("?");
      // Project-scoped routes under /api/v1/projects/proj_A11Y.
      if (path.startsWith("/api/v1/projects/proj_A11Y")) {
        const tail = path.replace("/api/v1/projects/proj_A11Y", "");
        if (tail === "") return jsonResponse(PROJECT);
        if (tail === "/timeline") return jsonResponse(TIMELINE);
        if (tail === "/timeline/versions")
          return jsonResponse({ versions: [1], latest: 1 });
        if (tail === "/scenes") return jsonResponse({ scenes: [] });
        if (tail === "/events") return jsonResponse({ events: [] });
        if (tail === "/runs") return jsonResponse({ runs: [] });
        if (tail === "/budget")
          return jsonResponse({ configured_usd: 5, spent_usd: 0, estimate: true, ledger_writable: true });
      }
      // Global routes.
      if (path === "/api/v1/projects")
        return jsonResponse({ projects: [], next_cursor: null });
      if (path === "/api/v1/config") return jsonResponse(CONFIG);
      if (path === "/api/v1/unclaimed/ledger")
        return jsonResponse({ rows: [], total_count: 0, next_cursor: null });
      if (path === "/api/v1/unclaimed/projects")
        return jsonResponse({ projects: [] });
      if (path === "/api/v1/unclaimed/cache")
        return jsonResponse({ rows: [], total_count: 0, next_cursor: null });
      return jsonResponse({ error: { code: "not_found" } }, 404);
    }),
  );
}

const screens: [string, unknown, Record<string, unknown>][] = [
  ["projects index", ProjectsIndex, { onNavigate: () => {} }],
  ["create", CreateProject, { onNavigate: () => {} }],
  ["control room", ControlRoom, { id: "proj_A11Y" }],
  ["unclaimed", Unclaimed, { onNavigate: () => {} }],
  ["config", ConfigScreen, { onNavigate: () => {} }],
  ["help overlay", HelpOverlay, { open: true, onclose: () => {} }],
];

async function runAxe(target: HTMLElement, label: string) {
  const results = await axe.run(target, {
    rules: {
      // Colour contrast needs a layout engine; the token pairs are pinned
      // separately in contrast.test.ts.
      "color-contrast": { enabled: false },
      // jsdom has no viewport; region/landmark checks are not meaningful.
      region: { enabled: false },
    },
  });
  const serious = results.violations.filter((v) =>
    ["serious", "critical"].includes(v.impact ?? ""),
  );
  expect(serious, `${label}: ${JSON.stringify(
    serious.map((v) => v.id),
  )}`).toEqual([]);
}

it("axe finds no serious violations on any route", async () => {
  stubFetch();
  for (const [name, component, props] of screens) {
    const { target, app } = await mountScreen(component, props);
    await runAxe(target, name);
    // The control room's panels are display:none until their tab opens, and
    // axe skips hidden content — click through every tab so each panel is
    // audited in turn.
    if (name === "control room") {
      const tabs = [...target.querySelectorAll<HTMLElement>('[role="tab"]')];
      for (const tab of tabs) {
        tab.click();
        for (let i = 0; i < 4; i += 1) {
          await new Promise((r) => setTimeout(r, 0));
        }
        await runAxe(target, `${name} / ${tab.textContent?.trim()}`);
      }
    }
    unmount(app);
  }
});

it("no native title= attribute survives in any rendered screen", async () => {
  stubFetch();
  for (const [name, component, props] of screens) {
    const { target, app } = await mountScreen(component, props);
    const titles = [...target.querySelectorAll("[title]")];
    unmount(app);
    expect(
      titles.map((el) => el.getAttribute("title")),
      `${name} still carries native title help`,
    ).toEqual([]);
  }
});

it("every interactive control on the control room is keyboard-reachable", async () => {
  stubFetch();
  const { target, app } = await mountScreen(ControlRoom, { id: "proj_A11Y" });
  const controls = [
    ...target.querySelectorAll<HTMLElement>(
      'button:not([disabled]), a[href], input:not([disabled]), [tabindex]:not([tabindex="-1"])',
    ),
  ];
  expect(controls.length).toBeGreaterThan(10);
  for (const el of controls) {
    const tabindex = el.getAttribute("tabindex");
    expect(
      tabindex,
      `element ${el.tagName} unreachable: ${el.outerHTML.slice(0, 120)}`,
    ).not.toBe("-1");
    // No positive tabindex: a positive value would reorder the tab sequence
    // away from the visual/DOM order (task 22's focus-order rule).
    const n = tabindex == null ? 0 : Number(tabindex);
    expect(n, `element ${el.tagName} reorders the tab sequence`).toBeLessThanOrEqual(0);
  }
  unmount(app);
});
