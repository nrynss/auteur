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
  import { tooltipTip } from "./tooltip";
  import type { Snippet } from "svelte";

  interface Props {
    text?: string;
    children?: Snippet;
  }

  let { text, children }: Props = $props();

  const tipId = uid();
  let open = $state(false);
  let anchor = $state<HTMLSpanElement | null>(null);
  let box = $state<HTMLDivElement | null>(null);
  // Position after the tip is in the DOM, and again on open.
  $effect(() => {
    if (!open || !anchor || !box) return;
    const r = anchor.getBoundingClientRect();
    const w = box.offsetWidth;
    const h = box.offsetHeight;
    const vw = window.innerWidth || 0;
    const vh = window.innerHeight || 0;
    let top = r.bottom + 6;
    if (top + h > vh) top = Math.max(6, r.top - h - 6); // flip above
    const left = Math.min(Math.max(r.left, 8), Math.max(8, vw - w - 8));
    box.style.top = `${top}px`;
    box.style.left = `${left}px`;
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
  <div bind:this={box} id={tipId} role="tooltip" class="tooltip" data-tooltip>
    {text}
  </div>
{/if}

<style>
  .tt-anchor {
    display: inline;
  }

  .tooltip {
    position: fixed;
    z-index: 60;
    max-width: 260px;
    padding: 6px 9px;
    font-size: var(--text-11);
    line-height: 1.45;
    color: var(--text);
    background: var(--raised);
    border: 1px solid var(--line);
    border-radius: var(--r);
    box-shadow: none; /* the one shadow is reserved for the video frame */
    pointer-events: none;
  }
</style>
