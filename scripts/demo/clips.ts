/**
 * Clip inventory for product capture (demoplan.md).
 *
 * Source of truth: inventory.json (also loaded by run.mjs for --list).
 * Cards (02, 03, 06, 11) and VO are produced elsewhere. holdMs deliberately
 * overshoots target_s — ffmpeg trims to SEG_DUR later; undershoot is a re-run.
 *
 * Free by design: navigation, tabs, hover, select, drag, arrow-nudge, A/B
 * preview, scrub, help. Never Mix / Export / Regenerate / director send /
 * cost confirm.
 */
import { PROJECTS, type ProjectKey } from "./config.ts";
import inventory from "./inventory.json" with { type: "json" };

export interface Clip {
  /** Manifest / output id — one row = one re-take unit. */
  id: string;
  /** Human description for --list. */
  title: string;
  /** Which seeded project to open. */
  project: ProjectKey;
  /**
   * Minimum wall time on the beat after the page is settled (ms). Capture
   * longer than the VO budget; never shorter.
   */
  holdMs: number;
  /** Whether this clip is free against the paid service. */
  free: boolean;
  /** Notes for --list / README. */
  notes: string;
}

const knownProjectKeys = new Set(Object.keys(PROJECTS));

function loadClips(): Clip[] {
  const out: Clip[] = [];
  for (const c of inventory.clips) {
    if (!knownProjectKeys.has(c.project)) {
      throw new Error(
        `inventory.json: clip ${c.id} has unknown project key "${c.project}". ` +
          `Known: ${[...knownProjectKeys].join(", ")}`,
      );
    }
    if (c.free !== true) {
      throw new Error(
        `inventory.json: clip ${c.id} has free=${JSON.stringify(c.free)}. ` +
          `This harness only captures free beats — set free: true or remove the row.`,
      );
    }
    out.push({
      id: c.id,
      title: c.title,
      project: c.project as ProjectKey,
      holdMs: c.holdMs,
      free: c.free,
      notes: c.notes,
    });
  }
  return out;
}

export const CLIPS: Clip[] = loadClips();

export function clipById(id: string): Clip | undefined {
  return CLIPS.find((c) => c.id === id);
}

export function filterClips(only: string | undefined): Clip[] {
  if (!only) return CLIPS;
  const ids = only.split(",").map((s) => s.trim()).filter(Boolean);
  const missing = ids.filter((id) => !clipById(id));
  if (missing.length) {
    throw new Error(
      `Unknown clip id(s): ${missing.join(", ")}. Known: ${CLIPS.map((c) => c.id).join(", ")}`,
    );
  }
  return ids.map((id) => clipById(id)!);
}

/** Resolve a clip's project id; fails if the inventory key is unknown. */
export function projectIdFor(clip: Clip): string {
  const id = PROJECTS[clip.project];
  if (!id) {
    throw new Error(
      `No project id for key "${clip.project}" (clip ${clip.id})`,
    );
  }
  return id;
}
