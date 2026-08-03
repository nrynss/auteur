/** Meter — the chrome's readiness bar. The number is the evidence and the
 * bar is decoration: the label shows the server's own percent (unclamped —
 * it is the measurement), the bar width is clamped to the track. Absent
 * readiness renders "—", never a fake 0%. */
import { expect, it } from "vitest";
import { render } from "svelte/server";

import Meter from "./Meter.svelte";

it("shows the measured percent with its label", () => {
  const { body } = render(Meter, { props: { percent: 83 } });
  expect(body).toContain("83%");
  expect(body).toContain("ready");
  expect(body).toContain('width:83%');
});

it("absent readiness renders as '—', never as a number", () => {
  const { body } = render(Meter, { props: { percent: null } });
  // (server render returns HTML — the label's b tag sits between the
  // characters, so assert on the tokens)
  expect(body).toContain(">n/a</b>");
  expect(body).toContain("> ready");
  // The bar is empty decoration — no painted width.
  expect(body).toContain('width:0%');
});

it("the bar clamps at 100% while the label still shows the server's number", () => {
  const { body } = render(Meter, { props: { percent: 150 } });
  // The evidence is the number the API reported.
  expect(body).toContain("150%");
  // The decoration cannot overflow its track.
  expect(body).toContain('width:100%');
});
