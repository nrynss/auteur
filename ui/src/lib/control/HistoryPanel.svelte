<script lang="ts">
  /** History tab: timeline versions, prior runs/mixes (with the latest mix's
   * real peaks from unit 7.5), and the event log. */
  import Chip from "../components/Chip.svelte";
  import WaveformStrip from "./WaveformStrip.svelte";
  import { friendlyActor, friendlyEventType } from "./helpers";
  import type {
    EventOut,
    PeaksResponse,
    RunOut,
    TimelineVersionsResponse,
  } from "../api/projects";

  interface Props {
    versions: TimelineVersionsResponse | null;
    runs: RunOut[];
    events: EventOut[];
    mixPeaks: PeaksResponse | null;
    /** The run the mix peaks were fetched for — the project's latest mix. */
    mixRunId: string | null;
  }

  let { versions, runs, events, mixPeaks, mixRunId }: Props = $props();

  // Runs arrive oldest-first, so the latest mix is the LAST one.
  const mixes = $derived(runs.filter((r) => r.kind === "mix"));
  const latestMix = $derived(mixes[mixes.length - 1] ?? null);

  const clock = (iso: string) => iso.slice(11, 19);
</script>

<div class="panel-body">
  <p class="eyebrow">Timeline versions</p>
  {#if versions && versions.versions.length > 0}
    <div class="vers">
      {#each versions.versions as v (v)}
        <Chip state={v === versions.latest ? "ok" : "pending"} label={`v${v}`} live={v === versions.latest} />
      {/each}
    </div>
  {:else}
    <p class="empty">No timeline versions yet — analysis creates the first.</p>
  {/if}

  <p class="eyebrow">Runs &amp; mixes</p>
  {#if runs.length === 0}
    <p class="empty">No runs yet — mix, repair or export create one.</p>
  {:else}
    <ul class="runs">
      {#each runs as r (r.id)}
        <li class="run">
          <span class="kind">{r.kind}</span>
          <span class="meta num">
            v{r.timeline_version ?? "—"} · {clock(r.created_at)}
          </span>
          <span class="state">{r.status}</span>
          {#if r.error_code}
            <span class="err num">{r.error_code}</span>
          {/if}
        </li>
      {/each}
    </ul>
  {/if}

  {#if mixRunId && mixPeaks}
    <div class="mixwave">
      <span class="mixwave-label">latest mix · {mixRunId}</span>
      <WaveformStrip peaks={mixPeaks} />
    </div>
  {/if}

  <p class="eyebrow">Event log</p>
  {#if events.length === 0}
    <p class="empty">No events yet — analysis and repairs populate the log.</p>
  {:else}
    <ul class="events">
      {#each events as e (e.id)}
        <li class="event">
          <span class="etype">{friendlyEventType(e.type)}</span>
          <span class="actor">{friendlyActor(e.actor)}</span>
          <span class="time num">{clock(e.created_at)}</span>
        </li>
      {/each}
    </ul>
  {/if}
</div>

<style>
  .panel-body {
    display: flex;
    flex-direction: column;
  }

  .eyebrow {
    margin: 0 0 8px;
  }

  .empty {
    color: var(--faint);
    font-size: var(--text-12);
  }

  .vers {
    display: flex;
    flex-wrap: wrap;
    gap: 5px;
  }

  .runs,
  .events {
    list-style: none;
    margin: 0;
    padding: 0;
  }

  .run,
  .event {
    display: flex;
    align-items: baseline;
    gap: 8px;
    padding: 5px 0;
    border-bottom: 1px solid var(--line-soft);
    font-size: var(--text-12);
  }

  .run:last-child,
  .event:last-child {
    border-bottom: 0;
  }

  .kind {
    font-weight: var(--w-560);
  }

  .meta,
  .time {
    color: var(--faint);
    font-size: var(--text-11);
    margin-left: auto;
  }

  .state {
    color: var(--dim);
    font-size: var(--text-11);
  }

  .err {
    color: var(--stop);
    font-size: var(--text-10);
  }

  .mixwave {
    margin: 10px 0 4px;
  }

  .mixwave-label {
    display: block;
    font-size: 10px;
    color: var(--faint);
    margin-bottom: 4px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .etype {
    font-weight: var(--w-500);
  }

  .actor {
    color: var(--dim);
    font-size: var(--text-11);
  }

  .num {
    font-family: var(--font-mono);
    font-variant-numeric: tabular-nums;
  }
</style>
