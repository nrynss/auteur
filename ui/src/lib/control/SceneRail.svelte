<script lang="ts">
  /** Scene rail — the thin strip that belongs to the picture. Real scene
   * data: index, thumbnail, honest span, mean luma when measured. */
  import SceneThumb from "../components/SceneThumb.svelte";
  import type { SceneOut } from "../api/projects";

  interface Props {
    scenes: SceneOut[];
    /** True when the fetch failed. An empty rail must not claim a cause it
     * does not know — "analysis has not run" is a specific, checkable claim
     * and is false when the request simply failed. */
    failed?: boolean;
  }

  let { scenes, failed = false }: Props = $props();

  let selected = $state<number | null>(null);
</script>

<div class="rail" role="group" aria-label="Scenes">
  {#if scenes.length === 0}
    <span class="empty">
      {failed ? "Scenes could not be loaded." : "No scenes yet. Analysis has not run."}
    </span>
  {:else}
    {#each scenes as s (s.id)}
      <SceneThumb
        index={s.index}
        src={s.thumbnail_url}
        alt={`Scene ${s.index + 1}`}
        start={s.start_s}
        end={s.end_s}
        current={selected === s.index}
        onclick={() => (selected = s.index)}
      />
    {/each}
  {/if}
</div>

<style>
  .rail {
    display: flex;
    gap: 7px;
    align-items: flex-start;
    padding: 8px 14px 9px;
    background: var(--surface);
    border-top: 1px solid var(--line-soft);
    overflow-x: auto;
  }

  .empty {
    color: var(--faint);
    font-size: var(--text-11);
    padding: 6px 0;
  }
</style>
