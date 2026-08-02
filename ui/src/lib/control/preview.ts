/** Preview scheduling — pure logic for the browser preview (unit 7.9).
 * Everything numeric is either recorded (start_time, stem_key) or the
 * documented constant below; nothing is re-derived from audio. */

import type { TimelineEventOut } from "../api/projects";

/** assemble.py DISCRETE_BIAS_S — the +50 ms late bias so audio never precedes
 * the visual. The per-clip onset trim (trim_onset) is NOT exposed by the API,
 * so the client can only show the bias-adjusted position — and says so. */
export const DISCRETE_BIAS_S = 0.05;

/** The audible hit point of an event, in seconds (control-room.md §7: the
 * playhead shows the transient, not start_time). Discrete one-shots are
 * placed at `start_time + bias − onset_s` in the server mix; onset is
 * per-clip and unexposed, so the transient shown is bias-adjusted only.
 * Rhythmic (tiled loop) and continuous (bed) start at start_time. */
export function transientOffset(
  ev: Pick<TimelineEventOut, "kind" | "start_time">,
): number {
  return ev.kind === "discrete" ? ev.start_time + DISCRETE_BIAS_S : ev.start_time;
}

export interface PreviewItem {
  index: number;
  key: string;
  offset: number;
  kind: string;
}

/** The previewable stems at their offsets, sorted by offset. Events without a
 * stamped `stem_key` are omitted — a missing address is a hole, never a
 * guessed one. */
export function buildSchedule(events: TimelineEventOut[]): PreviewItem[] {
  return events
    .filter((e) => e.stem_key != null)
    .map((e) => ({
      index: e.index,
      key: e.stem_key as string,
      offset: transientOffset(e),
      kind: e.kind,
    }))
    .sort((a, b) => a.offset - b.offset);
}
