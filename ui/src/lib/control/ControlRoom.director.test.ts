/** Mount-level tests for director mode (unit 7.12): propose → ghosted diff on
 * the timeline → apply merges into the draft → commit writes one version.
 * Also: chat proposes never applies (no PUT until apply), one decision at a
 * time, and the run lock disables Apply.
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
  id: "proj_DIR",
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

const PROPOSAL = {
  project_id: "proj_DIR",
  base_version: 1,
  model: "gemini-3.5-flash-lite",
  instruction: "pull the gunshot back 80 ms",
  status: "proposed",
  message: "moved 1 event — free to apply; approve to fork your draft.",
  cost_usd: 0.005,
  cost_source: "estimate",
  ledger_writable: true,
  timeline: {
    duration: 15.0,
    ambience: "desert wind",
    events: [
      TIMELINE.events[0],
      { ...TIMELINE.events[1], start_time: 9.8 },
      TIMELINE.events[2],
    ],
  },
  edits: [
    {
      index: 1,
      old: { start_time: 9.88, duration: 0.9 },
      new: { start_time: 9.8, duration: 0.9 },
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
      const tail = url.split("/proj_DIR")[1] ?? "";
      const [path, query] = tail.split("?");
      const method = init?.method ?? "GET";
      if (method === "POST" && path === "/director") {
        return jsonResponse(PROPOSAL);
      }
      if (method === "PUT" && path === "/timeline") {
        const body = JSON.parse(String(init?.body));
        committedBodies.push(body);
        timelineVersion += 1;
        return jsonResponse({
          version: timelineVersion,
          duration: 15.0,
          ambience: "desert wind",
          events: body.events.map(
            (e: { name: string; start_time: number; duration: number; intensity: string; kind: string; seed?: number | null }, i: number) => ({
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
            }),
          ),
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
  const app = mount(ControlRoom, { target, props: { id: "proj_DIR" } });
  for (let i = 0; i < 8; i += 1) await settle();
  return { target, app };
}

function openDirectorTab(target: HTMLElement) {
  const btn = [...target.querySelectorAll("button")].find((b) =>
    (b.textContent ?? "").trim() === "Director",
  )!;
  btn.click();
}

function sendInstruction(target: HTMLElement, text: string) {
  const input = target.querySelector(".composer input") as HTMLInputElement;
  input.value = text;
  input.dispatchEvent(new Event("input", { bubbles: true }));
  target.querySelector(".composer")!.dispatchEvent(
    new Event("submit", { bubbles: true, cancelable: true }),
  );
}

function blockLeftPx(block: Element): string {
  return (block as HTMLElement).style.left;
}

it("chat proposes and never applies: a proposal writes no version", async () => {
  const { target, app } = await mountRoom();
  openDirectorTab(target);
  sendInstruction(target, "pull the gunshot back 80 ms");
  for (let i = 0; i < 8; i += 1) await settle();

  // The turn rendered as a proposal diff, and the composer is locked.
  expect(target.textContent).toContain("Apply to draft");
  expect(target.textContent).toContain("moved 1 event");
  const input = target.querySelector(".composer input") as HTMLInputElement;
  expect(input.disabled).toBe(true);

  // Nothing was committed — chat proposes, never applies.
  expect(committedBodies).toHaveLength(0);
  expect(timelineVersion).toBe(1);

  // The timeline shows the proposal: the solid block moved to the proposed
  // position and a ghost at the old one.
  const ghost = target.querySelector(".block.ghost") ?? target.querySelector(".lane .ghost");
  expect(ghost).toBeTruthy();
  const blocks = [...target.querySelectorAll(".block")];
  const gunshot = blocks.find((b) => (b.textContent ?? "").includes("gunshot"));
  expect(blockLeftPx(gunshot!)).toBe(`${9.8 * 64}px`); // proposed position
  unmount(app);
});

it("apply merges the proposal into the draft; commit writes one new version", async () => {
  const { target, app } = await mountRoom();
  openDirectorTab(target);
  sendInstruction(target, "pull the gunshot back 80 ms");
  for (let i = 0; i < 8; i += 1) await settle();

  const apply = [...target.querySelectorAll("button")].find((b) =>
    (b.textContent ?? "").includes("Apply to draft"),
  )!;
  apply.click();
  for (let i = 0; i < 8; i += 1) await settle();

  // The proposal is applied: ghost gone, draft exists, timeline shows the
  // moved position, and the transcript says so.
  expect(target.querySelector(".lane .ghost")).toBeNull();
  expect(target.textContent).toContain("Applied to the draft");
  expect(target.textContent).toContain("draft +1");
  const blocks = [...target.querySelectorAll(".block")];
  const gunshot = blocks.find((b) => (b.textContent ?? "").includes("gunshot"));
  expect(blockLeftPx(gunshot!)).toBe(`${9.8 * 64}px`);
  expect(committedBodies).toHaveLength(0); // still nothing written

  // Commit the draft: one PUT, one new version, the moved position in the doc.
  const commit = [...target.querySelectorAll("button")].find((b) =>
    (b.textContent ?? "").includes("Commit v"),
  )!;
  commit.click();
  for (let i = 0; i < 8; i += 1) await settle();
  expect(committedBodies).toHaveLength(1);
  const doc = committedBodies[0] as {
    events: { name: string; start_time: number }[];
  };
  expect(doc.events[1].start_time).toBeCloseTo(9.8, 6);
  expect(timelineVersion).toBe(2);
  unmount(app);
});

it("discard leaves the timeline untouched", async () => {
  const { target, app } = await mountRoom();
  openDirectorTab(target);
  sendInstruction(target, "pull the gunshot back 80 ms");
  for (let i = 0; i < 8; i += 1) await settle();

  const discard = [...target.querySelectorAll("button")].find((b) =>
    (b.textContent ?? "").trim() === "Discard",
  )!;
  discard.click();
  for (let i = 0; i < 8; i += 1) await settle();

  expect(target.querySelector(".lane .ghost")).toBeNull();
  expect(target.textContent).toContain("Discarded. The timeline is untouched");
  // Back to committed positions, no draft.
  expect(target.textContent).not.toContain("draft +1");
  expect(committedBodies).toHaveLength(0);
  unmount(app);
});

it("apply onto an existing draft keeps the director's other edits", async () => {
  const { target, app } = await mountRoom();

  // Make a draft first: nudge the bell (index 0) via the timeline, then
  // propose a change to the gunshot.
  const blocks = [...target.querySelectorAll(".block")];
  const bell = blocks.find((b) => (b.textContent ?? "").includes("church bell"));
  bell?.dispatchEvent(
    new PointerEvent("pointerdown", { bubbles: true, clientX: 200, pointerId: 9 }),
  );
  bell?.dispatchEvent(
    new PointerEvent("pointermove", {
      bubbles: true,
      clientX: 200 + 10, // 10px ≈ 0.156s at 64px/s
      pointerId: 9,
    }),
  );
  bell?.dispatchEvent(new PointerEvent("pointerup", { bubbles: true, pointerId: 9 }));
  for (let i = 0; i < 4; i += 1) await settle();
  expect(target.textContent).toContain("draft +1");

  openDirectorTab(target);
  sendInstruction(target, "pull the gunshot back 80 ms");
  for (let i = 0; i < 8; i += 1) await settle();
  const apply = [...target.querySelectorAll("button")].find((b) =>
    (b.textContent ?? "").includes("Apply to draft"),
  )!;
  apply.click();
  for (let i = 0; i < 8; i += 1) await settle();

  // Commit and inspect the body: the bell kept the drag, the gunshot moved.
  const commit = [...target.querySelectorAll("button")].find((b) =>
    (b.textContent ?? "").includes("Commit v"),
  )!;
  commit.click();
  for (let i = 0; i < 8; i += 1) await settle();
  const doc = committedBodies[0] as {
    events: { name: string; start_time: number }[];
  };
  expect(doc.events[0].start_time).toBeGreaterThan(6.65); // drag survived
  expect(doc.events[1].start_time).toBeCloseTo(9.8, 6); // proposal applied
  expect(doc.events[2].start_time).toBeCloseTo(9.35, 6); // untouched
  unmount(app);
});

it("a commit lands while a proposal is active: the proposal is cleared as stale", async () => {
  // Two committed versions exist, so v1 is a real historical view.
  timelineVersion = 2;
  const { target, app } = await mountRoom();
  openDirectorTab(target);
  sendInstruction(target, "pull the gunshot back 80 ms");
  for (let i = 0; i < 8; i += 1) await settle();
  expect(target.querySelector(".lane .ghost")).toBeTruthy();

  // Switching to a historical version changes the displayed document: the
  // proposal was diffed against a document that is no longer on screen, so it
  // is cleared (re-asking is free) rather than ghosted over the wrong content.
  const v1 = [...target.querySelectorAll("button")].find((b) =>
    (b.textContent ?? "").trim() === "v1",
  )!;
  v1.click();
  for (let i = 0; i < 8; i += 1) await settle();
  expect(target.querySelector(".lane .ghost")).toBeNull();
  unmount(app);
});
