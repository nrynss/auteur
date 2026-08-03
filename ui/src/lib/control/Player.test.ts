/** Player (unit 7.9) — the A/B transport's rendering contract. jsdom cannot
 * decode audio, so the preview engine's stems all fail here: that is
 * exactly the branch under test — a stem that fails to load is counted and
 * named, never substituted. Also pins the no-source fallback (a project
 * without the original video must not present a dead Original button as the
 * active view). */
/**
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

import Player from "./Player.svelte";

const EVENTS: Array<{
  index: number;
  name: string;
  kind: string;
  intensity: string;
  start_time: number;
  duration: number;
  seed: number | null;
  source_duration_s: number | null;
  stem_key: string | null;
  scene_id: string;
}> = [
  {
    index: 0,
    name: "leather holster creaking",
    kind: "continuous",
    intensity: "low",
    start_time: 0,
    duration: 3.5,
    seed: null,
    source_duration_s: null,
    stem_key: "cache/leather.wav",
    scene_id: "scene_01",
  },
  {
    index: 1,
    name: "gunshot blast",
    kind: "discrete",
    intensity: "high",
    start_time: 11,
    duration: 1.5,
    seed: null,
    source_duration_s: null,
    stem_key: null, // no stamped address — omitted, never guessed
    scene_id: "scene_05",
  },
];

function mountPlayer(props: Record<string, unknown>) {
  const target = document.createElement("div");
  document.body.appendChild(target);
  const app = mount(Player, { target, props: props as never });
  return { target, app };
}

const settle = () => new Promise((r) => setTimeout(r, 0));

afterEach(() => {
  vi.unstubAllGlobals();
  document.body.innerHTML = "";
});

it("renders one transport with Original / Foley mix / Preview and the keyboard hint", () => {
  vi.stubGlobal("fetch", vi.fn());
  const { target, app } = mountPlayer({
    id: "proj_TEST",
    sourceUrl: "/src.mp4",
    mixUrl: "/mix.mp4",
    events: EVENTS,
  });
  const text = target.textContent ?? "";
  expect(text).toContain("Original");
  expect(text).toContain("Foley mix");
  expect(text).toContain("Preview");
  expect(text).toContain("K play · ← → seek");
  expect(target.querySelector("video[aria-label='Original source video']")).toBeTruthy();
  expect(target.querySelector("video[aria-label='Foley mix video']")).toBeTruthy();
  // The preview mode is a labelled approximation — it says so up front.
  unmount(app);
});

it("a project without the original video falls back to the mix without lying about the pressed button", () => {
  vi.stubGlobal("fetch", vi.fn());
  const { target, app } = mountPlayer({
    id: "proj_TEST",
    sourceUrl: null,
    mixUrl: "/mix.mp4",
    events: EVENTS,
  });
  const text = target.textContent ?? "";
  // Original is disabled (there is no original) and NOT pressed; the mix is.
  const seg = target.querySelector(".seg")!;
  const buttons = [...seg.querySelectorAll("button")];
  const [original, mix, preview] = buttons;
  expect((original as HTMLButtonElement).disabled).toBe(true);
  expect(original.getAttribute("aria-pressed")).toBe("false");
  expect(mix.getAttribute("aria-pressed")).toBe("true");
  expect(preview.getAttribute("aria-pressed")).toBe("false");
  expect(text).toContain("Foley mix");
  unmount(app);
});

it("Preview is disabled when no event carries a stamped stem key", () => {
  vi.stubGlobal("fetch", vi.fn());
  const noStems = EVENTS.map((e) => ({ ...e, stem_key: null }));
  const { target, app } = mountPlayer({
    id: "proj_TEST",
    sourceUrl: "/src.mp4",
    mixUrl: "/mix.mp4",
    events: noStems,
  });
  const preview = [...target.querySelectorAll(".seg button")].at(-1) as HTMLButtonElement;
  expect(preview.disabled).toBe(true);
  unmount(app);
});

it("a stem that fails to load is counted and named — never substituted by silence", async () => {
  // The preview engine fetches each stamped stem; every one fails in this
  // harness. The player must say how many were unavailable rather than let
  // the preview read as complete.
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({ ok: false, status: 500 }) as Response),
  );
  const { target, app } = mountPlayer({
    id: "proj_TEST",
    sourceUrl: "/src.mp4",
    mixUrl: null,
    events: EVENTS,
  });
  // Svelte 5 flushes bind:this asynchronously under jsdom; a real tick
  // guarantees origEl is bound before the click reaches switchTo.
  await new Promise((r) => setTimeout(r, 50));
  const preview = [...target.querySelectorAll(".seg button")].at(-1) as HTMLButtonElement;
  expect(preview.disabled).toBe(false);
  preview.click();
  for (let i = 0; i < 6; i += 1) await settle();

  const text = target.textContent ?? "";
  expect(text).toContain("Browser preview: not the measured mix");
  expect(text).toContain("1 stem unavailable");
  unmount(app);
});
