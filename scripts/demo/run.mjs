#!/usr/bin/env node
/**
 * Entry point for the demo capture harness.
 *
 *   node run.mjs --list              # print clip inventory (no browser)
 *   node run.mjs                     # capture all product clips
 *   node run.mjs --clip 01_room      # re-shoot one clip
 *   node run.mjs --clip 01_room,05_listen
 *
 * Hits the live paid service (default from inventory.json). Free beats
 * only — never Mix / Export / Regenerate / director send. Ledger counts are
 * printed before and after so a session can prove it spent nothing.
 *
 * Clip ids and default project ids: inventory.json (single source of truth).
 */
import { spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import inventory from "./inventory.json" with { type: "json" };

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(HERE, "../..");
const OUT_DIR = path.join(REPO_ROOT, "demo/product");

const BASE_URL = (
  process.env.DEMO_BASE_URL || inventory.defaultBaseUrl
).replace(/\/$/, "");

const PROJECTS = {
  primary: process.env.DEMO_PROJECT_ID || inventory.defaultProjects.primary,
};

/** Clip list — loaded from inventory.json, same file as the Playwright specs. */
const CLIPS = inventory.clips;

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

  const projectIds = [
    ...new Set(selected.map((c) => PROJECTS[c.project] || PROJECTS.primary)),
  ];

  let before = null;
  if (!skipLedger) {
    before = await ledgerSnapshot(projectIds);
    console.log("Ledger BEFORE:");
    printLedger(before);
    console.log("");
    const baselineErr = validateSnapshot(before, projectIds, "BEFORE");
    if (baselineErr) {
      console.error(baselineErr);
      console.error(
        "Cannot establish a ledger baseline — refusing to capture (fail closed).",
      );
      process.exit(2);
    }
  }

  const env = {
    ...process.env,
    DEMO_BASE_URL: BASE_URL,
    DEMO_PROJECT_ID: PROJECTS.primary,
  };
  if (only) env.DEMO_CLIP = only;

  // Always take AFTER ledger even when Playwright fails — money check is not
  // success-path only.
  const code = await runPlaywright(env);
  if (code !== 0) {
    console.error(`\nPlaywright exited ${code}`);
  }

  let ledgerExit = 0;
  if (!skipLedger) {
    const after = await ledgerSnapshot(projectIds);
    console.log("\nLedger AFTER:");
    printLedger(after);
    console.log("");
    ledgerExit = compareLedgers(before, after, projectIds);
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
  console.log(
    "\nffprobe tip: ffprobe -v error -select_streams v:0 -show_entries stream=width,height -show_entries format=duration -of default=nw=1 demo/product/01_room.webm",
  );

  if (code !== 0) process.exit(code);
  if (ledgerExit !== 0) process.exit(ledgerExit);
}

/**
 * Fail closed: every project must have a numeric total_count and no error.
 * Returns an error message, or null if the snapshot is usable.
 */
function validateSnapshot(snap, projectIds, label) {
  for (const id of projectIds) {
    const row = snap[id];
    if (!row) return `FAIL: ${label} ledger missing entry for ${id}`;
    if (row.error) {
      return `FAIL: ${label} ledger unreadable for ${id}: ${row.error}`;
    }
    if (typeof row.total_count !== "number" || !Number.isFinite(row.total_count)) {
      return `FAIL: ${label} ledger total_count not numeric for ${id}: ${row.total_count}`;
    }
  }
  return null;
}

/**
 * Compare before/after. Returns 0 if clean, 2 if dirty or unreadable.
 * null === null is NOT OK (that was the fail-open bug).
 */
function compareLedgers(before, after, projectIds) {
  const afterErr = validateSnapshot(after, projectIds, "AFTER");
  if (afterErr) {
    console.error(afterErr);
    console.error(
      "Cannot verify spend — treating session as unclean (fail closed).",
    );
    return 2;
  }

  let dirty = false;
  for (const id of projectIds) {
    const b = before[id].total_count;
    const a = after[id].total_count;
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
    return 2;
  }
  return 0;
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
  console.log(`inventory: ${path.join(HERE, "inventory.json")}`);
  console.log("");
  console.log("id\tproject\tfree\ttitle");
  for (const c of selected) {
    const pid = PROJECTS[c.project] || PROJECTS.primary;
    console.log(
      `${c.id}\t${c.project} (${pid})\t${c.free ? "yes" : "NO"}\t${c.title}`,
    );
  }
  console.log("");
  console.log(
    "Not captured here (cards / VO elsewhere): 02_name, 03_chapter_director, 06_chapter_cost, 11_end",
  );
  console.log(
    "04_director: free panel only — ghost/approve needs a billed director turn (not run by default).",
  );
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
      const n = j.total_count;
      out[id] = {
        total_count: typeof n === "number" && Number.isFinite(n) ? n : null,
        ledger_writable: j.ledger_writable,
        error:
          typeof n === "number" && Number.isFinite(n)
            ? undefined
            : "total_count missing or non-numeric",
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
    else
      console.log(
        `  ${id}: total_count=${v.total_count} writable=${v.ledger_writable}`,
      );
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
