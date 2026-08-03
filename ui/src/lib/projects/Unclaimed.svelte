<script lang="ts">
  /** The archive of material no project claims (unit 7.17, docs/api.md §3.15).
   *
   * Three sections, three different truths, never one list with one button:
   * - Orphan ledger rows — money spent and quota gone that no project's view
   *   shows. Claimable into an existing project (POST /attributions) or into
   *   a newly-created spend archive.
   * - Orphan project prefixes — artifacts that still cost storage while the
   *   whole product cannot see them. Adoptable by writing their record.
   * - Shared cache stems — keys are addresses, not provenance; NEVER
   *   claimable. Shown with the reason, inspection URLs only.
   */
  import { onMount } from "svelte";

  import Button from "../components/Button.svelte";
  import Chip from "../components/Chip.svelte";
  import ConfirmDialog from "../components/ConfirmDialog.svelte";
  import Icon from "../components/Icon.svelte";
  import Spinner from "../components/Spinner.svelte";
  import { ApiError } from "../api/client";

  import HelpHint from "../help/HelpHint.svelte";
  import Term from "../help/Term.svelte";

  import { formatCost } from "../format";
  import {
    adoptOrphanProject,
    attributeOrphanRows,
    claimOrphanLedger,
    getUnclaimedCache,
    getUnclaimedLedger,
    getUnclaimedProjects,
    listProjects,
    type AdoptProjectResponse,
    type ClaimLedgerResponse,
    type OrphanProjectOut,
    type ProjectSummary,
    type UnclaimedCacheResponse,
    type UnclaimedLedgerResponse,
    type UnclaimedProjectsResponse,
  } from "../api/projects";

  interface Props {
    onNavigate: (path: string) => void;
  }

  let { onNavigate }: Props = $props();

  let ledger = $state<UnclaimedLedgerResponse | null>(null);
  let ledgerCursor = $state(0);
  let ledgerStack = $state<number[]>([]);
  let projects = $state<UnclaimedProjectsResponse | null>(null);
  let cache = $state<UnclaimedCacheResponse | null>(null);
  let cacheCursor = $state(0);
  let cacheStack = $state<number[]>([]);
  let failed = $state(false);
  let busy = $state(false);
  let statusLine = $state<string | null>(null);
  let errorLine = $state<string | null>(null);

  // -- claim flows ----------------------------------------------------------
  type Dialog =
    | { kind: "claim-into"; projectId: string | null; projects: ProjectSummary[]; loading: boolean }
    | { kind: "claim-new" }
    | { kind: "adopt"; prefix: OrphanProjectOut };

  let dialog = $state<Dialog | null>(null);
  let titleValue = $state("");
  let budgetValue = $state("");

  async function loadAll() {
    try {
      const [l, p, c] = await Promise.all([
        getUnclaimedLedger(ledgerCursor, 50),
        getUnclaimedProjects(),
        getUnclaimedCache(cacheCursor, 50),
      ]);
      ledger = l;
      projects = p;
      cache = c;
    } catch (e) {
      failed = true;
      console.error("unclaimed: load failed", e);
    }
  }

  onMount(() => {
    void loadAll();
  });

  const ledgerBroken = $derived(ledger?.ledger_writable === false);

  function ledgerTime(ts: number): string {
    const d = new Date(ts * 1000);
    if (!Number.isFinite(d.getTime())) return "n/a";
    return d.toISOString().slice(5, 16).replace("T", " ");
  }

  async function refreshLedger() {
    try {
      ledger = await getUnclaimedLedger(ledgerCursor, 50);
    } catch (e) {
      errorLine = e instanceof Error ? e.message : String(e);
    }
  }

  function ledgerOlder() {
    if (ledger?.next_cursor == null) return;
    ledgerStack.push(ledgerCursor);
    ledgerCursor = ledger.next_cursor;
    void refreshLedger();
  }

  function ledgerNewer() {
    if (ledgerStack.length === 0 && ledgerCursor <= 0) return;
    ledgerCursor = ledgerStack.pop() ?? 0;
    void refreshLedger();
  }

  async function refreshCache() {
    try {
      cache = await getUnclaimedCache(cacheCursor, 50);
    } catch (e) {
      errorLine = e instanceof Error ? e.message : String(e);
    }
  }

  function cacheOlder() {
    if (cache?.next_cursor == null) return;
    cacheStack.push(cacheCursor);
    cacheCursor = cache.next_cursor;
    void refreshCache();
  }

  function cacheNewer() {
    if (cacheStack.length === 0 && cacheCursor <= 0) return;
    cacheCursor = cacheStack.pop() ?? 0;
    void refreshCache();
  }

  // -- claim into an existing project --------------------------------------
  async function openClaimInto() {
    try {
      const idx = await listProjects(200);
      dialog = {
        kind: "claim-into",
        projectId: idx.projects[0]?.id ?? null,
        projects: idx.projects,
        loading: false,
      };
    } catch (e) {
      errorLine = e instanceof Error ? e.message : String(e);
    }
  }

  async function confirmClaimInto(pid: string) {
    if (!pid || busy) return;
    busy = true;
    errorLine = null;
    try {
      const result = await attributeOrphanRows(pid);
      statusLine =
        `Claimed ${result.claimed} orphan ${result.claimed === 1 ? "row" : "rows"} into ` +
        `${pid}. The money now reconciles into that project's budget.`;
      dialog = null;
      await loadAll();
    } catch (e) {
      errorLine = e instanceof ApiError ? e.message : String(e);
    } finally {
      busy = false;
    }
  }

  async function confirmClaimNew() {
    if (busy) return;
    const title = titleValue.trim();
    if (!title) return;
    busy = true;
    errorLine = null;
    try {
      const budget = budgetValue.trim() === "" ? null : Number(budgetValue);
      if (budget != null && (!Number.isFinite(budget) || budget <= 0)) {
        errorLine = "Budget must be a positive number when set.";
        busy = false;
        return;
      }
      const result: ClaimLedgerResponse = await claimOrphanLedger(title, budget);
      statusLine =
        `Created “${result.project.title}” and claimed ${result.claimed} ` +
        `orphan ${result.claimed === 1 ? "row" : "rows"}.`;
      dialog = null;
      titleValue = "";
      budgetValue = "";
      await loadAll();
    } catch (e) {
      errorLine = e instanceof ApiError ? e.message : String(e);
    } finally {
      busy = false;
    }
  }

  // -- adopt an orphan prefix ----------------------------------------------
  function openAdopt(prefix: OrphanProjectOut) {
    dialog = { kind: "adopt", prefix };
    titleValue = prefix.project_id;
  }

  async function confirmAdopt() {
    if (dialog?.kind !== "adopt" || busy) return;
    const title = titleValue.trim();
    if (!title) return;
    busy = true;
    errorLine = null;
    try {
      const result: AdoptProjectResponse = await adoptOrphanProject(
        dialog.prefix.project_id,
        title,
      );
      statusLine = result.repaired
        ? `Adopted ${result.project.id} and repaired its unreadable record.`
        : `Adopted ${result.project.id}. Its artifacts are reachable again.`;
      dialog = null;
      await loadAll();
    } catch (e) {
      errorLine = e instanceof ApiError ? e.message : String(e);
    } finally {
      busy = false;
    }
  }

  const canGoNewerLedger = $derived(ledgerStack.length > 0 || ledgerCursor > 0);
  const canGoNewerCache = $derived(cacheStack.length > 0 || cacheCursor > 0);
  const cacheTotal = $derived(cache?.total_count ?? 0);
</script>

<div class="page">
  <header class="chrome">
    <div class="proj">
      <h1><Term term="unclaimed" /></h1>
      <span class="sub">material this system owns that no project claims: money spent, storage held, addresses shared</span>
    </div>
    <span class="spacer"></span>
    <HelpHint />
    <button type="button" class="nav" onclick={() => onNavigate("/")}>← Projects</button>
  </header>

  {#if statusLine}
    <div class="banner info" role="status"><span>{statusLine}</span></div>
  {/if}
  {#if errorLine}
    <div class="banner err" role="alert">
      <span class="word">Action failed</span>
      <span class="detail">{errorLine}</span>
      <button class="dismiss" onclick={() => (errorLine = null)}>Dismiss</button>
    </div>
  {/if}
  {#if failed}
    <p class="stop">Could not load the archive. See the console.</p>
  {:else if !ledger || !projects || !cache}
    <div class="center"><Spinner label="Loading archive" /><span class="dim">Loading archive…</span></div>
  {:else}
    <section class="panel">
      <div class="head">
        <p class="eyebrow">Orphaned <Term term="transactions" /></p>
        <span class="count num">{ledger.total_count}</span>
      </div>
      {#if ledgerBroken}
        <div class="fiction" role="alert">
          <span class="head">
            <Icon name="alert" size={12} />
            <span class="word">Ledger unwritable: figures withheld</span>
          </span>
          <p>
            Nothing is being recorded, so every spend figure would read zero no
            matter how much was actually spent. The rows are not shown until
            storage writes work again (docs/api.md §3.10).
          </p>
        </div>
      {:else if ledger.total_count === 0}
        <p class="empty">Every row has an owner. Nothing to claim.</p>
      {:else}
        <p class="note">
          Money spent and daily usage consumed that no project’s budget counts. Claiming
          adopts <b>every currently-unclaimed row</b> (first-come by time, docs/api.md §3.11).
        </p>
        <table class="rows">
          <thead>
            <tr><th>When</th><th>Endpoint</th><th class="r">Cost</th><th class="r">Result</th><th></th></tr>
          </thead>
          <tbody>
            {#each ledger.rows as r, i (i)}
              <tr>
                <td class="k">{ledgerTime(r.timestamp)}</td>
                <td class="k ep">{r.endpoint}</td>
                <td class="r num">{formatCost(r.cost_estimate)}</td>
                <td class="s">
                  {#if r.success}
                    <Chip state="ok" label="OK" />
                  {:else}
                    <Chip state="blocked" label="Failed" detail={r.error ?? ""} />
                  {/if}
                </td>
                <td class="s">
                  {#if r.url}
                    <a class="mini" href={r.url} target="_blank" rel="noreferrer">inspect</a>
                  {/if}
                </td>
              </tr>
            {/each}
          </tbody>
        </table>
        <div class="foot">
          <span class="count num">
            {ledger.rows.length} of {ledger.total_count}{busy ? " (working…)" : ""}
          </span>
          <span class="spacer"></span>
          <Button size="sm" variant="ghost" disabled={!canGoNewerLedger || busy} onclick={ledgerNewer}>newer</Button>
          <Button size="sm" variant="ghost" disabled={ledger.next_cursor == null || busy} onclick={ledgerOlder}>older</Button>
          <span class="sep" aria-hidden="true"></span>
          <Button size="sm" variant="ghost" disabled={busy} onclick={openClaimInto}>Claim into project…</Button>
          <Button size="sm" variant="primary" disabled={busy} onclick={() => (dialog = { kind: "claim-new" })}>Create spend archive…</Button>
        </div>
      {/if}
    </section>

    <section class="panel">
      <div class="head">
        <p class="eyebrow">Orphan project prefixes</p>
        <span class="count num">{projects.projects.length}</span>
      </div>
      {#if projects.projects.length === 0}
        <p class="empty">Every project prefix has a readable record.</p>
      {:else}
        <p class="note">
          Artifacts that still cost storage while the whole product cannot see them.
          No <span class="k">project.json</span>, so no route finds them. Adoption writes
          the record for the prefix's own id; nothing is copied or moved.
        </p>
        <table class="rows">
          <thead>
            <tr><th>Project id</th><th class="r">Objects</th><th class="r">Bytes</th><th></th><th></th></tr>
          </thead>
          <tbody>
            {#each projects.projects as p (p.project_id)}
              <tr>
                <td class="k">{p.project_id}</td>
                <td class="r num">{p.object_count}</td>
                <td class="r num">{p.total_bytes}</td>
                <td class="s">
                  {#if p.unreadable}
                    <Chip state="warn" label="Unreadable record" detail="exists but does not parse. Adoption replaces it, recorded" />
                  {/if}
                </td>
                <td class="s">
                  <Button size="sm" variant="ghost" disabled={busy} onclick={() => openAdopt(p)}>Adopt…</Button>
                </td>
              </tr>
            {/each}
          </tbody>
        </table>
      {/if}
    </section>

    <section class="panel">
      <div class="head">
        <p class="eyebrow">Shared cache stems</p>
        <span class="count num">{cacheTotal}</span>
      </div>
      <div class="fiction caveat" role="note">
        <span class="head">
          <Icon name="alert" size={12} />
          <span class="word">These are not orphans, and they are never claimable</span>
        </span>
        <p>
          A cache key is <span class="k">sha256(endpoint, prompt, duration)</span>: an
          <b>address, not provenance</b>. Many projects legitimately resolve to the same
          stem, so assigning one to a project would assert something false about both and
          break the sharing that makes the cache a cache. They appear here only because
          this is the only screen that can show them.
        </p>
      </div>
      {#if cacheTotal > 0}
        <table class="rows">
          <thead>
            <tr><th>Key</th><th class="r">Bytes</th><th></th></tr>
          </thead>
          <tbody>
            {#each cache.rows as r, i (i)}
              <tr>
                <td class="k">{r.key}</td>
                <td class="r num">{r.size_bytes}</td>
                <td class="s">
                  {#if r.url}
                    <a class="mini" href={r.url} target="_blank" rel="noreferrer">inspect</a>
                  {/if}
                </td>
              </tr>
            {/each}
          </tbody>
        </table>
        <div class="foot">
          <span class="count num">{cache.rows.length} of {cacheTotal}</span>
          <span class="spacer"></span>
          <Button size="sm" variant="ghost" disabled={!canGoNewerCache} onclick={cacheNewer}>newer</Button>
          <Button size="sm" variant="ghost" disabled={cache.next_cursor == null} onclick={cacheOlder}>older</Button>
        </div>
      {/if}
    </section>
  {/if}

  {#if dialog?.kind === "claim-into"}
    {@const d = dialog}
    <ConfirmDialog
      open={true}
      title="Claim every unclaimed row into a project?"
      body="This adopts every currently-unclaimed ledger row into the chosen project (first-come by time). The money then reconciles into that project's budget."
      confirmLabel={busy ? "Claiming…" : "Claim rows"}
      busy={busy}
      onconfirm={() => d.projectId && confirmClaimInto(d.projectId)}
      oncancel={() => (dialog = null)}
    >
      {#snippet children()}
        <select
          class="select"
          aria-label="Target project"
          value={d.projectId ?? ""}
          onchange={(e) => {
            if (dialog?.kind === "claim-into") dialog.projectId = (e.target as HTMLSelectElement).value;
          }}
        >
          {#each d.projects as p (p.id)}
            <option value={p.id}>{p.title}: {p.id}</option>
          {/each}
        </select>
      {/snippet}
    </ConfirmDialog>
  {:else if dialog?.kind === "claim-new"}
    <ConfirmDialog
      open={true}
      title="Create a spend archive?"
      body="Creates a project with no video (there is nothing to upload) and claims every currently-unclaimed ledger row into it, first-come by time. Its budget then counts that spend."
      confirmLabel={busy ? "Creating…" : "Create & claim"}
      busy={busy}
      onconfirm={confirmClaimNew}
      oncancel={() => (dialog = null)}
    >
      {#snippet children()}
        <input class="input" placeholder="Title (e.g. “Legacy spend”)" bind:value={titleValue} aria-label="Archive title" />
        <input class="input" placeholder="Advisory budget (optional)" bind:value={budgetValue} aria-label="Advisory budget" />
      {/snippet}
    </ConfirmDialog>
  {:else if dialog?.kind === "adopt"}
    {@const d = dialog}
    <ConfirmDialog
      open={true}
      title={`Adopt ${d.prefix.project_id}?`}
      body={d.prefix.unreadable
        ? "This prefix holds a project.json that does not parse. Adoption replaces it with a new record (the one deliberate overwrite), recorded in the event log. The artifacts underneath become reachable again."
        : "Writes the project record for this prefix's own id. The artifacts underneath become reachable through every normal screen; nothing is copied or moved."}
      confirmLabel={busy ? "Adopting…" : "Adopt"}
      busy={busy}
      onconfirm={confirmAdopt}
      oncancel={() => (dialog = null)}
    >
      {#snippet children()}
        <input class="input" placeholder="Title" bind:value={titleValue} aria-label="Project title" />
      {/snippet}
    </ConfirmDialog>
  {/if}
</div>

<style>
  .page {
    max-width: 980px;
    margin: 0 auto;
    padding: 0 18px 48px;
  }

  .chrome {
    display: flex;
    align-items: baseline;
    gap: 12px;
    padding: 18px 0 6px;
  }

  .proj h1 {
    margin: 0;
    font-size: var(--text-14);
    font-weight: var(--w-590);
  }

  .proj .sub {
    display: block;
    font-size: var(--text-11);
    color: var(--faint);
    margin-top: 2px;
  }

  .spacer {
    flex: 1;
  }

  .nav {
    font: inherit;
    font-size: var(--text-12);
    color: var(--dim);
    background: transparent;
    border: 1px solid var(--line);
    border-radius: var(--r);
    padding: 5px 11px;
    cursor: pointer;
  }

  .nav:hover {
    color: var(--text);
    border-color: var(--faint);
  }

  .banner {
    display: flex;
    align-items: flex-start;
    gap: 8px;
    padding: 8px 12px;
    border: 1px solid var(--line);
    border-radius: var(--r);
    margin: 10px 0 0;
    font-size: var(--text-12);
  }

  .banner.info {
    background: var(--raised);
    color: var(--dim);
  }

  .banner.err {
    background: color-mix(in srgb, var(--stop) 10%, var(--surface));
  }

  .banner .word {
    font-weight: var(--w-590);
    color: var(--stop);
    flex: 0 0 auto;
  }

  .banner .detail {
    min-width: 0;
    line-height: 1.4;
  }

  .banner .dismiss {
    margin-left: auto;
    border: 0;
    background: transparent;
    color: var(--faint);
    cursor: pointer;
    font: inherit;
    font-size: var(--text-11);
  }

  .panel {
    background: var(--surface);
    border: 1px solid var(--line);
    border-radius: var(--r-lg);
    padding: 14px 16px 16px;
    margin-top: 16px;
  }

  .head {
    display: flex;
    align-items: baseline;
    gap: 8px;
  }

  .head .count {
    color: var(--faint);
    font-size: var(--text-11);
  }

  .eyebrow {
    margin: 0;
    font-size: var(--text-12);
    font-weight: var(--w-590);
  }

  .note {
    margin: 6px 0 10px;
    font-size: var(--text-11);
    line-height: 1.5;
    color: var(--dim);
  }

  .note .k,
  .fiction .k {
    font-family: var(--font-mono);
    font-size: 10px;
  }

  .empty {
    margin: 8px 0 0;
    color: var(--faint);
    font-size: var(--text-12);
  }

  .stop {
    color: var(--stop);
  }

  .center {
    display: grid;
    place-content: center;
    gap: 10px;
    justify-items: center;
    color: var(--dim);
    min-height: 50vh;
  }

  .fiction {
    border: 1px solid var(--warn);
    border-radius: var(--r);
    background: var(--warn-q);
    padding: 10px 12px;
    margin-bottom: 12px;
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
    margin: 7px 0 0;
    font-size: var(--text-11);
    line-height: 1.5;
    color: var(--dim);
  }

  .caveat {
    margin-bottom: 12px;
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

  .rows .ep {
    max-width: 260px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .rows .r {
    text-align: right;
    white-space: nowrap;
  }

  .rows .s {
    text-align: right;
    padding-left: 10px;
  }

  .num {
    font-family: var(--font-mono);
    font-variant-numeric: tabular-nums;
  }

  .foot {
    display: flex;
    align-items: center;
    gap: 6px;
    margin-top: 10px;
  }

  .foot .count {
    font-size: 10.5px;
    color: var(--faint);
  }

  .foot .spacer {
    flex: 1;
  }

  .sep {
    width: 1px;
    height: 16px;
    background: var(--line);
    margin: 0 4px;
  }

  .mini {
    font: inherit;
    font-size: 10px;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    color: var(--dim);
    text-decoration: none;
    border: 1px solid var(--line);
    border-radius: 4px;
    padding: 1px 7px;
    cursor: pointer;
    white-space: nowrap;
  }

  .mini:hover {
    color: var(--text);
    border-color: var(--faint);
  }

  .select,
  .input {
    font: inherit;
    font-size: var(--text-12);
    color: var(--text);
    background: var(--raised);
    border: 1px solid var(--line);
    border-radius: var(--r);
    padding: 6px 9px;
    width: 100%;
    margin-top: 8px;
    box-sizing: border-box;
  }
</style>