/** DirectorPanel (unit 7.12): the chat transcript, the one-decision-at-a-time
 * rule, and the fail-closed cost line. Renders props only — the API call and
 * the draft merge live in ControlRoom (tested there).
 *
 * @vitest-environment jsdom
 */
import { expect, it, afterEach } from "vitest";
import { mount, unmount } from "svelte";

import DirectorPanel, { type DirectorTurn } from "./DirectorPanel.svelte";

const BASE = {
  version: 1,
  duration: 15.04,
  ambience: "desert wind",
  events: [
    {
      index: 0,
      name: "boot on gravel",
      kind: "rhythmic",
      intensity: "low",
      start_time: 0.0,
      duration: 0.4,
      seed: null,
      source_duration_s: null,
    },
    {
      index: 1,
      name: "gunshot",
      kind: "discrete",
      intensity: "high",
      start_time: 11.0,
      duration: 1.5,
      seed: null,
      source_duration_s: null,
    },
  ],
};

const PROPOSAL = {
  project_id: "proj_TEST",
  base_version: 1,
  model: "gemini-3.5-flash-lite",
  instruction: "pull the gunshot back 80 ms",
  status: "proposed",
  message: "moved 1 event — free to apply; approve to fork your draft.",
  cost_usd: 0.005,
  cost_source: "estimate",
  ledger_writable: true,
  timeline: {
    duration: 15.04,
    ambience: "desert wind",
    events: [
      {
        index: 0,
        name: "boot on gravel",
        kind: "rhythmic",
        intensity: "low",
        start_time: 0.0,
        duration: 0.4,
        seed: null,
        source_duration_s: null,
      },
      {
        index: 1,
        name: "gunshot",
        kind: "discrete",
        intensity: "high",
        start_time: 10.92,
        duration: 1.5,
        seed: null,
        source_duration_s: null,
      },
    ],
  },
  edits: [
    {
      index: 1,
      old: { start_time: 11.0, duration: 1.5 },
      new: { start_time: 10.92, duration: 1.5 },
    },
  ],
};

function turnFor(overrides: Partial<DirectorTurn>): DirectorTurn {
  return {
    role: "assistant",
    text: "…",
    proposal: null,
    base: null,
    outcome: "noted",
    ...overrides,
  };
}

function mountPanel(props: Record<string, unknown> & {
  onSend: (i: string) => void;
}) {
  const target = document.createElement("div");
  document.body.appendChild(target);
  const app = mount(DirectorPanel, {
    target,
    props: {
      projectId: "proj_TEST",
      turns: [],
      busy: false,
      locked: false,
      active: null,
      baseTimeline: BASE,
      onApply: () => {},
      onDiscard: () => {},
      ...props,
    } as never,
  });
  return { target, app };
}

afterEach(() => {
  document.body.innerHTML = "";
});

function unmountAll(apps: unknown[]) {
  for (const a of apps) unmount(a as never);
}

it("invites the first instruction and names the model and the cost", () => {
  const { target, app } = mountPanel({ onSend: () => {} });
  expect(target.textContent).toContain("gemini-3.5-flash-lite");
  expect(target.textContent).toContain("Describe a change");
  unmount(app);
});

it("sends the instruction on submit and hands it to ControlRoom", async () => {
  let sent = "";
  const { target, app } = mountPanel({ onSend: (i: string) => (sent = i) });
  const input = target.querySelector("input") as HTMLInputElement;
  input.value = "move the footsteps 40 ms earlier";
  input.dispatchEvent(new Event("input", { bubbles: true }));
  target.querySelector("form")!.dispatchEvent(
    new Event("submit", { bubbles: true, cancelable: true }),
  );
  expect(sent).toBe("move the footsteps 40 ms earlier");
  unmount(app);
});

it("disables the composer while a proposal is under review — one decision at a time", () => {
  const { target, app } = mountPanel({
    onSend: () => {},
    turns: [turnFor({ text: "moved 1 event", proposal: PROPOSAL, base: BASE, outcome: "reviewing" })],
    active: PROPOSAL,
  });
  const input = target.querySelector("input") as HTMLInputElement;
  expect(input.disabled).toBe(true);
  expect(input.placeholder).toBe("Approve or discard the current proposal first");
  unmount(app);
});

it("renders the diff with the old→new placement and both cost halves", () => {
  const { target, app } = mountPanel({
    onSend: () => {},
    turns: [turnFor({ text: "moved 1 event", proposal: PROPOSAL, base: BASE, outcome: "reviewing" })],
    active: PROPOSAL,
  });
  expect(target.textContent).toContain("gunshot");
  expect(target.textContent).toContain("idx 1");
  expect(target.textContent).toContain("11.000s → 10.920s");
  // The call costs money; the move itself is free — both named.
  expect(target.textContent).toContain("call ~$0.0050 · moves free");
  expect(target.textContent).toContain("Apply to draft");
  expect(target.textContent).toContain("Discard");
  unmount(app);
});

it("fails closed when the ledger could not record the call", () => {
  const { target, app } = mountPanel({
    onSend: () => {},
    turns: [turnFor({ text: "moved 1 event", proposal: { ...PROPOSAL, ledger_writable: false }, base: BASE, outcome: "reviewing" })],
    active: { ...PROPOSAL, ledger_writable: false },
  });
  // No clean cost figure — the number is withheld and the reason given
  // (docs/api.md §3.10: an unrecorded call's cost must not be presented).
  expect(target.textContent).toContain("call billed but not recorded: ledger unwritable");
  expect(target.textContent).not.toContain("call ~$0.0050");
  unmount(app);
});

it("disables Apply while a run is in flight but keeps Discard live", () => {
  const { target, app } = mountPanel({
    onSend: () => {},
    locked: true,
    turns: [turnFor({ text: "moved 1 event", proposal: PROPOSAL, base: BASE, outcome: "reviewing" })],
    active: PROPOSAL,
  });
  const buttons = [...target.querySelectorAll("button")];
  const apply = buttons.find((b) => b.textContent?.includes("Apply"))!;
  const discard = buttons.find((b) => b.textContent?.includes("Discard"))!;
  expect((apply as HTMLButtonElement).disabled).toBe(true);
  expect((discard as HTMLButtonElement).disabled).toBe(false);
  unmount(app);
});

it("shows no-changes and invalid-output turns as plain messages with no apply affordance", () => {
  const { target, app } = mountPanel({
    onSend: () => {},
    turns: [
      turnFor({
        text: "The model proposed no changes.",
        proposal: { ...PROPOSAL, status: "no_changes", edits: [] },
        base: BASE,
        outcome: "noted",
      }),
      turnFor({
        text: "The model returned an unparseable proposal.",
        proposal: { ...PROPOSAL, status: "invalid_output", edits: [] },
        base: BASE,
        outcome: "noted",
      }),
    ],
  });
  expect(target.textContent).toContain("The model proposed no changes.");
  expect(target.textContent).toContain("The model returned an unparseable proposal.");
  expect(target.textContent).not.toContain("Apply to draft");
  unmount(app);
});

it("records applied and discarded outcomes in the transcript", () => {
  const { target, app } = mountPanel({
    onSend: () => {},
    turns: [
      turnFor({ text: "moved 1 event", proposal: PROPOSAL, base: BASE, outcome: "applied" }),
      turnFor({ text: "moved 2 events", proposal: PROPOSAL, base: BASE, outcome: "discarded" }),
    ],
  });
  expect(target.textContent).toContain("Applied to the draft");
  expect(target.textContent).toContain("Discarded. The timeline is untouched.");
  unmount(app);
});
