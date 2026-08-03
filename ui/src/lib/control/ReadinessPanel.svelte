<script lang="ts">
  /** Readiness tab: the §6.1 checklist (real items from ProjectDetail) and
   * the most recent stage-bearing run (live mid-flight — 7.11 polls;
   * read-only here). Runs arrive oldest-first, so the \"latest\" is the last
   * element, and a run with stages is the one worth showing. */
  import ChecklistRow from "./ChecklistRow.svelte";
  import StageRow from "../components/StageRow.svelte";
  import Spinner from "../components/Spinner.svelte";
  import type { ProjectDetail, RunOut } from "../api/projects";

  interface Props {
    project: ProjectDetail;
    runs: RunOut[];
    /** Live mid-flight run from the poller (merged into runs by ControlRoom). */
    liveRun?: RunOut | null;
    locked?: boolean;
    /** The run list fetch failed. An absent run section must not read as
     * "nothing ever ran" — that is a checkable claim and is false when the
     * request simply failed. */
    failed?: boolean;
  }

  let { project, runs, liveRun = null, locked = false, failed = false }: Props = $props();

  // Prefer an explicitly tracked live run; else the most recent stage-bearing
  // run; else the most recent run overall (export may have no stages).
  const latestRun = $derived(
    liveRun ??
      [...runs].reverse().find((r) => (r.stages ?? []).length > 0) ??
      runs[runs.length - 1] ??
      null,
  );

  const runLabel = $derived.by(() => {
    if (!latestRun) return null;
    if (liveRun && liveRun.id === latestRun.id && locked) {
      return `In flight · ${latestRun.kind}`;
    }
    return `Last run · ${latestRun.kind}`;
  });
</script>

<div class="panel-body">
  <p class="eyebrow">Project checklist</p>
  {#if (project.readiness ?? []).length === 0}
    <p class="empty">No checklist items yet.</p>
  {:else}
    <ul class="check">
      {#each project.readiness ?? [] as item (item.key)}
        <ChecklistRow {item} />
      {/each}
    </ul>
  {/if}

  {#if latestRun}
    <p class="eyebrow stages-head">
      {#if locked && liveRun && liveRun.id === latestRun.id}
        <Spinner size={11} label="Run in flight" />
      {/if}
      <span>{runLabel}</span>
      <span class="rid num">{latestRun.id}</span>
    </p>
    {#if (latestRun.stages ?? []).length > 0}
      <ul class="stages">
        {#each latestRun.stages ?? [] as stage}
          <StageRow
            name={stage.name}
            status={stage.status}
            elapsed_s={stage.elapsed_s}
            retries={stage.retries}
            provider={stage.provider}
          />
        {/each}
      </ul>
    {:else if locked && liveRun}
      <p class="empty">Stages will appear as they complete.</p>
    {/if}
  {:else if failed}
    <p class="empty">Run status could not be loaded.</p>
  {/if}
</div>

<style>
  .panel-body {
    display: flex;
    flex-direction: column;
    gap: 0;
  }

  .eyebrow {
    margin: 0 0 8px;
  }

  .stages-head {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-top: 14px;
    flex-wrap: wrap;
  }

  .rid {
    color: var(--faint);
    font-size: var(--text-11);
  }

  .num {
    font-family: var(--font-mono);
    font-variant-numeric: tabular-nums;
  }

  .check {
    list-style: none;
    margin: 0;
    padding: 0;
    display: grid;
    gap: 1px;
  }

  .stages {
    list-style: none;
    margin: 0;
    padding: 0;
  }

  .empty {
    color: var(--faint);
    font-size: var(--text-12);
  }
</style>
