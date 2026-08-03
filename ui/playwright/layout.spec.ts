/** Layout gate (the "I can't validate the UI myself" gap): screenshots are
 * the mechanical substitute for eyes, and geometry assertions catch the
 * classes neither axe (semantics) nor contrast (colour) can: overflow,
 * overlap, and a panel collapsing at some width.
 *
 * Three widths: the 1280 desktop, 768 just under the 1000px stage breakpoint
 * (viewer stacks above the rail), and 480 narrow. At every width nothing may
 * overflow the viewport horizontally. The screenshots are goldens: a layout
 * regression that a human would see on first paint fails the gate.
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
  source_sha256: "5f1bb4e419c9a3f090dac13627dc568897ec1e33b27cfe5bce06ce083e447c29",
  latest_run_id: "run_2",
  timeline_version: 5,
  scene_count: 5,
  event_count: 57,
  readiness_percent: 83,
  readiness: [
    { key: "source", label: "Source video stored in B2", state: "ok", detail: "standoff_kling.mp4" },
    { key: "scenes", label: "Scene analysis complete", state: "ok", detail: "5 scenes detected" },
    { key: "timeline", label: "Foley plan proposed", state: "ok", detail: "timeline v5" },
    { key: "mix", label: "Foley mix rendered", state: "ok", detail: "full tier, 5 stems" },
    { key: "qa", label: "QA checks", state: "warn", detail: "exposure_discontinuity" },
    { key: "export", label: "Final export approved", state: "pending", detail: null },
  ],
  has_export: false,
  blocks_export: false,
  mix_run_id: "run_2",
  source_url: "https://cdn/src.mp4",
  mix_url: "https://cdn/mix.mp4",
  export_url: null,
  manifest_url: null,
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
      source_duration_s: 2.0,
      stem_key: "cache/bell.wav",
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
      source_duration_s: 0.9,
      stem_key: "cache/shot.wav",
      scene_id: "scene_05",
    },
  ],
};

const RUNS = [
  {
    id: "run_2",
    project_id: "proj_PW",
    kind: "mix",
    status: "completed",
    created_at: "2026-08-02T10:00:00Z",
    timeline_version: 5,
    parent_run_id: null,
    genblaze_run_id: null,
    current_stage: "qa",
    error: null,
    error_code: null,
    stages: [
      { name: "stems", status: "succeeded", elapsed_s: 12, retries: 0, provider: "genblaze", error: null, genblaze_run_id: null, started_at: "2026-08-02T10:00:01Z", detail: "5/5 stems (0 cached)" },
      { name: "mix", status: "succeeded", elapsed_s: 3, retries: 0, provider: "foleyflow-ffmpeg", error: null, genblaze_run_id: null, started_at: "2026-08-02T10:00:13Z", detail: null },
    ],
  },
];

const SCENES = {
  scenes: [0, 1, 2, 3, 4].map((i) => ({
    id: `scene_0${i + 1}`,
    // Scene index is 1-based on the wire (scene_01 → index 1); the rail
    // renders it verbatim. A 0-based fixture hid the old `index + 1` bug.
    index: i + 1,
    start_s: i * 3,
    end_s: i * 3 + 3,
    duration_s: 3,
    stats: { mean_luma: 100 + i },
    thumbnail_url: null,
  })),
};

const BUDGET = {
  project_id: "proj_PW",
  budget_usd: 5,
  spent: 3.7,
  remaining: 1.3,
  estimate: true,
  ledger_writable: true,
  spent_estimated: 3.4,
  spent_actual: 0.3,
  thresholds: [
    { threshold: 0.5, crossed: true },
    { threshold: 0.8, crossed: false },
  ],
  by_model: [
    { endpoint: "gemini-2.5-flash", spent: 0.42 },
    { endpoint: "eleven_text_to_sound_v2", spent: 3.28 },
  ],
};

const LEDGER = {
  project_id: "proj_PW",
  total_count: 2,
  rows: [
    {
      timestamp: 1753305781.234,
      endpoint: "gemini-2.5-flash",
      cost_estimate: 0.005,
      cost_source: "estimate",
      success: true,
      error: null,
      project_id: "proj_PW",
      params_hash: "a1b2c3d4",
    },
    {
      timestamp: 1753305780.0,
      endpoint: "eleven_text_to_sound_v2",
      cost_estimate: 0.3,
      cost_source: "actual",
      success: true,
      error: null,
      project_id: "proj_PW",
      params_hash: null,
    },
  ],
  next_cursor: null,
  ledger_writable: true,
};

const EVENTS = [
  { id: "evt_1", type: "postproduction.project.created", actor: "system", created_at: "2026-08-02T10:00:00Z", run_id: null, payload: { title: "Western standoff (silent)" } },
  { id: "evt_2", type: "postproduction.repair.approved", actor: "director", created_at: "2026-08-02T10:06:00Z", run_id: "run_2", payload: { event_index: 2 } },
];

const QA = {
  run_id: "run_2",
  overall_status: "needs_review",
  audio: {
    integrated_lufs: -18.3,
    true_peak_dbtp: -1.2,
    target_lufs: -14.0,
    target_true_peak_dbtp: -1.0,
    loudness_range_lu: 19.3,
    measured: true,
    measured_run_id: "run_2",
    issues: [
      {
        severity: "warning",
        code: "loudness_out_of_range",
        scene_id: null,
        compared_with: null,
        confidence: null,
        detail: "integrated loudness -18.3 LUFS is 4.3 LU from target -14.0",
      },
    ],
  },
  visual: {
    luma_delta_threshold: 20.0,
    issues: [
      {
        severity: "warning",
        code: "exposure_discontinuity",
        scene_id: "scene_05",
        compared_with: "scene_04",
        confidence: 0.79,
        detail: "mean luma jumps 31.8 between scene_04 (106.7) and scene_05 (138.4)",
      },
    ],
  },
};

const PEAKS = {
  peaks_per_second: 10,
  channels: 1,
  sample_rate: 100,
  duration_s: 15,
  min: Array.from({ length: 150 }, (_, i) => -0.2 - 0.3 * Math.abs(Math.sin(i / 8))),
  max: Array.from({ length: 150 }, (_, i) => 0.3 + 0.6 * Math.abs(Math.sin(i / 8))),
};

const QUOTA = {
  estimate: true,
  ledger_writable: true,
  // Models with a declared daily allowance. The paid SFX tier is counted
  // from the ElevenLabs workspace usage report (real requests + dollars);
  // billed-only endpoints with no allowance (e.g. the fal SFX fallback) do
  // not appear here at all.
  models: [
    {
      model_id: "gemini-2.5-flash",
      status: "warn",
      per_minute: { used: 1, limit: 5, remaining: 4, fraction: 0.2, exhausted: false },
      per_day: { used: 18, limit: 20, remaining: 2, fraction: 0.9, exhausted: false },
      resets_at: "2026-08-04T00:00:00-07:00",
    },
    {
      model_id: "eleven_text_to_sound_v2",
      status: "ok",
      per_minute: { used: 0, limit: null, remaining: null, fraction: null, exhausted: false },
      per_day: { used: 7, limit: 20, remaining: 13, fraction: 0.35, exhausted: false, cost_usd: 5.27 },
      resets_at: "2026-08-04T00:00:00Z",
    },
    {
      model_id: "gemini-3.5-flash",
      status: "ok",
      per_minute: { used: 0, limit: 5, remaining: 5, fraction: 0, exhausted: false },
      per_day: { used: 0, limit: 20, remaining: 20, fraction: 0, exhausted: false },
      resets_at: "2026-08-04T00:00:00-07:00",
    },
    {
      model_id: "gemini-3.5-flash-lite",
      status: "ok",
      per_minute: { used: 0, limit: 15, remaining: 15, fraction: 0, exhausted: false },
      per_day: { used: 1, limit: 500, remaining: 499, fraction: 0.002, exhausted: false },
      resets_at: "2026-08-04T00:00:00-07:00",
    },
    {
      model_id: "gpt-5.6-luna",
      status: "ok",
      per_minute: { used: 0, limit: 500, remaining: 500, fraction: 0, exhausted: false },
      per_day: { used: 0, limit: 2000, remaining: 2000, fraction: 0, exhausted: false },
      resets_at: "2026-08-04T00:00:00-07:00",
    },
  ],
};

const CONFIG = {
  ok: true,
  version: "0.1.0",
  brand: "Auteur",
  git_sha: "abc123",
  build_time: "2026-08-03T12:00:00Z",
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
};

async function stubApi(page: Page): Promise<void> {
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    const json = (body: unknown) => route.fulfill({ json: body });
    if (path === "/api/v1/projects") return json({ projects: [PROJECT], next_cursor: null });
    if (path === "/api/v1/projects/proj_PW") return json(PROJECT);
    if (path === "/api/v1/projects/proj_PW/timeline") return json(TIMELINE);
    if (path === "/api/v1/projects/proj_PW/timeline/versions")
      return json({ versions: [1], latest: 1 });
    if (path === "/api/v1/projects/proj_PW/scenes") return json(SCENES);
    if (path === "/api/v1/projects/proj_PW/events") return json({ events: EVENTS });
    if (path === "/api/v1/projects/proj_PW/runs") return json({ runs: RUNS });
    if (path === "/api/v1/projects/proj_PW/runs/run_2")
      return json({ ...RUNS[0], status: "completed" });
    if (path === "/api/v1/projects/proj_PW/dossier")
      return json({
        project_id: "proj_PW",
        artifact_count: 1,
        total_bytes: 1536,
        artifacts: [
          { kind: "mix", key: "projects/proj_PW/runs/run_2/mix.wav", size_bytes: 1536, sha256: "a1b2c3d4e5f6a7b8c9d0" },
        ],
        budget: BUDGET,
      });
    if (path === "/api/v1/projects/proj_PW/qa") return json(QA);
    if (path === "/api/v1/projects/proj_PW/ledger") return json(LEDGER);
    if (path === "/api/v1/projects/proj_PW/budget") return json(BUDGET);
    if (path === "/api/v1/projects/proj_PW/peaks/mix")
      return json(PEAKS);
    if (path === "/api/v1/quota") return json(QUOTA);
    if (path === "/api/v1/config") return json(CONFIG);
    if (path === "/api/v1/unclaimed/ledger")
      return json({ rows: [], total_count: 0, next_cursor: null });
    if (path === "/api/v1/unclaimed/projects") return json({ projects: [] });
    if (path === "/api/v1/unclaimed/cache")
      return json({ rows: [], total_count: 0, next_cursor: null });
    if (path.includes("/preview/stems/"))
      return route.fulfill({ status: 404, json: { error: { code: "not_found" } } });
    return route.fulfill({ status: 404, json: { error: { code: "not_found", message: "", detail: {} } } });
  });
}

async function openControlRoom(page: Page): Promise<void> {
  await stubApi(page);
  await page.goto("/projects/proj_PW");
  // The h1 truncates at narrow widths (its text is present but the box can
  // shrink); wait on the brand and the tab row, which are always full-size.
  await expect(page.locator(".brand").first()).toBeVisible();
  await expect(page.getByRole("tab", { name: "Checklist" })).toBeVisible();
}

/** Nothing may push the page wider than the viewport: a horizontal scrollbar
 * at any width is a layout regression, and every interactive element must
 * sit inside the viewport box. */
async function expectNoHorizontalOverflow(page: Page): Promise<void> {
  const overflow = await page.evaluate(() => {
    const vw = document.documentElement.clientWidth;
    // Diagnostic only: an element that sticks out past the viewport but is
    // clipped by an ancestor's overflow (e.g. the horizontally scrolling
    // timeline grid) is not a document-overflow bug. The hard gate below is
    // document.documentElement.scrollWidth.
    const offenders: string[] = [];
    for (const el of document.querySelectorAll<HTMLElement>("body *")) {
      const r = el.getBoundingClientRect();
      if (r.width === 0 || r.right <= vw + 1) continue;
      let clipped = false;
      for (let p = el.parentElement; p && p !== document.body; p = p.parentElement) {
        const ox = getComputedStyle(p).overflowX;
        if (ox === "auto" || ox === "scroll" || ox === "hidden" || ox === "clip") {
          clipped = true;
          break;
        }
      }
      if (!clipped) {
        offenders.push(
          `${el.tagName.toLowerCase()}.${(el.className as string).toString().slice(0, 40)} right=${Math.round(r.right)}`,
        );
      }
    }
    return {
      scrollWidth: document.documentElement.scrollWidth,
      bodyScrollWidth: document.body.scrollWidth,
      vw,
      offenders: offenders.slice(0, 12),
    };
  });
  expect(
    overflow.scrollWidth <= overflow.vw,
    `document overflows: scrollWidth ${overflow.scrollWidth} > ${overflow.vw}\n${overflow.offenders.join("\n")}`,
  ).toBe(true);
  expect(overflow.bodyScrollWidth <= overflow.vw, "body overflows").toBe(true);
}

// -- geometry ---------------------------------------------------------------

test("1280px: viewer and rail sit side by side, nothing overflows", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await openControlRoom(page);
  await expectNoHorizontalOverflow(page);

  const grid = await page.evaluate(() => {
    const viewer = document.querySelector(".viewer")!.getBoundingClientRect();
    // `aside.rail` is the tab rail; the scene strip (SceneRail) shares the
    // plain `.rail` class, so be specific.
    const rail = document.querySelector("aside.rail")!.getBoundingClientRect();
    const stage = document.querySelector(".stage")!.getBoundingClientRect();
    return {
      viewer, rail, stage,
      sideBySide: rail.left >= viewer.right - 1,
      railAtRightEdge: Math.abs(rail.right - window.innerWidth) < 2,
    };
  });
  expect(grid.sideBySide, `viewer right ${grid.viewer.right}, rail left ${grid.rail.left}`).toBe(true);
  expect(grid.railAtRightEdge).toBe(true);
});

test("768px (under the 1000px breakpoint): the stage stacks, nothing overflows", async ({ page }) => {
  await page.setViewportSize({ width: 768, height: 900 });
  await openControlRoom(page);
  await expectNoHorizontalOverflow(page);

  const stacked = await page.evaluate(() => {
    const viewer = document.querySelector(".viewer")!.getBoundingClientRect();
    const rail = document.querySelector("aside.rail")!.getBoundingClientRect();
    return {
      viewerBelow: rail.top >= viewer.bottom - 1,
      railSpansWidth: Math.abs(rail.left - 0) < 2 && Math.abs(rail.right - window.innerWidth) < 2,
    };
  });
  expect(stacked.viewerBelow, "viewer should stack above the rail under 1000px").toBe(true);
  expect(stacked.railSpansWidth).toBe(true);
});

test("480px narrow: chrome, player and timeline all fit with zero horizontal overflow", async ({ page }) => {
  await page.setViewportSize({ width: 480, height: 800 });
  await openControlRoom(page);
  await expectNoHorizontalOverflow(page);

  // The player transport is the densest row — play, scrub, three A/B
  // segments, hint. It must not push the page wider.
  const transport = await page.evaluate(() => {
    const t = document.querySelector(".transport")!;
    const r = t.getBoundingClientRect();
    return { right: r.right, width: t.scrollWidth, clientWidth: t.clientWidth };
  });
  expect(transport.right, `transport right edge ${transport.right} > 480`).toBeLessThanOrEqual(480);
  expect(transport.width).toBeLessThanOrEqual(transport.clientWidth + 1);
});

// §6: money is stated before it is spent — at 480 the chrome wraps instead
// of hiding the meter and the budget chip.

test("480px: the readiness meter and the budget figure stay visible (money is never hidden)", async ({ page }) => {
  await page.setViewportSize({ width: 480, height: 800 });
  await openControlRoom(page);

  const meter = page.locator(".chrome .meter");
  const budget = page.locator(".chrome .budget");
  await expect(meter).toBeVisible();
  await expect(budget).toBeVisible();
  await expect(meter).toContainText("83%");
  await expect(budget).toContainText("$3.70");
  await expect(budget).toContainText("$5.00");
  // They sit inside the viewport, on the second chrome row.
  const box = await budget.boundingBox();
  expect(box).not.toBeNull();
  expect(box!.x + box!.width).toBeLessThanOrEqual(480);
});

// §7: the stacked viewer must not collapse to a broken lozenge.

test("480px: the viewer frame is a real height, not a 32px strip", async ({ page }) => {
  await page.setViewportSize({ width: 480, height: 800 });
  await openControlRoom(page);

  const frame = await page.locator(".frame").evaluate((el) => {
    const r = el.getBoundingClientRect();
    return { h: r.height, w: r.width };
  });
  expect(
    frame.h,
    `frame height ${frame.h}px — the collapsed 32px lozenge must not return`,
  ).toBeGreaterThanOrEqual(150);
  // And the video fills it (16:9 within the frame).
  // Two <video> elements exist (original + mix, the latter classed off).
  const video = await page.locator(".frame video:not(.off)").evaluate((el) => {
    const r = el.getBoundingClientRect();
    return { h: r.height, w: r.width };
  });
  expect(video.h, `video height ${video.h}px`).toBeGreaterThanOrEqual(120);
});

// -- the measured waveform actually paints ----------------------------------

test("the mix's measured peaks paint real pixels on the History tab", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await openControlRoom(page);
  await page.getByRole("tab", { name: "History" }).click();
  await expect(page.locator(".mixwave canvas")).toBeVisible();
  await page.waitForTimeout(200); // paint effect settles
  const painted = await page.locator(".mixwave canvas").evaluate((canvas) => {
    const ctx = (canvas as HTMLCanvasElement).getContext("2d");
    if (!ctx) return false;
    const { data } = ctx.getImageData(0, 0, canvas.width, canvas.height);
    let nonTransparent = 0;
    for (let i = 3; i < data.length; i += 4) if (data[i] > 0) nonTransparent += 1;
    return nonTransparent > 50;
  });
  expect(painted, "the waveform canvas should contain drawn pixels").toBe(true);
});

// -- the Evidence source row never breaks mid-phrase -----------------------

test("the Evidence source row keeps 'summed from <artifact>' on one line with a long source", async ({ page }) => {
  // §3 regression: with a long project id the phrase (.k) shrank and wrapped
  // while the artifact (.v) held its width — the source row read as two
  // broken lines. The phrase and the artifact must share one line; the
  // artifact truncates instead.
  await page.setViewportSize({ width: 1280, height: 800 });
  await stubApi(page);
  const LONG_ID = "proj_01KZ0JYN9M6Y4Q2WQTV3X8ZHD0EXPORTSLATEST";
  await page.route("**/api/v1/projects/proj_PW/budget", (route) =>
    route.fulfill({ json: { ...BUDGET, project_id: LONG_ID } }),
  );
  await page.goto("/projects/proj_PW");
  await expect(page.locator(".brand").first()).toBeVisible();
  await page.getByRole("tab", { name: "Spend" }).click();
  await expect(page.getByText("This project", { exact: true })).toBeVisible();

  // All tab panels are mounted (hidden ones too), so scope to the Spend panel.
  const row = page.locator("#tab-spend .evidence .src");
  await expect(row).toContainText("summed from");
  await expect(row).toContainText(LONG_ID);
  const line = await row.evaluate((el) => {
    const k = el.querySelector(".k")!.getBoundingClientRect();
    const v = el.querySelector(".v")!.getBoundingClientRect();
    return {
      // Baseline alignment with mixed font sizes offsets the tops; the real
      // regression was the phrase WRAPPING to a second line. Two elements on
      // one line have overlapping vertical ranges; a wrapped phrase does not.
      overlap: k.bottom > v.top && v.bottom > k.top,
      kHeight: k.height,
      vHeight: v.height,
      rowHeight: el.getBoundingClientRect().height,
      phraseText: el.querySelector(".k")!.textContent,
    };
  });
  expect(
    line.overlap,
    `phrase and artifact must share one line (${JSON.stringify(line)})`,
  ).toBe(true);
  // The phrase itself is one line of text, never two.
  expect(line.kHeight).toBeLessThanOrEqual(16);
  expect(line.phraseText).toBe("summed from");
});

// -- screenshots: the mechanical substitute for eyes ------------------------
//
// SKIPPED IN CI, deliberately. Baselines are rasterised on whatever machine
// generated them, and GitHub's Ubuntu runner has a different font stack from a
// developer box: the same DOM differs by 2-3% of pixels with no layout change
// at all. Comparing across those two environments only ever produces noise, and
// the tolerance needed to absorb it (>3%) is wide enough to hide a genuinely
// collapsed panel — which would make the gate worse than useless.
//
// So these run locally, where the baselines were made and where a human reviews
// the PNGs. What guards layout in CI is the behavioural half of this file above:
// zero horizontal overflow at three widths, the viewer frame's minimum height,
// the money-visible-at-480 assertion, and the Evidence source-row geometry.
// Those assert measured values rather than pixels and hold on any machine.
//
// Regenerate after an intended visual change, then LOOK at the PNGs:
//   npx playwright test layout.spec.ts --update-snapshots
const screenshotsOnlyLocally = () =>
  test.skip(
    !!process.env.CI,
    "screenshot baselines are machine-specific; the behavioural layout assertions above cover CI",
  );

test("screenshots: control room checklist tab, desktop", async ({ page }) => {
  screenshotsOnlyLocally();
  await page.setViewportSize({ width: 1280, height: 800 });
  await openControlRoom(page);
  await expect(page.getByRole("tab", { name: "Checklist" })).toBeVisible();
  await expect(page.getByText("Foley plan proposed")).toBeVisible();
  await expect(page.locator(".block").first()).toBeVisible();
  await expect(page).toHaveScreenshot("control-room-checklist-1280.png");
});

test("screenshots: control room spend tab, desktop", async ({ page }) => {
  screenshotsOnlyLocally();
  await page.setViewportSize({ width: 1280, height: 800 });
  await openControlRoom(page);
  await page.getByRole("tab", { name: "Spend" }).click();
  await expect(page.getByText("This project", { exact: true })).toBeVisible();
  await expect(page.getByText("Transactions", { exact: true })).toBeVisible();
  await expect(page).toHaveScreenshot("control-room-spend-1280.png");
});

test("screenshots: control room, narrow 480", async ({ page }) => {
  screenshotsOnlyLocally();
  await page.setViewportSize({ width: 480, height: 800 });
  await openControlRoom(page);
  await expect(page.getByRole("tab", { name: "Checklist" })).toBeVisible();
  await expect(page).toHaveScreenshot("control-room-480.png");
});

test("screenshots: projects index and config", async ({ page }) => {
  screenshotsOnlyLocally();
  await page.setViewportSize({ width: 1280, height: 800 });
  await stubApi(page);
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Projects" })).toBeVisible();
  await expect(page.getByText("Western standoff (silent)")).toBeVisible();
  await expect(page).toHaveScreenshot("projects-index-1280.png");

  await page.goto("/config");
  await expect(page.locator("p.summary")).toContainText("Deployment checks pass");
  await expect(page).toHaveScreenshot("config-1280.png");
});

// -- screenshots: dark mode --------------------------------------------------
// The same states under prefers-color-scheme: dark, so a token regression
// that only shows in one theme fails the gate (contrast.test.ts checks the
// token pairs numerically; this checks the rendered result).

test("dark screenshots: control room checklist tab, desktop", async ({ page }) => {
  screenshotsOnlyLocally();
  await page.emulateMedia({ colorScheme: "dark" });
  await page.setViewportSize({ width: 1280, height: 800 });
  await openControlRoom(page);
  await expect(page.getByRole("tab", { name: "Checklist" })).toBeVisible();
  await expect(page.getByText("Foley plan proposed")).toBeVisible();
  await expect(page).toHaveScreenshot("control-room-checklist-1280-dark.png");
});

test("dark screenshots: control room spend tab, desktop", async ({ page }) => {
  screenshotsOnlyLocally();
  await page.emulateMedia({ colorScheme: "dark" });
  await page.setViewportSize({ width: 1280, height: 800 });
  await openControlRoom(page);
  await page.getByRole("tab", { name: "Spend" }).click();
  await expect(page.getByText("This project", { exact: true })).toBeVisible();
  await expect(page.getByText("Transactions", { exact: true })).toBeVisible();
  await expect(page).toHaveScreenshot("control-room-spend-1280-dark.png");
});

test("dark screenshots: control room narrow 480 and projects index", async ({ page }) => {
  screenshotsOnlyLocally();
  await page.emulateMedia({ colorScheme: "dark" });
  await page.setViewportSize({ width: 480, height: 800 });
  await openControlRoom(page);
  await expect(page.getByRole("tab", { name: "Checklist" })).toBeVisible();
  await expect(page).toHaveScreenshot("control-room-480-dark.png");

  await page.setViewportSize({ width: 1280, height: 800 });
  await stubApi(page);
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Projects" })).toBeVisible();
  await expect(page).toHaveScreenshot("projects-index-1280-dark.png");
});

test("dark screenshots: config", async ({ page }) => {
  screenshotsOnlyLocally();
  await page.emulateMedia({ colorScheme: "dark" });
  await page.setViewportSize({ width: 1280, height: 800 });
  await stubApi(page);
  await page.goto("/config");
  await expect(page.locator("p.summary")).toContainText("Deployment checks pass");
  await expect(page).toHaveScreenshot("config-1280-dark.png");
});
