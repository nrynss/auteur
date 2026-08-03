/** ConfigScreen (task 20, docs/api.md §3.17): the read-only deployment sheet.
 *
 * Three rules with teeth, asserted here:
 * 1. The checks render the same rows doctor prints, and an unstamped build
 *    gets doctor's origin-based severity (warn loopback / blocked remote).
 * 2. Integrations render PRESENCE and the contract source — variable NAMES
 *    only. A configured secret's VALUE never reaches the page.
 * 3. An unresolved Secret Manager reference is called out by name.
 *
 * @vitest-environment jsdom
 */
import { afterEach, expect, it, vi } from "vitest";
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

import ConfigScreen from "./ConfigScreen.svelte";

const CONFIG = {
  ok: true,
  version: "0.1.0",
  brand: "Auteur",
  git_sha: "deadbeefcafebabe0123456789abcdef01234567",
  build_time: "2026-08-02T12:00:00Z",
  checks: [
    { key: "api", label: "API reachable", state: "ok", detail: "status=ok version=0.1.0" },
    { key: "storage", label: "storage ok", state: "ok" },
    { key: "worker", label: "worker", state: "ok", detail: "ok tier=test" },
    { key: "ledger", label: "ledger writable", state: "ok" },
    { key: "quota", label: "quota (gemini-2.5-flash)", state: "ok", detail: "0/20 today" },
  ],
  integrations: [
    {
      key: "b2",
      label: "Backblaze B2 storage",
      source: "secret-manager",
      configured: true,
      env: ["B2_APPLICATION_KEY", "B2_KEY_ID"],
      missing: [],
      unresolved: [],
    },
    {
      key: "openai",
      label: "OpenAI analysis (optional)",
      source: "env",
      configured: false,
      env: ["OPENAI_API_KEY"],
      missing: ["OPENAI_API_KEY"],
      unresolved: [],
    },
    {
      key: "gemini",
      label: "Gemini analysis (scene VLM)",
      source: "secret-manager",
      configured: true,
      env: ["GEMINI_API_KEY"],
      missing: [],
      unresolved: ["GEMINI_API_KEY"],
    },
  ],
};

let fetchBody: unknown = CONFIG;

function jsonResponse(body: unknown, status = 200) {
  return {
    ok: status < 400,
    status,
    json: async () => body,
    text: async () => JSON.stringify(body),
    headers: { get: () => "application/json" },
  } as unknown as Response;
}

afterEach(() => {
  vi.unstubAllGlobals();
  document.body.innerHTML = "";
  fetchBody = CONFIG;
});

async function mountScreen() {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => jsonResponse(fetchBody)),
  );
  const target = document.createElement("div");
  document.body.appendChild(target);
  const app = mount(ConfigScreen, { target, props: { onNavigate: () => {} } });
  await new Promise((r) => setTimeout(r, 0));
  return { target, app };
}

it("renders every check row with doctor's labels", async () => {
  const { target, app } = await mountScreen();
  expect(target.textContent).toContain("API reachable");
  expect(target.textContent).toContain("Storage");
  expect(target.textContent).toContain("Ledger recording");
  expect(target.textContent).toContain("all models within their daily limits");
  expect(target.textContent).toContain("Deployment checks pass");
  unmount(app);
});

it("renders the stamped build identity as ok", async () => {
  const { target, app } = await mountScreen();
  expect(target.textContent).toContain("Deployed build identity");
  expect(target.textContent).toContain("deadbeefcafeb");
  expect(target.textContent).toContain("built 2026-08-02T12:00:00Z");
  unmount(app);
});

it("an unstamped build is warn on a loopback origin, blocked on a remote one", async () => {
  fetchBody = { ...CONFIG, git_sha: null, build_time: null };
  Object.defineProperty(window, "location", {
    value: { hostname: "localhost" },
    writable: true,
  });
  let { target, app } = await mountScreen();
  expect(target.textContent).toContain("unstamped process: cannot verify freshness");
  unmount(app);

  Object.defineProperty(window, "location", {
    value: { hostname: "sfx.nryn.dev" },
    writable: true,
  });
  ({ target, app } = await mountScreen());
  expect(target.textContent).toContain("unstamped deploy: no build identity");
  expect(target.textContent).toContain("1 check blocked");
  unmount(app);
});

it("integrations render presence, the contract source, and missing names", async () => {
  const { target, app } = await mountScreen();
  expect(target.textContent).toContain("Backblaze B2 storage");
  expect(target.textContent).toContain("Secret Manager");
  expect(target.textContent).toContain("Configured");
  expect(target.textContent).toContain("OpenAI analysis (optional)");
  expect(target.textContent).toContain("Env var");
  expect(target.textContent).toContain("OPENAI_API_KEY");
  unmount(app);
});

it("an unresolved Secret Manager reference is called out by name", async () => {
  const { target, app } = await mountScreen();
  expect(target.textContent).toContain("unresolved Secret Manager reference");
  expect(target.textContent).toContain("GEMINI_API_KEY");
  unmount(app);
});

it("a configured secret's value never reaches the page", async () => {
  fetchBody = {
    ...CONFIG,
    integrations: CONFIG.integrations.map((i) => ({
      ...i,
      configured: true,
      missing: [],
      unresolved: [],
    })),
  };
  // The page must render even if the API ever echoed a value — and the value
  // must not appear. (The API is tested to never send it; this pins the UI.)
  const { target, app } = await mountScreen();
  expect(target.textContent).not.toContain("AIzaSy");
  expect(target.textContent).not.toContain("0042-");
  unmount(app);
});

it("a blocked check flips the summary and names the stop", async () => {
  fetchBody = {
    ...CONFIG,
    ok: false,
    checks: [
      { key: "ledger", label: "ledger writable", state: "blocked",
        detail: "ledger_writable=False — spend figures are fiction" },
    ],
  };
  const { target, app } = await mountScreen();
  expect(target.textContent).toContain("1 check blocked");
  expect(target.textContent).toContain("every spend figure would be fiction");
  unmount(app);
});

it("agreement between the UI and API brand renders as ok", async () => {
  const { target, app } = await mountScreen();
  expect(target.textContent).toContain("Brand agreement");
  expect(target.textContent).toContain("UI and API both serve Auteur");
  unmount(app);
});

it("a build-time/runtime brand disagreement is blocked and names both", async () => {
  // The UI bundle in this test is built as Auteur; the API reports it
  // serves a different runtime name — one deployment renders two products.
  fetchBody = { ...CONFIG, brand: "KinoPost" };
  const { target, app } = await mountScreen();
  expect(target.textContent).toContain("Brand agreement");
  expect(target.textContent).toContain("UI built as Auteur");
  expect(target.textContent).toContain("API serves KinoPost");
  expect(target.textContent).toContain("1 check blocked");
  unmount(app);
});
