/** WaveformStrip — the mix's measured peak envelope on canvas. The paint
 * itself needs a real 2D context (Playwright's screenshot spec asserts the
 * pixels); here the contract is: a canvas of the right height, aria-hidden
 * (it is decoration — the mix's numbers live in the evidence rows), and no
 * throw on empty or single-sided peak arrays.
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

import WaveformStrip from "./WaveformStrip.svelte";

const settle = () => new Promise((r) => setTimeout(r, 0));

function mountStrip(peaks: unknown, height?: number) {
  const target = document.createElement("div");
  document.body.appendChild(target);
  const app = mount(WaveformStrip, {
    target,
    props: { peaks: peaks as never, ...(height ? { height } : {}) } as never,
  });
  return { target, app };
}

afterEach(() => {
  document.body.innerHTML = "";
});

it("renders a canvas at the given height, hidden from the accessibility tree", async () => {
  const { target, app } = mountStrip(
    { peaks_per_second: 10, channels: 1, sample_rate: 100, duration_s: 15, min: [-0.2], max: [0.8] },
    26,
  );
  for (let i = 0; i < 3; i += 1) await settle();
  const canvas = target.querySelector("canvas");
  expect(canvas).toBeTruthy();
  expect(canvas!.getAttribute("aria-hidden")).toBe("true");
  expect(canvas!.style.height).toBe("26px");
  unmount(app);
});

it("does not throw on empty peaks or one-sided arrays", async () => {
  const empty = mountStrip({ peaks_per_second: 10, channels: 1, sample_rate: 100, duration_s: 0, min: [], max: [] });
  for (let i = 0; i < 3; i += 1) await settle();
  expect(empty.target.querySelector("canvas")).toBeTruthy();
  unmount(empty.app);

  const oneSided = mountStrip({ peaks_per_second: 10, channels: 1, sample_rate: 100, duration_s: 10, min: [], max: [0.5, 0.9] });
  for (let i = 0; i < 3; i += 1) await settle();
  expect(oneSided.target.querySelector("canvas")).toBeTruthy();
  unmount(oneSided.app);
});
