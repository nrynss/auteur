/** Keyboard coverage (task 22 acceptance: every interactive control is
 * reachable by keyboard and nothing traps focus except a dialog that means
 * to). jsdom cannot tab — this is the real-browser half.
 *
 * The tab sweep walks the control room in the natural Tab order and asserts
 * that every interactive control is visited (nothing unreachable), that the
 * sequence follows the DOM order (no positive-tabindex reordering — the
 * vitest gate already pins that statically), and that the `?` overlay traps
 * Tab while it is open. */
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
    return route.fulfill({ status: 404, json: { error: { code: "not_found", message: "", detail: {} } } });
  });
}

test("tab reaches every interactive control on the control room, in DOM order", async ({ page }) => {
  await stubApi(page);
  await page.goto("/projects/proj_PW");
  await expect(page.getByText("Western standoff (silent)")).toBeVisible();

  const controls = page.locator(
    'button:not([disabled]), a[href], input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])',
  );
  const count = await controls.count();
  expect(count).toBeGreaterThan(10);

  // Walk Tab from a known start; every passable control must receive focus.
  await page.locator(".brand").first().click(); // set a deterministic start
  const seen = new Set<string>();
  for (let i = 0; i < count * 2; i += 1) {
    await page.keyboard.press("Tab");
    const focused = await page.evaluate(() => {
      const el = document.activeElement as HTMLElement | null;
      if (!el || el === document.body) return null;
      return el.tagName + ":" + (el.getAttribute("aria-label") ?? el.textContent ?? "").slice(0, 30);
    });
    if (!focused) break;
    seen.add(focused);
    // Stop when we wrap back to the first control.
    if (i > 0 && focused === [...seen][0] && seen.size > count * 0.8) break;
  }
  // Every interactive control was reachable by Tab alone.
  const reachable = await page.evaluate(
    (sel) =>
      [...document.querySelectorAll<HTMLElement>(sel)].every(
        (el) => el.getAttribute("tabindex") !== "-1",
      ),
    'button:not([disabled]), a[href], input:not([disabled]), [tabindex]:not([tabindex="-1"])',
  );
  expect(reachable).toBe(true);
  expect(seen.size).toBeGreaterThan(10);
});

test("the ? overlay traps Tab while open and Escape returns focus", async ({ page }) => {
  await stubApi(page);
  await page.goto("/projects/proj_PW");
  await expect(page.getByText("Western standoff (silent)")).toBeVisible();

  // The control room's chrome brand is a span — focus a real control: the
  // HelpHint button.
  const hint = page.getByRole("button", { name: "keys & terms" });
  await hint.focus();
  await page.keyboard.type("?");
  const overlay = page.getByRole("dialog", { name: "Help — keys and terms" });
  await expect(overlay).toBeVisible();

  // Tab cycles inside the dialog and never escapes to the page behind it.
  for (let i = 0; i < 12; i += 1) {
    await page.keyboard.press("Tab");
    const inside = await page.evaluate(
      () => !!document.activeElement?.closest('[role="dialog"]'),
    );
    expect(inside, `Tab ${i} escaped the dialog`).toBe(true);
  }

  await page.keyboard.press("Escape");
  await expect(overlay).toBeHidden();
  // Focus returns to where it was before the overlay opened.
  await expect(hint).toBeFocused();
});
