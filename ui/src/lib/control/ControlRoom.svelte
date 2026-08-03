<script lang="ts">
  /** Control room (unit 7.8 + 7.11). Everything on screen is the API's
   * measured/recorded value — absent data renders absent, never invented.
   * Spend paths (regenerate / mix / export) and run locking land here; editing
   * is 7.10, the player is 7.9, director mode is 7.12. */
  import { onMount } from "svelte";

  import BudgetChip from "./BudgetChip.svelte";
  import ConfirmDialog from "../components/ConfirmDialog.svelte";
  import DossierPanel from "./DossierPanel.svelte";
  import SpendPanel from "./SpendPanel.svelte";
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
    buildCommit,
    clearDraft,
    forkDraft,
    loadDraft,
    missingFreezeCount,
    rebaseDraft,
    saveDraft,
    type Draft,
  } from "./draft";
  import {
    getBudget,
    getDossier,
    getEvents,
    getLedgerRows,
    getMixPeaks,
    getProject,
    getQa,
    getQuota,
    getRun,
    getRuns,
    getScenes,
    getTimeline,
    getTimelineVersions,
    putTimeline,
    type BudgetResponse,
    type QuotaResponse,
    type DossierResponse,
    type EventOut,
    type LedgerRowsResponse,
    type PeaksResponse,
    type ProjectDetail,
    type QaReportResponse,
    type RunAccepted,
    type RunOut,
    type SceneOut,
    type TimelineEventOut,
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

  type Tab = "readiness" | "evidence" | "spend" | "history" | "dossier";

  let project = $state<ProjectDetail | null>(null);
  let scenes = $state<SceneOut[]>([]);
  let timeline = $state<TimelineResponse | null>(null);
  let versions = $state<TimelineVersionsResponse | null>(null);
  let events = $state<EventOut[]>([]);
  let runs = $state<RunOut[]>([]);
  let qa = $state<QaReportResponse | null>(null);
  let dossier = $state<DossierResponse | null>(null);
  // Whether the (slow) dossier fetch has been kicked off for this load.
  let dossierRequested = $state(false);
  let budget = $state<BudgetResponse | null>(null);
  let quota = $state<QuotaResponse | null>(null);
  let mixPeaks = $state<PeaksResponse | null>(null);
  // -- 7.16 ledger rows: lazy on the Spend tab (never first-paint) ---------
  let ledger = $state<LedgerRowsResponse | null>(null);
  let ledgerRequested = $state(false);
  let ledgerLoading = $state(false);
  let ledgerCursor = $state(0);

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

  // -- 7.10: draft / versions ----------------------------------------------
  let draft = $state<Draft | null>(null);
  /** Whether the persisted draft has been restored for this mount. */
  let draftRestoredOnce = $state(false);
  /** Persistence is debounced: a drag fires onEdit per pointermove, and a
   * synchronous localStorage write per frame is measurable jank for zero
   * benefit. In-memory state is always current; only the crash-recovery copy
   * lags by 200 ms. */
  let saveTimer: ReturnType<typeof setTimeout> | null = null;

  function scheduleDraftSave() {
    if (saveTimer) clearTimeout(saveTimer);
    saveTimer = setTimeout(() => {
      saveTimer = null;
      if (draft) saveDraft(id, draft);
    }, 200);
  }
  /** What the timeline shows: "draft" (the draft, or latest when none yet)
   * or a historical committed version (read-only — undo is version switching). */
  let viewing = $state<number | "draft">("draft");
  let historyTimeline = $state<TimelineResponse | null>(null);
  let commitError = $state<string | null>(null);
  let commitBusy = $state(false);
  let draftNote = $state<string | null>(null);
  let restoringVersion = $state<number | null>(null);
  let confirmDiscard = $state(false);
  /** Freezes the latest version has that this draft lacks — a commit would be
   * refused, and the timeline changed underneath (mix/commit elsewhere). */
  let draftStaleCount = $state(0);

  const locked = $derived(lock.kind === "locked");
  const displayRuns = $derived(mergeLiveRun(runs, liveRun));
  const displayEvents = $derived<TimelineEventOut[]>(
    viewing === "draft"
      ? draft
        ? (draft.events as unknown as TimelineEventOut[])
        : (timeline?.events ?? [])
      : (historyTimeline?.events ?? timeline?.events ?? []),
  );
  const showTimeline = $derived(
    viewing === "draft" ? timeline : (historyTimeline ?? timeline),
  );

  // `quiet` lives in quiet.ts, NOT here: written inline as a generic arrow,
  // Svelte's TS strip drops its parameter list and every fetch throws at
  // runtime while svelte-check stays green. See that file.
  // NOTE: no generic arrow functions in this file — see quiet.ts.
  function onAncillaryFailure(e: unknown) {
    ancillaryFailed = true;
    console.error("control room: ancillary fetch failed", e);
  }

  /** Load the project record, then let every panel fill in as its own fetch
   * resolves.
   *
   * These were once awaited together in one Promise.all, which made first
   * paint hostage to the slowest of eight calls. Measured against a real B2
   * bucket: dossier 56s (one HEAD per object), budget 25s (a full ledger
   * scan), qa 7s — while scenes and timeline, the two things a director
   * actually looks at, both land under 1s. The screen sat on "Loading
   * project…" for the best part of a minute with the content already in hand.
   *
   * The project record is still awaited: it is the shell, and without it there
   * is nothing to render. Everything else is a panel that knows how to be
   * empty. The dossier is not fetched here at all — see `ensureDossier`. */
  async function loadCore() {
    // Fresh load: reset sticky failure flag so a recovered poll does not
    // leave empty rows labelled as permanent failure.
    ancillaryFailed = false;
    dossierRequested = false;
    project = await getProject(id);

    void quiet(getScenes(id), onAncillaryFailure).then((r) => {
      scenes = r?.scenes ?? [];
    });
    void quiet(getTimeline(id), onAncillaryFailure).then((r) => {
      timeline = r;
      checkDraftStale();
    });
    void quiet(getTimelineVersions(id), onAncillaryFailure).then((r) => {
      versions = r;
      checkDraftStale();
      // Restore a persisted draft once the committed versions are known — a
      // draft whose base version is gone must not be resurrected. Runs once:
      // commits and post-run refreshes clear/preserve drafts deliberately.
      if (!draftRestoredOnce) {
        draftRestoredOnce = true;
        restorePersistedDraft();
      }
    });
    void quiet(getEvents(id), onAncillaryFailure).then((r) => {
      events = r?.events ?? [];
    });
    void quiet(getRuns(id), onAncillaryFailure).then((r) => {
      runs = r?.runs ?? [];
    });
    void quiet(getQa(id), onAncillaryFailure).then((r) => {
      qa = r;
    });
    void quiet(getBudget(id), onAncillaryFailure).then((r) => {
      budget = r;
    });
    void quiet(getQuota(), onAncillaryFailure).then((r) => {
      quota = r;
    });

    if (project.mix_run_id) {
      void quiet(getMixPeaks(id, project.mix_run_id), onAncillaryFailure).then(
        (r) => {
          mixPeaks = r;
        },
      );
    } else {
      mixPeaks = null;
    }

    // The dossier tab may already be the open one (a reload after a run).
    if (tab === "dossier") void ensureDossier();
  }

  /** The dossier composes every artifact in the project with a backend HEAD
   * per object — 56s on a real bucket. It is the closing-beat view, not
   * first-paint data, so it is fetched the first time its tab is opened and
   * kept until the next reload. */
  async function ensureDossier() {
    if (dossierRequested) return;
    dossierRequested = true;
    dossier = await quiet(getDossier(id), onAncillaryFailure);
  }

  /** The ledger rows are the audit trail behind the Spend tab's totals —
   * useful at the closing beat, noise at first paint, so they load on the
   * tab's first open like the dossier. The server's row cache bounds the
   * scan; pagination moves the cursor. */
  async function ensureLedger() {
    if (ledgerRequested) return;
    ledgerRequested = true;
    await refreshLedger();
  }

  async function refreshLedger() {
    ledgerLoading = true;
    const r = await quiet(getLedgerRows(id, ledgerCursor, 50), onAncillaryFailure);
    if (r) ledger = r;
    ledgerLoading = false;
  }

  function ledgerNewer() {
    if (ledgerCursor <= 0) return;
    ledgerCursor = 0;
    void refreshLedger();
  }

  function ledgerOlder() {
    if (ledger?.next_cursor == null) return;
    ledgerCursor = ledger.next_cursor;
    void refreshLedger();
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
      // A run bills new rows; the next Spend-tab open must see them.
      ledger = null;
      ledgerRequested = false;
      ledgerCursor = 0;
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

  // -- 7.10: draft editing / commit / version switching ---------------------

  /** Every edit funnels through here: fork a draft from the latest committed
   * version on the first edit, apply the pure operation, persist. Edits are
   * free — nothing leaves the client until Commit. A no-op apply (an unchanged
   * rename) must not fork a draft, so the fork is speculative until the
   * operation actually changed something. */
  function applyEdit(apply: (d: Draft) => Draft) {
    if (locked || viewing !== "draft") return;
    const base =
      draft ?? (timeline ? forkDraft(timeline.version, timeline) : null);
    if (!base) return;
    const next = apply(base);
    if (next === base) return;
    draft = next;
    commitError = null;
    draftStaleCount = 0;
    scheduleDraftSave();
  }

  async function commitDraft() {
    if (!draft || !timeline || locked || commitBusy) return;
    commitBusy = true;
    commitError = null;
    try {
      const saved = await putTimeline(id, buildCommit(draft));
      clearDraft(id);
      if (saveTimer) {
        clearTimeout(saveTimer);
        saveTimer = null;
      }
      draft = null;
      viewing = "draft";
      historyTimeline = null;
      draftStaleCount = 0;
      draftNote = null;
      timeline = saved;
      const v = await quiet(getTimelineVersions(id), onAncillaryFailure);
      if (v) versions = v;
      const p = await quiet(getProject(id), onAncillaryFailure);
      if (p) project = p;
      statusLine =
        `Committed v${saved.version} — one new immutable version, ` +
        `${saved.events.length} events. Move and resize are free; the next ` +
        `Regenerate or mix bills what it generates.`;
    } catch (e) {
      if (e instanceof ApiError) {
        commitError =
          Array.isArray(e.detail?.violations) &&
          (e.detail.violations as string[]).length > 0
            ? `The server refused this document: ${(e.detail.violations as string[]).join("; ")}`
            : `The server refused this document: ${e.message}`;
        // A run landed while the draft was open — the draft is stale now.
        checkDraftStale();
      } else {
        commitError = e instanceof Error ? e.message : String(e);
      }
    } finally {
      commitBusy = false;
    }
  }

  async function viewVersion(v: number) {
    if (viewing === v && historyTimeline) return;
    // The latest version IS the editable draft view when no draft exists — a
    // "historical" read-only view of it would be a pointless extra step.
    if (!draft && v === (versions?.latest ?? v)) {
      viewDraft();
      return;
    }
    // Fetch BEFORE switching the view: the timeline must never show one
    // version's content under another version's label. The committed versions
    // are immutable, so the fetch is short — the chip click may lag a beat,
    // and that beat is the honesty.
    const fetched = await quiet(getTimeline(id, v), onAncillaryFailure);
    if (!fetched) return;
    historyTimeline = fetched;
    viewing = v;
    commitError = null;
  }

  function viewDraft() {
    viewing = "draft";
    historyTimeline = null;
  }

  /** Undo by version switching: re-fork the draft from a historical version.
   * A dirty draft is replaced only with explicit confirmation. */
  function requestRestore(v: number) {
    if (!historyTimeline) return;
    if (draft) {
      restoringVersion = v;
      return;
    }
    restoreFrom(v);
  }

  function restoreFrom(v: number) {
    if (!historyTimeline) return;
    draft = forkDraft(v, historyTimeline);
    saveDraft(id, draft);
    restoringVersion = null;
    viewing = "draft";
    historyTimeline = null;
    draftStaleCount = 0;
    commitError = null;
    statusLine =
      `Undo to v${v}: the draft now starts from that version's document. ` +
      `Commit writes it as v${(timeline?.version ?? v) + 1} beside the others.`;
  }

  function requestDiscard() {
    confirmDiscard = true;
  }

  function discardDraft() {
    clearDraft(id);
    draft = null;
    confirmDiscard = false;
    viewing = "draft";
    historyTimeline = null;
    draftStaleCount = 0;
    commitError = null;
    draftNote = null;
    statusLine = "Draft discarded — the committed versions are untouched.";
  }

  /** The timeline moved underneath (a mix or commit landed elsewhere): the
   * draft is missing the freezes the latest version records, so a commit
   * would be refused. Importing the recorded freezes is a merge, not a lie. */
  function rebaseDraftNow() {
    if (!draft || !timeline) return;
    draft = rebaseDraft(draft, timeline);
    saveDraft(id, draft);
    draftStaleCount = 0;
    commitError = null;
    statusLine =
      `Merged v${timeline.version}'s recorded durations, addresses and seeds ` +
      `into the draft — your placement edits are untouched.`;
  }

  function checkDraftStale() {
    if (draft && timeline) {
      draftStaleCount =
        draft.baseVersion < timeline.version
          ? missingFreezeCount(timeline, draft)
          : 0;
    } else {
      draftStaleCount = 0;
    }
  }

  /** Restore a persisted draft once the committed versions are known. */
  function restorePersistedDraft() {
    const restored = loadDraft(id);
    if (!restored) return;
    const known = versions?.versions ?? [];
    if (!known.includes(restored.baseVersion)) {
      // The base version no longer exists (it never happens — versions are
      // immutable — but a corrupt store must not resurrect a phantom draft).
      clearDraft(id);
      draftNote = "A saved draft was discarded: its base version is gone.";
      return;
    }
    draft = restored;
    draftNote =
      "Draft restored from a previous session — not committed.";
    checkDraftStale();
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
    { key: "spend", label: "Spend" },
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
    {#if draftNote}
      <div class="banner info" role="status">
        <span class="detail">{draftNote}</span>
      </div>
    {/if}

    <div class="stage">
      <section class="viewer">
        {#if project.source_url || project.mix_url}
          <Player
            id={project.id}
            sourceUrl={project.source_url ?? null}
            mixUrl={project.mix_url ?? null}
            events={displayEvents}
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
              onclick={() => {
                tab = t.key;
                // The dossier is slow enough (a HEAD per object) that it is
                // fetched on first open rather than at load; so are the
                // ledger rows (the audit trail behind the Spend tab).
                if (t.key === "dossier") void ensureDossier();
                if (t.key === "spend") void ensureLedger();
              }}
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
        <div class="panel" role="tabpanel" aria-labelledby="tabbtn-spend" id="tab-spend" hidden={tab !== "spend"}>
          <SpendPanel
            {budget}
            {quota}
            failed={ancillaryFailed}
            ledger={ledger}
            ledgerLoading={ledgerLoading}
            onLedgerNewer={ledgerNewer}
            onLedgerOlder={ledgerOlder}
          />
        </div>
        <div class="panel" role="tabpanel" aria-labelledby="tabbtn-history" id="tab-history" hidden={tab !== "history"}>
          <HistoryPanel {versions} runs={displayRuns} {events} {mixPeaks} mixRunId={project.mix_run_id ?? null} />
        </div>
        <div class="panel" role="tabpanel" aria-labelledby="tabbtn-dossier" id="tab-dossier" hidden={tab !== "dossier"}>
          <DossierPanel {dossier} loading={dossierRequested && dossier === null} />
        </div>
      </aside>
    </div>

    <Timeline
      timeline={showTimeline}
      failed={ancillaryFailed}
      locked={locked}
      draft={draft}
      editable={!locked && viewing === "draft"}
      versions={versions?.versions ?? null}
      viewing={viewing}
      selectedIndex={selectedEventIndex}
      onselect={(i) => (selectedEventIndex = i)}
      onEdit={applyEdit}
      onCommit={() => void commitDraft()}
      onView={(v) => {
        if (typeof v === "number") void viewVersion(v);
        else viewDraft();
      }}
      onRestoreFrom={(v) => requestRestore(v)}
      onDiscardDraft={requestDiscard}
      commitError={commitError}
      staleCount={draftStaleCount}
      onRebase={rebaseDraftNow}
      commitPending={commitBusy}
    >
      {#snippet inspectorActions(ev)}
        {#if !draft && viewing === "draft"}
          <RegenerateAction
            projectId={id}
            eventIndex={ev.index}
            eventName={ev.name}
            locked={locked}
            onaccepted={onRunAccepted}
            onerror={onActionError}
          />
        {/if}
      {/snippet}
    </Timeline>

    {#if restoringVersion != null && historyTimeline}
      <ConfirmDialog
        open={true}
        title={`Restore v${restoringVersion} as the working draft?`}
        body={`Your current draft's edits will be replaced by v${restoringVersion}'s document (the committed versions themselves are untouched). This is undo: commit then writes your new document as the next version.`}
        confirmLabel="Replace draft"
        danger
        onconfirm={() => restoreFrom(restoringVersion as number)}
        oncancel={() => (restoringVersion = null)}
      />
    {/if}
    {#if confirmDiscard}
      <ConfirmDialog
        open={true}
        title="Discard this draft?"
        body="The draft's edits are lost — the committed versions are untouched. If you want to keep any of it, commit first."
        confirmLabel="Discard draft"
        danger
        onconfirm={discardDraft}
        oncancel={() => (confirmDiscard = false)}
      />
    {/if}
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
