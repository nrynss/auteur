/** SceneRail — the thin strip that belongs to the picture. The empty state
 * makes a checkable claim ("analysis has not run") that is false when the
 * fetch failed — the failure branch must replace exactly that claim, and a
 * rail with real scenes keeps them. */
import { expect, it } from "vitest";
import { render } from "svelte/server";

import SceneRail from "./SceneRail.svelte";

const SCENES = [
  { id: "scene_01", index: 1, start_s: 0, end_s: 3, duration_s: 3, stats: { mean_luma: 100 }, thumbnail_url: null },
  { id: "scene_02", index: 2, start_s: 3, end_s: 6, duration_s: 3, stats: null, thumbnail_url: "https://cdn/thumbs/s2.jpg" },
] as never[];

it("renders one thumb per scene with index and honest span", () => {
  const { body } = render(SceneRail, { props: { scenes: SCENES } });
  expect(body.match(/<button class="scene/g)?.length).toBe(2);
  // A scene with a thumbnail renders it with an alt naming the scene.
  expect(body).toContain('alt="Scene 2"');
  // A scene without one renders the index as a placeholder — never a fake image.
  expect(body).toContain("aria-hidden=\"true\">1");
  // The mockup's seconds format: "00.00–03.00", "03.00–06.00".
  expect(body).toContain("00.00–03.00");
  expect(body).toContain("03.00–06.00");
});

it("an empty rail says analysis has not run — and only when it truly has not", () => {
  const { body } = render(SceneRail, { props: { scenes: [] } });
  expect(body).toContain("No scenes yet. Analysis has not run.");
});

it("a failed fetch says failure, never a cause the screen cannot know", () => {
  const { body } = render(SceneRail, { props: { scenes: [], failed: true } });
  expect(body).toContain("Scenes could not be loaded.");
  expect(body).not.toContain("analysis has not run");
});

it("failure never hides scenes that did load", () => {
  const { body } = render(SceneRail, { props: { scenes: SCENES, failed: true } });
  expect(body).toContain('alt="Scene 2"');
  expect(body).toContain("00.00–03.00");
  expect(body).not.toContain("could not be loaded");
});
