/** Tooltip action (handoff.md task 22). Wires the anchor to its tip:
 *
 * - `aria-describedby` on the interactive element itself (the first
 *   focusable descendant, or the wrapper when the child is disabled so the
 *   reason stays keyboard-reachable — a disabled button cannot take focus,
 *   so its explanation would otherwise be pointer-only);
 * - focus- and hover-triggered open/close via `onOpen`/`onClose`;
 * - Escape dismisses.
 *
 * The visual lives in Tooltip.svelte; this file owns the accessibility
 * wiring, which is the part that must not be reimplemented per call site.
 */

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), ' +
  'select:not([disabled]), textarea:not([disabled]), ' +
  '[tabindex]:not([tabindex="-1"])';

export interface TooltipTipOptions {
  id: string;
  onOpen: () => void;
  onClose: () => void;
}

export function tooltipTip(
  node: HTMLElement,
  opts?: TooltipTipOptions,
): { destroy: () => void; update?: (o: TooltipTipOptions) => void } {
  if (!opts || !opts.id) return { destroy() {} };
  const { id, onOpen, onClose } = opts;
  // The interactive element the help attaches to: the anchor itself, a
  // focusable descendant, or — because the common pattern is wrapping a
  // button's CONTENT (`<button><Tooltip><span>label</span></Tooltip></button>`)
  // — the anchor's parent button. Without the parent check the anchor would
  // become a second focus stop inside an enabled button.
  let target: HTMLElement | null = node.matches(FOCUSABLE)
    ? node
    : node.querySelector<HTMLElement>(FOCUSABLE);
  // The common pattern wraps a button's CONTENT
  // (`<button><Tooltip><span>label</span></Tooltip></button>`): the anchor's
  // parent is the interactive element. A disabled button is still the
  // target — focus cannot land on it, but its explanation stays hover-
  // reachable and the visible copy + `?` overlay serve keyboard users;
  // creating a focusable span inside a disabled button would be a
  // focusable-inside-disabled-control smell.
  if (!target && node.parentElement?.tagName === "BUTTON") {
    target = node.parentElement;
  }
  const wrapperFocusable = !target;
  if (!target) {
    // Non-interactive content (an informational chip, a key cell) that still
    // needs its explanation: the wrapper becomes the keyboard handle.
    target = node;
    node.tabIndex = 0;
  }
  target.setAttribute("aria-describedby", id);

  // Listeners go on the TARGET, not the anchor: when the target is the
  // parent button, focus/hover events fire on it, and they do not bubble
  // down to the anchor inside it.
  function onFocusIn() {
    onOpen();
  }
  function onFocusOut() {
    onClose();
  }
  function onMouseEnter() {
    onOpen();
  }
  function onMouseLeave() {
    onClose();
  }
  function onKeydown(e: KeyboardEvent) {
    if (e.key === "Escape") {
      onClose();
    }
  }

  target.addEventListener("focusin", onFocusIn);
  target.addEventListener("focusout", onFocusOut);
  target.addEventListener("mouseenter", onMouseEnter);
  target.addEventListener("mouseleave", onMouseLeave);
  target.addEventListener("keydown", onKeydown);

  const cleanup: { destroy: () => void; update?: (o: TooltipTipOptions) => void } = {
    destroy() {
      target?.removeEventListener("focusin", onFocusIn);
      target?.removeEventListener("focusout", onFocusOut);
      target?.removeEventListener("mouseenter", onMouseEnter);
      target?.removeEventListener("mouseleave", onMouseLeave);
      target?.removeEventListener("keydown", onKeydown);
      target?.removeAttribute("aria-describedby");
      if (wrapperFocusable) node.tabIndex = -1;
    },
    // The anchor's help text can flip while mounted (e.g. the budget chip's
    // ledger-writable flag): re-wire when the id changes.
    update(next?: TooltipTipOptions) {
      if (!next) {
        // The help text went away while mounted: tear the wiring down.
        cleanup.destroy();
        cleanup.destroy = () => {};
        return;
      }
      if (next.id !== id) {
        cleanup.destroy();
        const again = tooltipTip(node, next);
        cleanup.destroy = again.destroy;
        cleanup.update = again.update;
      }
    },
  };
  return cleanup;
}
