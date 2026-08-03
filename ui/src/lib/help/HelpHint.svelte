<script lang="ts">
  /** Help hint — the chrome affordance for the `?` keyboard map + glossary
   * overlay (task 22). A real button (keyboard-reachable, unlike a hint
   * glyph) labelled with the Kbd key and the surface it opens. */
  import Kbd from "../components/Kbd.svelte";
  import { openHelp } from "./helpStore.svelte";

  interface Props {
    /** Extra text after the key — defaults to "keys & terms". */
    label?: string;
  }

  let { label = "keys & terms" }: Props = $props();
</script>

<button type="button" class="hint" onclick={openHelp}>
  <Kbd>?</Kbd>
  <span class="txt">{label}</span>
</button>

<style>
  .hint {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    font: inherit;
    font-size: var(--text-11);
    color: var(--dim);
    background: transparent;
    border: 0;
    padding: 3px 6px;
    border-radius: 4px;
    cursor: pointer;
  }

  .hint:hover,
  .hint:focus-visible {
    color: var(--text);
    background: var(--line-soft);
  }

  .txt {
    letter-spacing: 0.02em;
  }
</style>
