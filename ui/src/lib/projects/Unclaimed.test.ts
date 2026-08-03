/** Mount-level test for the unclaimed archive (unit 7.17): three sections,
 * three truths. Orphan rows are claimable into a project or a new spend
 * archive; orphan prefixes are adoptable; shared cache stems render with the
 * never-claimable caveat and no claim control.
 *
 * @vitest-environment jsdom
 */
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { mount, unmount } from "svelte";

vi.hoisted(() => {
  (globalThis as unknown as { matchMedia: unknown }).matchMedia = (
    query: string,
  ) => ({
    matches: false,
    media: query,
    addEventListener: () => {},
    removeEventListener: () => {},
  });
});

import Unclaimed from "./Unclaimed.svelte";

const LEDGER = {
  total_count: 2,
  rows: [
    {
      timestamp: 1753305781,
      endpoint: "gemini-2.5-flash",
      cost_estimate: 0.3,
      cost_source: "estimate",
      success: false,
      error: "auth failure",
      project_id: null,
      params_hash: "o2",
      url: "file:///ledger/o2.json",
    },
    {
      timestamp: 1753305780,
      endpoint: "eleven_text_to_sound_v2",
      cost_estimate: 0.05,
      cost_source: "actual",
      success: true,
      error: null,
      project_id: null,
      params_hash: "o1",
      url: "file:///ledger/o1.json",
    },
  ],
  next_cursor: null,
  ledger_writable: true,
};

const PROJECTS = {
  projects: [
    { project_id: "proj_01ARPHAN000000000000000000", object_count: 2, total_bytes: 14, unreadable: false },
    { project_id: "proj_01BR0K3N000000000000000000", object_count: 3, total_bytes: 42, unreadable: true },
  ],
};

const CACHE = {
  total_count: 1,
  rows: [
    { key: "cache/0000000000000000000.wav", size_bytes: 9, url: "file:///cache/0.wav" },
  ],
  next_cursor: null,
};

const PROJECT_LIST = {
  projects: [
    { id: "proj_01LIVE00000000000000000000", title: "Live", status: "ready", created_at: "2026-08-03T00:00:00Z", source_filename: "l.mp4", duration_s: 4, budget_usd: 5 },
  ],
  next_cursor: null,
};

let attributionsPosted = 0;
let claimsPosted = 0;
let adoptsPosted = 0;

function jsonResponse(body: unknown, status = 200) {
  return {
    ok: status < 400,
    status,
    json: async () => body,
    text: async () => JSON.stringify(body),
    headers: { get: () => "application/json" },
  } as unknown as Response;
}

beforeEach(() => {
  attributionsPosted = 0;
  claimsPosted = 0;
  adoptsPosted = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string | URL, init?: RequestInit) => {
      const path = String(input).split("?")[0];
      const method = init?.method ?? "GET";
      if (path === "/api/v1/unclaimed/ledger") return jsonResponse(LEDGER);
      if (path === "/api/v1/unclaimed/projects") return jsonResponse(PROJECTS);
      if (path === "/api/v1/unclaimed/cache") return jsonResponse(CACHE);
      if (path === "/api/v1/projects" && method === "GET") return jsonResponse(PROJECT_LIST);
      if (path.endsWith("/attributions") && method === "POST") {
        attributionsPosted += 1;
        return jsonResponse({ project_id: "proj_01LIVE00000000000000000000", before: 0, claimed: 2 }, 201);
      }
      if (path === "/api/v1/unclaimed/ledger/claim" && method === "POST") {
        claimsPosted += 1;
        return jsonResponse(
          { project: PROJECT_LIST.projects[0], claimed: 2 },
          201,
        );
      }
      if (path.includes("/adopt") && method === "POST") {
        adoptsPosted += 1;
        return jsonResponse(
          { project: PROJECT_LIST.projects[0], repaired: false },
          201,
        );
      }
      return jsonResponse({ error: { code: "not_found" } }, 404);
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
  document.body.innerHTML = "";
});

const settle = () => new Promise((r) => setTimeout(r, 0));

async function mountScreen() {
  const target = document.createElement("div");
  document.body.appendChild(target);
  const app = mount(Unclaimed, {
    target,
    props: { onNavigate: () => {} },
  });
  for (let i = 0; i < 6; i += 1) await settle();
  return { target, app };
}

it("renders all three sections with their distinct truths", async () => {
  const { target, app } = await mountScreen();
  const text = target.textContent ?? "";

  // Orphan rows: money, endpoint, failure visible, inspect link.
  expect(text).toContain("Orphan ledger rows");
  expect(text).toContain("gemini-2.5-flash");
  expect(text).toContain("$0.30");
  expect(text).toContain("Failed");
  expect(text).toContain("auth failure");
  expect(target.querySelector('a[href="file:///ledger/o2.json"]')).toBeTruthy();

  // Orphan prefixes: both listed, the unreadable one flagged.
  expect(text).toContain("Orphan project prefixes");
  expect(text).toContain("proj_01ARPHAN000000000000000000");
  expect(text).toContain("Unreadable record");

  // Cache stems: shown WITH the never-claimable caveat, no claim control.
  expect(text).toContain("Shared cache stems");
  expect(text).toContain("never claimable");
  expect(text).toContain("cache/0000000000000000000.wav");
  // The cache section itself carries no claim or adopt control.
  const sections = [...target.querySelectorAll("section.panel")];
  const cacheSection = sections[sections.length - 1]!;
  const cacheButtons = [...cacheSection.querySelectorAll("button")];
  expect(cacheButtons.every((b) => !/(Claim|Adopt)/.test(b.textContent ?? ""))).toBe(true);
  // Only the ledger section carries claim controls — the sections do not
  // blend into one list with one button.
  const claimButtons = [...target.querySelectorAll("button")].filter((b) =>
    /Claim|spend archive/.test(b.textContent ?? ""),
  );
  expect(claimButtons.length).toBeGreaterThanOrEqual(2);

  unmount(app);
});

it("claiming rows into an existing project calls the attribution endpoint", async () => {
  const { target, app } = await mountScreen();
  const claim = [...target.querySelectorAll("button")].find((b) =>
    (b.textContent ?? "").includes("Claim into project"),
  )!;
  claim.click();
  await settle();
  // The dialog names the truth: it adopts every currently-unclaimed row.
  expect(target.textContent).toContain("every currently-unclaimed ledger row");
  const confirm = [...target.querySelectorAll("button")].find((b) =>
    (b.textContent ?? "").includes("Claim rows"),
  )!;
  confirm.click();
  await settle();
  expect(attributionsPosted).toBe(1);
  expect(target.textContent).toContain("Claimed 2 orphan rows");
  unmount(app);
});

it("creating a spend archive posts the claim endpoint", async () => {
  const { target, app } = await mountScreen();
  const create = [...target.querySelectorAll("button")].find((b) =>
    (b.textContent ?? "").includes("Create spend archive"),
  )!;
  create.click();
  await settle();
  const input = target.querySelector("input") as HTMLInputElement;
  input.value = "Legacy spend";
  input.dispatchEvent(new Event("input", { bubbles: true }));
  const confirm = [...target.querySelectorAll("button")].find((b) =>
    (b.textContent ?? "").includes("Create & claim"),
  )!;
  confirm.click();
  await settle();
  expect(claimsPosted).toBe(1);
  expect(target.textContent).toContain("claimed 2 orphan rows");
  unmount(app);
});

it("adopting an orphan prefix posts the adopt endpoint", async () => {
  const { target, app } = await mountScreen();
  const adopt = [...target.querySelectorAll("button")].find((b) =>
    (b.textContent ?? "").includes("Adopt…"),
  )!;
  adopt.click();
  await settle();
  expect(target.textContent).toContain("Writes the project record for this prefix's own id");
  const confirm = [...target.querySelectorAll("button")].find((b) =>
    (b.textContent ?? "").trim() === "Adopt",
  )!;
  confirm.click();
  await settle();
  expect(adoptsPosted).toBe(1);
  unmount(app);
});

it("withholds orphan rows when the ledger is unwritable", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string | URL) => {
      const path = String(input).split("?")[0];
      if (path === "/api/v1/unclaimed/ledger")
        return jsonResponse({ ...LEDGER, ledger_writable: false });
      if (path === "/api/v1/unclaimed/projects") return jsonResponse(PROJECTS);
      if (path === "/api/v1/unclaimed/cache") return jsonResponse(CACHE);
      return jsonResponse({ error: { code: "not_found" } }, 404);
    }),
  );
  const { target, app } = await mountScreen();
  expect(target.textContent).toContain("Ledger unwritable — figures withheld");
  // No money figure may survive (docs/api.md §3.10).
  expect(target.textContent).not.toContain("$0.30");
  expect(target.textContent).not.toContain("Claim into project");
  unmount(app);
});
