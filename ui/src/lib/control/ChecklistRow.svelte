<script lang="ts">
  /** One readiness row: state glyph + label + detail + a metric. The state
   * never travels as colour alone (control-room.md §2): the glyph and word
   * map to the same state. */
  import Icon from "../components/Icon.svelte";
  import type { ReadinessItem } from "../api/projects";

  interface Props {
    item: ReadinessItem;
  }

  let { item }: Props = $props();

  const GLYPH = {
    ok: "check",
    warn: "alert",
    blocked: "stop",
    pending: "dot",
  } as const;
  const WORD = {
    ok: "OK",
    warn: "Warning",
    blocked: "Blocked",
    pending: "Pending",
  } as const;
  const state = $derived(item.state as keyof typeof WORD);
</script>

<li class="row">
  <span class="glyph" class:g-ok={state === "ok"} class:g-warn={state === "warn"}
    class:g-stop={state === "blocked"} class:g-wait={state === "pending"}
    aria-hidden="true">
    <Icon name={GLYPH[state] ?? "dot"} size={9} />
  </span>
  <span>
    <span class="label">{item.label}</span>
    {#if item.detail}
      <span class="detail">{item.detail}</span>
    {/if}
  </span>
  {#if state === "warn" || state === "blocked"}
    <span class="word" aria-hidden="true">{WORD[state]}</span>
  {/if}
</li>

<style>
  .row {
    display: grid;
    grid-template-columns: 16px 1fr auto;
    gap: 9px;
    align-items: start;
    padding: 7px 0;
    border-bottom: 1px solid var(--line-soft);
  }

  .row:last-child {
    border-bottom: 0;
  }

  .glyph {
    width: 16px;
    height: 16px;
    border-radius: 50%;
    display: grid;
    place-items: center;
    margin-top: 1px;
  }

  .g-ok {
    background: var(--ok-q);
    color: var(--ok);
  }

  .g-warn {
    background: var(--warn-q);
    color: var(--warn);
  }

  .g-stop {
    background: var(--stop-q);
    color: var(--stop);
  }

  .g-wait {
    background: var(--sunken);
    color: var(--faint);
  }

  .label {
    font-size: var(--text-12);
    font-weight: var(--w-500);
  }

  .detail {
    display: block;
    color: var(--faint);
    font-size: var(--text-11);
    margin-top: 1px;
  }

  .word {
    font-size: var(--text-10);
    letter-spacing: 0.05em;
    text-transform: uppercase;
    color: var(--dim);
    white-space: nowrap;
  }
</style>
