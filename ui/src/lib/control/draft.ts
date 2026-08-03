/** Draft model for direct manipulation (unit 7.10).
 *
 * A draft is the director's working copy of the timeline document: it forks
 * from an immutable committed version (`baseVersion`), accumulates edits
 * locally (persisted to localStorage so a reload does not lose work), and a
 * commit submits the WHOLE document — one commit, one new version (decision
 * A.2.1). Undo is version switching, not a mutation stack: the committed
 * versions stay untouched and the draft can be re-forked from any of them.
 *
 * Cost geometry: move, resize, rename and delete are free edits (they window
 * the clip that already exists — no generation, no bill). The one thing the
 * editor writes that is not pure geometry is the frozen mix seed: a rhythmic
 * event's texture is drawn from `seed`, so touching one without a seed would
 * re-roll its variation on the next mix. The editor freezes the seed the
 * event was always going to render with (assemble's legacy formula at the
 * *base* position) so a nudge translates instead of reshaping. Never-mixed
 * events have no texture to preserve; freezing the same legacy value makes
 * the first mix deterministic and stable across later nudges.
 */

import type { TimelineCommitRequest, TimelineEventIn } from "../api/projects";
import { legacySeed } from "./seed";

/** Snap for pointer drags, per the mockup ("snap 10 ms · ⌥ drag to override"). */
export const SNAP_S = 0.01;
/** The smallest span a resize may produce — a director's working resolution. */
export const MIN_DURATION_S = 0.05;
/** Keyboard nudge step (ms). Shift = coarse, Alt = fine. */
export const NUDGE_S = 0.01;
export const NUDGE_COARSE_S = 0.1;
export const NUDGE_FINE_S = 0.001;

/** An editable copy of a timeline event. `index` is the array position — the
 * API addresses events by index, never by name (names repeat). */
export interface DraftEvent {
  index: number;
  name: string;
  kind: string;
  intensity: string;
  start_time: number;
  duration: number;
  seed: number | null;
  source_duration_s: number | null;
  stem_key: string | null;
  scene_id: string | null;
}

export interface Draft {
  /** The immutable committed version this draft forked from. */
  baseVersion: number;
  /** The base document's events, unchanged — the committed truth the draft
   * is diffed against, and the position whose texture a legacy seed freezes. */
  baseEvents: DraftEvent[];
  duration: number;
  ambience: string;
  events: DraftEvent[];
}

export interface EditOptions {
  /** Apply the 10 ms snap (pointer drags); Alt-drag overrides. */
  snap?: boolean;
}

/** Clone a committed timeline into a draft forking at `baseVersion`. Indexes
 * are renumbered to array positions immediately — a fresh fork must render
 * correctly before the first edit (index is the keyed-each identity). */
export function forkDraft(
  baseVersion: number,
  timeline: { duration: number; ambience: string; events: TimelineEventIn[] },
): Draft {
  const events = timeline.events.map(toDraftEvent);
  return {
    baseVersion,
    baseEvents: events.map((e, i) => ({ ...e, index: i })),
    duration: timeline.duration,
    ambience: timeline.ambience,
    events: events.map((e, i) => ({ ...e, index: i })),
  };
}

export function toDraftEvent(e: TimelineEventIn): DraftEvent {
  return {
    index: 0, // caller renumbers
    name: e.name,
    kind: e.kind,
    intensity: e.intensity,
    start_time: e.start_time,
    duration: e.duration,
    seed: e.seed ?? null,
    source_duration_s: e.source_duration_s ?? null,
    stem_key: (e as { stem_key?: string | null }).stem_key ?? null,
    scene_id: (e as { scene_id?: string | null }).scene_id ?? null,
  };
}

/** Renumber indexes to array positions (the commit contract: order is index). */
export function renumber(draft: Draft): Draft {
  return {
    ...draft,
    events: draft.events.map((e, i) => ({ ...e, index: i })),
  };
}

/** The seed a rhythmic event's current audio was rendered with, frozen at the
 * base position. Only rhythmic events consult the seed (assemble tiles them);
 * non-rhythmic events keep theirs untouched — a frozen seed they never use
 * would be noise, and control-room.md §7 says fields appear only when they
 * mean something. */
export function freezeSeedOnTouch(draft: Draft, index: number): Draft {
  const ev = draft.events[index];
  if (!ev || ev.kind !== "rhythmic" || ev.seed != null) return draft;
  const base = draft.baseEvents[index];
  const seed = legacySeed(ev.name, base?.start_time ?? ev.start_time);
  return {
    ...draft,
    events: draft.events.map((e, i) => (i === index ? { ...e, seed } : e)),
  };
}

/** Snap a time to the 10 ms grid, producing clean decimal values (0.35, not
 * 0.3499999999999999) so the document stays deterministic and readable. */
export function snapToGrid(t: number, gridS: number = SNAP_S): number {
  return Math.round(t / gridS) * gridS;
}

export function clampStart(draft: Draft, t: number): number {
  return Math.min(Math.max(t, 0), draft.duration);
}

/** Move an event so it starts at `startTime` (seconds). Free. Clamped to the
 * clip; snapped to 10 ms unless `snap: false` (Alt-drag override). */
export function moveEvent(draft: Draft, index: number, startTime: number, opts: EditOptions = {}): Draft {
  const ev = draft.events[index];
  if (!ev) return draft;
  const snapped = opts.snap === false ? startTime : snapToGrid(startTime);
  const next = clampStart(draft, snapped);
  let out: Draft = {
    ...draft,
    events: draft.events.map((e, i) =>
      i === index ? { ...e, start_time: next } : e,
    ),
  };
  return renumber(freezeSeedOnTouch(out, index));
}

/** Resize by moving the event's END edge (right edge). Duration changes,
 * start stays. Free — it windows the clip that already exists. */
export function resizeEnd(draft: Draft, index: number, endTime: number, opts: EditOptions = {}): Draft {
  const ev = draft.events[index];
  if (!ev) return draft;
  const snapped = opts.snap === false ? endTime : snapToGrid(endTime);
  const end = clampStart(draft, snapped);
  const duration = Math.max(MIN_DURATION_S, end - ev.start_time);
  let out: Draft = {
    ...draft,
    events: draft.events.map((e, i) =>
      i === index ? { ...e, duration } : e,
    ),
  };
  return renumber(freezeSeedOnTouch(out, index));
}

/** Resize by moving the event's START edge (left edge); the end edge stays
 * put, so the span shifts as well as resizes. */
export function resizeStart(draft: Draft, index: number, startTime: number, opts: EditOptions = {}): Draft {
  const ev = draft.events[index];
  if (!ev) return draft;
  const snapped = opts.snap === false ? startTime : snapToGrid(startTime);
  const end = ev.start_time + ev.duration;
  const start = clampStart(draft, snapped);
  const newStart = Math.min(start, end - MIN_DURATION_S);
  const duration = end - newStart;
  let out: Draft = {
    ...draft,
    events: draft.events.map((e, i) =>
      i === index ? { ...e, start_time: newStart, duration } : e,
    ),
  };
  return renumber(freezeSeedOnTouch(out, index));
}

/** Rename an event. Free to commit; it re-routes the event's next generation
 * (the endpoint is keyword-matched from the name) and re-prompts it, so the
 * next Regenerate bills by the new name. Frozen events refuse this at commit
 * — the UI explains that before the user reaches the server. */
export function renameEvent(draft: Draft, index: number, name: string): Draft | null {
  const ev = draft.events[index];
  const trimmed = name.trim();
  if (!ev || trimmed === "" || trimmed === ev.name) return null;
  const base = draft.baseEvents[index];
  // Freeze the seed with the OLD name first: the current texture was rendered
  // from it, and a rename must not quietly re-roll a variation the director
  // has been listening to (7.1's "preserve seed" rule).
  let withName: Draft = {
    ...draft,
    events: draft.events.map((e, i) =>
      i === index ? { ...e, name: trimmed } : e,
    ),
  };
  if (withName.events[index].kind === "rhythmic" && withName.events[index].seed == null) {
    const seed = legacySeed(ev.name, base?.start_time ?? ev.start_time);
    withName = {
      ...withName,
      events: withName.events.map((e, i) => (i === index ? { ...e, seed } : e)),
    };
  }
  return renumber(withName);
}

/** Delete an event. Free: removing a generated sound costs nothing — the
 * money is already spent (7.3's freeze-conservation hole for deletion). */
export function deleteEvent(draft: Draft, index: number): Draft {
  if (index < 0 || index >= draft.events.length) return draft;
  return renumber({
    ...draft,
    events: draft.events.filter((_, i) => i !== index),
  });
}

/** The commit body — the WHOLE document, order = index (decision A.2.11). */
export function buildCommit(draft: Draft): TimelineCommitRequest {
  return {
    duration: draft.duration,
    ambience: draft.ambience,
    events: draft.events.map(
      (e): TimelineEventIn => ({
        name: e.name,
        start_time: e.start_time,
        duration: e.duration,
        intensity: e.intensity,
        kind: e.kind,
        seed: e.seed,
        source_duration_s: e.source_duration_s,
      }),
    ),
  };
}

/** Whether the draft differs from the version it forked from — nothing to
 * commit (and no draft chip) when false. */
export function draftChanged(draft: Draft): boolean {
  const base = draft.baseEvents;
  if (draft.events.length !== base.length) return true;
  return draft.events.some((e, i) => {
    const b = base[i];
    return (
      e.name !== b.name ||
      e.kind !== b.kind ||
      e.intensity !== b.intensity ||
      e.start_time !== b.start_time ||
      e.duration !== b.duration ||
      e.seed !== b.seed
    );
  });
}

/** A human summary of what changed, for the commit line and undo affordance.
 * Returns the count of touched events (a rename + a move on the same event is
 * one touched event). */
export function draftChangeCount(draft: Draft): number {
  return draft.events.reduce((n, e, i) => {
    const b = draft.baseEvents[i];
    if (!b) return n + 1;
    return n +
      (e.name !== b.name ||
        e.kind !== b.kind ||
        e.intensity !== b.intensity ||
        e.start_time !== b.start_time ||
        e.duration !== b.duration ||
        e.seed !== b.seed
        ? 1
        : 0);
  }, 0);
}

/** Whether the draft carries every freeze the latest version has, matched by
 * content (name, intensity) — the identity the commit conserves. When false,
 * a commit would be refused and the draft is stale under a newer version. */
export function missingFreezeCount(
  latest: { events: TimelineEventIn[] },
  draft: Draft,
): number {
  const draftByContent = new Map(
    draft.events.map((e) => [`${e.name}\u0000${e.intensity}`, e]),
  );
  let missing = 0;
  for (const e of latest.events) {
    if (e.source_duration_s == null) continue;
    const inDraft = draftByContent.get(`${e.name}\u0000${e.intensity}`);
    if (!inDraft || inDraft.source_duration_s == null) missing += 1;
  }
  return missing;
}

/** Merge the latest version's freeze fields (source_duration_s, stem_key,
 * seed) onto draft events whose content matches, and re-base the draft on
 * the latest version. The latest's recorded facts win — they are the address
 * and texture the most recent mix actually used, which is the truth the
 * director has been listening to; the draft's own freeze values were merely
 * copied forward or auto-frozen and are stale by definition here. Placement
 * and name edits stay the director's. Events with no content match in the
 * latest keep the draft's values (the director deleted them or renamed them
 * — deletion of frozen content is legal). */
export function rebaseDraft(
  draft: Draft,
  latest: { version: number; duration: number; ambience: string; events: TimelineEventIn[] },
): Draft {
  const latestByContent = new Map(
    latest.events.map((e) => [`${e.name}\u0000${e.intensity}`, e]),
  );
  const events = draft.events.map((e) => {
    const src = latestByContent.get(`${e.name}\u0000${e.intensity}`);
    if (!src) return e;
    return {
      ...e,
      source_duration_s: src.source_duration_s ?? e.source_duration_s,
      stem_key: (src as { stem_key?: string | null }).stem_key ?? e.stem_key,
      seed: src.seed ?? e.seed,
    };
  });
  return {
    baseVersion: latest.version,
    baseEvents: latest.events.map(toDraftEvent).map((e, i) => ({ ...e, index: i })),
    duration: latest.duration,
    ambience: latest.ambience,
    events,
  };
}

// -- persistence ------------------------------------------------------------
// localStorage keyed per project. The whole draft (including the base
// snapshot) is persisted so a reload restores the working copy without
// refetching — and so "what changed vs committed" survives a refresh.

const KEY_PREFIX = "ff:draft:";

export function draftKey(projectId: string): string {
  return `${KEY_PREFIX}${projectId}`;
}

export function saveDraft(projectId: string, draft: Draft): void {
  try {
    localStorage.setItem(draftKey(projectId), JSON.stringify(draft));
  } catch {
    // Storage full / unavailable — the draft stays in memory for this
    // session; losing persistence must not break editing.
  }
}

export function loadDraft(projectId: string): Draft | null {
  try {
    const raw = localStorage.getItem(draftKey(projectId));
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<Draft>;
    if (
      typeof parsed.baseVersion !== "number" ||
      typeof parsed.duration !== "number" ||
      typeof parsed.ambience !== "string" ||
      !Array.isArray(parsed.events) ||
      !Array.isArray(parsed.baseEvents)
    ) {
      return null;
    }
    return parsed as Draft;
  } catch {
    return null;
  }
}

export function clearDraft(projectId: string): void {
  try {
    localStorage.removeItem(draftKey(projectId));
  } catch {
    // ignore — same rule as save
  }
}
