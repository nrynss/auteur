// @vitest-environment jsdom
/** Toggle chips are real buttons — clicking one must fire onclick even when
 * it is off (7.10's version selector needs the off chips to be clickable). */
import { afterEach, describe, expect, it } from "vitest";
import { mount, unmount } from "svelte";

import Chip from "./Chip.svelte";

interface ChipProps {
  state: "ok" | "warn" | "blocked" | "pending";
  toggle?: boolean;
  pressed?: boolean;
  label?: string;
  detail?: string;
  live?: boolean;
  onclick?: (event: MouseEvent) => void;
}

let mounted: ReturnType<typeof mount>[] = [];

function mountChip(props: ChipProps): {
  target: HTMLElement;
  clicks: { n: number };
} {
  const target = document.createElement("div");
  document.body.appendChild(target);
  const clicks = { n: 0 };
  mounted.push(
    mount(Chip, {
      target,
      props: { ...props, onclick: () => (clicks.n += 1) },
    }),
  );
  return { target, clicks };
}

afterEach(() => {
  for (const c of mounted) unmount(c);
  mounted = [];
  document.body.innerHTML = "";
});

describe("Chip (toggle)", () => {
  it("clicking an off toggle fires onclick", () => {
    const { target, clicks } = mountChip({
      state: "ok",
      toggle: true,
      pressed: false,
    });
    target.querySelector("button")!.click();
    expect(clicks.n).toBe(1);
  });

  it("clicking an on toggle fires onclick", () => {
    const { target, clicks } = mountChip({
      state: "ok",
      toggle: true,
      pressed: true,
    });
    target.querySelector("button")!.click();
    expect(clicks.n).toBe(1);
  });

  it("a display chip is not clickable", () => {
    const { target, clicks } = mountChip({ state: "ok" });
    expect(target.querySelector("button")).toBeNull();
    (target.querySelector(".chip") as HTMLElement).click();
    expect(clicks.n).toBe(0);
  });
});
