/** Pure helpers for the read-only control room (unit 7.8): timeline
 * geometry, QA comparisons, and history labels. Everything here is tested in
 * vitest; components are thin renderers over these. */

import type { TimelineEventOut } from "../api/projects";

/** One lane in the read-only timeline. Grouping by kind is honest data — the
 * API does not expose per-event routing endpoints, and kind is stable. */
export interface Lane {
  kind: string;
  label: string;
  events: TimelineEventOut[];
}

export function groupLanes(events: TimelineEventOut[]): Lane[] {
  const byKind = new Map<string, TimelineEventOut[]>();
  for (const e of events) {
    const list = byKind.get(e.kind) ?? [];
    list.push(e);
    byKind.set(e.kind, list);
  }
  const order = ["continuous", "rhythmic", "discrete"];
  const kinds = [...byKind.keys()].sort(
    (a, b) => (order.indexOf(a) - order.indexOf(b)) || a.localeCompare(b),
  );
  return kinds.map((kind) => ({
    kind,
    label: kind === "continuous" ? "Beds" : kind === "rhythmic" ? "Rhythmic" : "One-shots",
    events: byKind.get(kind)!,
  }));
}

export interface EventPlacement {
  index: number;
  leftPx: number;
  widthPx: number;
}

/** Position an event on a lane. `minWidthPx` keeps a 50 ms click audible as a
 * visible block (honest placement, honest minimum). */
export function placeEvent(
  event: TimelineEventOut,
  pxPerSec: number,
  minWidthPx = 12,
): EventPlacement {
  const leftPx = event.start_time * pxPerSec;
  const widthPx = Math.max(event.duration * pxPerSec, minWidthPx);
  return { index: event.index, leftPx, widthPx };
}

/** QA — loudness delta in LU against the target, measured-phrase style
 * (control-room.md §8: the measurement, never a verdict). */
export function loudnessDelta(
  measured: number | null | undefined,
  target: number,
): string {
  if (measured == null) return "—";
  const delta = measured - target; // negative = under target
  if (Math.abs(delta) < 0.05) return "at target";
  return delta < 0
    ? `${(-delta).toFixed(1)} LU under target`
    : `${delta.toFixed(1)} LU over target`;
}

/** Continuity: the measured jump and the confidence — a comparison, never a
 * fault. Returns null when the issue carries no scene figures to compare. */
export function continuityFigures(
  issue: {
    scene_id?: string | null;
    compared_with?: string | null;
    confidence?: number | null;
    detail?: string | null;
  },
  threshold: number,
  scenes: { id: string; start_s: number }[],
): {
  left: string | null;
  right: string | null;
  lumaJump: string | null;
  threshold: string;
  confidence: string | null;
} | null {
  if (!issue.scene_id && !issue.compared_with && !issue.detail) return null;
  const left = issue.compared_with ?? null;
  const right = issue.scene_id ?? null;
  const conf =
    issue.confidence == null ? null : Number(issue.confidence.toFixed(2));
  return {
    left,
    right,
    lumaJump: issue.detail ?? null,
    threshold: String(threshold),
    confidence: conf == null ? null : String(conf),
  };
}

/** History: postproduction.run.started → "run.started" — the stable tail of
 * the event type, which is what a director reads. */
export function friendlyEventType(type: string): string {
  const parts = type.split(".");
  // The stable tail — postproduction.run.started → run.started. The
  // postproduction prefix is namespace noise for a director; the last two
  // segments name what happened.
  return parts.length > 1 ? parts.slice(-2).join(".") : type;
}

export function friendlyActor(actor: string): string {
  return actor === "system" ? "system" : actor || "—";
}
