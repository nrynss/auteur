<script lang="ts">
  /** Scene rail — the thin strip that belongs to the picture. Real scene
   * data: index, thumbnail, honest span, mean luma when measured. */
  import SceneThumb from "../components/SceneThumb.svelte";
  import type { SceneOut } from "../api/projects";

  interface Props {
    scenes: SceneOut[];
  }

  let { scenes }: Props = $props();

  let selected = $state<number | null>(null);
</script>

<div class="rail" role="group" aria-label="Scenes">
  {#if scenes.length === 0}
    <span class="empty">No scenes yet — analysis has not run.</span>
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
