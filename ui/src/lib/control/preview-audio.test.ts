// @vitest-environment node
/** Scheduling maths for the preview engine — the part the pure-function tests
 * cannot see. Same shape as the review probe: a stubbed AudioContext at a
 * mid-clip playhead, asserting where each stem starts INTO its buffer. */
import { describe, expect, it, vi } from "vitest";

import { PreviewEngine } from "./preview-audio";
import type { PreviewItem } from "./preview";

interface StartCall {
  when: number;
  into: number | undefined;
}

function fakeContext() {
  const starts: StartCall[] = [];
  const sources: Array<Record<string, unknown>> = [];
  const gains: Array<{
    gain: { setValueAtTime: ReturnType<typeof vi.fn>; linearRampToValueAtTime: ReturnType<typeof vi.fn> };
    connect: () => Record<string, unknown>;
  }> = [];
  const ctx = {
    state: "running",
    currentTime: 100,
    destination: {},
    resume: vi.fn(),
    close: vi.fn(),
    createBufferSource: () => {
      const src: Record<string, unknown> = {
        buffer: null,
        connect: () => src,
        start: (when: number, into?: number) => starts.push({ when, into }),
        stop: vi.fn(),
      };
      sources.push(src);
      return src;
    },
    createGain: () => {
      const gain = {
        gain: { setValueAtTime: vi.fn(), linearRampToValueAtTime: vi.fn() },
        connect: () => ({}),
      };
      gains.push(gain);
      return gain;
    },
  } as unknown as AudioContext;
  return { ctx, starts, sources, gains };
}

const buf = (duration: number) => ({ duration }) as unknown as AudioBuffer;

function engineWith(
  ctx: AudioContext,
  items: PreviewItem[],
  videoTime: number,
  buffers: Record<string, AudioBuffer>,
) {
  const engine = new PreviewEngine(
    { currentTime: videoTime } as HTMLVideoElement,
    "proj_x",
    items,
    () => {},
  );
  const e = engine as unknown as {
    ctx: AudioContext | null;
    buffers: Map<string, AudioBuffer>;
  };
  e.ctx = ctx;
  for (const [k, v] of Object.entries(buffers)) e.buffers.set(k, v);
  return engine;
}

describe("PreviewEngine.schedule", () => {
  it("resumes a straddling stem at its offset INTO the buffer", () => {
    // The review probe: a 15 s bed at offset 0, video at t=10. It must
    // start at context time 100 (now) from buffer position 10 — not restart
    // from 0 and run 10 s past the video's end.
    const { ctx, starts } = fakeContext();
    const engine = engineWith(
      ctx,
      [{ index: 0, key: "bed", offset: 0, kind: "continuous" }],
      10,
      { bed: buf(15) },
    );
    engine.schedule();
    expect(starts).toEqual([{ when: 100, into: 10 }]);
  });

  it("plays an upcoming stem from its start at the right delay", () => {
    const { ctx, starts } = fakeContext();
    const engine = engineWith(
      ctx,
      [{ index: 0, key: "shot", offset: 11, kind: "discrete" }],
      10,
      { shot: buf(1) },
    );
    engine.schedule();
    expect(starts).toEqual([{ when: 101, into: 0 }]);
  });

  it("skips a stem fully in the past", () => {
    const { ctx, starts } = fakeContext();
    const engine = engineWith(
      ctx,
      [{ index: 0, key: "done", offset: 0, kind: "discrete" }],
      10,
      { done: buf(1) },
    );
    engine.schedule();
    expect(starts).toEqual([]);
  });

  it("schedules multiple stems in one pass", () => {
    const { ctx, starts } = fakeContext();
    const engine = engineWith(
      ctx,
      [
        { index: 0, key: "bed", offset: 0, kind: "continuous" },
        { index: 1, key: "shot", offset: 11, kind: "discrete" },
      ],
      10,
      { bed: buf(15), shot: buf(1) },
    );
    engine.schedule();
    expect(starts).toEqual([
      { when: 100, into: 10 },
      { when: 101, into: 0 },
    ]);
  });

  it("shapes the release over the REMAINING duration, not the full buffer", () => {
    const { ctx, gains } = fakeContext();
    const engine = engineWith(
      ctx,
      [{ index: 0, key: "bed", offset: 0, kind: "continuous" }],
      10,
      { bed: buf(15) },
    );
    engine.schedule();
    // The release ramp's endpoint is when + remaining (100 + 5), never
    // when + full duration (100 + 15) — the source ends at 105.
    const rampEnds = gains[0].gain.linearRampToValueAtTime.mock.calls.map(
      (c) => c[1],
    );
    expect(rampEnds[rampEnds.length - 1]).toBe(105);
  });
});

describe("PreviewEngine.dispose", () => {
  it("closes the AudioContext so navigation does not leak one per project", () => {
    const { ctx } = fakeContext();
    const engine = engineWith(ctx, [], 0, {});
    engine.dispose();
    expect(ctx.close).toHaveBeenCalledOnce();
  });
});
