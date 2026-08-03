/** EnvelopeCanvas — a timeline event block's structural envelope. It is
 * decoration with a determinism contract: the same (kind, seed) must paint
 * the same shape, so a renumbering or a re-render never redraws neighbours.
 * jsdom has no 2D context (the pixel assertion lives in the Playwright
 * screenshot spec), so here: the canvas renders and the module's paint path
 * does not throw across every kind.
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

import EnvelopeCanvas from "./EnvelopeCanvas.svelte";

const settle = () => new Promise((r) => setTimeout(r, 0));

async function mountEnv(kind: string, seed: number) {
  const target = document.createElement("div");
  document.body.appendChild(target);
  const app = mount(EnvelopeCanvas, { target, props: { kind, seed } });
  for (let i = 0; i < 3; i += 1) await settle();
  return { target, app };
}

afterEach(() => {
  document.body.innerHTML = "";
});

it("renders a canvas for every envelope kind without throwing", async () => {
  for (const kind of ["discrete", "rhythmic", "continuous"]) {
    const { target, app } = await mountEnv(kind, 123);
    const canvas = target.querySelector("canvas");
    expect(canvas, kind).toBeTruthy();
    expect(canvas!.getAttribute("aria-hidden")).toBe("true");
    unmount(app);
  }
});

it("seed zero (a valid hash) does not degenerate the rng", async () => {
  const { target, app } = await mountEnv("continuous", 0);
  expect(target.querySelector("canvas")).toBeTruthy();
  unmount(app);
});
