<script lang="ts">
  /** Term — a glossary term rendered where it is used (task 22). Underlined
   * with a dotted rule so it reads as defined, focusable so a keyboard user
   * reaches the definition without a pointer, and wired through Tooltip to
   * the glossary — the definition lives in exactly one place. An undefined
   * term renders plainly; help.test.ts fails the suite if a rendered dense
   * term has no entry. */
  import Tooltip from "./Tooltip.svelte";
  import { termDef } from "./glossary";

  interface Props {
    term: string;
  }

  let { term }: Props = $props();
  const def = $derived(termDef(term));
</script>

{#if def}
  <Tooltip text={def.def}>
    <button type="button" class="term" aria-label={`${def.label}: ${def.def}`}>
      {def.label}
    </button>
  </Tooltip>
{:else}
  <span>{term}</span>
{/if}

<style>
  .term {
    font: inherit;
    color: inherit;
    background: transparent;
    border: 0;
    padding: 0;
    text-decoration: underline dotted;
    text-underline-offset: 3px;
    cursor: help;
    border-radius: 2px;
  }

  .term:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: 1px;
  }
</style>
