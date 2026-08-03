/**
 * Product footage capture against the live seeded app.
 *
 * Each test = one clip = one browser context with recordVideo → one WebM
 * in demo/product/{id}.webm. Video is flushed on context.close(); path is
 * read only after that. Never stubs the API with fixtures (real backend only).
 */
import { test, expect, type Browser, type Page } from "@playwright/test";
import fs from "node:fs/promises";
import path from "node:path";

import {
  BASE_URL,
  COLOR_SCHEME,
  OUT_DIR,
  PROJECTS,
  REDUCED_MOTION,
  VIDEO_SIZE,
  VIEWPORT,
} from "./config.ts";
import { CLIPS, filterClips, type Clip } from "./clips.ts";
import { installSafety } from "./safety.ts";

const ONLY = process.env.DEMO_CLIP; // comma-separated ids, optional

test.describe.configure({ mode: "serial" });

test.beforeAll(async () => {
  await fs.mkdir(OUT_DIR, { recursive: true });
  await fs.mkdir(path.join(OUT_DIR, ".tmp"), { recursive: true });
  // Sanity: live service is up before we open a browser.
  const health = await fetch(`${BASE_URL}/api/v1/health`);
  if (!health.ok) {
    throw new Error(`Live service health failed: ${health.status} ${BASE_URL}`);
  }
});

for (const clip of filterClips(ONLY)) {
  test(`capture ${clip.id}: ${clip.title}`, async ({ browser }) => {
    await runClip(browser, clip);
  });
}

// ---------------------------------------------------------------------------
// Harness
// ---------------------------------------------------------------------------

async function runClip(browser: Browser, clip: Clip): Promise<void> {
  const projectId = PROJECTS[clip.project];
  const dest = path.join(OUT_DIR, `${clip.id}.webm`);

  const context = await browser.newContext({
    baseURL: BASE_URL,
    viewport: { ...VIEWPORT },
    colorScheme: COLOR_SCHEME,
    reducedMotion: REDUCED_MOTION,
    recordVideo: {
      dir: path.join(OUT_DIR, ".tmp"),
      size: { ...VIDEO_SIZE },
    },
    // Prefer dark film grain without OS bleed-through.
    ignoreHTTPSErrors: false,
  });

  const page = await context.newPage();
  const safety = await installSafety(page);

  try {
    await openProject(page, projectId);
    await BEATS[clip.id](page, clip);
    safety.assertClean();
  } finally {
    // Video file is written when the context closes — not when the page does.
    const video = page.video();
    await context.close();
    if (!video) {
      throw new Error(`No video recorded for ${clip.id}`);
    }
    const tmpPath = await video.path();
    await fs.rename(tmpPath, dest);
    const st = await fs.stat(dest);
    if (st.size < 10_000) {
      throw new Error(
        `${clip.id}: output too small (${st.size} bytes) — capture may have failed`,
      );
    }
    console.log(`[demo] wrote ${dest} (${(st.size / 1024 / 1024).toFixed(2)} MiB)`);
  }
}

async function openProject(page: Page, projectId: string): Promise<void> {
  await page.goto(`/projects/${projectId}`, { waitUntil: "domcontentloaded" });

  // Control room shell: tablist is always present once status === "ready".
  await expect(page.getByRole("tablist", { name: "Project" })).toBeVisible({
    timeout: 60_000,
  });

  await expect(page.getByRole("region", { name: "Timeline" })).toBeVisible({
    timeout: 45_000,
  });
  // Wait until at least one event block is addressable by index (no brand strings).
  await expect(
    page.locator('[role="button"][aria-label*="index "]').first(),
  ).toBeVisible({ timeout: 45_000 });

  // Dead head after context creation: hold a settled frame before the beat.
  await page.waitForTimeout(1_500);
}

// ---------------------------------------------------------------------------
// Beats — free only
// ---------------------------------------------------------------------------

type Beat = (page: Page, clip: Clip) => Promise<void>;

const BEATS: Record<string, Beat> = {
  async "01_room"(page, clip) {
    // Pan attention across the room: player, rail tabs, timeline blocks.
    const blocks = page.locator('[role="button"][aria-label*="index "]');
    const n = Math.min(await blocks.count(), 5);
    for (let i = 0; i < n; i++) {
      await blocks.nth(i).hover({ force: true });
      await page.waitForTimeout(700);
    }
    // Brief tab glance without leaving the "room first" feel too long.
    await page.getByRole("tab", { name: "Checklist" }).click();
    await page.waitForTimeout(1_200);
    await hold(page, clip.holdMs - 1_200 - n * 700);
  },

  async "04_director"(page, clip) {
    // Free surface only: open Director, show the costline + empty composer.
    // Do NOT type into the composer or submit — that bills a model call.
    await page.getByRole("tab", { name: "Director" }).click();
    await expect(
      page.getByRole("region", { name: "Director mode" }),
    ).toBeVisible();
    // Cost geometry is on screen in the empty state / costline.
    await expect(page.getByText(/call per proposal|billed/i).first()).toBeVisible({
      timeout: 15_000,
    });
    // Slow scroll of the panel so VO can land on "model proposes, human decides".
    await page.waitForTimeout(clip.holdMs);
  },

  async "05_listen"(page, clip) {
    const mixBtn = page.getByRole("button", { name: "Foley mix" });
    await expect(mixBtn).toBeEnabled({ timeout: 30_000 });
    await mixBtn.click();
    await page.waitForTimeout(800);

    // Ensure the mix video element has metadata before we press play.
    await page.waitForFunction(() => {
      const vids = Array.from(document.querySelectorAll("video"));
      return vids.some((v) => v.readyState >= 2 && !v.classList.contains("off"));
    }, null, { timeout: 45_000 }).catch(() => {
      /* still try play — signed URLs can be slow */
    });

    await page.getByRole("button", { name: "Play" }).click();
    // Play the mix for most of the hold, then a short Original contrast.
    const mixHold = Math.max(8_000, clip.holdMs - 8_000);
    await page.waitForTimeout(mixHold);

    const orig = page.getByRole("button", { name: "Original" });
    if (await orig.isEnabled()) {
      await orig.click();
      await page.waitForTimeout(3_000);
      await mixBtn.click();
      await page.waitForTimeout(3_000);
    } else {
      await page.waitForTimeout(6_000);
    }
  },

  async "07_nudge"(page, clip) {
    // Prefer a mid-timeline discrete event if present; fall back to index 0.
    const preferred =
      page.locator('[role="button"][aria-label*="index 1,"]').first();
    const fallback =
      page.locator('[role="button"][aria-label*="index 0,"]').first();
    const block = (await preferred.count()) ? preferred : fallback;
    await expect(block).toBeVisible();
    await block.click();
    await page.waitForTimeout(600);

    const box = await block.boundingBox();
    if (box) {
      // Drag right ~48px — free draft edit, no server call until Commit.
      const x = box.x + Math.min(box.width / 2, 24);
      const y = box.y + box.height / 2;
      await page.mouse.move(x, y);
      await page.mouse.down();
      await page.mouse.move(x + 48, y, { steps: 12 });
      await page.mouse.up();
      await page.waitForTimeout(800);
    }

    // Focus the block and coarse-nudge with Shift+Arrow (100 ms steps).
    await block.focus();
    for (let i = 0; i < 6; i++) {
      await page.keyboard.press("Shift+ArrowRight");
      await page.waitForTimeout(350);
    }
    for (let i = 0; i < 3; i++) {
      await page.keyboard.press("Shift+ArrowLeft");
      await page.waitForTimeout(350);
    }

    // Leave the draft dirty on screen — do not Commit (mutates versions).
    await hold(page, Math.max(4_000, clip.holdMs - 12_000));
  },

  async "08_spend"(page, clip) {
    await page.getByRole("tab", { name: "Spend" }).click();
    // Labels are "Spent", "Advisory budget", "Alert thresholds", "Transactions".
    const panel = page.locator("#tab-spend");
    await expect(
      panel.getByText(/Spent|Advisory budget|Alert thresholds|No spend recorded|Transactions/i).first(),
    ).toBeVisible({ timeout: 45_000 });
    await expect(panel.getByText(/Spend could not be loaded/i)).toHaveCount(0);
    await panel.evaluate((el) => {
      el.scrollTop = 0;
    });
    await page.waitForTimeout(2_000);
    await panel.evaluate((el) => {
      el.scrollTop = el.scrollHeight;
    });
    await page.waitForTimeout(2_000);
    await panel.evaluate((el) => {
      el.scrollTop = 0;
    });
    await hold(page, clip.holdMs - 4_000);
  },

  async "09_evidence"(page, clip) {
    await page.getByRole("tab", { name: "Evidence" }).click();
    await expect(
      page.locator("#tab-evidence").getByText(/loudness|LUFS|true peak|dBTP|shot-to-shot|No measurements/i).first(),
    ).toBeVisible({ timeout: 45_000 });
    await page.waitForTimeout(2_000);
    // Slow scroll for shot-to-shot cards if present.
    const panel = page.locator("#tab-evidence");
    await panel.evaluate((el) => {
      el.scrollTop = el.scrollHeight * 0.4;
    });
    await page.waitForTimeout(3_000);
    await panel.evaluate((el) => {
      el.scrollTop = el.scrollHeight;
    });
    await hold(page, Math.max(6_000, clip.holdMs - 5_000));
  },

  async "10_dossier"(page, clip) {
    await page.getByRole("tab", { name: "Artifacts" }).click();
    // Composing can take tens of seconds (one HEAD per object).
    await expect(
      page.locator("#tab-artifacts").getByText(/artifacts ·|No artifacts|could not be loaded/i),
    ).toBeVisible({ timeout: 120_000 });
    // Prefer the real inventory when present.
    const summary = page.locator("#tab-artifacts .summary");
    if (await summary.count()) {
      await expect(summary).toBeVisible();
    }
    await page.waitForTimeout(2_000);
    const panel = page.locator("#tab-artifacts");
    await panel.evaluate((el) => {
      el.scrollTop = el.scrollHeight * 0.3;
    });
    await page.waitForTimeout(3_000);
    await panel.evaluate((el) => {
      el.scrollTop = el.scrollHeight * 0.7;
    });
    await hold(page, Math.max(6_000, clip.holdMs - 5_000));
  },
};

// Guard: every inventory clip has a beat implementation.
for (const c of CLIPS) {
  if (!BEATS[c.id]) {
    throw new Error(`Missing beat implementation for ${c.id}`);
  }
}

async function hold(page: Page, ms: number): Promise<void> {
  if (ms > 0) await page.waitForTimeout(ms);
}
