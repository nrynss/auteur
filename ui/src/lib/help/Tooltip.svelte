<script module lang="ts">
  // Module-scope counter: tip ids must be document-unique across instances
  // (two tips mounted together must not share an aria-describedby id).
  let uidCounter = 0;
  function uid() {
    uidCounter += 1;
    return `tt-${uidCounter}`;
  }
</script>

<script lang="ts">
  /** Tooltip/popover (handoff.md task 22) — the accessible replacement for
   * native `title=` help. Focus- and hover-triggered, Escape-dismissible,
   * `aria-describedby`-wired by the tooltipTip action, and positioned so it
   * never covers what it explains (below the anchor, flipped above when the
   * viewport would clip it). The definition text comes from the glossary —
   * one source, so a term cannot be defined two ways. */
  import { clampTipPosition, tooltipTip } from "./tooltip";
  import type { Snippet } from "svelte";

  /** Teleport the tip to <body> so no ancestor can clip it or re-base its
   * fixed positioning (the clipped-help-tip class of bugs). */
  const portal = (node: HTMLElement): { destroy: () => void } => {
    document.body.appendChild(node);
    return {
      destroy() {
        node.remove();
      },
    };
  };

  interface Props {
    text?: string;
    children?: Snippet;
  }

  let { text, children }: Props = $props();

  const tipId = uid();
  let open = $state(false);
  let anchor = $state<HTMLSpanElement | null>(null);
  let box = $state<HTMLDivElement | null>(null);

  // Escape dismisses a tip whether it was opened by hover or by focus — a
  // hover-opened tip has no focused anchor to receive the key (the action's
  // own keydown only fires when the anchor is focused), so the listener is
  // document-wide while the tip is open.
  $effect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") open = false;
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  // Position after the tip is in the DOM, and again on open. The geometry
  // is the pure clampTipPosition (unit-tested; jsdom cannot measure layout).
  $effect(() => {
    if (!open || !anchor || !box) return;
    const r = anchor.getBoundingClientRect();
    const pos = clampTipPosition(
      { top: r.top, bottom: r.bottom, left: r.left },
      { width: box.offsetWidth, height: box.offsetHeight },
      { width: window.innerWidth || 0, height: window.innerHeight || 0 },
    );
    box.style.top = `${pos.top}px`;
    box.style.left = `${pos.left}px`;
  });
</script>

<span
  bind:this={anchor}
  class="tt-anchor"
  data-tooltip-anchor
  use:tooltipTip={text
    ? {
        id: tipId,
        onOpen: () => (open = true),
        onClose: () => (open = false),
      }
    : undefined}
>
  {@render children?.()}
</span>

{#if open && text}
  <div
    bind:this={box}
    id={tipId}
    role="tooltip"
    class="tooltip"
    data-tooltip
    use:portal
  >
    {text}
  </div>
{/if}

<style>
  .tt-anchor {
    display: inline;
  }

  /* The tip is portaled to <body> (use:portal): no ancestor can clip it or
   * re-base its fixed positioning (a transform/filter/backdrop-filter on an
   * ancestor would otherwise make `position: fixed` relative to THAT box,
   * and an overflow ancestor would clip it — the clipped-help-tip class of
   * bugs this exists to rule out). Viewport clamping happens in the
   * position effect; this layer keeps it inside the viewport horizontally
   * and lets long definitions wrap instead of overflowing an edge. */
  .tooltip {
    position: fixed;
    z-index: 60; /* above the dialog scrim (--z-dialog) and the help overlay */
    max-width: min(280px, calc(100vw - 16px));
    padding: 6px 9px;
    font-size: var(--text-12);
    line-height: 1.45;
    color: var(--text);
    background: var(--raised);
    border: 1px solid var(--line);
    border-radius: var(--r);
    box-shadow: none; /* the one shadow is reserved for the video frame */
    pointer-events: none;
  }
</style>
