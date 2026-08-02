import { describe, expect, it } from "vitest";

import { friendlyEventType, groupLanes, loudnessDelta, placeEvent } from "./helpers";
import type { TimelineEventOut } from "../api/projects";

const ev = (partial: Partial<TimelineEventOut> & { index: number }): TimelineEventOut => ({
  name: "x",
  kind: "discrete",
  intensity: "med",
  start_time: 0,
  duration: 1,
  ...partial,
});

describe("groupLanes", () => {
  it("groups by kind with a stable order", () => {
    const lanes = groupLanes([
      ev({ index: 0, kind: "discrete" }),
      ev({ index: 1, kind: "continuous" }),
      ev({ index: 2, kind: "rhythmic" }),
      ev({ index: 3, kind: "discrete" }),
    ]);
    expect(lanes.map((l) => l.kind)).toEqual(["continuous", "rhythmic", "discrete"]);
    expect(lanes[2].events.map((e) => e.index)).toEqual([0, 3]);
    expect(lanes[0].label).toBe("Beds");
    expect(lanes[1].label).toBe("Rhythmic");
    expect(lanes[2].label).toBe("One-shots");
  });

  it("is empty for no events", () => {
    expect(groupLanes([])).toEqual([]);
  });

  it("sorts unknown kinds LAST with their own name as the label", () => {
    const lanes = groupLanes([
      ev({ index: 0, kind: "discrete" }),
      ev({ index: 1, kind: "mystery" }),
      ev({ index: 2, kind: "continuous" }),
    ]);
    expect(lanes.map((l) => l.kind)).toEqual(["continuous", "discrete", "mystery"]);
    expect(lanes[2].label).toBe("mystery"); // honest, not "One-shots"
  });
});

describe("placeEvent", () => {
  it("positions an event honestly at start × px-per-sec", () => {
    expect(placeEvent(ev({ index: 0, start_time: 3.5, duration: 1.5 }), 64)).toEqual({
      index: 0,
      leftPx: 224,
      widthPx: 96,
    });
  });

  it("keeps a tiny event visible, never zero-width", () => {
    const p = placeEvent(ev({ index: 0, start_time: 5, duration: 0.05 }), 64);
    expect(p.leftPx).toBe(320);
    expect(p.widthPx).toBe(12); // min block width
  });
});

describe("loudnessDelta", () => {
  it("phrases the measured gap, never a verdict", () => {
    expect(loudnessDelta(-21.4, -14)).toBe("7.4 LU under target");
    expect(loudnessDelta(-13.0, -14)).toBe("1.0 LU over target");
    expect(loudnessDelta(-14.02, -14)).toBe("at target");
  });

  it("renders absent measurements as a dash", () => {
    expect(loudnessDelta(null, -14)).toBe("—");
    expect(loudnessDelta(undefined, -14)).toBe("—");
  });
});

describe("friendlyEventType", () => {
  it("shows the stable tail of an event type", () => {
    expect(friendlyEventType("postproduction.run.started")).toBe("run.started");
    expect(friendlyEventType("postproduction.repair.approved")).toBe("repair.approved");
  });

  it("passes through types without dots", () => {
    expect(friendlyEventType("started")).toBe("started");
  });
});
