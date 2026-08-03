/** RegenerateAction (unit 7.11) — the billable-repair consent flow. The
 * amount line appears ONLY after the API returned a numeric estimate, and
 * the second confirm echoes that exact number back as confirm_cost_usd.
 * Money is never invented: a sub-cent estimate must not round to $0.00, and
 * the changed-estimate trap must re-confirm rather than silently proceed.
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

import RegenerateAction from "./RegenerateAction.svelte";

type Call = { body: unknown };

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
  run_id: "run_reg1",
  kind: "regenerate",
  status: "running",
};

beforeEach(() => {
  calls = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (_input: string | URL, init?: RequestInit) => {
      const body = JSON.parse(String(init?.body ?? "null"));
      calls.push({ body });
      return jsonResponse(ACCEPTED, 202);
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
  document.body.innerHTML = "";
});

const settle = () => new Promise((r) => setTimeout(r, 0));

function mountRegen(props: Record<string, unknown>) {
  const target = document.createElement("div");
  document.body.appendChild(target);
  const app = mount(RegenerateAction, {
    target,
    props: { projectId: "proj_TEST", eventIndex: 2, eventName: "gunshot blast", ...props } as never,
  });
  return { target, app };
}

const btn = (target: HTMLElement, label: string) =>
  [...target.querySelectorAll("button")].find(
    (b) => (b.textContent ?? "").trim() === label,
  ) as HTMLButtonElement | undefined;

// The dialog's confirm can share its label with the chrome button behind it
// (both say "Regenerate" before a cost is known) — scope to the dialog.
const dialogBtn = (target: HTMLElement, label: string) =>
  [...target.querySelectorAll(".dialog button")].find(
    (b) => (b.textContent ?? "").trim() === label,
  ) as HTMLButtonElement | undefined;

it("is disabled with no event selected or under a run lock", () => {
  const none = mountRegen({ eventIndex: null });
  expect(btn(none.target, "Regenerate")?.disabled).toBe(true);

  const locked = mountRegen({ locked: true });
  expect(btn(locked.target, "Regenerate")?.disabled).toBe(true);
  unmount(none.app);
  unmount(locked.app);
});

it("the first confirm probes WITHOUT a cost — free work starts on the 202", async () => {
  const accepted: string[] = [];
  const { target, app } = mountRegen({
    onaccepted: (a: { run_id: string }) => accepted.push(a.run_id),
  });
  btn(target, "Regenerate")!.click();
  await settle();
  // Intent confirm — no amount shown yet, and the dialog says confirming
  // probes the cost.
  expect(target.textContent).toContain("Regenerate event 2");
  expect(target.textContent).not.toContain("This will cost");

  dialogBtn(target, "Regenerate")!.click(); // the intent confirm
  await settle();

  // The probe body carried no confirm_cost_usd.
  expect(calls).toEqual([{ body: {} }]);
  expect(accepted).toEqual(["run_reg1"]);
  // Dialog closed; the run id reached the parent.
  expect(target.textContent).not.toContain("Regenerate event 2");
  unmount(app);
});

it("a billable probe shows the EXACT estimate — never rounded — and echoes it back", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (_input: string | URL, init?: RequestInit) => {
      const body = JSON.parse(String(init?.body ?? "null"));
      calls.push({ body });
      if (body.confirm_cost_usd == null) {
        return jsonResponse(
          {
            error: {
              code: "precondition_failed",
              message: "billable work requires consent",
              detail: { estimated_cost_usd: 0.0375 },
            },
          },
          422,
        );
      }
      return jsonResponse(ACCEPTED, 202);
    }),
  );

  const { target, app } = mountRegen({});
  btn(target, "Regenerate")!.click();
  await settle();
  // Intent confirm → probe → the estimate arrives and the amount line appears.
  dialogBtn(target, "Regenerate")!.click();
  await settle();

  expect(target.textContent).toContain("This will cost");
  // The exact sub-cent estimate survives formatting (0.0375 → "$0.0375").
  expect(target.textContent).toContain("$0.0375");
  expect(target.textContent).not.toContain("$0.04");
  // The confirm label changes to the consent form.
  expect(target.textContent).toContain("Confirm cost & regenerate");

  btn(target, "Confirm cost & regenerate")!.click();
  await settle();

  // The echo body carries the exact number the UI displayed.
  expect(calls[1]).toEqual({ body: { confirm_cost_usd: 0.0375 } });
  unmount(app);
});

it("a free regeneration names the $0.00 estimate and never asks for a second confirm", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (_input: string | URL, init?: RequestInit) => {
      const body = JSON.parse(String(init?.body ?? "null"));
      calls.push({ body });
      if (body.confirm_cost_usd == null) {
        return jsonResponse(
          {
            error: {
              code: "precondition_failed",
              message: "billable work requires consent",
              detail: { estimated_cost_usd: 0 },
            },
          },
          422,
        );
      }
      return jsonResponse(ACCEPTED, 202);
    }),
  );

  const { target, app } = mountRegen({});
  btn(target, "Regenerate")!.click();
  await settle();
  dialogBtn(target, "Regenerate")!.click();
  await settle();

  expect(target.textContent).toContain("Estimate $0.00. Free, no billable consent required.");
  // The estimate of zero still renders as "$0.00" — not hidden, not "-".
  expect(target.textContent).toContain("$0.00");
  unmount(app);
});

it("a changed estimate on the echo confirm is refused — the old number is never spent", async () => {
  // The echoed confirm returned a DIFFERENT estimate: the needs_confirm path
  // is gated off for echoes (regenerate.test.ts pins that), so the run must
  // NOT start and the dialog must NOT linger on the stale figure — the API's
  // refusal is surfaced instead. Consent to $0.10 never spends $0.18.
  vi.stubGlobal(
    "fetch",
    vi.fn(async (_input: string | URL, init?: RequestInit) => {
      const body = JSON.parse(String(init?.body ?? "null"));
      calls.push({ body });
      if (body.confirm_cost_usd == null) {
        return jsonResponse(
          {
            error: {
              code: "precondition_failed",
              message: "billable work requires consent",
              detail: { estimated_cost_usd: 0.10 },
            },
          },
          422,
        );
      }
      return jsonResponse(
        {
          error: {
            code: "precondition_failed",
            message: "confirm_cost_usd must match the current estimate",
            detail: { estimated_cost_usd: 0.18, confirm_cost_usd: 0.1 },
          },
        },
        422,
      );
    }),
  );
  const errors: string[] = [];
  const { target, app } = mountRegen({ onerror: (m: string) => errors.push(m) });

  btn(target, "Regenerate")!.click();
  await settle();
  dialogBtn(target, "Regenerate")!.click();
  await settle();
  // The consent dialog shows the first estimate.
  expect(target.textContent).toContain("$0.10");
  btn(target, "Confirm cost & regenerate")!.click();
  await settle();

  // The run never started; the refusal reached the parent; no stale amount
  // lingers on a closed dialog.
  expect(calls[calls.length - 1].body).toEqual({ confirm_cost_usd: 0.10 });
  expect(errors).toEqual(["confirm_cost_usd must match the current estimate"]);
  expect(target.textContent).not.toContain("$0.10");
  unmount(app);
});
