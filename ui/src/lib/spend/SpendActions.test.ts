/** SpendActions (unit 7.11) — the chrome Mix/Export path. Nothing here may
 * invent money: mix shows a projection only after the 202 arrives (and calls
 * it a projection, not a bill), export override is an explicit danger
 * confirm that records the actor. The run lock disables both.
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

import SpendActions from "./SpendActions.svelte";

type Call = { path: string; method: string; body: unknown };

let calls: Call[] = [];

function jsonResponse(body: unknown, status = 200) {
  return {
    ok: status < 400,
    status,
    json: async () => body,
    text: async () => JSON.stringify(body),
    headers: { get: () => "application/json" },
  } as unknown as Response;
}

const ACCEPTED = {
  run_id: "run_mix1",
  kind: "mix",
  status: "running",
  estimated_cost_usd: 0.42,
  projected_cost_usd: 0.42,
  cache_hits: 3,
  cache_misses: 2,
  projected_total_usd: 3.1,
  budget_usd: 5,
};

beforeEach(() => {
  calls = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string | URL, init?: RequestInit) => {
      const url = String(input);
      const path = url.replace("/api/v1/projects/proj_TEST", "");
      calls.push({ path, method: init?.method ?? "GET", body: JSON.parse(String(init?.body ?? "null")) });
      if (path === "/mixes") return jsonResponse(ACCEPTED, 202);
      if (path === "/exports") return jsonResponse(ACCEPTED, 202);
      return jsonResponse({ error: { code: "not_found" } }, 404);
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
  document.body.innerHTML = "";
});

const settle = () => new Promise((r) => setTimeout(r, 0));

function mountActions(props: Record<string, unknown>) {
  const target = document.createElement("div");
  document.body.appendChild(target);
  const app = mount(SpendActions, {
    target,
    props: { projectId: "proj_TEST", ...props } as never,
  });
  return { target, app };
}

const btn = (target: HTMLElement, label: string) =>
  [...target.querySelectorAll("button")].find(
    (b) => (b.textContent ?? "").trim() === label,
  ) as HTMLButtonElement | undefined;

it("renders Mix and Export; the run lock disables both", () => {
  const { target, app } = mountActions({});
  expect(btn(target, "Mix")?.disabled).toBe(false);
  expect(btn(target, "Export")?.disabled).toBe(false);

  const locked = mountActions({ locked: true });
  expect(btn(locked.target, "Mix")?.disabled).toBe(true);
  expect(btn(locked.target, "Export")?.disabled).toBe(true);
  unmount(app);
  unmount(locked.app);
});

it("Mix goes through a confirm dialog and posts the exact tier/version", async () => {
  const accepted: string[] = [];
  const { target, app } = mountActions({
    timelineVersion: 2,
    onaccepted: (_acc: unknown, line?: string) => accepted.push(line ?? ""),
  });

  btn(target, "Mix")!.click();
  await settle();
  // The dialog is explicit: what will happen, and that a projection is not a bill.
  expect(target.textContent).toContain("Start mix");
  expect(target.textContent).toContain("Cost projection is returned when the run is accepted. It prices cache misses only and is not a bill.");

  btn(target, "Start mix")!.click();
  await settle();

  expect(calls).toEqual([
    { path: "/mixes", method: "POST", body: { tier: "full", timeline_version: 2 } },
  ]);
  // The status line is the projection, and it says what a projection is.
  expect(accepted).toHaveLength(1);
  expect(accepted[0]).toContain("Mix accepted · run run_mix1");
  expect(accepted[0]).toContain("projection $0.42 (not a bill)");
  expect(accepted[0]).toContain("cache 3 hit / 2 miss");
  expect(accepted[0]).toContain("projected total $3.10 of $5.00 budget");
  unmount(app);
});

it("Export posts the mix run id, and a blocked export offers the explicit override", async () => {
  const overrides: unknown[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string | URL, init?: RequestInit) => {
      const path = String(input).replace("/api/v1/projects/proj_TEST", "");
      const body = JSON.parse(String(init?.body ?? "null"));
      if (path === "/exports" && body.override_blocking === true) {
        overrides.push(body);
        return jsonResponse(ACCEPTED, 202);
      }
      if (path === "/exports") {
        return jsonResponse(
          {
            error: {
              code: "precondition_failed",
              message: "QA blocks this export",
              detail: { blockers: ["integrated_lufs over target", "no provenance manifest"] },
            },
          },
          422,
        );
      }
      return jsonResponse({ error: { code: "not_found" } }, 404);
    }),
  );

  const { target, app } = mountActions({ mixRunId: "run_mix1" });
  btn(target, "Export")!.click();
  await settle();
  expect(target.textContent).toContain("Export latest mix");

  // First confirm: blocked → the override dialog appears listing blockers.
  // (The dialog's own confirm is also labelled "Export" — scope to the
  // dialog, not the chrome button behind it.)
  const dialogBtn = (label: string) =>
    [...target.querySelectorAll(".dialog button")].find(
      (b) => (b.textContent ?? "").trim() === label,
    ) as HTMLButtonElement | undefined;
  dialogBtn("Export")!.click();
  await settle();
  expect(target.textContent).toContain("Override QA blockers");
  expect(target.textContent).toContain("integrated_lufs over target");
  expect(target.textContent).toContain("no provenance manifest");
  expect(target.textContent).toContain("Override and export");

  btn(target, "Override and export")!.click();
  await settle();
  expect(overrides).toEqual([
    { override_blocking: true, run_id: "run_mix1" },
  ]);
  unmount(app);
});

it("a failed mix surfaces the API message through onerror and closes the dialog", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      jsonResponse({ error: { code: "quota_exhausted", message: "daily mix budget spent" } }, 429),
    ),
  );
  const errors: string[] = [];
  const { target, app } = mountActions({ onerror: (m: string) => errors.push(m) });

  btn(target, "Mix")!.click();
  await settle();
  btn(target, "Start mix")!.click();
  await settle();

  expect(errors).toEqual(["daily mix budget spent"]);
  // The dialog is gone — no dead modal left over a failed action.
  expect(target.textContent).not.toContain("Start mix");
  unmount(app);
});
