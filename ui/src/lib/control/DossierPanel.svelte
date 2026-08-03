<script lang="ts">
  /** Dossier tab: the artifact inventory — provenance only (key, size,
   * recorded sha256), one row per physical object. */
  import type { DossierResponse } from "../api/projects";
  import Tooltip from "../help/Tooltip.svelte";

  interface Props {
    dossier: DossierResponse | null;
    /** The fetch is in flight. It composes every artifact with one backend
     * HEAD per object and takes tens of seconds on a real bucket, so a
     * pending dossier must read as "working", never as "none". */
    loading?: boolean;
  }

  let { dossier, loading = false }: Props = $props();

  const fmtBytes = (n: number | null | undefined) => {
    if (n == null) return "—";
    if (n < 1024) return `${n} B`;
    if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
    return `${(n / (1024 * 1024)).toFixed(1)} MB`;
  };

  const shortKey = (k: string) => (k.length > 44 ? `${k.slice(0, 20)}…${k.slice(-20)}` : k);
  const shortSha = (s: string | null | undefined) =>
    s ? s.slice(0, 10) : "—";
</script>

<div class="panel-body">
  {#if loading}
    <p class="empty">Composing the dossier — one lookup per artifact…</p>
  {:else if !dossier}
    <p class="empty">No artifacts yet — mix, repair or export creates them.</p>
  {:else}
    <p class="summary num">
      {dossier.artifact_count} artifacts · {fmtBytes(dossier.total_bytes)}
    </p>
    <table class="artifacts">
      <thead>
        <tr>
          <th>kind</th>
          <th>object</th>
          <th>size</th>
          <th>sha256</th>
        </tr>
      </thead>
      <tbody>
        {#each dossier.artifacts as a (a.key)}
          <tr>
            <td class="kind">{a.kind}</td>
            <td class="key num"><Tooltip text={a.key}><span>{shortKey(a.key)}</span></Tooltip></td>
            <td class="size num">{fmtBytes(a.size_bytes)}</td>
            <td class="sha num">{shortSha(a.sha256)}</td>
          </tr>
        {/each}
      </tbody>
    </table>
  {/if}
</div>

<style>
  .panel-body {
    display: flex;
    flex-direction: column;
  }

  .summary {
    font-size: var(--text-11);
    color: var(--dim);
    margin: 0 0 8px;
  }

  table.artifacts {
    border-collapse: collapse;
    width: 100%;
  }

  th {
    text-align: left;
    font-size: 10px;
    letter-spacing: 0.05em;
    text-transform: uppercase;
    color: var(--faint);
    font-weight: var(--w-500);
    padding: 3px 6px;
    border-bottom: 1px solid var(--line);
  }

  td {
    font-size: var(--text-11);
    padding: 4px 6px;
    border-bottom: 1px solid var(--line-soft);
    vertical-align: top;
  }

  tr:last-child td {
    border-bottom: 0;
  }

  .kind {
    color: var(--dim);
    white-space: nowrap;
  }

  .key {
    color: var(--text);
    max-width: 130px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .size,
  .sha {
    color: var(--faint);
    white-space: nowrap;
  }

  .num {
    font-family: var(--font-mono);
    font-variant-numeric: tabular-nums;
  }
</style>
