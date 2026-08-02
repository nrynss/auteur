/** Shape a CreateProjectRequest from form fields + a File.
 * Pure — no network. Content-type prefers the browser's File.type when it is
 * one the API accepts; falls back to extension. Never invents video/mp4 for
 * an unknown type (would be signed into the presigned PUT). */

import type { CreateProjectRequest } from "../api/projects";

/** Content types CreateProjectRequest accepts (schemas.py validator). */
export const ALLOWED_CONTENT_TYPES = [
  "video/mp4",
  "video/quicktime",
  "video/x-matroska",
  "video/webm",
] as const;

export type AllowedContentType = (typeof ALLOWED_CONTENT_TYPES)[number];

const ALLOWED = new Set<string>(ALLOWED_CONTENT_TYPES);

const EXT_TO_TYPE: Record<string, AllowedContentType> = {
  ".mp4": "video/mp4",
  ".m4v": "video/mp4",
  ".mov": "video/quicktime",
  ".qt": "video/quicktime",
  ".mkv": "video/x-matroska",
  ".webm": "video/webm",
};

/** Basename only — path separators are refused by the API validator. */
export function safeFilename(name: string): string {
  const base = name.split(/[/\\]/).pop()?.trim() || "source.mp4";
  if (base === "." || base === "..") return "source.mp4";
  return base;
}

/** Resolve a wire content_type from a File. Prefer file.type when allowed.
 * Returns null when neither type nor extension is a known video. */
export function contentTypeForFile(file: File): AllowedContentType | null {
  const t = (file.type || "").toLowerCase().trim();
  if (ALLOWED.has(t)) return t as AllowedContentType;
  const lower = file.name.toLowerCase();
  for (const [ext, ct] of Object.entries(EXT_TO_TYPE)) {
    if (lower.endsWith(ext)) return ct;
  }
  return null;
}

export interface CreateFormInput {
  title: string;
  /** Empty / whitespace / null / undefined → omit budget_usd.
   * Accepts number because Svelte 5 coerces type=number bind:value. */
  budgetUsd: string | number | null | undefined;
  file: File;
}

export type CreatePayloadResult =
  | { ok: true; body: CreateProjectRequest }
  | { ok: false; error: string };

/** Normalize budget field from string | number | empty. */
export function normalizeBudget(
  budgetUsd: string | number | null | undefined,
): { ok: true; value: number | undefined } | { ok: false; error: string } {
  if (budgetUsd == null || budgetUsd === "") {
    return { ok: true, value: undefined };
  }
  if (typeof budgetUsd === "number") {
    if (!Number.isFinite(budgetUsd) || budgetUsd <= 0) {
      return { ok: false, error: "Budget must be a positive dollar amount." };
    }
    return { ok: true, value: budgetUsd };
  }
  const raw = String(budgetUsd).trim();
  if (raw === "") return { ok: true, value: undefined };
  const n = Number(raw);
  if (!Number.isFinite(n) || n <= 0) {
    return { ok: false, error: "Budget must be a positive dollar amount." };
  }
  return { ok: true, value: n };
}

/** Build the POST /projects body, or a form-level validation error. */
export function buildCreatePayload(input: CreateFormInput): CreatePayloadResult {
  const title = input.title.trim();
  if (!title) return { ok: false, error: "Title is required." };
  if (title.length > 200) {
    return { ok: false, error: "Title must be 200 characters or fewer." };
  }

  const filename = safeFilename(input.file.name);
  if (filename.length > 255) {
    return { ok: false, error: "Filename is too long." };
  }

  const budget = normalizeBudget(input.budgetUsd);
  if (!budget.ok) return budget;

  const content_type = contentTypeForFile(input.file);
  if (!content_type) {
    return {
      ok: false,
      error:
        "Unrecognized video type — use mp4, mov, mkv, or webm (or a file whose browser type is one of those).",
    };
  }

  const body: CreateProjectRequest = {
    title,
    filename,
    content_type,
  };
  if (budget.value !== undefined) body.budget_usd = budget.value;
  return { ok: true, body };
}
