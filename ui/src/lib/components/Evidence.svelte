<script lang="ts">
  /** Evidence row — the structural honesty rule (control-room.md §7):
   * a measured number CANNOT render without its source. `source` is required
   * (TypeScript-enforced: a screen that shows a QA number must name the
   * artifact it was measured from, or it does not compile). The source row
   * reads "measured from <artifact>" by default, overridable for evidence
   * that is not a measurement.
   *
   * `warn` is a *note*, not a boolean (control-room.md §2: colour never
   * carries meaning alone). Passing a truthy note turns the value warn-toned
   * AND renders an alert glyph with the note — there is no way to get the
   * colour without the word. The note is the measured comparison ("7.4 LU
   * under target"), never a synthesised verdict (§8). */
  import Icon from "./Icon.svelte";
  import type { Snippet } from "svelte";

  interface Props {
    label: string;
    /** The measured number — mono, tabular. */
    value: string;
    /** The artifact it was measured from — REQUIRED, structurally. */
    source: string;
    /** Override the "measured from" phrasing (budget spend is not measured). */
    sourcePhrase?: string;
    /** False for long-form statements (e.g. the continuity headline, which
     * is a recorded sentence rather than −21.4 LUFS) — UI font, wraps, and
     * the row stops squeezing the label. Default true. */
    mono?: boolean;
    /** Measured comparison when the value is past its threshold — present
     * means warn-tone + glyph + word. */
    warn?: string | null;
    /** Meter-bar row (3px, with a threshold marker) — see EvidencePanel. */
    children?: Snippet;
    /** Full-width content between the value row and the source row — e.g.
     * the continuity comparison (thumbnails + note). */
    body?: Snippet;
  }

  let {
    label,
    value,
    source,
    sourcePhrase = "measured from",
    warn = null,
    mono = true,
    children,
    body,
  }: Props = $props();
</script>

<div class="evidence">
  <div class="row" class:statement={!mono}>
    <span class="name">{label}</span>
    <span class="valwrap" class:statement={!mono}>
      {#if warn}
        <Icon name="alert" size={11} />
      {/if}
      <span class="val" class:num={mono} class:statement={!mono} class:warn={!!warn}>{value}</span>
      {#if warn}
        <span class="warn-note">{warn}</span>
      {/if}
    </span>
  </div>
  {#if children}<div class="bar">{@render children()}</div>{/if}
  {#if body}<div class="body">{@render body()}</div>{/if}
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

  .valwrap {
    display: inline-flex;
    align-items: baseline;
    gap: 6px;
    min-width: 0;
  }

  .row.statement {
    align-items: flex-start;
  }

  .valwrap.statement {
    flex: 1 1 auto;
    justify-content: flex-end;
  }

  .valwrap :global(svg) {
    color: var(--warn);
    flex: 0 0 auto;
    align-self: center;
  }

  .val {
    font-size: var(--text-13);
    font-weight: var(--w-590);
  }

  .val.statement {
    font-family: var(--font-ui);
    font-weight: var(--w-560);
    font-size: var(--text-12);
    line-height: 1.45;
  }

  .val.warn {
    color: var(--warn);
  }

  .warn-note {
    font-size: var(--text-11);
    font-weight: var(--w-500);
    color: var(--warn);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .bar {
    height: 3px;
    background: var(--sunken);
  }

  .body {
    border-top: 1px solid var(--line);
    background: var(--surface);
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
