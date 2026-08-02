<script lang="ts">
  /** Panel — the rail/right-column unit: surface, hairline border, no nested
   * boxes, no shadows (separation by hairline per control-room.md §4). */
  import type { Snippet } from "svelte";

  interface Props {
    class?: string;
    children?: Snippet;
  }

  let { class: klass, children }: Props = $props();
</script>

<section class={klass ? `panel ${klass}` : "panel"}>
  {@render children?.()}
</section>

<style>
  .panel {
    background: var(--surface);
    border: 1px solid var(--line);
    border-radius: var(--r);
  }
</style>
