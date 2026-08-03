<script lang="ts">
  /** Meter — the chrome's readiness bar. Shows the measured percent and its
   * label; the bar is decoration, the number is the evidence. */
  interface Props {
    percent: number | null;
    label?: string;
  }

  let { percent, label = "ready" }: Props = $props();
  const pct = $derived(percent == null ? 0 : Math.max(0, Math.min(100, percent)));
  const shown = $derived(percent == null ? "n/a" : `${Math.round(percent)}%`);
</script>

<div class="meter">
  <span class="val">
    <b class="num">{shown}</b> {label}
  </span>
  <span class="track" aria-hidden="true"><i style={`width:${pct}%`}></i></span>
</div>

<style>
  .meter {
    display: flex;
    align-items: center;
    gap: 8px;
  }

  .val {
    font-size: var(--text-12);
    color: var(--dim);
  }

  .val b {
    color: var(--text);
    font-weight: var(--w-590);
  }

  .track {
    width: 92px;
    height: 4px;
    border-radius: 2px;
    background: var(--sunken);
    overflow: hidden;
  }

  .track > i {
    display: block;
    height: 100%;
    background: var(--accent);
  }
</style>
