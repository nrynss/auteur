/**
 * Clip inventory for product capture (demoplan.md).
 *
 * Cards (02, 03, 06, 11) and VO are produced elsewhere. holdMs deliberately
 * overshoots target_s — ffmpeg trims to SEG_DUR later; undershoot is a re-run.
 *
 * Free by design: navigation, tabs, hover, select, drag, arrow-nudge, A/B
 * preview, scrub, help. Never Mix / Export / Regenerate / director send /
 * cost confirm.
 */
import type { ProjectKey } from "./config.ts";

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
  /** Whether this clip is free against the paid service (all of these are). */
  free: true;
  /** Notes for --list / README. */
  notes: string;
}

export const CLIPS: Clip[] = [
  {
    id: "01_room",
    title: "Control room on a seeded project, real timeline",
    project: "primary",
    holdMs: 22_000,
    free: true,
    notes: "Hook: room first. Hover timeline blocks; no edits required.",
  },
  {
    id: "04_director",
    title: "Director mode panel (free surface only)",
    project: "primary",
    holdMs: 32_000,
    free: true,
    notes:
      "Director proposals are client-side only and a send bills a model call. " +
      "This take films the Director tab (cost geometry + empty composer) without " +
      "sending. Ghosted old vs new / Apply requires a live turn — do not run " +
      "without explicit approval. See README.",
  },
  {
    id: "05_listen",
    title: "A/B or mix playing — picture for the listen beat",
    project: "primary",
    holdMs: 24_000,
    free: true,
    notes: "Foley mix selected + play + brief Original toggle. Product audio bed is exported separately.",
  },
  {
    id: "07_nudge",
    title: "Free nudge: drag / arrow an event",
    project: "primary",
    holdMs: 20_000,
    free: true,
    notes: "Draft-only drag and Shift+arrow. Never commit (also free, but mutates server versions).",
  },
  {
    id: "08_spend",
    title: "Spend tab: budget, thresholds, transactions",
    project: "primary",
    holdMs: 18_000,
    free: true,
    notes:
      "Read-only on the warm primary seed (budget + thresholds). The Inhaler has " +
      "richer ledger rows but a broken timeline poisons the Spend panel via ancillaryFailed.",
  },
  {
    id: "09_evidence",
    title: "Evidence tab: loudness, true peak, shot-to-shot",
    project: "primary",
    holdMs: 20_000,
    free: true,
    notes: "QA measurements already on the seed.",
  },
  {
    id: "10_dossier",
    title: "Artifacts tab: full inventory with sizes",
    project: "primary",
    holdMs: 18_000,
    free: true,
    notes: "Dossier composes slowly (HEAD per object) — wait for the table, not 'Composing…'.",
  },
];

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
