#!/usr/bin/env node
/**
 * Entry point for the demo capture harness.
 *
 *   node run.mjs --list              # print clip inventory (no browser)
 *   node run.mjs                     # capture all product clips
 *   node run.mjs --clip 01_room      # re-shoot one clip
 *   node run.mjs --clip 01_room,05_listen
 *
 * Hits the live paid service (default https://auteur.nryn.dev). Free beats
 * only — never Mix / Export / Regenerate / director send. Ledger counts are
 * printed before and after so a session can prove it spent nothing.
 */
import { spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(HERE, "../..");
const OUT_DIR = path.join(REPO_ROOT, "demo/product");

// Keep in sync with config.ts / clips.ts — pure data so --list needs no TS.
const BASE_URL = (process.env.DEMO_BASE_URL || "https://auteur.nryn.dev").replace(
  /\/$/,
  "",
);
const PROJECTS = {
  primary: process.env.DEMO_PROJECT_ID || "proj_01KZ1151RJ9GP7MSRGHB0X77ET",
};

/** Mirrors clips.ts (ids + project key + notes). */
const CLIPS = [
  {
    id: "01_room",
    project: "primary",
    title: "Control room on a seeded project, real timeline",
    free: true,
  },
  {
    id: "04_director",
    project: "primary",
    title: "Director mode panel (free surface only — no send)",
    free: true,
  },
  {
    id: "05_listen",
    project: "primary",
    title: "A/B or mix playing",
    free: true,
  },
  {
    id: "07_nudge",
    project: "primary",
    title: "Free nudge: drag / arrow an event",
    free: true,
  },
  {
    id: "08_spend",
    project: "primary",
    title: "Spend tab: budget, thresholds, transactions",
    free: true,
  },
  {
    id: "09_evidence",
    project: "primary",
    title: "Evidence tab: loudness, true peak, shot-to-shot",
    free: true,
  },
  {
    id: "10_dossier",
    project: "primary",
    title: "Artifacts tab: full inventory with sizes",
    free: true,
  },
];

const args = process.argv.slice(2);
const listOnly = args.includes("--list") || args.includes("-l");
const clipArg = flagValue(args, "--clip") || flagValue(args, "-c");
const skipLedger = args.includes("--skip-ledger");

if (listOnly) {
  printList(clipArg);
  process.exit(0);
}

await main(clipArg);

// ---------------------------------------------------------------------------

async function main(only) {
  const selected = selectClips(only);
  console.log("Demo capture harness");
  console.log(`  baseURL:  ${BASE_URL}`);
  console.log(`  primary:  ${PROJECTS.primary}`);
  console.log(`  out:      ${OUT_DIR}`);
  console.log(`  clips:    ${selected.map((c) => c.id).join(", ")}`);
  console.log("");
  console.log("Will NOT click: Mix, Export, Regenerate, director send, cost confirm.");
  console.log("Will: navigate, hover, select, drag, A/B play, open tabs (free).");
  console.log("");

  fs.mkdirSync(OUT_DIR, { recursive: true });

  const projectIds = [...new Set(selected.map((c) => PROJECTS[c.project]))];
  let before = null;
  if (!skipLedger) {
    before = await ledgerSnapshot(projectIds);
    console.log("Ledger BEFORE:");
    printLedger(before);
    console.log("");
  }

  const env = {
    ...process.env,
    DEMO_BASE_URL: BASE_URL,
    DEMO_PROJECT_ID: PROJECTS.primary,
  };
  if (only) env.DEMO_CLIP = only;

  const code = await runPlaywright(env);
  if (code !== 0) {
    console.error(`\nPlaywright exited ${code}`);
    process.exit(code);
  }

  if (!skipLedger) {
    const after = await ledgerSnapshot(projectIds);
    console.log("\nLedger AFTER:");
    printLedger(after);
    console.log("");
    let dirty = false;
    for (const id of projectIds) {
      const b = before[id]?.total_count ?? null;
      const a = after[id]?.total_count ?? null;
      if (b !== a) {
        dirty = true;
        console.error(
          `FAIL: ledger total_count changed for ${id}: ${b} → ${a}`,
        );
      } else {
        console.log(`OK: ${id} total_count unchanged (${a})`);
      }
    }
    if (dirty) {
      console.error(
        "\nBillable rows appeared during capture. Inspect the ledger and re-shoot after fixing the harness.",
      );
      process.exit(2);
    }
  }

  console.log("\nOutputs:");
  for (const c of selected) {
    const p = path.join(OUT_DIR, `${c.id}.webm`);
    if (fs.existsSync(p)) {
      const st = fs.statSync(p);
      console.log(`  ${c.id}.webm  ${(st.size / 1024 / 1024).toFixed(2)} MiB`);
    } else {
      console.log(`  ${c.id}.webm  MISSING`);
    }
  }
  console.log("\nffprobe tip: ffprobe -v error -select_streams v:0 -show_entries stream=width,height -show_entries format=duration -of default=nw=1 demo/product/01_room.webm");
}

function selectClips(only) {
  if (!only) return CLIPS;
  const ids = only.split(",").map((s) => s.trim()).filter(Boolean);
  const out = [];
  for (const id of ids) {
    const c = CLIPS.find((x) => x.id === id);
    if (!c) {
      console.error(`Unknown clip: ${id}`);
      console.error(`Known: ${CLIPS.map((x) => x.id).join(", ")}`);
      process.exit(1);
    }
    out.push(c);
  }
  return out;
}

function printList(only) {
  const selected = selectClips(only);
  console.log(`baseURL: ${BASE_URL}`);
  console.log(`primary project: ${PROJECTS.primary}`);
  console.log("");
  console.log("id\tproject\tfree\ttitle");
  for (const c of selected) {
    const pid = PROJECTS[c.project];
    console.log(
      `${c.id}\t${c.project} (${pid})\t${c.free ? "yes" : "NO"}\t${c.title}`,
    );
  }
  console.log("");
  console.log("Not captured here (cards / VO elsewhere): 02_name, 03_chapter_director, 06_chapter_cost, 11_end");
  console.log("04_director: free panel only — ghost/approve needs a billed director turn (not run by default).");
}

async function ledgerSnapshot(projectIds) {
  const out = {};
  for (const id of projectIds) {
    try {
      const r = await fetch(`${BASE_URL}/api/v1/projects/${id}/ledger?limit=1`);
      if (!r.ok) {
        out[id] = { error: `HTTP ${r.status}`, total_count: null };
        continue;
      }
      const j = await r.json();
      out[id] = {
        total_count: j.total_count ?? null,
        ledger_writable: j.ledger_writable,
      };
    } catch (e) {
      out[id] = { error: String(e), total_count: null };
    }
  }
  return out;
}

function printLedger(snap) {
  for (const [id, v] of Object.entries(snap)) {
    if (v.error) console.log(`  ${id}: ERROR ${v.error}`);
    else console.log(`  ${id}: total_count=${v.total_count} writable=${v.ledger_writable}`);
  }
}

function runPlaywright(env) {
  return new Promise((resolve) => {
    const bin = path.join(HERE, "node_modules", ".bin", "playwright");
    const playwright = fs.existsSync(bin) ? bin : "npx";
    const args =
      playwright === "npx"
        ? ["playwright", "test", "-c", "playwright.config.ts"]
        : ["test", "-c", "playwright.config.ts"];
    const child = spawn(playwright === "npx" ? "npx" : bin, args, {
      cwd: HERE,
      env,
      stdio: "inherit",
    });
    child.on("exit", (code) => resolve(code ?? 1));
  });
}

function flagValue(argv, name) {
  const i = argv.indexOf(name);
  if (i === -1) return null;
  return argv[i + 1] || null;
}
