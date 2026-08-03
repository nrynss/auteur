import { defineConfig } from "@playwright/test";

/** Focused browser gate (handoff.md task 22 + the clipping review): not a
 * full e2e suite — one spec that asserts what jsdom cannot: rendered tooltip
 * geometry (within the viewport, clear of the anchor, bottom-edge flip, inside
 * scrolling rails), hover/focus/Escape behaviour, the `?` help overlay, and
 * zero console errors across the routes.
 *
 * The app under test is the BUILT bundle (vite build → the package's static
 * dir), served by scripts/serve-static.mjs with an SPA fallback; the API is
 * stubbed at the network level with the same fixtures the vitest suite uses,
 * so no server, storage or credentials are needed.
 *
 * The static dir comes from the ui-build CI job as an artifact (it is
 * gitignored); `npm run build` produces it locally. */
export default defineConfig({
  testDir: "./playwright",
  timeout: 30_000,
  fullyParallel: true,
  use: {
    baseURL: "http://127.0.0.1:4173",
    trace: "retain-on-failure",
  },
  webServer: {
    command: "node scripts/serve-static.mjs",
    url: "http://127.0.0.1:4173",
    reuseExistingServer: !process.env.CI,
  },
  projects: [{ name: "chromium", use: { browserName: "chromium" } }],
});
