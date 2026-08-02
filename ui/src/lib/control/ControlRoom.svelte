<script lang="ts">
  /** Control room, read-only (unit 7.8). Everything on screen is the API's
   * measured/recorded value — absent data renders absent, never invented.
   * Editing is 7.10, the player is 7.9, director mode is 7.12. */
  import { onMount } from "svelte";

  import BudgetChip from "./BudgetChip.svelte";
  import DossierPanel from "./DossierPanel.svelte";
  import EvidencePanel from "./EvidencePanel.svelte";
  import HistoryPanel from "./HistoryPanel.svelte";
  import Meter from "../components/Meter.svelte";
  import ReadinessPanel from "./ReadinessPanel.svelte";
  import SceneRail from "./SceneRail.svelte";
  import Spinner from "../components/Spinner.svelte";
  import ThemeToggle from "../components/ThemeToggle.svelte";
  import Timeline from "./Timeline.svelte";
  import ViewerPlaceholder from "./ViewerPlaceholder.svelte";
  import { ApiError } from "../api/client";
  import {
    getBudget,
    getDossier,
    getEvents,
    getMixPeaks,
    getProject,
    getQa,
    getRuns,
    getScenes,
    getTimeline,
    getTimelineVersions,
    type BudgetResponse,
    type DossierResponse,
    type EventOut,
    type PeaksResponse,
    type ProjectDetail,
    type QaReportResponse,
    type RunOut,
    type SceneOut,
    type TimelineResponse,
    type TimelineVersionsResponse,
  } from "../api/projects";

  interface Props {
    id: string;
  }

  let { id }: Props = $props();

  type Tab = "readiness" | "evidence" | "history" | "dossier";

  let project = $state<ProjectDetail | null>(null);
  let scenes = $state<SceneOut[]>([]);
  let timeline = $state<TimelineResponse | null>(null);
  let versions = $state<TimelineVersionsResponse | null>(null);
  let events = $state<EventOut[]>([]);
  let runs = $state<RunOut[]>([]);
  let qa = $state<QaReportResponse | null>(null);
  let dossier = $state<DossierResponse | null>(null);
  let budget = $state<BudgetResponse | null>(null);
  let mixPeaks = $state<PeaksResponse | null>(null);

  let status = $state<"loading" | "ready" | "error">("loading");
  let error = $state("");
  let tab = $state<Tab>("readiness");

  // An ancillary endpoint that has not produced yet is a 404 → empty state,
  // never an error. Only the project record failing is fatal.
  const quiet = async <T,>(p: Promise<T>): Promise<T | null> => {
    try {
      return await p;
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) return null;
      console.error("control room: ancillary fetch failed", e);
      return null;
    }
  };

  onMount(async () => {
    try {
      project = await getProject(id);
    } catch (e) {
      status = "error";
      error = e instanceof ApiError ? e.message : String(e);
      return;
    }
    const [sc, tl, vs, ev, rs, qaR, dos, bud] = await Promise.all([
      quiet(getScenes(id)),
      quiet(getTimeline(id)),
      quiet(getTimelineVersions(id)),
      quiet(getEvents(id)),
      quiet(getRuns(id)),
      quiet(getQa(id)),
      quiet(getDossier(id)),
      quiet(getBudget(id)),
    ]);
    scenes = sc?.scenes ?? [];
    timeline = tl;
    versions = vs;
    events = ev?.events ?? [];
    runs = rs?.runs ?? [];
    qa = qaR;
    dossier = dos;
    budget = bud;
    if (project.mix_run_id) {
      mixPeaks = await quiet(getMixPeaks(id, project.mix_run_id));
    }
    status = "ready";
  });

  const tabs: { key: Tab; label: string }[] = [
    { key: "readiness", label: "Readiness" },
    { key: "evidence", label: "Evidence" },
    { key: "history", label: "History" },
    { key: "dossier", label: "Dossier" },
  ];
</script>

{#if status === "loading"}
  <div class="center">
    <Spinner label="Loading project" />
    <span class="dim">Loading project…</span>
  </div>
{:else if status === "error"}
  <div class="center">
    <p class="stop">Could not load this project — {error}</p>
  </div>
{:else if project}
  <div class="app">
    <header class="chrome">
      <span class="brand">FoleyFlow</span>
      <div class="proj">
        <h1>{project.title}</h1>
        <span class="ver num">
          {project.source_filename} · {project.duration_s == null ? "—" : `${project.duration_s.toFixed(2)}s`} · {project.scene_count} scenes
        </span>
      </div>
      <span class="spacer"></span>
      <Meter percent={project.readiness_percent ?? null} />
      <span class="sep" aria-hidden="true"></span>
      <BudgetChip {budget} />
      <span class="sep" aria-hidden="true"></span>
      <ThemeToggle />
    </header>

    <div class="stage">
      <section class="viewer">
        <ViewerPlaceholder />
        <SceneRail {scenes} />
      </section>

      <aside class="rail">
        <div class="tabs" role="tablist" aria-label="Project">
          {#each tabs as t (t.key)}
            <button
              role="tab"
              aria-selected={tab === t.key}
              aria-controls={`tab-${t.key}`}
              id={`tabbtn-${t.key}`}
              class:active={tab === t.key}
              onclick={() => (tab = t.key)}
            >
              {t.label}
            </button>
          {/each}
        </div>

        <div class="panel" role="tabpanel" aria-labelledby="tabbtn-readiness" id="tab-readiness" hidden={tab !== "readiness"}>
          <ReadinessPanel {project} latestRun={runs[0] ?? null} />
        </div>
        <div class="panel" role="tabpanel" aria-labelledby="tabbtn-evidence" id="tab-evidence" hidden={tab !== "evidence"}>
          <EvidencePanel {qa} {scenes} />
        </div>
        <div class="panel" role="tabpanel" aria-labelledby="tabbtn-history" id="tab-history" hidden={tab !== "history"}>
          <HistoryPanel {versions} {runs} {events} {mixPeaks} />
        </div>
        <div class="panel" role="tabpanel" aria-labelledby="tabbtn-dossier" id="tab-dossier" hidden={tab !== "dossier"}>
          <DossierPanel {dossier} />
        </div>
      </aside>
    </div>

    <Timeline {timeline} />
  </div>
{/if}

<style>
  .app {
    display: flex;
    flex-direction: column;
    height: 100vh;
    min-height: 0;
  }

  .chrome {
    display: flex;
    align-items: center;
    gap: 12px;
    height: var(--chrome-h);
    padding: 0 1rem;
    background: var(--surface);
    border-bottom: 1px solid var(--line);
    flex: 0 0 auto;
  }

  .brand {
    font-weight: var(--w-590);
    font-size: var(--text-14);
  }

  .proj {
    display: flex;
    align-items: baseline;
    gap: 9px;
    min-width: 0;
  }

  .proj h1 {
    margin: 0;
    font-size: var(--text-14);
    font-weight: var(--w-590);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .proj .ver {
    color: var(--faint);
    font-size: var(--text-11);
    white-space: nowrap;
  }

  .spacer {
    flex: 1 1 auto;
  }

  .sep {
    width: 1px;
    height: 20px;
    background: var(--line);
  }

  .stage {
    display: grid;
    grid-template-columns: minmax(0, 1fr) var(--rail-w);
    min-height: 0;
    flex: 1 1 auto;
  }

  .viewer {
    display: flex;
    flex-direction: column;
    min-width: 0;
    min-height: 0;
    border-right: 1px solid var(--line);
  }

  .rail {
    display: flex;
    flex-direction: column;
    min-height: 0;
    background: var(--surface);
  }

  .tabs {
    display: flex;
    gap: 2px;
    padding: 7px 10px 0;
    border-bottom: 1px solid var(--line);
    flex: 0 0 auto;
  }

  .tabs button {
    background: transparent;
    border: 0;
    border-bottom: 2px solid transparent;
    border-radius: 0;
    padding: 5px 9px 7px;
    color: var(--dim);
    font-size: var(--text-12);
    font-weight: var(--w-500);
    cursor: pointer;
  }

  .tabs button.active {
    color: var(--text);
    border-bottom-color: var(--accent);
  }

  .panel {
    flex: 1 1 auto;
    min-height: 0;
    overflow-y: auto;
    padding: 12px 13px 18px;
  }

  .panel[hidden] {
    display: none;
  }

  .center {
    height: 100vh;
    display: grid;
    place-content: center;
    gap: 10px;
    justify-items: center;
    color: var(--dim);
  }

  .stop {
    color: var(--stop);
  }

  .num {
    font-family: var(--font-mono);
    font-variant-numeric: tabular-nums;
  }

  @media (max-width: 1000px) {
    .stage {
      grid-template-columns: 1fr;
      grid-template-rows: auto 1fr;
    }
  }
</style>
