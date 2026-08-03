<script lang="ts">
  /** Live run lock / stuck-run error banner (unit 7.11). Colour never alone
   * (control-room.md §2): glyph + word. No infinite spinner on stale lease. */
  import Icon from "../components/Icon.svelte";
  import Spinner from "../components/Spinner.svelte";
  import type { LockState } from "./lock";
  import { staleRunMessage } from "./lock";

  interface Props {
    lock: LockState;
  }

  let { lock }: Props = $props();

  const staleText = $derived(
    lock.kind === "stale" ? staleRunMessage(lock) : "",
  );
</script>

{#if lock.kind === "locked"}
  <div class="banner locked" role="status" aria-live="polite">
    <Spinner size={12} label="Run in flight" />
    <span class="word">Locked</span>
    <span class="detail">
      {lock.runKind} run
      <span class="num">{lock.runId}</span>
      {#if lock.currentStage}
        · stage <span class="stage">{lock.currentStage}</span>
      {/if}
      : spend and edits disabled until it finishes
    </span>
  </div>
{:else if lock.kind === "stale"}
  <div class="banner stale" role="alert">
    <Icon name="stop" size={12} />
    <span class="word">Stuck run</span>
    <span class="detail">{staleText}</span>
  </div>
{/if}

<style>
  .banner {
    display: flex;
    align-items: flex-start;
    gap: 8px;
    padding: 7px 14px;
    font-size: var(--text-12);
    border-bottom: 1px solid var(--line-soft);
  }

  .banner.locked {
    background: var(--accent-q);
    color: var(--text);
  }

  .banner.stale {
    background: color-mix(in srgb, var(--stop) 12%, var(--surface));
    color: var(--text);
  }

  .word {
    font-weight: var(--w-590);
    flex: 0 0 auto;
    letter-spacing: 0.02em;
  }

  .banner.stale .word {
    color: var(--stop);
  }

  .detail {
    color: var(--dim);
    min-width: 0;
    line-height: 1.4;
  }

  .num,
  .stage {
    font-family: var(--font-mono);
    font-variant-numeric: tabular-nums;
    color: var(--text);
  }
</style>
