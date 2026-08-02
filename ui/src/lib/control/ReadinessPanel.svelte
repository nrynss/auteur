<script lang="ts">
  /** Readiness tab: the §6.1 checklist (real items from ProjectDetail) and
   * the latest run's stages (live mid-flight — 7.11 polls; read-only here). */
  import ChecklistRow from "./ChecklistRow.svelte";
  import StageRow from "../components/StageRow.svelte";
  import type { ProjectDetail, RunOut } from "../api/projects";

  interface Props {
    project: ProjectDetail;
    latestRun: RunOut | null;
  }

  let { project, latestRun }: Props = $props();
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
