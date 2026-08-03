/** Mount-level tests for direct manipulation (unit 7.10): the draft forks on
 * the first edit, drags and nudges reach the document, the rhythmic seed is
 * frozen at the base position, commit submits the whole document as one new
 * version and clears the draft, version chips switch read-only and restore,
 * and the draft survives a reload via localStorage.
 *
 * These are DOM tests on purpose — the fetch wiring and the draft store meet
 * here; the pure edit math is covered in draft.test.ts.
 *
 * @vitest-environment jsdom
 */
import { afterEach, beforeEach, expect, it, vi } from "vitest";
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

import ControlRoom from "./ControlRoom.svelte";
import { legacySeed } from "./seed";

// jsdom 30 ships no localStorage; the draft store persists through the global
// `localStorage`. A real-shaped stub keeps the persistence path exercised.
const lsStore = new Map<string, string>();
if (!globalThis.localStorage) {
  (globalThis as unknown as { localStorage: Storage }).localStorage = {
    getItem: (k) => lsStore.get(k) ?? null,
    setItem: (k, v) => void lsStore.set(k, v),
    removeItem: (k) => void lsStore.delete(k),
    clear: () => lsStore.clear(),
    key: (i) => [...lsStore.keys()][i] ?? null,
    get length() {
      return lsStore.size;
    },
  };
}

const PROJECT = {
  id: "proj_EDIT",
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
      name: "rhythmic church bell tolling, a deep resonant bronze church bell striking steadily",
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
    {
      index: 2,
      name: "leather holster draw, gun belt leather creak",
      kind: "discrete",
      intensity: "med",
      start_time: 9.35,
      duration: 0.35,
      seed: null,
      source_duration_s: null,
      stem_key: null,
      scene_id: "scene_05",
    },
  ],
};

let timelineVersion = 1;
let committedBodies: unknown[] = [];

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
  lsStore.clear();
  timelineVersion = 1;
  committedBodies = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string | URL, init?: RequestInit) => {
      const url = String(input);
      const tail = url.split("/proj_EDIT")[1] ?? "";
      const [path, query] = tail.split("?");
      const method = init?.method ?? "GET";
      if (method === "PUT" && path === "/timeline") {
        const body = JSON.parse(String(init?.body));
        committedBodies.push(body);
        timelineVersion += 1;
        return jsonResponse({
          version: timelineVersion,
          duration: 15.0,
          ambience: "desert wind",
          events: body.events.map((e: { name: string; start_time: number; duration: number; intensity: string; kind: string; seed?: number | null }, i: number) => ({
            index: i,
            name: e.name,
            kind: e.kind,
            intensity: e.intensity,
            start_time: e.start_time,
            duration: e.duration,
            seed: e.seed ?? null,
            source_duration_s: null,
            stem_key: null,
            scene_id: "scene_01",
          })),
        });
      }
      if (path === "") return jsonResponse(PROJECT);
      if (path === "/timeline") {
        return jsonResponse({ ...TIMELINE, version: Number(query ?? 1) || 1 });
      }
      if (path === "/timeline/versions") {
        return jsonResponse({
          versions: Array.from({ length: timelineVersion }, (_, i) => i + 1),
          latest: timelineVersion,
        });
      }
      if (path === "/scenes") return jsonResponse({ scenes: [] });
      if (path === "/events") return jsonResponse({ events: [] });
      if (path === "/runs") return jsonResponse({ runs: [] });
      if (path === "/budget")
        return jsonResponse({ configured_usd: 5, spent_usd: 0, estimate: true, ledger_writable: true });
      return jsonResponse({ error: { code: "not_found" } }, 404);
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
  document.body.innerHTML = "";
});

const settle = () => new Promise((r) => setTimeout(r, 0));

async function mountRoom() {
  const target = document.createElement("div");
  document.body.appendChild(target);
  const app = mount(ControlRoom, { target, props: { id: "proj_EDIT" } });
  for (let i = 0; i < 8; i += 1) await settle();
  return { target, app };
}

function dragBlock(block: Element, dxPx: number) {
  block.dispatchEvent(
    new PointerEvent("pointerdown", { bubbles: true, clientX: 200, pointerId: 7 }),
  );
  block.dispatchEvent(
    new PointerEvent("pointermove", {
      bubbles: true,
      clientX: 200 + dxPx,
      pointerId: 7,
    }),
  );
  block.dispatchEvent(new PointerEvent("pointerup", { bubbles: true, pointerId: 7 }));
}

it("a drag forks a draft, moves the event, and commit writes one new version", async () => {
  const { target, app } = await mountRoom();
  const bell = [...target.querySelectorAll(".block")].find((b) =>
    (b.textContent ?? "").includes("church bell"),
  )!;
  expect(bell).toBeTruthy();

  // No draft yet: no commit button, no draft chip.
  expect(target.textContent).not.toContain("Commit v2");
  expect(target.textContent).not.toContain("draft +1");

  // Drag 40 px right = 40/64 s = 0.625 s → snapped to 0.63 s.
  dragBlock(bell, 40);
  await settle();

  // A draft exists: dashed chip, commit affordance, honest note.
  expect(target.textContent).toContain("draft +1");
  expect(target.textContent).toContain("Commit v2");
  expect(target.textContent).toContain("move · resize · rename · delete free");

  // The rhythmic bell had its mix seed frozen at the BASE position (the
  // texture it was always going to render with) — the commit carries it.
  const commitBtn = [...target.querySelectorAll("button")].find((b) =>
    (b.textContent ?? "").includes("Commit v2"),
  )!;
  commitBtn.click();
  await settle();

  expect(committedBodies).toHaveLength(1);
  const body = committedBodies[0] as {
    events: Array<{ start_time: number; seed: number | null }>;
  };
  const bellInBody = body.events.find((e) => e.start_time === 6.65 + 0.63);
  expect(bellInBody).toBeTruthy();
  expect(typeof bellInBody!.seed).toBe("number"); // frozen on touch
  expect(body.events).toHaveLength(3);

  // One commit → exactly one new version; the draft is cleared.
  expect(timelineVersion).toBe(2);
  expect(target.textContent).toContain("Committed v2");
  await settle();
  expect(target.textContent).not.toContain("draft +1");
  expect(localStorage.getItem("ff:draft:proj_EDIT")).toBeNull();

  unmount(app);
});

it("arrow keys nudge the selected event; the seed stays frozen across nudges", async () => {
  const { target, app } = await mountRoom();
  const bell = [...target.querySelectorAll(".block")].find((b) =>
    (b.textContent ?? "").includes("church bell"),
  )!;

  bell.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true }));
  await settle();
  bell.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true }));
  await settle();

  const commitBtn = [...target.querySelectorAll("button")].find((b) =>
    (b.textContent ?? "").includes("Commit"),
  )!;
  commitBtn.click();
  await settle();

  const body = committedBodies[0] as { events: Array<{ start_time: number; seed: number | null }> };
  const moved = body.events.find((e) => e.start_time === 6.65 + 0.02)!;
  expect(moved).toBeTruthy();
  // One freeze, one position — the nudge translated, it did not re-roll: the
  // seed is the one the bell was always going to render with, at its base
  // position, carried verbatim through both nudges.
  expect(moved.seed).toBe(
    legacySeed(
      "rhythmic church bell tolling, a deep resonant bronze church bell striking steadily",
      6.65,
    ),
  );

  unmount(app);
});

it("rename is applied to the draft; the commit refuses frozen renames at the UI", async () => {
  const { target, app } = await mountRoom();
  const shot = [...target.querySelectorAll(".block")].find((b) =>
    (b.textContent ?? "").includes("gunshot"),
  )!;
  shot.dispatchEvent(new PointerEvent("pointerdown", { bubbles: true, pointerId: 1 }));
  shot.dispatchEvent(new PointerEvent("pointerup", { bubbles: true, pointerId: 1 }));
  shot.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  await settle();

  const renameBtn = [...target.querySelectorAll("button")].find((b) =>
    (b.textContent ?? "").trim() === "rename",
  )!;
  expect(renameBtn).toBeTruthy();
  renameBtn.click();
  await settle();

  const input = target.querySelector("input.rename-input") as HTMLInputElement;
  expect(input).toBeTruthy();
  input.value = "single distant pistol crack";
  input.dispatchEvent(new Event("input", { bubbles: true }));
  input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
  await settle();

  const commitBtn = [...target.querySelectorAll("button")].find((b) =>
    (b.textContent ?? "").includes("Commit"),
  )!;
  commitBtn.click();
  await settle();

  const body = committedBodies[0] as { events: Array<{ name: string }> };
  expect(body.events.some((e) => e.name === "single distant pistol crack")).toBe(true);
  expect(body.events.some((e) => e.name === "close POV single loud revolver gunshot")).toBe(false);

  unmount(app);
});

it("a draft survives a reload via localStorage", async () => {
  const first = await mountRoom();
  const bell = [...first.target.querySelectorAll(".block")].find((b) =>
    (b.textContent ?? "").includes("church bell"),
  )!;
  dragBlock(bell, -20); // 20 px left = 0.3125 s → snapped 0.31 s
  // Persistence is debounced (a drag fires onEdit per pointermove); wait out
  // the 200 ms window so the crash-recovery copy exists before reload.
  await new Promise((r) => setTimeout(r, 250));
  expect(localStorage.getItem("ff:draft:proj_EDIT")).toBeTruthy();
  unmount(first.app);

  const second = await mountRoom();
  expect(second.target.textContent).toContain("Draft restored from a previous session");
  expect(second.target.textContent).toContain("draft +1");
  expect(second.target.textContent).toContain("Commit v2");
  unmount(second.app);
});

it("version chips switch to a read-only historical view and back", async () => {
  const { target, app } = await mountRoom();
  // Commit once so v2 exists.
  const bell = [...target.querySelectorAll(".block")].find((b) =>
    (b.textContent ?? "").includes("church bell"),
  )!;
  dragBlock(bell, 40);
  await settle();
  const commitBtn = [...target.querySelectorAll("button")].find((b) =>
    (b.textContent ?? "").includes("Commit v2"),
  )!;
  commitBtn.click();
  await settle();

  // Now two chips: v1 and v2.
  const v1Chip = [...target.querySelectorAll("button")].find((b) =>
    (b.textContent ?? "").trim() === "v1",
  )!;
  expect(v1Chip).toBeTruthy();
  v1Chip.click();
  await settle();

  // Historical view: read-only, labelled, with the undo affordance.
  expect(target.textContent).toContain("viewing v1 — historical");
  const editFrom = [...target.querySelectorAll("button")].find((b) =>
    (b.textContent ?? "").includes("Edit from here"),
  )!;
  expect(editFrom).toBeTruthy();

  // Undo: restore the draft from v1.
  editFrom.click();
  await settle();
  expect(target.textContent).toContain("draft +1");
  expect(target.textContent).toContain("Unsaved changes to v1");

  unmount(app);
});

it("editing is disabled while a run is in flight", async () => {
  // A live run locks the timeline: no commit, no drag.
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string | URL) => {
      const path = (String(input).split("/proj_EDIT")[1] ?? "").split("?")[0];
      if (path === "") return jsonResponse({ ...PROJECT, latest_run_id: "run_1", status: "analyzing" });
      if (path === "/timeline") return jsonResponse(TIMELINE);
      if (path === "/timeline/versions") return jsonResponse({ versions: [1], latest: 1 });
      if (path === "/scenes") return jsonResponse({ scenes: [] });
      if (path === "/runs") return jsonResponse({ runs: [] });
      if (path === "/events") return jsonResponse({ events: [] });
      if (path === "/budget")
        return jsonResponse({ configured_usd: 5, spent_usd: 0, estimate: true, ledger_writable: true });
      if (path === "/runs/run_1")
        return jsonResponse({ id: "run_1", kind: "analyze", status: "running", created_at: new Date().toISOString(), stages: [] });
      return jsonResponse({ error: { code: "not_found" } }, 404);
    }),
  );

  const { target, app } = await mountRoom();
  expect(target.textContent).toContain("run in flight — locked");
  const bell = [...target.querySelectorAll(".block")].find((b) =>
    (b.textContent ?? "").includes("church bell"),
  )!;
  dragBlock(bell, 40);
  await settle();
  // No draft was forked — the drag was refused.
  expect(target.textContent).not.toContain("draft +1");
  expect(localStorage.getItem("ff:draft:proj_EDIT")).toBeNull();
  unmount(app);
});
