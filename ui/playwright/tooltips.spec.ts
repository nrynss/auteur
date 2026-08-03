/** Focused browser gate (task 22 + the clipping review): the guarantees
 * jsdom cannot give — rendered tooltip geometry, hover/focus/Escape
 * behaviour, the `?` help overlay, and zero console errors across the
 * routes. The API is stubbed at the network level with the same fixtures
 * the vitest suite uses; the app is the built bundle served with an SPA
 * fallback.
 *
 * Not a full e2e suite: one spec, real chromium, the specific guarantees.
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

/** Intercept every /api/v1 call with the fixture the route needs. */
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
    if (path === "/api/v1/projects/proj_PW/qa")
      return json({ run_id: "run_1", audio: null, visual: null });
    if (path === "/api/v1/projects/proj_PW/ledger")
      return json({ rows: [], total_count: 0, next_cursor: null });
    if (path === "/api/v1/projects/proj_PW/budget")
      return json({ configured_usd: 5, spent_usd: 0, estimate: true, ledger_writable: true });
    if (path === "/api/v1/quota")
      return json({
        estimate: true,
        ledger_writable: true,
        models: [
          {
            model_id: "gemini-2.5-flash",
            status: "ok",
            per_minute: { used: 0, limit: 5, remaining: 5, fraction: 0, exhausted: false },
            per_day: { used: 0, limit: 20, remaining: 20, fraction: 0, exhausted: false },
            resets_at: "2026-08-04T00:00:00Z",
          },
        ],
      });
    if (path === "/api/v1/config")
      return json({
        ok: true,
        version: "0.1.0",
        brand: "Auteur",
        git_sha: null,
        build_time: null,
        checks: [
          { key: "api", label: "API reachable", state: "ok", detail: "status=ok" },
          { key: "storage", label: "storage ok", state: "ok" },
          { key: "worker", label: "worker", state: "ok" },
          { key: "ledger", label: "ledger writable", state: "ok" },
          { key: "quota", label: "quota", state: "ok" },
        ],
        integrations: [
          {
            key: "b2",
            label: "Backblaze B2 storage",
            source: "secret-manager",
            configured: true,
            env: ["B2_APPLICATION_KEY"],
            missing: [],
            unresolved: [],
          },
        ],
      });
    if (path === "/api/v1/unclaimed/ledger")
      return json({ rows: [], total_count: 0, next_cursor: null });
    if (path === "/api/v1/unclaimed/projects") return json({ projects: [] });
    if (path === "/api/v1/unclaimed/cache")
      return json({ rows: [], total_count: 0, next_cursor: null });
    return route.fulfill({ status: 404, json: { error: { code: "not_found", message: "", detail: {} } } });
  });
}

async function openControlRoom(page: Page): Promise<void> {
  await stubApi(page);
  await page.goto("/projects/proj_PW");
  // Wait for the chrome to render past the loading state.
  await expect(page.getByText("Western standoff (silent)")).toBeVisible();
}

function withinViewport(box: { x: number; y: number; width: number; height: number }, vp: { width: number; height: number }): boolean {
  return (
    box.x >= 0 &&
    box.y >= 0 &&
    box.x + box.width <= vp.width &&
    box.y + box.height <= vp.height
  );
}

function overlaps(a: { x: number; y: number; width: number; height: number }, b: { x: number; y: number; width: number; height: number }): boolean {
  return !(
    a.x + a.width <= b.x ||
    a.x >= b.x + b.width ||
    a.y + a.height <= b.y ||
    a.y >= b.y + b.height
  );
}

async function expectTipClearOf(page: Page, anchor: { boundingBox: () => Promise<{ x: number; y: number; width: number; height: number } | null> }) {
  const tip = page.locator('[role="tooltip"]');
  await expect(tip).toBeVisible();
  const tipBox = await tip.boundingBox();
  const anchorBox = await anchor.boundingBox();
  const vp = page.viewportSize() ?? { width: 1280, height: 720 };
  expect(tipBox, "tip visible with a box").not.toBeNull();
  expect(anchorBox, "anchor has a box").not.toBeNull();
  const t = tipBox!;
  const a = anchorBox!;
  expect(withinViewport(t, vp), `tip ${JSON.stringify(t)} outside the viewport ${JSON.stringify(vp)}`).toBe(true);
  expect(overlaps(t, a), "tip must not cover the anchor it explains").toBe(false);
}

// -- tooltip geometry -------------------------------------------------------

test("focusing a glossary term shows a tip inside the viewport, clear of the term", async ({ page }) => {
  await openControlRoom(page);
  const term = page.locator("button.term", { hasText: "readiness" });
  await term.focus();
  await expectTipClearOf(page, term);
});

test("hovering a button with help shows the tip too", async ({ page }) => {
  await openControlRoom(page);
  const mix = page.getByRole("button", { name: "Mix", exact: true });
  await mix.hover();
  await expectTipClearOf(page, mix);
  await expect(page.locator('[role="tooltip"]')).toContainText("Render every stem");
});

test("Escape dismisses an open tip", async ({ page }) => {
  await openControlRoom(page);
  const term = page.locator("button.term", { hasText: "readiness" });
  await term.focus();
  await expect(page.locator('[role="tooltip"]')).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.locator('[role="tooltip"]')).toBeHidden();
});

test("a tip near the bottom edge flips above its anchor instead of clipping", async ({ page }) => {
  await openControlRoom(page);
  // The inspector is the last row of the 100vh layout — the delete button
  // sits a hair above the viewport's bottom, so a tip below it would clip
  // and must flip above instead.
  await page.locator(".block").first().click();
  const del = page.getByRole("button", { name: "delete" });
  await expect(del).toBeVisible();
  const before = await del.boundingBox();
  expect(before).not.toBeNull();
  expect(before!.y + before!.height, "the delete button is near the bottom").toBeGreaterThan(650);
  await del.focus();
  const tip = page.locator('[role="tooltip"]');
  await expect(tip).toBeVisible();
  const tipBox = await tip.boundingBox();
  const vp = page.viewportSize() ?? { width: 1280, height: 720 };
  expect(tipBox).not.toBeNull();
  expect(withinViewport(tipBox!, vp), "flipped tip still inside the viewport").toBe(true);
  expect(tipBox!.y + tipBox!.height, "tip is above its anchor").toBeLessThanOrEqual(before!.y + 1);
});

test("a tip inside a scrolling rail is never clipped", async ({ page }) => {
  await openControlRoom(page);
  // Open the Spend tab: the transactions Term lives in the scrollable rail.
  await page.getByRole("tab", { name: "Spend" }).click();
  const term = page.locator("button.term", { hasText: "transactions" });
  await expect(term).toBeVisible();
  await term.focus();
  await expectTipClearOf(page, term);
});

// -- the ? help overlay ------------------------------------------------------

test("? opens the keyboard map and glossary; Escape closes and restores focus", async ({ page }) => {
  await openControlRoom(page);
  await page.keyboard.type("?");
  const overlay = page.getByRole("dialog", { name: "Help — keys and terms" });
  await expect(overlay).toBeVisible();
  await expect(overlay).toContainText("Keyboard map");
  await expect(overlay).toContainText("Glossary");
  await expect(overlay).toContainText("nudge the selected event 10 ms");
  await expect(overlay).toContainText("the onset of a one-shot sound");
  await page.keyboard.press("Escape");
  await expect(overlay).toBeHidden();
});

// -- console errors across the routes ----------------------------------------

test("no console errors or unhandled rejections on any route", async ({ page }) => {
  await stubApi(page);
  const errors: string[] = [];
  page.on("console", (m) => {
    if (m.type() === "error") errors.push(m.text());
  });
  page.on("pageerror", (e) => errors.push(String(e)));

  for (const path of ["/", "/projects/proj_PW", "/config", "/unclaimed"]) {
    await page.goto(path);
    await page.waitForTimeout(400);
  }
  expect(errors, `console errors on the routes:\n${errors.join("\n")}`).toEqual([]);
});
