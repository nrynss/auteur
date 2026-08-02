<script lang="ts">
  /** Budget chip for the chrome: spent against the advisory budget, the
   * estimate tag (SPEC §9.4 / docs/api.md §3.11 — every figure is an
   * estimate), and a visible warning when ledger_writable is false (the
   * figures are fiction). Absent budget → nothing, not a fake number. */
  import Icon from "../components/Icon.svelte";
  import { formatCost } from "../format";
  import type { BudgetResponse } from "../api/projects";

  interface Props {
    budget: BudgetResponse | null;
  }

  let { budget }: Props = $props();

  const show = $derived(budget != null && budget.budget_usd != null);
</script>

{#if show && budget}
  <span class="budget" title={budget.ledger_writable ? undefined : "ledger unwritable — these figures are fiction"}>
    <span class="amt num">{formatCost(budget.spent)}</span>
    <span class="of">of</span>
    <span class="cap num">{formatCost(budget.budget_usd)}</span>
    <span class="est">estimate</span>
    {#if !budget.ledger_writable}
      <span class="ledger-warn" role="status">
        <Icon name="alert" size={10} />
        <span>ledger unwritable</span>
      </span>
    {/if}
  </span>
{/if}

<style>
  .budget {
    display: inline-flex;
    align-items: baseline;
    gap: 5px;
    font-size: var(--text-12);
    color: var(--dim);
  }

  .amt {
    color: var(--text);
    font-weight: var(--w-590);
  }

  .cap {
    color: var(--text);
  }

  .of {
    color: var(--faint);
  }

  .est {
    font-size: 10px;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    color: var(--faint);
    border: 1px solid var(--line);
    border-radius: 3px;
    padding: 1px 4px;
  }

  .ledger-warn {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    font-size: 10px;
    letter-spacing: 0.03em;
    text-transform: uppercase;
    color: var(--warn);
  }

  .num {
    font-family: var(--font-mono);
    font-variant-numeric: tabular-nums;
  }
</style>
