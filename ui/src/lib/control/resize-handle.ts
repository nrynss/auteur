/** Per-event resize handle as a Svelte action (unit 7.10 review): the same
 * machinery timeline-grip.ts gives the panel-height separator — role,
 * tabindex, aria-valuemin/max/now, a keydown listener AND the pointer
 * handlers — attached programmatically, so the a11y analyzer never sees a
 * plain span with interactions and a screen reader announces a labelled,
 * operable separator (WCAG 2.1.1: resize is a primary editing operation and
 * must not be pointer-only). */

export interface ResizeHandleOptions {
  /** The edge's current position in seconds (the value aria reports). */
  value: number;
  min: number;
  max: number;
  onKey: (e: KeyboardEvent) => void;
  onDown: (e: PointerEvent) => void;
  onMove: (e: PointerEvent) => void;
  onUp: (e: PointerEvent) => void;
}

export function resizeHandle(node: HTMLElement, opts: ResizeHandleOptions) {
  node.setAttribute("role", "separator");
  node.setAttribute("aria-orientation", "vertical");
  node.setAttribute("tabindex", "0");
  node.setAttribute("aria-valuemin", String(opts.min));
  node.setAttribute("aria-valuemax", String(opts.max));
  node.setAttribute("aria-valuenow", String(opts.value));

  const onKey = (e: KeyboardEvent) => opts.onKey(e);
  const onDown = (e: PointerEvent) => opts.onDown(e);
  const onMove = (e: PointerEvent) => opts.onMove(e);
  const onUp = (e: PointerEvent) => opts.onUp(e);

  node.addEventListener("keydown", onKey);
  node.addEventListener("pointerdown", onDown);
  node.addEventListener("pointermove", onMove);
  node.addEventListener("pointerup", onUp);
  node.addEventListener("pointercancel", onUp);

  return {
    update(next: ResizeHandleOptions) {
      node.setAttribute("aria-valuemin", String(next.min));
      node.setAttribute("aria-valuemax", String(next.max));
      node.setAttribute("aria-valuenow", String(next.value));
      Object.assign(opts, next);
    },
    destroy() {
      node.removeEventListener("keydown", onKey);
      node.removeEventListener("pointerdown", onDown);
      node.removeEventListener("pointermove", onMove);
      node.removeEventListener("pointerup", onUp);
      node.removeEventListener("pointercancel", onUp);
    },
  };
}
