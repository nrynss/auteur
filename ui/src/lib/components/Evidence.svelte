<script lang="ts">
  /** Evidence row — the structural honesty rule (control-room.md §7):
   * a measured number CANNOT render without its source. `source` is required
   * (TypeScript-enforced: a screen that shows a QA number must name the
   * artifact it was measured from, or it does not compile). The source row
   * reads "measured from <artifact>" by default, overridable for evidence
   * that is not a measurement. */
  import type { Snippet } from "svelte";

  interface Props {
    label: string;
    /** The measured number — mono, tabular. */
    value: string;
    /** The artifact it was measured from — REQUIRED, structurally. */
    source: string;
    /** Override the "measured from" phrasing (budget spend is not measured). */
    sourcePhrase?: string;
    /** Warn tone for values past a threshold (e.g. loudness under target). */
    warn?: boolean;
    /** Extra row below the value (e.g. a meter bar with a threshold). */
    children?: Snippet;
  }

  let {
    label,
    value,
    source,
    sourcePhrase = "measured from",
    warn = false,
    children,
  }: Props = $props();
</script>

<div class="evidence">
  <div class="row">
    <span class="name">{label}</span>
    <span class="val num" class:warn>{value}</span>
  </div>
  {#if children}<div class="bar">{@render children()}</div>{/if}
  <div class="src">
    <span class="k">{sourcePhrase}</span>
    <span class="v num">{source}</span>
  </div>
</div>

<style>
  .evidence {
    border: 1px solid var(--line);
    border-radius: var(--r);
    overflow: hidden;
    background: var(--raised);
  }

  .row {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 10px;
    padding: 9px 11px;
  }

  .name {
    font-size: var(--text-12);
    font-weight: var(--w-500);
  }

  .val {
    font-size: var(--text-13);
    font-weight: var(--w-590);
  }

  .val.warn {
    color: var(--warn);
  }

  .bar {
    height: 3px;
    background: var(--sunken);
  }

  .src {
    display: flex;
    gap: 6px;
    align-items: baseline;
    padding: 0 11px 9px;
    font-size: 11px;
    color: var(--faint);
  }

  .src .k {
    font-family: var(--font-mono);
    font-size: 10.5px;
    letter-spacing: 0.03em;
  }

  .src .v {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
</style>
