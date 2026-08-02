/** Map API project status strings to Chip vocabulary (control-room.md §2).
 * Colour never carries meaning alone — Chip always pairs glyph + word.
 * ChipState is duplicated here (not imported from Chip.svelte) so pure
 * helpers stay free of Svelte component module resolution in vitest. */

export type StatusChipState = "ok" | "warn" | "blocked" | "pending";

/** Statuses derived by ProjectStore._derive_status (projects.py). */
export type ProjectStatus =
  | "new"
  | "awaiting_upload"
  | "analyzing"
  | "needs_review"
  | "ready"
  | "failed"
  | string;

export interface StatusPresentation {
  state: StatusChipState;
  /** Short label shown in the chip word. */
  label: string;
}

const MAP: Record<string, StatusPresentation> = {
  new: { state: "pending", label: "New" },
  awaiting_upload: { state: "pending", label: "Awaiting upload" },
  analyzing: { state: "pending", label: "Analyzing" },
  needs_review: { state: "warn", label: "Needs review" },
  ready: { state: "ok", label: "Ready" },
  failed: { state: "blocked", label: "Failed" },
};

/** Present a ProjectSummary.status for a Chip. Unknown → pending + raw word. */
export function presentStatus(status: string): StatusPresentation {
  const known = MAP[status];
  if (known) return known;
  // Do not invent a friendly name for an unknown status — surface the wire value.
  return { state: "pending", label: status || "Unknown" };
}

/** Relative-ish created_at for the index list. ISO from the API. */
export function formatCreatedAt(iso: string): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}
