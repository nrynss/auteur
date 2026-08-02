// @vitest-environment jsdom
/** ConfirmDialog interaction rules — the ones 7.11's spend paths rely on:
 * cost is shown before confirm, Escape and backdrop cancel, and a pending
 * operation cannot be cancelled mid-flight. */
import { afterEach, describe, expect, it } from "vitest";
import { mount, unmount } from "svelte";

import ConfirmDialog from "./ConfirmDialog.svelte";

interface Ctx {
  target: HTMLElement;
  calls: { confirm: number; cancel: number };
}

let mounted: ReturnType<typeof mount>[] = [];

function openDialog(overrides: Record<string, unknown> = {}): Ctx {
  const target = document.createElement("div");
  document.body.appendChild(target);
  const calls = { confirm: 0, cancel: 0 };
  mounted.push(
    mount(ConfirmDialog, {
      target,
      props: {
        open: true,
        title: "Regenerate stem 4",
        amount: "$0.30",
        ...overrides,
        onconfirm: () => (calls.confirm += 1),
        oncancel: () => (calls.cancel += 1),
      },
    }),
  );
  return { target, calls };
}

function dialog(target: HTMLElement): HTMLElement {
  return target.querySelector(".dialog")!;
}

function key(el: HTMLElement, keyName: string) {
  el.dispatchEvent(new KeyboardEvent("keydown", { key: keyName, bubbles: true }));
}

afterEach(() => {
  for (const c of mounted) unmount(c);
  mounted = [];
  document.body.innerHTML = "";
});

describe("ConfirmDialog", () => {
  it("renders the title and the cost the user is about to pay", () => {
    const { target } = openDialog();
    const text = target.textContent ?? "";
    expect(text).toContain("Regenerate stem 4");
    expect(text).toContain("This will cost");
    expect(text).toContain("$0.30");
  });

  it("Escape cancels", () => {
    const { target, calls } = openDialog();
    const dlg = dialog(target);
    console.log("DLG?", !!dlg, dlg?.className);
    dlg?.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    console.log("CANCEL IN TEST:", calls.cancel);
    expect(calls.cancel).toBe(1);
  });

  it("Escape does nothing while a spend path is pending", () => {
    const { target, calls } = openDialog({ busy: true });
    key(dialog(target), "Escape");
    expect(calls.cancel).toBe(0);
  });

  it("backdrop click cancels", () => {
    const { target, calls } = openDialog();
    target
      .querySelector(".backdrop")!
      .dispatchEvent(new MouseEvent("click", { bubbles: true }));
    expect(calls.cancel).toBe(1);
  });

  it("backdrop click does nothing while pending", () => {
    const { target, calls } = openDialog({ busy: true });
    target
      .querySelector(".backdrop")!
      .dispatchEvent(new MouseEvent("click", { bubbles: true }));
    expect(calls.cancel).toBe(0);
  });

  it("confirm fires the confirm handler", () => {
    const { target, calls } = openDialog();
    dialog(target)
      .querySelector<HTMLButtonElement>("button[data-autofocus]")!
      .click();
    expect(calls.confirm).toBe(1);
  });

  it("renders nothing when closed", () => {
    const { target } = openDialog({ open: false });
    expect(target.textContent).toBe("");
  });

  it("omits the cost line when there is no cost to show", () => {
    const { target } = openDialog({ amount: null });
    expect(target.textContent ?? "").not.toContain("This will cost");
  });

  it("moves focus to the confirm button on open", async () => {
    openDialog();
    // The focus effect runs on a setTimeout after mount.
    await new Promise((r) => setTimeout(r, 50));
    expect(document.activeElement?.hasAttribute("data-autofocus")).toBe(true);
  });
});
