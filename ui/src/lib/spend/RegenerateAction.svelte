<script lang="ts">
  /** Regenerate the selected timeline event by index (unit 7.11).
   * Always ConfirmDialog first. On confirm: probe without confirm_cost —
   * free 202 accepts; billable 422 opens the same dialog with the exact
   * estimated_cost_usd, and a second confirm echoes it as confirm_cost_usd. */
  import Button from "../components/Button.svelte";
  import ConfirmDialog from "../components/ConfirmDialog.svelte";
  import { formatConfirmCost } from "../format";
  import type { RunAccepted } from "../api/projects";
  import { probeRegenerate } from "./regenerate";

  interface Props {
    projectId: string;
    eventIndex: number | null;
    eventName?: string | null;
    locked?: boolean;
    onaccepted?: (accepted: RunAccepted) => void;
    onerror?: (message: string) => void;
  }

  let {
    projectId,
    eventIndex,
    eventName = null,
    locked = false,
    onaccepted,
    onerror,
  }: Props = $props();

  type Phase =
    | { kind: "idle" }
    | {
        kind: "confirm";
        /** null = not yet probed (intent confirm); number = billable echo amount. */
        cost: number | null;
      };

  let phase = $state<Phase>({ kind: "idle" });
  let busy = $state(false);

  const canAct = $derived(
    eventIndex != null && !locked && phase.kind === "idle" && !busy,
  );

  /** Amount line: only after the API returned a numeric estimate. */
  const confirmAmount = $derived(
    phase.kind === "confirm" && phase.cost != null
      ? formatConfirmCost(phase.cost)
      : null,
  );

  const confirmBody = $derived.by(() => {
    if (phase.kind !== "confirm" || eventIndex == null) return "";
    const who =
      eventName != null && eventName !== ""
        ? `"${eventName}" (index ${eventIndex})`
        : `event index ${eventIndex}`;
    if (phase.cost != null && phase.cost > 0) {
      return `Regenerate the stem for ${who} and re-mix. The amount below is the estimate the API returned — confirming echoes it as confirm_cost_usd.`;
    }
    if (phase.cost === 0) {
      return `Regenerate the stem for ${who} and re-mix. Estimate $0.00 — free, no billable consent required.`;
    }
    return `Regenerate the stem for ${who} and re-mix. Confirming probes the cost: free work starts immediately; billable work will show the estimate for a second confirm.`;
  });

  function start() {
    if (eventIndex == null || locked || busy) return;
    phase = { kind: "confirm", cost: null };
  }

  async function confirmSpend() {
    if (eventIndex == null || phase.kind !== "confirm" || locked || busy) return;
    const cost = phase.cost;
    busy = true;
    try {
      if (cost == null) {
        // Intent confirm — probe without confirm_cost_usd.
        const result = await probeRegenerate(projectId, eventIndex);
        if (result.status === "accepted") {
          // Free (or already-confirmed) — run started.
          phase = { kind: "idle" };
          onaccepted?.(result.accepted);
          return;
        }
        if (result.status === "needs_confirm") {
          // Billable: keep dialog open with the exact estimate.
          phase = { kind: "confirm", cost: result.estimatedCostUsd };
          return;
        }
        onerror?.(result.error.message);
        phase = { kind: "idle" };
        return;
      }

      // Billable second confirm — echo the exact number.
      const result = await probeRegenerate(projectId, eventIndex, {
        confirmCostUsd: cost,
      });
      if (result.status === "accepted") {
        phase = { kind: "idle" };
        onaccepted?.(result.accepted);
        return;
      }
      if (result.status === "needs_confirm") {
        phase = { kind: "confirm", cost: result.estimatedCostUsd };
        onerror?.(
          "Estimate changed — review the new amount before confirming again.",
        );
        return;
      }
      onerror?.(result.error.message);
      phase = { kind: "idle" };
    } catch (e) {
      onerror?.(e instanceof Error ? e.message : "Regenerate failed");
      phase = { kind: "idle" };
    } finally {
      busy = false;
    }
  }

  function cancel() {
    if (busy) return;
    phase = { kind: "idle" };
  }
</script>

<Button
  size="sm"
  disabled={!canAct}
  pending={busy && phase.kind === "idle"}
  help={
    locked
      ? "A run is in flight — editing is locked"
      : eventIndex == null
        ? "Select a timeline event"
        : "Regenerate this event's stem by index"
  }
  onclick={start}
>
  Regenerate
</Button>

<ConfirmDialog
  open={phase.kind === "confirm"}
  title={eventIndex == null ? "Regenerate" : `Regenerate event ${eventIndex}`}
  body={confirmBody}
  amount={confirmAmount}
  confirmLabel={phase.kind === "confirm" && phase.cost != null && phase.cost > 0
    ? "Confirm cost & regenerate"
    : "Regenerate"}
  busy={busy}
  onconfirm={confirmSpend}
  oncancel={cancel}
/>
