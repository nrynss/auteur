<script lang="ts">
  /** Continuity — a comparison, never a fault (control-room.md §7, SPEC
   * §6.3). Rendered through the Evidence primitive so the honesty guarantee
   * holds structurally: the headline is the QA report's OWN recorded detail
   * (verbatim, never re-derived from scene stats — the report can be
   * refreshed independently), and the source names the artifact it was
   * recorded in. The comparison below is visual: the two thumbnails with
   * their recorded per-scene luma. */
  import Evidence from "../components/Evidence.svelte";
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
    /** The run the QA report was written by (QaReportResponse.run_id). */
    runId: string | null;
  }

  let { issue, threshold, scenes, runId }: Props = $props();

  const byId = $derived(new Map(scenes.map((s) => [s.id, s])));
  const curr = $derived(issue.scene_id ? byId.get(issue.scene_id) : undefined);
  const prev = $derived(
    issue.compared_with ? byId.get(issue.compared_with) : undefined,
  );
  const luma = $derived((s: SceneOut | undefined) => s?.stats?.mean_luma ?? null);
  const a = $derived(luma(prev));
  const b = $derived(luma(curr));
  const confidence = $derived(
    issue.confidence == null ? null : Number(issue.confidence.toFixed(2)),
  );
  // The recorded measurement — the QA report's own wording. Nothing numeric
  // is derived here; if the report and the scene stats ever disagree, this
  // sentence is the authority and the source row says where it came from.
  const recorded = $derived(
    issue.detail ??
      (confidence != null ? `confidence ${confidence}` : null) ??
      "continuity warning",
  );
  const source = $derived(
    runId ? `qa/qa-report.json · run ${runId}` : "qa/qa-report.json",
  );
</script>

<Evidence
  label={`${issue.compared_with ?? "n/a"} → ${issue.scene_id ?? "n/a"}`}
  value={recorded}
  source={source}
  sourcePhrase="recorded at"
  mono={false}
>
  {#snippet body()}
    <div class="compare">
      <figure>
        {#if prev?.thumbnail_url}
          <img src={prev.thumbnail_url} alt={`Scene ${prev.index + 1}`} loading="lazy" />
        {:else}
          <span class="ph" aria-hidden="true"></span>
        {/if}
        <figcaption class="num">
          <span>{issue.compared_with ?? "n/a"}</span>
          <span>luma {a == null ? "n/a" : a.toFixed(1)}</span>
        </figcaption>
      </figure>
      <figure>
        {#if curr?.thumbnail_url}
          <img src={curr.thumbnail_url} alt={`Scene ${curr.index + 1}`} loading="lazy" />
        {:else}
          <span class="ph" aria-hidden="true"></span>
        {/if}
        <figcaption class="num">
          <span>{issue.scene_id ?? "n/a"}</span>
          <span>luma {b == null ? "n/a" : b.toFixed(1)}</span>
        </figcaption>
      </figure>
    </div>
    <p class="note">
      This is an <b>adjacent-shot mean-luma comparison</b>, not a detected fault.
      The headline above is the QA report's recorded figure; the per-scene luma
      below is from the scene analysis. Threshold
      <b class="num">{threshold.toFixed(1)}</b>
      {#if confidence != null}, confidence <b class="num">{confidence}</b>{/if}.
      Nothing has been corrected. Decide whether the difference is intentional.
    </p>
  {/snippet}
</Evidence>

<style>
  .compare {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 7px;
    padding: 10px 11px 0;
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
