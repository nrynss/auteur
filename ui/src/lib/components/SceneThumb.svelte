<script lang="ts">
  /** Scene thumbnail — the thin scene rail's unit. Events are addressed by
   * index everywhere (control-room.md §7), so the index is always shown, and
   * the span is the honest seconds the API reports (mockup format), not a
   * label. */
  import { formatSeconds } from "../format";

  interface Props {
    index: number;
    src?: string | null;
    alt: string;
    start: number | null;
    end: number | null;
    current?: boolean;
    onclick?: (event: MouseEvent) => void;
  }

  let { index, src, alt, start, end, current = false, onclick }: Props = $props();
</script>

<button
  class="scene"
  class:current
  aria-current={current || undefined}
  onclick={onclick}
>
  {#if src}
    <img src={src} alt={alt} loading="lazy" />
  {:else}
    <span class="ph" aria-hidden="true">{index + 1}</span>
  {/if}
  <span class="m num">
    <span>{index + 1}</span>
    <span>{formatSeconds(start)}–{formatSeconds(end)}</span>
  </span>
</button>

<style>
  .scene {
    flex: 0 0 auto;
    width: 86px;
    padding: 0;
    border: 0;
    background: transparent;
    text-align: left;
    cursor: pointer;
    border-radius: var(--r-sm);
  }

  .scene img,
  .scene .ph {
    width: 86px;
    aspect-ratio: 16/9;
    display: block;
    border-radius: 4px;
    border: 1px solid var(--line);
  }

  .scene .ph {
    display: grid;
    place-items: center;
    color: var(--faint);
    font-family: var(--font-mono);
    font-size: var(--text-11);
    background: var(--sunken);
  }

  .scene[aria-current="true"] img,
  .scene[aria-current="true"] .ph {
    border-color: var(--accent);
    box-shadow: 0 0 0 1px var(--accent);
  }

  .scene:hover img,
  .scene:hover .ph {
    border-color: var(--faint);
  }

  .m {
    display: flex;
    justify-content: space-between;
    gap: 6px;
    margin-top: 4px;
    font-size: 9.5px;
    color: var(--faint);
  }

  .scene[aria-current="true"] .m {
    color: var(--text);
  }
</style>
