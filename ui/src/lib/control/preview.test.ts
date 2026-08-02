import { describe, expect, it } from "vitest";

import { DISCRETE_BIAS_S, buildSchedule, transientOffset } from "./preview";
import type { TimelineEventOut } from "../api/projects";

const ev = (
  partial: Partial<TimelineEventOut> & { index: number },
): TimelineEventOut => ({
  name: "x",
  kind: "discrete",
  intensity: "med",
  start_time: 0,
  duration: 1,
  ...partial,
});

describe("transientOffset", () => {
  it("discrete events hit at start_time + the 50 ms bias", () => {
    expect(transientOffset(ev({ index: 0, kind: "discrete", start_time: 11 })))
      .toBe(11 + DISCRETE_BIAS_S);
  });

  it("rhythmic and continuous start at start_time", () => {
    expect(transientOffset(ev({ index: 0, kind: "rhythmic", start_time: 4 })))
      .toBe(4);
    expect(transientOffset(ev({ index: 0, kind: "continuous", start_time: 0 })))
      .toBe(0);
  });
});

describe("buildSchedule", () => {
  it("schedules only events with a stamped stem key", () => {
    const sched = buildSchedule([
      ev({ index: 0, start_time: 4, stem_key: "cache/a.wav" }),
      ev({ index: 1, start_time: 1, kind: "discrete" }), // never mixed
      ev({ index: 2, start_time: 2, stem_key: "cache/b.wav", kind: "rhythmic" }),
    ]);
    expect(sched).toEqual([
      { index: 2, key: "cache/b.wav", offset: 2, kind: "rhythmic" },
      { index: 0, key: "cache/a.wav", offset: 4.05, kind: "discrete" },
    ]);
  });

  it("is empty when nothing has a stem key", () => {
    expect(buildSchedule([ev({ index: 0 }), ev({ index: 1 })])).toEqual([]);
  });
});
