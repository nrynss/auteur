<script lang="ts">
  /** Stage row — one workflow stage: name, status, elapsed, retries,
   * provider. Renders live mid-flight (7.11 polls runs; stages arrive as
   * they happen). Status vocabulary from StageOut: succeeded | failed |
   * running; anything else reads as pending. Only succeeded earns the ok
   * chip — an unknown status must never be presented as a confident
   * success (control-room.md §7). */
  import Chip from "./Chip.svelte";
  import Tooltip from "../help/Tooltip.svelte";
  import Spinner from "./Spinner.svelte";
  import Timecode from "./Timecode.svelte";

  interface Props {
    name: string;
    status: string;
    elapsed_s?: number | null;
    retries?: number;
    provider?: string | null;
  }

  let { name, status, elapsed_s = null, retries = 0, provider = null }: Props =
    $props();

  const running = $derived(status === "running");
  const succeeded = $derived(status === "succeeded");
  const failed = $derived(status === "failed");
  const chipState = $derived(
    succeeded ? "ok" : failed ? "blocked" : "pending",
  );
  const chipLabel = $derived(
    succeeded ? "Done" : failed ? "Failed" : running ? "Running" : "Pending",
  );
</script>

<div class="stage" class:running class:failed>
  {#if running}
    <Spinner size={12} label={`${name} running`} />
  {:else}
    <span class="dot" aria-hidden="true"></span>
  {/if}
  <span class="name">{name}</span>
  {#if elapsed_s != null && elapsed_s > 0}
    <span class="elapsed"><Timecode seconds={elapsed_s} /></span>
  {/if}
  {#if retries > 0}
    <span class="retries num"><Tooltip text={`${retries} retries`}><span>×{retries}</span></Tooltip></span>
  {/if}
  {#if provider}
    <span class="provider">{provider}</span>
  {/if}
  <span class="chipwrap"><Chip state={chipState} label={chipLabel} /></span>
</div>

<style>
  .stage {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 7px 0;
    border-bottom: 1px solid var(--line-soft);
    font-size: var(--text-12);
  }

  .stage:last-child {
    border-bottom: 0;
  }

  .dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: var(--ok);
    flex: 0 0 auto;
  }

  .stage.failed .dot {
    background: var(--stop);
  }

  .name {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .elapsed {
    color: var(--dim);
    font-size: var(--text-11);
  }

  .retries {
    color: var(--warn);
    font-size: var(--text-10);
  }

  .provider {
    color: var(--faint);
    font-size: var(--text-10);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .chipwrap {
    margin-left: auto;
    flex: 0 0 auto;
  }
</style>
