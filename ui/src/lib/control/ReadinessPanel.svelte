<script lang="ts">
  /** Readiness tab: the §6.1 checklist (real items from ProjectDetail) and
   * the most recent stage-bearing run (live mid-flight — 7.11 polls;
   * read-only here). Runs arrive oldest-first, so the \"latest\" is the last
   * element, and a run with stages is the one worth showing. */
  import ChecklistRow from "./ChecklistRow.svelte";
  import StageRow from "../components/StageRow.svelte";
  import type { ProjectDetail, RunOut } from "../api/projects";

  interface Props {
    project: ProjectDetail;
    runs: RunOut[];
  }

  let { project, runs }: Props = $props();

  // The most recent run that actually recorded stages; fall back to the most
  // recent run overall (which may legitimately have none, e.g. an export).
  const latestRun = $derived(
    [...runs].reverse().find((r) => (r.stages ?? []).length > 0) ??
      runs[runs.length - 1] ??
      null,
  );
</script>

<div class="panel-body">
  <p class="eyebrow">Post-production readiness</p>
  {#if (project.readiness ?? []).length === 0}
    <p class="empty">No readiness rows yet.</p>
  {:else}
    <ul class="check">
      {#each project.readiness ?? [] as item (item.key)}
        <ChecklistRow {item} />
      {/each}
    </ul>
  {/if}

  {#if latestRun && (latestRun.stages ?? []).length > 0}
    <p class="eyebrow">Last run · {latestRun.kind}</p>
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
