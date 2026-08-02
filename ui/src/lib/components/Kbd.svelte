<script lang="ts">
  /** Kbd — keyboard-key display for the hint line and the keyboard map.
   * A real key's glyph, not a text decoration. */
  interface Props {
    children?: import("svelte").Snippet;
  }

  let { children }: Props = $props();
</script>

<kbd><s>{@render children?.()}</s></kbd>

<style>
  kbd {
    font-family: var(--font-mono);
    font-size: 10px;
    line-height: 1;
    color: var(--dim);
  }

  kbd s {
    display: inline-block;
    padding: 3px 5px;
    border: 1px solid var(--line);
    border-bottom-width: 2px;
    border-radius: 4px;
    background: var(--raised);
    text-decoration: none;
    font-weight: var(--w-500);
  }
</style>
