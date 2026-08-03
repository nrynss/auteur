/**
 * Demo capture pins. Project ids and defaults live in inventory.json —
 * the single hardcode site. Env can override at runtime.
 *
 * Warmth (verified against the live service before capture):
 * - primary: Desert duel — timeline 200, mix+export, QA measured + visual
 *   shot-to-shot, dossier ~49 artifacts, budget $5 (spent $0 from warm cache).
 *
 * The Inhaler has real ledger rows (~$2.50) but its timeline endpoint 500s;
 * that flips ControlRoom's ancillaryFailed flag and the Spend panel fails
 * closed ("Spend could not be loaded") even though budget would load. Do not
 * use it until the timeline is fixed — film Spend on the primary seed instead.
 */
import path from "node:path";
import { fileURLToPath } from "node:url";

import inventory from "./inventory.json" with { type: "json" };

const HERE = path.dirname(fileURLToPath(import.meta.url));

/** Repo root (scripts/demo → ../..). */
export const REPO_ROOT = path.resolve(HERE, "../..");

/** Capture output — gitignored under demo/. */
export const OUT_DIR = path.join(REPO_ROOT, "demo/product");

/** Live service. Never the browser-gate stubs. */
export const BASE_URL =
  process.env.DEMO_BASE_URL?.replace(/\/$/, "") || inventory.defaultBaseUrl;

/**
 * Seeded projects. Override with DEMO_PROJECT_ID when re-seeding.
 * Default ids come only from inventory.json.
 */
export const PROJECTS = {
  /** All product clips (including Spend) until a seed has both ledger + healthy timeline. */
  primary: process.env.DEMO_PROJECT_ID || inventory.defaultProjects.primary,
} as const;

export type ProjectKey = keyof typeof PROJECTS;

/** Explicit pins the environment would otherwise decide (demoplan.md). */
export const VIEWPORT = { width: 1920, height: 1080 } as const;
export const COLOR_SCHEME = (process.env.DEMO_COLOR_SCHEME || "dark") as
  | "dark"
  | "light"
  | "no-preference";
export const REDUCED_MOTION = "reduce" as const;

/** recordVideo.size — leave unset and Playwright softens to an 800×800 fit. */
export const VIDEO_SIZE = { width: 1920, height: 1080 } as const;
