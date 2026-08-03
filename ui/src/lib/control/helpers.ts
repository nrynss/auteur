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
  // Unknown kinds go LAST, never to the top: an unrecognised kind taking a
  // deliberate-looking position is the same failure mode as an unknown stage
  // reading "Done". They keep their own name as the lane label.
  const kinds = [...byKind.keys()].sort((a, b) => {
    const ia = order.indexOf(a);
    const ib = order.indexOf(b);
    return (
      (ia === -1 ? order.length : ia) - (ib === -1 ? order.length : ib) ||
      a.localeCompare(b)
    );
  });
  const LABELS: Record<string, string> = {
    continuous: "Beds",
    rhythmic: "Rhythmic",
    discrete: "One-shots",
  };
  return kinds.map((kind) => ({
    kind,
    label: LABELS[kind] ?? kind,
    events: byKind.get(kind)!,
  }));
}

export interface EventPlacement {
  index: number;
  leftPx: number;
  widthPx: number;
}

/** A stable non-cryptographic string hash (djb2) — the envelope seed for
 * events without a frozen mix seed. Stable across renumbering: deleting or
 * reordering events must not redraw every later event's envelope, which would
 * read as "those sounds changed" (unit 7.10 review). */
export function stableHash(s: string): number {
  let h = 5381;
  for (let i = 0; i < s.length; i += 1) {
    h = ((h * 33) ^ s.charCodeAt(i)) >>> 0;
  }
  return h;
}

/** The deterministic shape-seed for an event's structural envelope. Prefers
 * the frozen mix seed (for rhythmic events it IS the variation the sound was
 * rendered with); falls back to a name hash — two events with the same name
 * are the same sound, so identical envelopes are the honest read. */
export function envelopeSeed(
  ev: Pick<TimelineEventOut, "name" | "seed">,
): number {
  return ev.seed ?? stableHash(ev.name);
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
