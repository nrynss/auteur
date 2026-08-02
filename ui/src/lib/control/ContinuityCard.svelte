<script lang="ts">
  /** Continuity — a comparison, never a fault (control-room.md §7). Two real
   * thumbnails side by side with their measured mean luma, the delta against
   * the threshold, the confidence, and the sentence that nothing was
   * corrected. */
  import { formatSeconds } from "../format";
  import type { SceneOut } from "../api/projects";

  interface ContinuityIssue {
    scene_id?: string | null;
    compared_with?: string | null;
    confidence?: number | null;
    detail?: string | null;
  }

  interface Props {
    issue: ContinuityIssue;
    threshold: number;
    scenes: SceneOut[];
  }

  let { issue, threshold, scenes }: Props = $props();

  const byId = $derived(new Map(scenes.map((s) => [s.id, s])));
  const curr = $derived(issue.scene_id ? byId.get(issue.scene_id) : undefined);
  const prev = $derived(
    issue.compared_with ? byId.get(issue.compared_with) : undefined,
  );
  const luma = $derived((s: SceneOut | undefined) => s?.stats?.mean_luma ?? null);
  const a = $derived(luma(prev));
  const b = $derived(luma(curr));
  const delta = $derived(a != null && b != null ? Math.abs(b - a) : null);
  const confidence = $derived(
    issue.confidence == null ? null : Number(issue.confidence.toFixed(2)),
  );
</script>

<div class="evidence">
  <div class="row">
    <span class="name">
      {issue.compared_with ?? "—"} → {issue.scene_id ?? "—"}
    </span>
    <span class="val num">{confidence == null ? "—" : String(confidence)} conf</span>
  </div>
  <div class="compare">
    <figure>
      {#if prev?.thumbnail_url}
        <img src={prev.thumbnail_url} alt={`Scene ${prev.index + 1}`} loading="lazy" />
      {:else}
        <span class="ph" aria-hidden="true"></span>
      {/if}
      <figcaption class="num">
        <span>{issue.compared_with ?? "—"}</span>
        <span>luma {a == null ? "—" : a.toFixed(1)}</span>
      </figcaption>
    </figure>
    <figure>
      {#if curr?.thumbnail_url}
        <img src={curr.thumbnail_url} alt={`Scene ${curr.index + 1}`} loading="lazy" />
      {:else}
        <span class="ph" aria-hidden="true"></span>
      {/if}
      <figcaption class="num">
        <span>{issue.scene_id ?? "—"}</span>
        <span>luma {b == null ? "—" : b.toFixed(1)}</span>
      </figcaption>
    </figure>
  </div>
  <p class="note">
    This is an <b>adjacent-shot mean-luma comparison</b>, not a detected fault.
    {#if delta != null}
      Mean luma jumps <b class="num">{delta.toFixed(1)}</b> against a
      <b class="num">{threshold.toFixed(1)}</b> threshold
      {#if confidence != null}at confidence <b class="num">{String(confidence)}</b>{/if}.
    {:else if issue.detail}
      {issue.detail}.
    {/if}
    Nothing has been corrected — decide whether the difference is intentional.
  </p>
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

  .val {
    font-size: var(--text-13);
    font-weight: var(--w-590);
  }

  .compare {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 7px;
    padding: 10px 11px 11px;
  }

  figure {
    margin: 0;
  }

  img,
  .ph {
    width: 100%;
    aspect-ratio: 16/9;
    display: block;
    border-radius: 4px;
    border: 1px solid var(--line);
  }

  .ph {
    background: var(--sunken);
  }

  figcaption {
    font-size: 10px;
    color: var(--faint);
    margin-top: 4px;
    display: flex;
    justify-content: space-between;
    gap: 8px;
  }

  .note {
    font-size: var(--text-11);
    line-height: 1.5;
    color: var(--dim);
    padding: 9px 11px;
    border-top: 1px solid var(--line);
    background: var(--surface);
    margin: 0;
  }

  .note b {
    color: var(--text);
    font-weight: var(--w-590);
  }

  .num {
    font-family: var(--font-mono);
    font-variant-numeric: tabular-nums;
  }
</style>
