/** Pure logic for the editable draft (unit 7.10): forking, move/resize with
 * the 10 ms snap, seed freezing on touch, rename, delete, commit building,
 * diffing, and the stale-freeze rebase. Everything a pointer drag can do is
 * tested here first — components stay thin renderers. */
import { describe, expect, it } from "vitest";
import {
  buildCommit,
  deleteEvent,
  draftChanged,
  draftChangeCount,
  forkDraft,
  freezeSeedOnTouch,
  MIN_DURATION_S,
  missingFreezeCount,
  moveEvent,
  rebaseDraft,
  renameEvent,
  resizeEnd,
  resizeStart,
  SNAP_S,
  type Draft,
} from "./draft";
import { legacySeed } from "./seed";

interface Ev {
  name: string;
  kind: string;
  intensity: string;
  start_time: number;
  duration: number;
  seed?: number | null;
  source_duration_s?: number | null;
}

function make(events: Ev[], duration = 15.0): Draft {
  const timeline = {
    duration,
    ambience: "desert wind",
    events: events.map((e) => ({
      name: e.name,
      kind: e.kind,
      intensity: e.intensity,
      start_time: e.start_time,
      duration: e.duration,
      seed: e.seed ?? null,
      source_duration_s: e.source_duration_s ?? null,
    })),
  };
  return forkDraft(2, timeline);
}

const BELL: Ev = {
  name: "church bell tolling",
  kind: "rhythmic",
  intensity: "med",
  start_time: 6.65,
  duration: 2.0,
};
const SHOT: Ev = {
  name: "gunshot blast",
  kind: "discrete",
  intensity: "high",
  start_time: 10.0,
  duration: 0.9,
};

describe("forkDraft", () => {
  it("copies events and keeps the base snapshot for diffing", () => {
    const d = make([BELL, SHOT]);
    expect(d.baseVersion).toBe(2);
    expect(d.events).toHaveLength(2);
    // Editing must not mutate the committed base.
    const moved = moveEvent(d, 0, 7.0);
    expect(moved.events[0].start_time).toBe(7.0);
    expect(d.events[0].start_time).toBe(6.65);
    expect(d.baseEvents[0].start_time).toBe(6.65);
  });
});

describe("moveEvent", () => {
  it("moves to a snapped position", () => {
    const d = make([BELL, SHOT]);
    const moved = moveEvent(d, 0, 6.657);
    expect(moved.events[0].start_time).toBe(6.66);
  });

  it("clamps to the clip and never goes negative", () => {
    const d = make([BELL, SHOT], 12.0);
    expect(moveEvent(d, 0, -3).events[0].start_time).toBe(0);
    expect(moveEvent(d, 0, 99).events[0].start_time).toBe(12.0);
  });

  it("Alt-drag overrides the snap", () => {
    const d = make([BELL, SHOT]);
    const fine = moveEvent(d, 0, 6.657, { snap: false });
    expect(fine.events[0].start_time).toBe(6.657);
  });

  it("a drag is a free edit — no freeze change for non-rhythmic", () => {
    const d = make([BELL, SHOT]);
    const moved = moveEvent(d, 1, 10.05);
    expect(moved.events[1].seed).toBeNull();
  });

  it("preserves a frozen seed across moves (translation, not re-roll)", () => {
    const d = make([{ ...BELL, seed: 12345 }]);
    const moved = moveEvent(d, 0, 6.85);
    expect(moved.events[0].seed).toBe(12345);
  });
});

describe("freezeSeedOnTouch", () => {
  it("freezes the legacy seed at the BASE position when a rhythmic event is touched", () => {
    const d = make([BELL, SHOT]);
    const touched = freezeSeedOnTouch(d, 0);
    // The texture the timeline was always going to render — the seed at the
    // committed position, not the dragged one.
    expect(touched.events[0].seed).toBe(legacySeed(BELL.name, 6.65));
    expect(touched.events[0].seed).not.toBe(legacySeed(BELL.name, 6.66));
  });

  it("freezes through a move — the nudge then translates", () => {
    const d = make([BELL, SHOT]);
    const moved = moveEvent(d, 0, 6.95);
    expect(moved.events[0].seed).toBe(legacySeed(BELL.name, 6.65));
    expect(moved.events[0].start_time).toBe(6.95);
    const again = moveEvent(moved, 0, 7.2);
    expect(again.events[0].seed).toBe(legacySeed(BELL.name, 6.65));
  });

  it("never touches a non-rhythmic event's seed", () => {
    const d = make([BELL, SHOT]);
    const touched = freezeSeedOnTouch(d, 1);
    expect(touched.events[1].seed).toBeNull();
  });

  it("keeps an already-frozen seed verbatim", () => {
    const d = make([{ ...BELL, seed: 999 }, SHOT]);
    expect(freezeSeedOnTouch(d, 0).events[0].seed).toBe(999);
  });
});

describe("resize", () => {
  it("right-edge resize changes duration only (free windowing)", () => {
    const d = make([BELL, SHOT]);
    const r = resizeEnd(d, 0, 9.004);
    expect(r.events[0].start_time).toBe(6.65);
    expect(r.events[0].duration).toBeCloseTo(2.35, 9);
  });

  it("left-edge resize keeps the end edge put", () => {
    const d = make([BELL, SHOT]);
    const r = resizeStart(d, 0, 6.4);
    expect(r.events[0].start_time).toBe(6.4);
    expect(r.events[0].duration).toBeCloseTo(2.25, 9);
  });

  it("enforces a minimum span", () => {
    const d = make([BELL, SHOT]);
    const r = resizeEnd(d, 0, 6.65);
    expect(r.events[0].duration).toBe(MIN_DURATION_S);
  });

  it("snaps resize to the 10 ms grid", () => {
    const d = make([BELL, SHOT]);
    expect(resizeEnd(d, 0, 8.653).events[0].duration).toBeCloseTo(2.0, 9);
  });

  it("a resize also freezes a rhythmic seed at the base position", () => {
    const d = make([BELL, SHOT]);
    const r = resizeEnd(d, 0, 9.0);
    expect(r.events[0].seed).toBe(legacySeed(BELL.name, 6.65));
  });
});

describe("renameEvent", () => {
  it("renames and freezes the seed with the OLD name first", () => {
    const d = make([{ ...BELL, seed: null }, SHOT]);
    const r = renameEvent(d, 0, "church bell, distant")!;
    expect(r.events[0].name).toBe("church bell, distant");
    // The texture already heard was drawn from the old name's seed.
    expect(r.events[0].seed).toBe(legacySeed(BELL.name, 6.65));
  });

  it("refuses empty, whitespace-only, or unchanged names", () => {
    const d = make([BELL, SHOT]);
    expect(renameEvent(d, 0, "   ")).toBeNull();
    expect(renameEvent(d, 0, "")).toBeNull();
    expect(renameEvent(d, 0, BELL.name)).toBeNull();
  });

  it("renaming is a free document edit for unfrozen events", () => {
    const d = make([SHOT]);
    const r = renameEvent(d, 0, "pistol shot")!;
    expect(r.events[0].seed).toBeNull(); // non-rhythmic: untouched
    expect(draftChanged(r)).toBe(true);
  });
});

describe("deleteEvent", () => {
  it("removes the event and renumbers the rest", () => {
    const d = make([BELL, SHOT]);
    const r = deleteEvent(d, 0);
    expect(r.events).toHaveLength(1);
    expect(r.events[0].name).toBe("gunshot blast");
    expect(r.events[0].index).toBe(0);
  });
});

describe("buildCommit", () => {
  it("submits the whole document with order as index", () => {
    const d = make([BELL, SHOT]);
    const moved = moveEvent(d, 0, 6.95);
    const body = buildCommit(moved);
    expect(body.duration).toBe(15.0);
    expect(body.ambience).toBe("desert wind");
    expect(body.events).toHaveLength(2);
    expect(body.events[0].name).toBe(BELL.name);
    expect(body.events[0].start_time).toBe(6.95);
    expect(body.events[0].seed).toBe(legacySeed(BELL.name, 6.65));
    // The commit body has no index — order IS the index.
    expect("index" in body.events[0]).toBe(false);
    expect("stem_key" in body.events[0]).toBe(false);
  });
});

describe("draftChanged / draftChangeCount", () => {
  it("a fresh fork has nothing to commit", () => {
    expect(draftChanged(make([BELL, SHOT]))).toBe(false);
    expect(draftChangeCount(make([BELL, SHOT]))).toBe(0);
  });

  it("counts touched events, not edits", () => {
    let d = make([BELL, SHOT]);
    d = moveEvent(d, 0, 6.8);
    d = moveEvent(d, 0, 6.9);
    expect(draftChangeCount(d)).toBe(1);
    d = renameEvent(d, 1, "pistol crack")!;
    expect(draftChangeCount(d)).toBe(2);
    expect(draftChanged(d)).toBe(true);
  });

  it("a seed freeze alone is a real change", () => {
    const d = make([BELL, SHOT]);
    const touched = freezeSeedOnTouch(d, 0);
    expect(draftChanged(touched)).toBe(true);
  });
});

describe("stale-freeze detection and rebase", () => {
  const latest = {
    version: 4,
    duration: 15.0,
    ambience: "desert wind",
    events: [
      { ...BELL, source_duration_s: 1.75, seed: 111 },
      { ...SHOT, source_duration_s: 1.4 },
      { name: "boot scuff", kind: "discrete", intensity: "low", start_time: 12.0, duration: 0.3, source_duration_s: 0.8 },
    ],
  };

  it("reports freezes the draft lacks", () => {
    const d = make([{ ...BELL }, { ...SHOT }]); // fork of v2, no freezes
    expect(missingFreezeCount(latest, d)).toBe(3);
  });

  it("is quiet when the draft carries the freezes", () => {
    const d = make([
      { ...BELL, source_duration_s: 1.75 },
      { ...SHOT, source_duration_s: 1.4 },
      { name: "boot scuff", kind: "discrete", intensity: "low", start_time: 12.0, duration: 0.3, source_duration_s: 0.8 },
    ]);
    expect(missingFreezeCount(latest, d)).toBe(0);
  });

  it("rebase imports the latest freeze fields, keeping the director's edits", () => {
    const d = moveEvent(make([{ ...BELL }, { ...SHOT }]), 0, 6.95);
    const rebased = rebaseDraft(d, latest);
    expect(rebased.baseVersion).toBe(4);
    // Director's placement survives.
    expect(rebased.events[0].start_time).toBe(6.95);
    // The mix's recorded facts win over the editor's auto-frozen values — the
    // latest version names the address and texture the director actually heard.
    expect(rebased.events[0].source_duration_s).toBe(1.75);
    expect(rebased.events[0].seed).toBe(111);
    expect(rebased.events[1].source_duration_s).toBe(1.4);
    // A content match in the latest always adopts the latest's freeze fields.
    const withFreeze = make([{ ...BELL, source_duration_s: 1.75, seed: 555 }, { ...SHOT }]);
    const rebased2 = rebaseDraft(withFreeze, latest);
    expect(rebased2.events[0].seed).toBe(111);
    expect(rebased2.events[0].source_duration_s).toBe(1.75);
    // Content absent from the latest (the director deleted it) keeps its values.
    const deleted = deleteEvent(make([{ ...BELL }, { ...SHOT }]), 1);
    const rebased3 = rebaseDraft(deleted, latest);
    expect(rebased3.events[0].source_duration_s).toBe(1.75);
    // After rebase the draft conserves the latest's freezes — a realistic
    // fork carries every event, so nothing is missing.
    const full = moveEvent(
      make([{ ...BELL }, { ...SHOT }, { name: "boot scuff", kind: "discrete", intensity: "low", start_time: 12.0, duration: 0.3 }]),
      0,
      6.95,
    );
    expect(missingFreezeCount(latest, rebaseDraft(full, latest))).toBe(0);
  });
});

describe("snap grid", () => {
  it("produces clean decimal values", () => {
    expect(SNAP_S).toBe(0.01);
    const d = make([BELL, SHOT]);
    expect(moveEvent(d, 0, 6.657).events[0].start_time).toBe(6.66);
    expect(moveEvent(d, 0, 6.654).events[0].start_time).toBe(6.65);
  });
});
