<script lang="ts">
  /** Spend tab: where the money went, from the two endpoints that already
   * served it (docs/api.md §3.10, §3.11). Nothing here is computed — the
   * split, the thresholds and the per-model rows are all the server's own
   * figures.
   *
   * The hard rule is §3.10's: `ledger_writable: false` means the numbers are
   * fiction, not merely imprecise — nothing is being recorded, so usage reads
   * zero however much was actually spent. A UI seeing that "must stop
   * presenting the numbers at all", so this panel fails closed rather than
   * showing a warning above figures that would still be read. */
  import Chip from "../components/Chip.svelte";
  import Evidence from "../components/Evidence.svelte";
  import Icon from "../components/Icon.svelte";
  import { formatCost } from "../format";
  import type {
    BudgetResponse,
    LedgerRowsResponse,
    QuotaResponse,
  } from "../api/projects";

  interface Props {
    budget: BudgetResponse | null;
    quota: QuotaResponse | null;
    /** The fetch failed — distinct from "no data yet". */
    failed?: boolean;
    /** Per-transaction audit trail (§3.14); null until first loaded. */
    ledger?: LedgerRowsResponse | null;
    ledgerLoading?: boolean;
    onLedgerNewer?: () => void;
    onLedgerOlder?: () => void;
  }

  let {
    budget,
    quota,
    failed = false,
    ledger = null,
    ledgerLoading = false,
    onLedgerNewer,
    onLedgerOlder,
  }: Props = $props();

  // Either source saying the ledger is unwritable poisons every figure.
  const ledgerBroken = $derived(
    (budget != null && budget.ledger_writable === false) ||
      (quota != null && quota.ledger_writable === false),
  );

  const models = $derived(quota?.models ?? []);
  const byModel = $derived(budget?.by_model ?? []);
  const thresholds = $derived(budget?.thresholds ?? []);

  const spentSource = $derived(
    budget ? `ledger rows attributed to ${budget.project_id}` : "",
  );

  /** Estimated / actual split as a share of spend, for the bar only. */
  const actualPct = $derived.by(() => {
    if (!budget || !budget.spent) return 0;
    const actual = budget.spent_actual ?? 0;
    return Math.max(0, Math.min(100, (actual / budget.spent) * 100));
  });

  function quotaState(status: string): "ok" | "warn" | "blocked" | "pending" {
    if (status === "ok") return "ok";
    if (status === "warn") return "warn";
    if (status === "blocked") return "blocked";
    return "pending";
  }

  /** Row timestamp at the precision an audit trail needs — date and minute,
   * mono, UTC (the ledger's own clock). Full seconds on hover. */
  function ledgerTime(ts: number): string {
    const d = new Date(ts * 1000);
    if (!Number.isFinite(d.getTime())) return "—";
    return d.toISOString().slice(5, 16).replace("T", " ");
  }

  const ledgerShown = $derived(ledger != null && !ledgerBroken);
  const ledgerAtStart = $derived(ledger == null || (ledger?.next_cursor ?? 0) <= 0);
</script>

<div class="panel-body">
  {#if failed}
    <p class="empty">Spend could not be loaded.</p>
  {:else if ledgerBroken}
    <!-- Fail closed. Showing figures under a warning invites reading them. -->
    <div class="fiction" role="alert">
      <span class="head">
        <Icon name="alert" size={12} />
        <span class="word">Ledger unwritable — figures withheld</span>
      </span>
      <p>
        Nothing is being recorded, so every spend figure would read zero no
        matter how much was actually spent. These are not imprecise numbers,
        they are absent ones, so they are not shown
        (<span class="k">docs/api.md §3.10</span>).
      </p>
      <p>Fix storage writes, then reload — the ledger is append-only and nothing already recorded is lost.</p>
    </div>
  {:else if !budget && !quota}
    <p class="empty">No spend recorded yet.</p>
  {:else}
    {#if budget}
      <p class="eyebrow">This project</p>
      <Evidence
        label="Spent"
        value={formatCost(budget.spent)}
        source={spentSource}
        sourcePhrase="summed from"
      >
        {#snippet body()}
          <div class="split">
            <div class="bar" aria-hidden="true">
              <i style={`width:${actualPct}%`}></i>
            </div>
            <div class="legend">
              <span><b class="num">{formatCost(budget.spent_actual)}</b> actual — the provider reported a cost</span>
              <span class="dim"><b class="num">{formatCost(budget.spent_estimated)}</b> estimated — priced from providers.toml</span>
            </div>
            {#if budget.budget_usd != null}
              <p class="note">
                Advisory budget {formatCost(budget.budget_usd)} ·
                {formatCost(budget.remaining)} remaining. Nothing is refused on
                budget grounds; billable repair still needs explicit consent.
              </p>
            {:else}
              <p class="note">No budget set — spend is tracked, not capped.</p>
            {/if}
          </div>
        {/snippet}
      </Evidence>

      {#if thresholds.length > 0}
        <p class="eyebrow">Alert thresholds</p>
        <div class="chips">
          {#each thresholds as t (t.threshold)}
            <Chip
              state={t.crossed ? "warn" : "pending"}
              label={`${Math.round(t.threshold * 100)}%`}
              detail={t.crossed ? "crossed" : "not reached"}
            />
          {/each}
        </div>
      {/if}

      {#if byModel.length > 0}
        <p class="eyebrow">By model</p>
        <table class="rows">
          <thead>
            <tr><th>Endpoint</th><th class="r">Spent</th></tr>
          </thead>
          <tbody>
            {#each byModel as m (m.endpoint)}
              <tr>
                <td class="k">{m.endpoint}</td>
                <td class="r num">{formatCost(m.spent)}</td>
              </tr>
            {/each}
          </tbody>
        </table>
      {/if}
    {/if}

    {#if models.length > 0}
      <p class="eyebrow">Model quota — all projects</p>
      <table class="rows">
        <thead>
          <tr>
            <th>Model</th>
            <th class="r">Today</th>
            <th class="r">Left</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {#each models as m (m.model_id)}
            <tr>
              <td class="k">
                {m.model_id}
                {#if m.resets_at}<span class="sub">resets {m.resets_at}</span>{/if}
              </td>
              <!-- requests_per_day is the binding constraint, not RPM:
                   gemini-2.5-flash allows 20/day (providers.toml). -->
              <td class="r num">{m.per_day.used}</td>
              <td class="r num">{m.per_day.remaining ?? "—"}</td>
              <td class="s"><Chip state={quotaState(m.status)} label={m.status} /></td>
            </tr>
          {/each}
        </tbody>
      </table>
      <p class="note">
        Daily request count — the binding constraint, not requests per minute.
        Counted from our own ledger, not a provider balance: Google exposes no
        remaining-balance field, so this is a guide and never authoritative.
      </p>
    {/if}

    {#if ledgerShown && ledger}
      <p class="eyebrow">Ledger — individual transactions</p>
      <table class="rows ledger">
        <thead>
          <tr>
            <th>When</th>
            <th>Endpoint</th>
            <th class="r">Cost</th>
            <th class="r">Result</th>
          </tr>
        </thead>
        <tbody>
          {#each ledger.rows as r, i (i)}
            <tr>
              <td class="k" title={new Date(r.timestamp * 1000).toISOString()}>{ledgerTime(r.timestamp)}</td>
              <td class="k ep">{r.endpoint}</td>
              <td class="r num">
                {formatCost(r.cost_estimate)}
                <span class="src">{r.cost_source === "actual" ? "actual" : "est"}</span>
              </td>
              <td class="s">
                {#if r.success}
                  <Chip state="ok" label="OK" />
                {:else}
                  <Chip state="blocked" label="Failed" detail={r.error ?? ""} />
                {/if}
              </td>
            </tr>
          {/each}
        </tbody>
      </table>
      <div class="ledger-foot">
        <span class="count num">
          {ledger.rows.length} of {ledger.total_count}
          {#if ledgerLoading}<span class="dim">loading…</span>{/if}
        </span>
        <span class="spacer"></span>
        <button class="mini" disabled={ledgerAtStart || ledgerLoading} onclick={onLedgerNewer}>newer</button>
        <button class="mini" disabled={ledger.next_cursor == null || ledgerLoading} onclick={onLedgerOlder}>older</button>
      </div>
      <p class="note">
        Failed calls are included — they may still have consumed provider
        quota, and the totals above sum them too. Each row carries its own
        <span class="src">est</span>/<span class="src">actual</span> source;
        the Spend summary's numbers reconcile to these rows.
      </p>
    {/if}
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

  .eyebrow + * {
    margin-bottom: 18px;
  }

  .empty {
    color: var(--faint);
    font-size: var(--text-12);
    margin: 0;
  }

  .fiction {
    border: 1px solid var(--warn);
    border-radius: var(--r);
    background: var(--warn-q);
    padding: 11px 12px;
  }

  .fiction .head {
    display: flex;
    align-items: center;
    gap: 6px;
    color: var(--warn);
    font-weight: var(--w-590);
    font-size: var(--text-12);
  }

  .fiction p {
    margin: 8px 0 0;
    font-size: var(--text-11);
    line-height: 1.5;
    color: var(--dim);
  }

  .fiction .k {
    font-family: var(--font-mono);
    font-size: 10.5px;
  }

  .split {
    padding: 10px 11px 11px;
  }

  .bar {
    height: 4px;
    border-radius: 2px;
    background: var(--sunken);
    overflow: hidden;
  }

  .bar > i {
    display: block;
    height: 100%;
    background: var(--accent);
  }

  .legend {
    display: flex;
    flex-direction: column;
    gap: 2px;
    margin-top: 8px;
    font-size: var(--text-11);
  }

  .legend .dim {
    color: var(--dim);
  }

  .note {
    margin: 8px 0 0;
    font-size: var(--text-11);
    line-height: 1.5;
    color: var(--faint);
  }

  .chips {
    display: flex;
    flex-wrap: wrap;
    gap: 5px;
  }

  .rows {
    width: 100%;
    border-collapse: collapse;
  }

  .rows th {
    text-align: left;
    font-size: var(--text-10);
    letter-spacing: 0.05em;
    text-transform: uppercase;
    color: var(--faint);
    font-weight: 600;
    padding: 0 0 5px;
    border-bottom: 1px solid var(--line-soft);
  }

  .rows td {
    padding: 6px 0;
    border-bottom: 1px solid var(--line-soft);
    font-size: var(--text-11);
    vertical-align: middle;
  }

  .rows tr:last-child td {
    border-bottom: 0;
  }

  .rows .k {
    font-family: var(--font-mono);
    font-size: 10.5px;
    word-break: break-all;
  }

  .rows .sub {
    display: block;
    color: var(--faint);
    font-size: 9.5px;
    margin-top: 1px;
  }

  .rows .r {
    text-align: right;
    white-space: nowrap;
  }

  .rows .s {
    text-align: right;
    padding-left: 8px;
  }

  .rows .src {
    font-family: var(--font-mono);
    font-size: 9px;
    color: var(--faint);
    margin-left: 3px;
  }

  .rows .ep {
    max-width: 150px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .ledger-foot {
    display: flex;
    align-items: center;
    gap: 6px;
    margin-top: 8px;
  }

  .ledger-foot .count {
    font-size: 10.5px;
    color: var(--faint);
  }

  .ledger-foot .dim {
    color: var(--faint);
    margin-left: 4px;
  }

  .ledger-foot .spacer {
    flex: 1;
  }

  .ledger-foot .mini {
    font: inherit;
    font-size: 10px;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    color: var(--dim);
    background: transparent;
    border: 1px solid var(--line);
    border-radius: 4px;
    padding: 2px 8px;
    cursor: pointer;
  }

  .ledger-foot .mini:hover:not(:disabled) {
    color: var(--text);
    border-color: var(--faint);
  }

  .ledger-foot .mini:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }
</style>
