<script lang="ts">
  /** Chrome spend actions: Mix and Export, each behind ConfirmDialog
   * (unit 7.11). Costs are never invented — mix shows a projection only after
   * the 202 accepts; export override is an explicit danger confirm. */
  import Button from "../components/Button.svelte";
  import ConfirmDialog from "../components/ConfirmDialog.svelte";
  import { formatCost } from "../format";
  import { createMix, type RunAccepted } from "../api/projects";
  import { ApiError } from "../api/client";
  import { tryExport } from "./export";

  interface Props {
    projectId: string;
    /** Disable while a non-stale run is in flight. */
    locked?: boolean;
    timelineVersion?: number | null;
    mixRunId?: string | null;
    onaccepted?: (accepted: RunAccepted, statusLine?: string) => void;
    onerror?: (message: string) => void;
  }

  let {
    projectId,
    locked = false,
    timelineVersion = null,
    mixRunId = null,
    onaccepted,
    onerror,
  }: Props = $props();

  type Dialog =
    | { kind: "none" }
    | { kind: "mix" }
    | { kind: "export" }
    | { kind: "export_override"; blockers: string[] };

  let dialog = $state<Dialog>({ kind: "none" });
  let busy = $state(false);

  function openMix() {
    if (locked || busy) return;
    dialog = { kind: "mix" };
  }

  function openExport() {
    if (locked || busy) return;
    dialog = { kind: "export" };
  }

  function close() {
    if (busy) return;
    dialog = { kind: "none" };
  }

  function mixProjectionLine(accepted: RunAccepted): string {
    const parts: string[] = [`Mix accepted · run ${accepted.run_id}`];
    const projected =
      accepted.projected_cost_usd ?? accepted.estimated_cost_usd ?? null;
    if (projected != null) {
      parts.push(`projection ${formatCost(projected)} (not a bill)`);
    } else {
      parts.push("projection unavailable");
    }
    if (accepted.cache_hits != null || accepted.cache_misses != null) {
      parts.push(
        `cache ${accepted.cache_hits ?? "—"} hit / ${accepted.cache_misses ?? "—"} miss`,
      );
    }
    if (
      accepted.projected_total_usd != null &&
      accepted.budget_usd != null
    ) {
      parts.push(
        `projected total ${formatCost(accepted.projected_total_usd)} of ${formatCost(accepted.budget_usd)} budget`,
      );
    }
    return parts.join(" · ");
  }

  async function confirmMix() {
    if (locked || busy) return;
    busy = true;
    try {
      const accepted = await createMix(projectId, {
        tier: "full",
        timeline_version: timelineVersion ?? undefined,
      });
      dialog = { kind: "none" };
      // Pass projection as the status line so onaccepted does not clobber it.
      onaccepted?.(accepted, mixProjectionLine(accepted));
    } catch (e) {
      const msg =
        e instanceof ApiError ? e.message : "Mix could not be started";
      onerror?.(msg);
      dialog = { kind: "none" };
    } finally {
      busy = false;
    }
  }

  async function confirmExport(override = false) {
    if (locked || busy) return;
    busy = true;
    try {
      const result = await tryExport(projectId, {
        run_id: mixRunId ?? undefined,
        override_blocking: override,
      });
      if (result.status === "accepted") {
        dialog = { kind: "none" };
        onaccepted?.(result.accepted);
        return;
      }
      if (result.status === "blocked") {
        dialog = { kind: "export_override", blockers: result.blockers };
        return;
      }
      onerror?.(result.error.message);
      dialog = { kind: "none" };
    } catch (e) {
      onerror?.(e instanceof ApiError ? e.message : "Export failed");
      dialog = { kind: "none" };
    } finally {
      busy = false;
    }
  }

  const overrideBody = $derived(
    dialog.kind === "export_override"
      ? `QA blocks this export (${dialog.blockers.join(", ")}). Override records the actor and marks the manifest qa_override: true. Only proceed if you accept publishing a blocked mix.`
      : "",
  );
</script>

<div class="actions">
  <Button
    size="sm"
    disabled={locked}
    help={locked ? "A run is in flight — editing is locked" : "Render every stem the timeline needs, mix, mux and re-measure QA"}
    onclick={openMix}
  >
    Mix
  </Button>
  <Button
    size="sm"
    variant="primary"
    disabled={locked}
    help={locked ? "A run is in flight — editing is locked" : "Publish the already-rendered mix — provenance manifest and the exports/latest pointer"}
    onclick={openExport}
  >
    Export
  </Button>
</div>

<ConfirmDialog
  open={dialog.kind === "mix"}
  title="Start mix"
  body="Render every stem the timeline needs, mix, mux against the source, and re-measure QA. Cost projection is returned when the run is accepted — it prices cache misses only and is not a bill."
  amount={null}
  confirmLabel="Start mix"
  busy={busy}
  onconfirm={confirmMix}
  oncancel={close}
/>

<ConfirmDialog
  open={dialog.kind === "export"}
  title="Export latest mix"
  body="Publish the already-rendered mix: write the provenance manifest and move the exports/latest pointer. Does not re-render."
  amount={null}
  confirmLabel="Export"
  busy={busy}
  onconfirm={() => confirmExport(false)}
  oncancel={close}
/>

<ConfirmDialog
  open={dialog.kind === "export_override"}
  title="Override QA blockers"
  body={overrideBody}
  amount={null}
  confirmLabel="Override and export"
  danger={true}
  busy={busy}
  onconfirm={() => confirmExport(true)}
  oncancel={close}
/>

<style>
  .actions {
    display: inline-flex;
    align-items: center;
    gap: 6px;
  }
</style>
