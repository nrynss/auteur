/** Basic navigation coverage (the gap the user flagged): real in-app
 * navigation — clicks, not deep-links. The console sweep deep-links via
 * `goto`; this spec walks the app the way a user does: index → create,
 * index → unclaimed, index → project, control-room tab switching, the brand
 * back-links, browser history (popstate), and `?` from a non-control-room
 * route. The API is stubbed as in the tooltip spec. */
import { expect, test, type Page } from "@playwright/test";

const SUMMARY = {
  id: "proj_PW",
  title: "Western standoff (silent)",
  status: "ready",
  created_at: "2026-08-02T10:00:00Z",
  source_filename: "standoff_kling.mp4",
  duration_s: 15.041667,
  budget_usd: 5,
  readiness_percent: 83,
};

const PROJECT = {
  ...SUMMARY,
  scene_count: 5,
  event_count: 3,
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
    if (path === "/api/v1/projects") return json({ projects: [SUMMARY], next_cursor: null });
    if (path === "/api/v1/projects/proj_PW") return json(PROJECT);
    if (path === "/api/v1/projects/proj_PW/timeline") return json(TIMELINE);
    if (path === "/api/v1/projects/proj_PW/timeline/versions")
      return json({ versions: [1], latest: 1 });
    if (path === "/api/v1/projects/proj_PW/scenes") return json({ scenes: [] });
    if (path === "/api/v1/projects/proj_PW/events") return json({ events: [] });
    if (path === "/api/v1/projects/proj_PW/runs") return json({ runs: [] });
    if (path === "/api/v1/projects/proj_PW/budget")
      return json({ configured_usd: 5, spent_usd: 0, estimate: true, ledger_writable: true });
    if (path === "/api/v1/unclaimed/ledger")
      return json({ rows: [], total_count: 0, next_cursor: null });
    if (path === "/api/v1/unclaimed/projects") return json({ projects: [] });
    if (path === "/api/v1/unclaimed/cache")
      return json({ rows: [], total_count: 0, next_cursor: null });
    return route.fulfill({ status: 404, json: { error: { code: "not_found", message: "", detail: {} } } });
  });
}

async function openIndex(page: Page): Promise<void> {
  await stubApi(page);
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Projects" })).toBeVisible();
}

test("index → new project → brand back-link", async ({ page }) => {
  await openIndex(page);
  await page.getByRole("button", { name: "New project" }).first().click();
  await expect(page).toHaveURL(/\/projects\/new$/);
  await expect(page.getByRole("heading", { name: /New project/ })).toBeVisible();
  // The brand link in the create chrome navigates back to the index.
  await page.locator(".brand").click();
  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByRole("heading", { name: "Projects" })).toBeVisible();
});

test("index → unclaimed → back to projects", async ({ page }) => {
  await openIndex(page);
  await page.getByRole("button", { name: "Unclaimed" }).click();
  await expect(page).toHaveURL(/\/unclaimed$/);
  await expect(page.getByRole("heading", { name: "Unclaimed" })).toBeVisible();
  await page.getByRole("button", { name: /Projects/ }).click();
  await expect(page).toHaveURL(/\/$/);
});

test("index → project card → control room, and browser Back returns", async ({ page }) => {
  await openIndex(page);
  await page.locator("a.row", { hasText: "Western standoff" }).click();
  await expect(page).toHaveURL(/\/projects\/proj_PW$/);
  await expect(page.getByText("Western standoff (silent)")).toBeVisible();
  await expect(page.getByRole("tab", { name: "Readiness" })).toBeVisible();
  // Browser history: Back returns to the index without a reload nav error.
  await page.goBack();
  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByRole("heading", { name: "Projects" })).toBeVisible();
});

test("control-room tabs switch content and aria-selected", async ({ page }) => {
  await stubApi(page);
  await page.goto("/projects/proj_PW");
  await expect(page.getByText("Western standoff (silent)")).toBeVisible();

  const tabs: [string, RegExp][] = [
    ["Readiness", /Post-production readiness|readiness/],
    ["Evidence", /No measurements yet|Loudness/],
    ["Director", /Describe a change/],
    ["Spend", /This project|transactions/],
    ["History", /Timeline versions/],
    ["Dossier", /No artifacts yet|Composing the dossier/],
  ];
  for (const [label, content] of tabs) {
    const tab = page.getByRole("tab", { name: label });
    await tab.click();
    await expect(tab).toHaveAttribute("aria-selected", "true");
    await expect(page.locator(`#tab-${label.toLowerCase()}`)).toBeVisible();
    await expect(page.locator(`#tab-${label.toLowerCase()}`)).toContainText(content);
  }
});

test("? opens help from the projects index too", async ({ page }) => {
  await openIndex(page);
  await page.keyboard.type("?");
  await expect(page.getByRole("dialog", { name: "Help — keys and terms" })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog", { name: "Help — keys and terms" })).toBeHidden();
});
