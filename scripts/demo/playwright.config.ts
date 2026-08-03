import { defineConfig } from "@playwright/test";
import { BASE_URL } from "./config.ts";

/**
 * Demo capture harness — separate from ui/playwright (task-22 correctness gate).
 * Real backend only. Never CI. Serial: one machine, one paid service, clear ledger.
 */
export default defineConfig({
  testDir: ".",
  testMatch: "capture.spec.ts",
  /* Dossier HEADs can take tens of seconds; each clip holds beyond VO budget. */
  timeout: 180_000,
  expect: { timeout: 30_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  use: {
    baseURL: BASE_URL,
    /* Video is opened per-clip with an explicit size; do not set recordVideo here. */
    headless: process.env.DEMO_HEADED === "1" ? false : true,
    actionTimeout: 20_000,
    navigationTimeout: 60_000,
    trace: "off",
    screenshot: "off",
  },
  projects: [{ name: "chromium", use: { browserName: "chromium" } }],
});
