<script lang="ts">
  /** Control room (unit 7.8 + 7.11). Everything on screen is the API's
   * measured/recorded value — absent data renders absent, never invented.
   * Spend paths (regenerate / mix / export) and run locking land here; editing
   * is 7.10, the player is 7.9, director mode is 7.12. */
  import { onMount } from "svelte";

  import BudgetChip from "./BudgetChip.svelte";
  import DossierPanel from "./DossierPanel.svelte";
  import EvidencePanel from "./EvidencePanel.svelte";
  import HistoryPanel from "./HistoryPanel.svelte";
  import Meter from "../components/Meter.svelte";
  import Player from "./Player.svelte";
  import ReadinessPanel from "./ReadinessPanel.svelte";
  import SceneRail from "./SceneRail.svelte";
  import Spinner from "../components/Spinner.svelte";
  import ThemeToggle from "../components/ThemeToggle.svelte";
  import Timeline from "./Timeline.svelte";
  import ViewerPlaceholder from "./ViewerPlaceholder.svelte";
  import { ApiError } from "../api/client";
  import { quiet } from "./quiet";
  import {
    getBudget,
    getDossier,
    getEvents,
    getMixPeaks,
    getProject,
    getQa,
    getRun,
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
    type RunAccepted,
    type RunOut,
    type SceneOut,
    type TimelineResponse,
    type TimelineVersionsResponse,
  } from "../api/projects";
  import {
    classifyRun,
    mergeLiveRun,
    POLL_INTERVAL_MS,
    type LockState,
  } from "../spend/lock";
  import RegenerateAction from "../spend/RegenerateAction.svelte";
  import RunLockBanner from "../spend/RunLockBanner.svelte";
  import SpendActions from "../spend/SpendActions.svelte";

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
  // A non-404 ancillary failure is NOT absence — say so rather than let a
  // panel render an empty state that looks like the data does not exist.
  let ancillaryFailed = $state(false);

  // -- 7.11: spend / lock -------------------------------------------------
  let selectedEventIndex = $state<number | null>(null);
  let liveRun = $state<RunOut | null>(null);
  let trackedRunId = $state<string | null>(null);
  let lock = $state<LockState>({ kind: "idle" });
  let statusLine = $state<string | null>(null);
  let actionError = $state<string | null>(null);

  const locked = $derived(lock.kind === "locked");
  const displayRuns = $derived(mergeLiveRun(runs, liveRun));

  // `quiet` lives in quiet.ts, NOT here: written inline as a generic arrow,
  // Svelte's TS strip drops its parameter list and every fetch throws at
  // runtime while svelte-check stays green. See that file.
  // NOTE: no generic arrow functions in this file — see quiet.ts.
  function onAncillaryFailure(e: unknown) {
    ancillaryFailed = true;
    console.error("control room: ancillary fetch failed", e);
  }

  async function loadCore() {
    // Fresh load: reset sticky failure flag so a recovered poll does not
    // leave empty rows labelled as permanent failure.
    ancillaryFailed = false;
    project = await getProject(id);
    const [sc, tl, vs, ev, rs, qaR, dos, bud] = await Promise.all([
      quiet(getScenes(id), onAncillaryFailure),
      quiet(getTimeline(id), onAncillaryFailure),
      quiet(getTimelineVersions(id), onAncillaryFailure),
      quiet(getEvents(id), onAncillaryFailure),
      quiet(getRuns(id), onAncillaryFailure),
      quiet(getQa(id), onAncillaryFailure),
      quiet(getDossier(id), onAncillaryFailure),
      quiet(getBudget(id), onAncillaryFailure),
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
      mixPeaks = await quiet(
        getMixPeaks(id, project.mix_run_id),
        onAncillaryFailure,
      );
    } else {
      mixPeaks = null;
    }
  }

  /** Light poll: project + tracked/latest run. Stages land on get_run.
   * A failed getRun must NOT unlock — null from quiet was the old trap. */
  async function pollTick() {
    if (!project) return;
    try {
      const next = await getProject(id);
      project = next;
      const runId = trackedRunId ?? next.latest_run_id ?? null;
      if (!runId) {
        // Only clear lock when we were not tracking something ourselves.
        if (trackedRunId == null && lock.kind !== "stale") {
          liveRun = null;
          lock = { kind: "idle" };
        }
        return;
      }

      // Direct getRun — do not use quiet() here. quiet nulls failures and
      // classifyRun(null) is idle, which would unlock mid-flight spend.
      let run: RunOut;
      try {
        run = await getRun(id, runId);
      } catch (e) {
        if (e instanceof ApiError && e.status === 404) {
          // Run id gone — treat as terminal only if we were tracking it.
          if (trackedRunId === runId || lock.kind === "locked") {
            trackedRunId = null;
            liveRun = null;
            lock = { kind: "idle" };
            await refreshAfterRun();
          }
          return;
        }
        // Network / 5xx: keep last lock, keep tracking, try again next tick.
        console.error("control room: getRun poll failed — holding lock", e);
        return;
      }

      liveRun = run;
      const nextLock = classifyRun(run);
      lock = nextLock;
      if (nextLock.kind === "stale") {
        // Lease expired — stop tracking so we do not poll forever; banner stays.
        trackedRunId = null;
        return;
      }
      if (nextLock.kind === "idle") {
        // Terminal (completed / failed) — full refresh so timeline/qa catch up.
        if (trackedRunId === runId || trackedRunId != null) {
          trackedRunId = null;
          await refreshAfterRun();
        }
      } else if (nextLock.kind === "locked" && trackedRunId == null) {
        // External live run discovered via latest_run_id — start tracking.
        trackedRunId = runId;
      }
    } catch (e) {
      // getProject failed — do not unlock.
      console.error("control room: poll failed", e);
    }
  }

  async function refreshAfterRun() {
    try {
      await loadCore();
      // Keep statusLine if it still names a projection; clear only blanks.
      // (Mix projection is set via onRunAccepted's optional line.)
    } catch (e) {
      console.error("control room: post-run refresh failed", e);
    }
  }

  function onRunAccepted(accepted: RunAccepted, line?: string) {
    trackedRunId = accepted.run_id;
    actionError = null;
    statusLine =
      line ?? `${accepted.kind} run ${accepted.run_id} accepted`;
    // Immediate lock so buttons disable before the first poll lands.
    // Placeholder liveRun is locked by construction — do not lease-classify
    // until the first successful getRun supplies server created_at.
    lock = {
      kind: "locked",
      runId: accepted.run_id,
      runKind: accepted.kind,
      stages: [],
      currentStage: null,
    };
    liveRun = {
      id: accepted.run_id,
      project_id: id,
      kind: accepted.kind,
      status: accepted.status ?? "running",
      // Placeholder only; pollTick replaces with server created_at.
      created_at: accepted.status === "running" ? "" : new Date().toISOString(),
      stages: [],
      current_stage: null,
      metadata: {},
    };
    tab = "readiness";
  }

  function onActionError(message: string) {
    actionError = message;
  }

  onMount(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setInterval> | null = null;

    (async () => {
      try {
        await loadCore();
        if (cancelled) return;
        // Seed lock from latest run if one is still live or stale.
        if (project?.latest_run_id) {
          trackedRunId = project.latest_run_id;
          try {
            const run = await getRun(id, project.latest_run_id);
            if (cancelled) return;
            liveRun = run;
            lock = classifyRun(run);
            if (lock.kind === "idle") trackedRunId = null;
          } catch (e) {
            // Failed seed must not leave us "unlocked and done" while a run
            // may still be live — keep tracked id and try on the first poll.
            console.error("control room: seed getRun failed", e);
            lock = {
              kind: "locked",
              runId: project.latest_run_id,
              runKind: "unknown",
              stages: [],
              currentStage: null,
            };
          }
        }
        status = "ready";
      } catch (e) {
        if (cancelled) return;
        status = "error";
        error = e instanceof ApiError ? e.message : String(e);
        return;
      }

      // Always light-poll: external mix/repair leaves status needs_review
      // (not analyzing), so a predicate on analyzing alone would miss it.
      // getProject + optional getRun is cheap; holding lock on failure is
      // the correctness-critical path (see pollTick).
      timer = setInterval(() => {
        void pollTick();
      }, POLL_INTERVAL_MS);
    })();

    return () => {
      cancelled = true;
      if (timer) clearInterval(timer);
    };
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
      <SpendActions
        projectId={id}
        locked={locked}
        timelineVersion={project.timeline_version ?? null}
        mixRunId={project.mix_run_id ?? null}
        onaccepted={onRunAccepted}
        onerror={onActionError}
      />
      <span class="sep" aria-hidden="true"></span>
      <ThemeToggle />
    </header>

    <RunLockBanner {lock} />

    {#if actionError}
      <div class="banner err" role="alert">
        <span class="word">Action failed</span>
        <span class="detail">{actionError}</span>
        <button class="dismiss" type="button" onclick={() => (actionError = null)}>
          Dismiss
        </button>
      </div>
    {/if}
    {#if statusLine && lock.kind !== "stale"}
      <div class="banner info" role="status">
        <span class="detail">{statusLine}</span>
      </div>
    {/if}

    <div class="stage">
      <section class="viewer">
        {#if project.source_url || project.mix_url}
          <Player
            id={project.id}
            sourceUrl={project.source_url ?? null}
            mixUrl={project.mix_url ?? null}
            events={timeline?.events ?? []}
          />
        {:else}
          <ViewerPlaceholder />
        {/if}
        <SceneRail {scenes} failed={ancillaryFailed} />
      </section>

      <aside class="rail">
        {#if ancillaryFailed}
          <p class="load-warn" role="status">
            Some data failed to load — the empty rows below are a failure, not
            absence. See the console.
          </p>
        {/if}
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
          <ReadinessPanel
            {project}
            runs={displayRuns}
            liveRun={liveRun}
            locked={locked}
          />
        </div>
        <div class="panel" role="tabpanel" aria-labelledby="tabbtn-evidence" id="tab-evidence" hidden={tab !== "evidence"}>
          <EvidencePanel {qa} {scenes} />
        </div>
        <div class="panel" role="tabpanel" aria-labelledby="tabbtn-history" id="tab-history" hidden={tab !== "history"}>
          <HistoryPanel {versions} runs={displayRuns} {events} {mixPeaks} mixRunId={project.mix_run_id ?? null} />
        </div>
        <div class="panel" role="tabpanel" aria-labelledby="tabbtn-dossier" id="tab-dossier" hidden={tab !== "dossier"}>
          <DossierPanel {dossier} />
        </div>
      </aside>
    </div>

    <Timeline
      {timeline}
      failed={ancillaryFailed}
      locked={locked}
      selectedIndex={selectedEventIndex}
      onselect={(i) => (selectedEventIndex = i)}
    >
      {#snippet inspectorActions(ev)}
        <RegenerateAction
          projectId={id}
          eventIndex={ev.index}
          eventName={ev.name}
          locked={locked}
          onaccepted={onRunAccepted}
          onerror={onActionError}
        />
      {/snippet}
    </Timeline>
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

  .banner {
    display: flex;
    align-items: flex-start;
    gap: 8px;
    padding: 6px 14px;
    font-size: var(--text-12);
    border-bottom: 1px solid var(--line-soft);
    flex: 0 0 auto;
  }

  .banner.err {
    background: color-mix(in srgb, var(--stop) 10%, var(--surface));
  }

  .banner.info {
    background: var(--raised);
    color: var(--dim);
  }

  .banner .word {
    font-weight: var(--w-590);
    color: var(--stop);
    flex: 0 0 auto;
  }

  .banner .detail {
    min-width: 0;
    line-height: 1.4;
    color: var(--dim);
  }

  .banner .dismiss {
    margin-left: auto;
    border: 0;
    background: transparent;
    color: var(--faint);
    cursor: pointer;
    font: inherit;
    font-size: var(--text-11);
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }

  .banner .dismiss:hover {
    color: var(--text);
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

  .load-warn {
    margin: 0;
    padding: 7px 13px;
    font-size: var(--text-11);
    color: var(--warn);
    border-bottom: 1px solid var(--line-soft);
    background: var(--warn-q);
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
