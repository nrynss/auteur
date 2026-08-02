/** Timeline resize grip as a Svelte action (control-room.md §5: "a real
 * role=separator, focusable, arrow-key operable"). Listeners and ARIA are
 * attached programmatically rather than in markup, so the a11y analyzer
 * never sees a plain div with handlers — and the component stays declarative. */

export interface GripOptions {
  min: number;
  value: number;
  onKey: (e: KeyboardEvent) => void;
  onDrag: (newHeight: number) => void;
}

export function timelineGrip(node: HTMLElement, opts: GripOptions) {
  node.setAttribute("role", "separator");
  node.setAttribute("aria-orientation", "horizontal");
  node.setAttribute("tabindex", "0");
  node.setAttribute("aria-label", "Resize timeline — arrow keys to adjust");
  node.setAttribute("aria-valuemin", String(opts.min));
  node.setAttribute("aria-valuenow", String(opts.value));

  let startY = 0;
  let startH = 0;
  let dragging = false;

  const onKey = (e: KeyboardEvent) => opts.onKey(e);

  const onDown = (e: PointerEvent) => {
    dragging = true;
    startY = e.clientY;
    startH = opts.value;
    document.body.classList.add("resizing");
    node.setPointerCapture(e.pointerId);
  };

  const onMove = (e: PointerEvent) => {
    if (!dragging) return;
    // Pointer up = smaller height (the grip sits on the timeline's top edge).
    opts.onDrag(startH - (e.clientY - startY));
  };

  const onUp = () => {
    dragging = false;
    document.body.classList.remove("resizing");
  };

  node.addEventListener("keydown", onKey);
  node.addEventListener("pointerdown", onDown);
  node.addEventListener("pointermove", onMove);
  node.addEventListener("pointerup", onUp);

  return {
    update(next: GripOptions) {
      node.setAttribute("aria-valuenow", String(next.value));
      Object.assign(opts, next);
    },
    destroy() {
      node.removeEventListener("keydown", onKey);
      node.removeEventListener("pointerdown", onDown);
      node.removeEventListener("pointermove", onMove);
      node.removeEventListener("pointerup", onUp);
      document.body.classList.remove("resizing");
    },
  };
}
