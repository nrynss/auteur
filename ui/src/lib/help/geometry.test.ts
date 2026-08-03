/** Tooltip placement geometry (task 22 + clipping review). The tip must
 * never leave the viewport and never cover what it explains; jsdom cannot
 * measure layout, so the clamp is a pure function pinned here. */
import { describe, expect, it } from "vitest";

import { clampTipPosition } from "./tooltip";

describe("clampTipPosition", () => {
  it("places the tip below the anchor when there is room", () => {
    const p = clampTipPosition(
      { top: 100, bottom: 130, left: 50 },
      { width: 200, height: 40 },
      { width: 1024, height: 768 },
    );
    expect(p).toEqual({ top: 136, left: 50 });
  });

  it("flips above when the bottom edge would clip", () => {
    const p = clampTipPosition(
      { top: 700, bottom: 730, left: 50 },
      { width: 200, height: 60 },
      { width: 1024, height: 768 },
    );
    expect(p).toEqual({ top: 634, left: 50 }); // 700 - 60 - 6
  });

  it("clamps horizontally into the viewport", () => {
    const p = clampTipPosition(
      { top: 100, bottom: 130, left: 900 },
      { width: 400, height: 40 },
      { width: 1024, height: 768 },
    );
    expect(p.left).toBeLessThanOrEqual(1024 - 400 - 8);
  });

  it("keeps the flip above inside the top edge for a tall tip", () => {
    const p = clampTipPosition(
      { top: 10, bottom: 40, left: 50 },
      { width: 200, height: 300 },
      { width: 1024, height: 768 },
    );
    expect(p.top).toBeGreaterThanOrEqual(8);
  });
});
