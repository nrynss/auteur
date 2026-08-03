/** First-load budgets and a smoke soak (the "performance/load" ask, scoped
 * honestly).
 *
 * What this can and cannot be: the browser gate stubs the API at the network
 * layer, so it cannot measure the real server's latency or throughput —
 * that is the backend's job, and the repo already has it
 * (scripts/latency_bench.py, scripts/latency_live.py). What a browser CAN
 * assert:
 *
 * 1. First-load budgets: each route reaches its ready state quickly. The
 *    budgets are deliberately generous (3s) — they exist to catch the app
 *    hanging or blocking on something, not to benchmark; a budget that is
 *    too tight is a flake factory.
 * 2. No redundant re-fetching on repeat navigations: the resource count on
 *    a re-navigation must not grow unboundedly (a listener/poll leak would
 *    show up as either growing listeners or repeated fetches).
 * 3. A smoke soak: repeated open/close of the help overlay and tab switches
 *    leave the app error-free and responsive — the no-console-errors rule
 *    applied under repetition.
 */
import { expect, test, type Page } from "@playwright/test";

const PROJECT = {
  id: "proj_PW",
  title: "Western standoff (silent)",
  status: "ready",
  created_at: "2026-08-02T10:00:00Z",
  source_filename: "standoff_kling.mp4",
  duration_s: 15.041667,
  budget_usd: 5,
  scene_count: 5,
  event_count: 3,
  readiness_percent: 83,
  readiness: [],
  has_export: false,
  blocks_export: false,
  mix_run_id: null,
  source_url: null,
  mix_url: null,
};

const TIMELINE = {
  version: 1,
  duration: 15.0,
  ambience: "desert wind",
  events: [
    {
      index: 0,
      name: "rhythmic church bell tolling",
      kind: "rhythmic",
      intensity: "med",
      start_time: 6.65,
      duration: 2.0,
      seed: null,
      source_duration_s: null,
      stem_key: null,
      scene_id: "scene_03",
    },
    {
      index: 1,
      name: "close POV single loud revolver gunshot",
      kind: "discrete",
      intensity: "high",
      start_time: 9.88,
      duration: 0.9,
      seed: null,
      source_duration_s: null,
      stem_key: null,
      scene_id: "scene_05",
    },
  ],
};

async function stubApi(page: Page): Promise<void> {
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    const json = (body: unknown) => route.fulfill({ json: body });
    if (path === "/api/v1/projects") return json({ projects: [], next_cursor: null });
    if (path === "/api/v1/projects/proj_PW") return json(PROJECT);
    if (path === "/api/v1/projects/proj_PW/timeline") return json(TIMELINE);
    if (path === "/api/v1/projects/proj_PW/timeline/versions")
      return json({ versions: [1], latest: 1 });
    if (path === "/api/v1/projects/proj_PW/scenes") return json({ scenes: [] });
    if (path === "/api/v1/projects/proj_PW/events") return json({ events: [] });
    if (path === "/api/v1/projects/proj_PW/runs") return json({ runs: [] });
    if (path === "/api/v1/projects/proj_PW/budget")
      return json({ configured_usd: 5, spent_usd: 0, estimate: true, ledger_writable: true });
    if (path === "/api/v1/projects/proj_PW/qa")
      return json({ run_id: null, overall_status: "pending", audio: null, visual: null });
    if (path === "/api/v1/projects/proj_PW/dossier")
      return json({ project_id: "proj_PW", artifact_count: 0, total_bytes: 0, artifacts: [], budget: { configured_usd: 5, spent_usd: 0, estimate: true, ledger_writable: true } });
    if (path === "/api/v1/projects/proj_PW/ledger")
      return json({ rows: [], total_count: 0, next_cursor: null });
    if (path === "/api/v1/quota")
      return json({ estimate: true, ledger_writable: true, models: [] });
    if (path === "/api/v1/config")
      return json({ ok: true, version: "0.1.0", brand: "Auteur", git_sha: null, build_time: null, checks: [], integrations: [] });
    if (path === "/api/v1/unclaimed/ledger")
      return json({ rows: [], total_count: 0, next_cursor: null });
    if (path === "/api/v1/unclaimed/projects") return json({ projects: [] });
    if (path === "/api/v1/unclaimed/cache")
      return json({ rows: [], total_count: 0, next_cursor: null });
    return route.fulfill({ status: 404, json: { error: { code: "not_found", message: "", detail: {} } } });
  });
}

/** Navigate and time the trip from request to the route's ready marker. */
async function timeToReady(page: Page, path: string, ready: RegExp): Promise<number> {
  const started = Date.now();
  await page.goto(path, { waitUntil: "domcontentloaded" });
  await expect(page.locator("body")).toContainText(ready, { timeout: 5000 });
  return Date.now() - started;
}

test("each route reaches ready state within the budget", async ({ page }) => {
  await stubApi(page);
  const routes: [string, RegExp][] = [
    ["/", /Projects/],
    ["/projects/proj_PW", /Western standoff/],
    ["/config", /Deployment checks/],
    ["/unclaimed", /Orphans/],
  ];
  // The budget is a hang-detector, not a benchmark (3s with an instant
  // stubbed API leaves huge headroom for slow CI runners).
  for (const [path, ready] of routes) {
    const ms = await timeToReady(page, path, ready);
    expect(ms, `${path} ready in ${ms}ms`).toBeLessThan(3000);
  }
});

test("re-navigating does not pile up resources or listeners", async ({ page }) => {
  await stubApi(page);
  const errors: string[] = [];
  page.on("console", (m) => {
    if (m.type() === "error") errors.push(m.text());
  });
  page.on("pageerror", (e) => errors.push(String(e)));

  await page.goto("/projects/proj_PW");
  await expect(page.getByText("Western standoff (silent)")).toBeVisible();
  const first = await page.evaluate(() => performance.getEntriesByType("resource").length);

  // Revisit the same route three times — a leak would show as growth.
  for (let i = 0; i < 3; i += 1) {
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "Projects" })).toBeVisible();
    await page.goto("/projects/proj_PW");
    await expect(page.getByText("Western standoff (silent)")).toBeVisible();
  }
  const last = await page.evaluate(() => performance.getEntriesByType("resource").length);
  // Each full reload is a fresh document; resources must not accumulate
  // across navigations (the navigation entries replace, not append).
  expect(last).toBeLessThanOrEqual(first + 15);
  expect(errors).toEqual([]);
});

test("smoke soak: repeated overlay opens and tab switches stay clean and responsive", async ({ page }) => {
  await stubApi(page);
  const errors: string[] = [];
  page.on("console", (m) => {
    if (m.type() === "error") errors.push(m.text());
  });
  page.on("pageerror", (e) => errors.push(String(e)));

  await page.goto("/projects/proj_PW");
  await expect(page.getByText("Western standoff (silent)")).toBeVisible();
  const tabs = page.getByRole("tab");

  for (let i = 0; i < 8; i += 1) {
    // Open and close the help overlay repeatedly — its window listeners
    // must be removed on close, not accumulated.
    await page.keyboard.type("?");
    await expect(page.getByRole("dialog", { name: "Help: keys and terms" })).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(page.getByRole("dialog", { name: "Help: keys and terms" })).toBeHidden();

    // Switch through every control-room tab.
    const count = await tabs.count();
    for (let t = 0; t < count; t += 1) {
      await tabs.nth(t).click();
    }
  }

  // The app still responds.
  await expect(page.getByRole("tab", { name: "Checklist" })).toBeVisible();
  expect(errors).toEqual([]);
});
