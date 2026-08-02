/** Mount-level test for the control room.
 *
 * This is the layer that was missing. 68 unit tests passed and svelte-check
 * was clean while the screen was completely non-functional in the browser: a
 * generic arrow in the component's own script (`quiet`) had its parameter list
 * dropped by Svelte's TS strip, so every ancillary fetch threw
 * `ReferenceError: p is not defined` and every panel rendered its empty state
 * against a project that had a five-scene index and a four-event timeline.
 *
 * Nothing below asserts a string in isolation — the point is that real API
 * payloads reach the screen. A regression in the fetch path fails here even
 * when the source still type-checks.
 *
 * @vitest-environment jsdom
 */
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { mount, unmount } from "svelte";

// jsdom has no matchMedia, and theme.ts subscribes to it at IMPORT time (so
// an OS theme flip repaints the canvases). ES imports hoist above ordinary
// statements, so the stub has to run in vi.hoisted or the module tree throws
// before any test body executes.
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

import ControlRoom from "./ControlRoom.svelte";

const PROJECT = {
  id: "proj_TEST",
  title: "Western standoff (silent)",
  status: "ready",
  created_at: "2026-08-02T10:00:00Z",
  source_filename: "standoff_kling.mp4",
  duration_s: 15.041667,
  budget_usd: 5,
  scene_count: 5,
  event_count: 4,
  readiness_percent: 83,
  readiness: [],
  has_export: false,
  blocks_export: false,
  mix_run_id: null,
  source_url: null,
  mix_url: null,
};

const SCENES = {
  scenes: [0, 1, 2, 3, 4].map((i) => ({
    id: `scene_0${i + 1}`,
    index: i + 1,
    start_s: i * 3,
    end_s: i * 3 + 3,
    duration_s: 3,
    stats: { mean_luma: 100 + i },
    thumbnail_url: null,
  })),
};

const TIMELINE = {
  version: 1,
  duration: 15.041667,
  ambience: "desert wind blowing through a quiet western town",
  events: [
    { index: 0, name: "leather holster creaking", kind: "continuous", intensity: "low", start_time: 0, duration: 3.5, seed: null, source_duration_s: null, stem_key: null, scene_id: "scene_01" },
    { index: 1, name: "leather holster creaking", kind: "continuous", intensity: "low", start_time: 4, duration: 2.5, seed: null, source_duration_s: null, stem_key: null, scene_id: "scene_02" },
    { index: 2, name: "bronze church bell ringing", kind: "discrete", intensity: "med", start_time: 7, duration: 1.2, seed: null, source_duration_s: null, stem_key: null, scene_id: "scene_03" },
    { index: 3, name: "gunshot blast", kind: "discrete", intensity: "high", start_time: 11, duration: 1.5, seed: null, source_duration_s: null, stem_key: null, scene_id: "scene_05" },
  ],
};

const ROUTES: Record<string, unknown> = {
  "": PROJECT,
  "/scenes": SCENES,
  "/timeline": TIMELINE,
  "/timeline/versions": { versions: [1], latest: 1 },
  "/events": { events: [] },
  "/runs": { runs: [] },
  "/budget": { configured_usd: 5, spent_usd: 0.01, remaining_usd: 4.99, estimate: true, ledger_writable: true },
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

beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string | URL) => {
      const url = String(input);
      const tail = url.split("/proj_TEST")[1] ?? "";
      const path = tail.split("?")[0];
      if (path in ROUTES) return jsonResponse(ROUTES[path]);
      // Everything else genuinely has not produced yet.
      return jsonResponse({ error: { code: "not_found" } }, 404);
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
  document.body.innerHTML = "";
});

const settle = () => new Promise((r) => setTimeout(r, 0));

it("renders real API payloads — every event and scene reaches the screen", async () => {
  const target = document.createElement("div");
  document.body.appendChild(target);
  const app = mount(ControlRoom, { target, props: { id: "proj_TEST" } });
  for (let i = 0; i < 8; i += 1) await settle();

  const text = target.textContent ?? "";

  // The bug's signature: panels showing "has not run" against a project that
  // plainly has both.
  expect(text).not.toContain("No timeline yet");
  expect(text).not.toContain("No scenes yet");
  // And the failure banner must be absent — nothing here failed.
  expect(text).not.toContain("failed to load");

  // Real content, not just the absence of empty states.
  expect(text).toContain("Western standoff (silent)");
  expect(text).toContain("gunshot blast");
  expect(text).toContain("bronze church bell ringing");
  // Four events land as four blocks on three lanes (two continuous, two
  // discrete → "Beds" and "One-shots"); five scenes as five thumbs.
  expect(target.querySelectorAll(".block").length).toBe(4);
  expect(target.querySelectorAll(".lane-row").length).toBe(2);
  expect(target.querySelectorAll("button.scene").length).toBe(5);

  unmount(app);
});

it("a failed ancillary fetch says failure — never 'analysis has not run'", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string | URL) => {
      const path = (String(input).split("/proj_TEST")[1] ?? "").split("?")[0];
      if (path === "") return jsonResponse(PROJECT);
      if (path === "/timeline" || path === "/scenes")
        return jsonResponse({ error: { code: "storage_unavailable" } }, 500);
      return jsonResponse({ error: { code: "not_found" } }, 404);
    }),
  );

  const target = document.createElement("div");
  document.body.appendChild(target);
  const app = mount(ControlRoom, { target, props: { id: "proj_TEST" } });
  for (let i = 0; i < 8; i += 1) await settle();

  const text = target.textContent ?? "";
  expect(text).toContain("failed to load");
  // The empty panels must not assert a cause they cannot know.
  expect(text).not.toContain("analysis has not run");
  expect(text).toContain("could not be loaded");

  unmount(app);
});
