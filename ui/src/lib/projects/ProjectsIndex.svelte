<script lang="ts">
  /** Projects index — dense list of ProjectSummary rows (unit 7.13).
   * Only fields the list API exposes: no invented readiness. */
  import { onMount } from "svelte";

  import Brand from "../components/Brand.svelte";
  import Tooltip from "../help/Tooltip.svelte";
  import Button from "../components/Button.svelte";
  import Chip from "../components/Chip.svelte";
  import Panel from "../components/Panel.svelte";
  import Spinner from "../components/Spinner.svelte";
  import ThemeToggle from "../components/ThemeToggle.svelte";
  import { ApiError } from "../api/client";
  import HelpHint from "../help/HelpHint.svelte";
  import {
    listProjects,
    type ProjectSummary,
  } from "../api/projects";
  import { formatCost } from "../format";
  import { formatCreatedAt, presentStatus } from "./status";

  interface Props {
    /** SPA navigate without full reload (App.svelte supplies this). */
    onNavigate?: (path: string) => void;
  }

  let { onNavigate }: Props = $props();

  let projects = $state<ProjectSummary[]>([]);
  let status = $state<"loading" | "ready" | "error">("loading");
  let message = $state("");
  let storageHint = $state("");
  let nextCursor = $state<string | null>(null);
  let loadingMore = $state(false);

  function go(path: string, event?: MouseEvent) {
    if (event && (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || event.button !== 0)) {
      return; // let the browser open a new tab / window
    }
    event?.preventDefault();
    if (onNavigate) onNavigate(path);
    else window.location.assign(path);
  }

  async function load() {
    status = "loading";
    message = "";
    storageHint = "";
    nextCursor = null;
    try {
      const list = await listProjects(50);
      projects = list.projects;
      nextCursor = list.next_cursor ?? null;
      status = "ready";
    } catch (e) {
      status = "error";
      message = e instanceof ApiError ? e.message : String(e);
      if (e instanceof ApiError && e.code === "storage_unavailable") {
        storageHint = e.message;
      }
    }
  }

  async function loadMore() {
    if (!nextCursor || loadingMore) return;
    loadingMore = true;
    try {
      const list = await listProjects(50, nextCursor);
      projects = [...projects, ...list.projects];
      nextCursor = list.next_cursor ?? null;
    } catch (e) {
      message = e instanceof ApiError ? e.message : String(e);
    } finally {
      loadingMore = false;
    }
  }

  onMount(() => {
    void load();
  });
</script>

<header class="chrome">
  <Brand onNavigate={onNavigate} />
  <span class="dim">Projects</span>
  <span class="spacer"></span>
  {#if storageHint}
    <span class="stop faint"><Tooltip text={storageHint ?? ""}><span>Storage issue</span></Tooltip></span>
  {/if}
  <HelpHint />
  <ThemeToggle />
  <Button variant="ghost" size="sm" onclick={() => go("/unclaimed")}>
    Orphans
  </Button>
  <Button variant="primary" size="sm" onclick={() => go("/projects/new")}>
    New project
  </Button>
</header>

<main>
  <div class="head">
    <h1>Projects</h1>
    <p class="lede dim">
      Open a control room, or create a project and upload source video.
    </p>
  </div>

  {#if status === "loading"}
    <Panel class="state-panel">
      <div class="state">
        <Spinner size={14} label="loading projects" />
        <span class="dim">Loading projects…</span>
      </div>
    </Panel>
  {:else if status === "error"}
    <Panel class="state-panel">
      <div class="state">
        <p class="stop">Could not load projects: {message}</p>
        <Button size="sm" onclick={() => load()}>Retry</Button>
      </div>
    </Panel>
  {:else if projects.length === 0}
    <Panel class="state-panel">
      <div class="state empty">
        <p class="dim">No projects yet.</p>
        <p class="faint">
          Create one to get a direct upload target, confirm ingest, and start analysis.
        </p>
        <Button variant="primary" size="sm" onclick={() => go("/projects/new")}>
          New project
        </Button>
      </div>
    </Panel>
  {:else}
    <Panel class="list-panel">
      <div class="table" role="table" aria-label="Projects">
        <div class="row head-row" role="row">
          <span role="columnheader">Title</span>
          <span role="columnheader">Status</span>
          <span role="columnheader" class="num-col">Duration</span>
          <span role="columnheader" class="num-col">Budget</span>
          <span role="columnheader">Source</span>
          <span role="columnheader">Created</span>
        </div>
        {#each projects as p (p.id)}
          {@const st = presentStatus(p.status)}
          <a
            class="row"
            role="row"
            href={`/projects/${encodeURIComponent(p.id)}`}
            onclick={(e) => go(`/projects/${encodeURIComponent(p.id)}`, e)}
          >
            <span class="title-cell" role="cell">
              <span class="title">{p.title}</span>
              <span class="id num faint">{p.id}</span>
            </span>
            <span role="cell">
              <Chip state={st.state} label={st.label} />
            </span>
            <span class="num num-col" role="cell">
              {p.duration_s != null ? `${p.duration_s.toFixed(1)}s` : "n/a"}
            </span>
            <span class="num num-col" role="cell">
              {formatCost(p.budget_usd)}
            </span>
            <span class="file faint" role="cell">
              <Tooltip text={p.source_filename}><span>{p.source_filename || "n/a"}</span></Tooltip>
            </span>
            <span class="created faint" role="cell">
              {formatCreatedAt(p.created_at)}
            </span>
          </a>
        {/each}
      </div>
      {#if nextCursor}
        <div class="more">
          <Button size="sm" pending={loadingMore} disabled={loadingMore} onclick={loadMore}>
            Load more
          </Button>
          <span class="faint">Showing {projects.length} · more available</span>
        </div>
      {/if}
    </Panel>
  {/if}
</main>

<style>
  .chrome {
    display: flex;
    align-items: center;
    gap: 0.75rem;
    height: var(--chrome-h);
    padding: 0 1rem;
    background: var(--surface);
    border-bottom: 1px solid var(--line);
  }

  .spacer {
    flex: 1;
  }

  main {
    max-width: 1100px;
    margin: 0 auto;
    padding: 1.25rem 1rem 2rem;
  }

  .head {
    margin-bottom: 0.85rem;
  }

  h1 {
    font-size: var(--text-14);
    font-weight: var(--w-590);
    margin: 0 0 0.25rem;
  }

  .lede {
    margin: 0;
    font-size: var(--text-12);
  }

  :global(.list-panel),
  :global(.state-panel) {
    padding: 0;
    overflow: hidden;
  }

  .state {
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    gap: 0.65rem;
    padding: 1.25rem 1rem;
  }

  .state.empty {
    padding: 1.75rem 1rem;
  }

  .state p {
    margin: 0;
  }

  .table {
    display: flex;
    flex-direction: column;
  }

  .row {
    display: grid;
    grid-template-columns:
      minmax(10rem, 1.6fr)
      minmax(7.5rem, auto)
      minmax(4.5rem, auto)
      minmax(4.5rem, auto)
      minmax(6rem, 1fr)
      minmax(7rem, auto);
    gap: 0.65rem 1rem;
    align-items: center;
    padding: 0.55rem 0.85rem;
    border-bottom: 1px solid var(--line-soft);
    color: inherit;
    text-decoration: none;
  }

  a.row:hover {
    background: var(--raised);
  }

  a.row:hover .title {
    color: var(--accent);
  }

  .head-row {
    font-size: var(--text-10);
    letter-spacing: 0.06em;
    text-transform: uppercase;
    color: var(--faint);
    font-weight: var(--w-560);
    border-bottom: 1px solid var(--line);
    padding-top: 0.55rem;
    padding-bottom: 0.45rem;
  }

  .title-cell {
    display: flex;
    flex-direction: column;
    gap: 0.1rem;
    min-width: 0;
  }

  .title {
    font-weight: var(--w-560);
    font-size: var(--text-13);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .id {
    font-size: var(--text-10);
  }

  .file {
    font-size: var(--text-11);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .created {
    font-size: var(--text-11);
    white-space: nowrap;
  }

  .num-col {
    text-align: right;
    font-variant-numeric: tabular-nums;
  }

  .num {
    font-family: var(--font-mono);
    font-variant-numeric: tabular-nums;
    font-size: var(--text-12);
  }

  .dim {
    color: var(--dim);
  }

  .faint {
    color: var(--faint);
  }

  .stop {
    color: var(--stop);
  }

  .more {
    display: flex;
    align-items: center;
    gap: 0.75rem;
    padding: 0.75rem 0.85rem;
    border-top: 1px solid var(--line-soft);
  }

  @media (max-width: 720px) {
    .row {
      grid-template-columns: 1fr auto;
      grid-template-rows: auto auto;
    }

    .head-row {
      display: none;
    }

    .num-col,
    .file,
    .created {
      display: none;
    }
  }
</style>
