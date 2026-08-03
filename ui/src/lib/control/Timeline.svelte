<script lang="ts">
  /** Timeline (units 7.8 + 7.10). Read-only rendering became a directly
   * manipulated timeline: drag to move, edge handles to resize, arrow keys to
   * nudge, inline rename, delete — all edits land in the draft via `onEdit`
   * and cost nothing (they window the clip that already exists). Version
   * chips switch committed versions (undo is version switching, decision
   * A.2.1); a historical view is read-only with an explicit "Edit from here".
   * 7.11: locked prop + selected index + regenerate slot. */
  import EnvelopeCanvas from "./EnvelopeCanvas.svelte";
  import Tooltip from "../help/Tooltip.svelte";
  import Term from "../help/Term.svelte";
  import { envelopeSeed, groupLanes, placeEvent } from "./helpers";
  import { resizeHandle } from "./resize-handle";
  import { timelineGrip } from "./timeline-grip";
  import { transientOffset } from "./preview";
  import {
    deleteEvent,
    draftChanged,
    moveEvent,
    NUDGE_COARSE_S,
    NUDGE_FINE_S,
    NUDGE_S,
    renameEvent,
    resizeEnd,
    resizeStart,
    type Draft,
  } from "./draft";
  import type { TimelineEventOut } from "../api/projects";
  import type { DirectorResponse } from "../api/projects";
  import type { Snippet } from "svelte";

  interface Props {
    timeline: {
      version: number;
      duration: number;
      ambience: string;
      events: TimelineEventOut[];
    } | null;
    /** True when the fetch failed — an empty timeline must not claim
     * "analysis has not run", which is a specific and in that case false
     * statement about the project. */
    failed?: boolean;
    /** When true, selection still works but edit affordances stay off (7.10/7.11). */
    locked?: boolean;
    /** Selected event index (API address for repair — never name). */
    selectedIndex?: number | null;
    onselect?: (index: number | null) => void;
    /** Inspector actions for the selected event (e.g. Regenerate, 7.11). */
    inspectorActions?: Snippet<[event: TimelineEventOut]>;
    // -- 7.10 editing ------------------------------------------------------
    /** The editable working copy; null while viewing committed only. */
    draft?: Draft | null;
    /** Interactions enabled: not locked, viewing the draft (or latest when no
     * draft exists yet — the first edit forks one). */
    editable?: boolean;
    /** An active director-mode proposal (unit 7.12): blocks render at the
     * proposed positions, ghosted outlines at the old ones, and editing is
     * suspended until the director applies or discards. */
    proposal?: DirectorResponse | null;
    /** Committed versions for the chips. */
    versions?: number[] | null;
    /** What is being shown: the draft/latest, or a historical version. */
    viewing?: number | "draft";
    /** Apply an edit to the draft (parent forks/persists). */
    onEdit?: (apply: (d: Draft) => Draft) => void;
    onCommit?: () => void;
    onView?: (v: number | "draft") => void;
    onRestoreFrom?: (v: number) => void;
    onDiscardDraft?: () => void;
    /** Server refusal from the last commit attempt (freeze violations etc.). */
    commitError?: string | null;
    /** Freezes the latest version records that this draft lacks — the
     * timeline changed underneath and a commit would be refused. */
    staleCount?: number;
    onRebase?: () => void;
    /** A commit is in flight — the button disables until it settles. */
    commitPending?: boolean;
  }

  let {
    timeline,
    failed = false,
    locked = false,
    selectedIndex = null,
    onselect,
    inspectorActions,
    draft = null,
    editable = false,
    proposal = null,
    versions = null,
    viewing = "draft" as number | "draft",
    onEdit,
    onCommit,
    onView,
    onRestoreFrom,
    onDiscardDraft,
    commitError = null,
    staleCount = 0,
    onRebase,
    commitPending = false,
  }: Props = $props();

  const PPS = 64; // px per second — the ruler's working density


  let selected = $state<number | null>(null);
  let height = $state(236);
  const MIN_H = 148;

  // Parent may drive selection (ControlRoom); keep local state in sync.
  $effect(() => {
    if (selectedIndex !== undefined && selectedIndex !== selected) {
      selected = selectedIndex;
    }
  });

  function select(index: number) {
    selected = index;
    onselect?.(index);
  }

  // The document on screen: the draft when one is being edited, else the
  // committed version being viewed. DraftEvent is structurally a
  // TimelineEventOut (same fields, mutable copies) — the preview, lanes and
  // transient ticks all read through here, so the browser preview follows a
  // nudge instantly ("hear only translation", free). An active director
  // proposal (unit 7.12) shows the PROPOSED document — solid blocks at where
  // the model suggested — with ghosted outlines at the old positions.
  const display = $derived(
    proposal
      ? {
          version: proposal.base_version,
          duration: proposal.timeline.duration,
          ambience: proposal.timeline.ambience,
          events: proposal.timeline.events,
        }
      : draft
        ? {
            version: draft.baseVersion,
            duration: draft.duration,
            ambience: draft.ambience,
            // DraftEvent is structurally TimelineEventOut — no cast, so adding
            // a field to TimelineEventOut breaks this assignment at build time.
            events: draft.events,
          }
        : timeline,
  );

  const isDraftView = $derived(!!draft && viewing === "draft");
  const isProposalView = $derived(proposal != null && proposal.edits.length > 0);
  const isHistorical = $derived(typeof viewing === "number" && timeline !== null);
  // Editing is live on the draft AND on the latest committed before a draft
  // exists — the first edit forks one (applyEdit in ControlRoom). Only a
  // historical view is off-limits, and an active proposal suspends editing
  // until the director applies or discards (the proposal is the decision).
  const canEdit = $derived(
    editable && !locked && viewing === "draft" && !proposal && display != null,
  );

  /** Ghost blocks: the OLD placements of touched events, hollowed out. New
   * placements are the solid blocks via `display`; these say "was here". */
  const ghosts = $derived(
    proposal
      ? proposal.edits.map((e) => ({
          index: e.index,
          leftPx: e.old.start_time * PPS,
          widthPx: Math.max(e.old.duration * PPS, 12),
        }))
      : [],
  );

  const lanes = $derived(display ? groupLanes(display.events) : []);
  const widthPx = $derived((display?.duration ?? 0) * PPS);
  const ticks = $derived(
    display
      ? Array.from({ length: Math.ceil(display.duration) + 1 }, (_, i) => i)
      : [],
  );
  const selectedEvent = $derived(
    selected == null || !display
      ? null
      : (display.events.find((e) => e.index === selected) ?? null),
  );

  // -- editing: move / resize / nudge --------------------------------------
  type Gesture =
    | { kind: "move"; index: number; startX: number; startTime: number }
    | { kind: "resize-end"; index: number; startX: number; startTime: number; duration: number }
    | { kind: "resize-start"; index: number; startX: number; startTime: number; duration: number };

  let gesture = $state<Gesture | null>(null);

  function beginMove(e: PointerEvent, ev: TimelineEventOut) {
    if (!canEdit) return;
    e.preventDefault();
    gesture = {
      kind: "move",
      index: ev.index,
      startX: e.clientX,
      startTime: ev.start_time,
    };
    capturePointer(e);
  }

  function beginResize(e: PointerEvent, ev: TimelineEventOut, side: "start" | "end") {
    if (!canEdit) return;
    e.preventDefault();
    e.stopPropagation();
    gesture = {
      kind: side === "end" ? "resize-end" : "resize-start",
      index: ev.index,
      startX: e.clientX,
      startTime: ev.start_time,
      duration: ev.duration,
    };
    capturePointer(e);
  }

  function capturePointer(e: PointerEvent) {
    try {
      (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    } catch {
      // jsdom / odd environments have no active pointer capture — the drag
      // still works from the window-level move/up handlers on the element.
    }
  }

  function onGestureMove(e: PointerEvent) {
    const g = gesture;
    if (!g || !onEdit) return;
    const dx = (e.clientX - g.startX) / PPS;
    const opts = { snap: !e.altKey };
    if (g.kind === "move") {
      onEdit((d) => moveEvent(d, g.index, g.startTime + dx, opts));
    } else if (g.kind === "resize-end") {
      onEdit((d) => resizeEnd(d, g.index, g.startTime + g.duration + dx, opts));
    } else {
      onEdit((d) => resizeStart(d, g.index, g.startTime + dx, opts));
    }
  }

  function endGesture() {
    gesture = null;
  }

  // A pointer capture failure (jsdom, exotic browsers) means the pointerup
  // that ends the drag can land outside the block. A window-level fallback
  // closes the gesture wherever the release happens; the effect's cleanup
  // removes it as soon as the gesture is over.
  $effect(() => {
    if (!gesture) return;
    window.addEventListener("pointerup", endGesture);
    return () => window.removeEventListener("pointerup", endGesture);
  });

  /** Keyboard resize on a focused handle (the separator pattern from
   * timeline-grip.ts, per-event): ←/→ move the edge 10 ms, Shift 100 ms,
   * Alt 1 ms — the same grid as pointer resize. Reads the draft's CURRENT
   * position (not the closure's) so a fast repeat never double-applies. The
   * handler must stop propagation: the block underneath also nudges on
   * arrows, and a focused handle must resize, not translate. */
  function onResizeKey(e: KeyboardEvent, ev: TimelineEventOut, side: "start" | "end") {
    if (!canEdit || !onEdit) return;
    if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
    e.preventDefault();
    e.stopPropagation();
    const step = e.altKey ? NUDGE_FINE_S : e.shiftKey ? NUDGE_COARSE_S : NUDGE_S;
    const dir = e.key === "ArrowLeft" ? -1 : 1;
    const idx = ev.index;
    onEdit((d) => {
      const cur = d.events.find((x) => x.index === idx);
      if (!cur) return d;
      return side === "end"
        ? resizeEnd(d, idx, cur.start_time + cur.duration + dir * step, { snap: false })
        : resizeStart(d, idx, cur.start_time + dir * step, { snap: false });
    });
  }

  /** Arrow-key nudge on the selected block: ←/→ 10 ms, Shift 100 ms,
   * Alt 1 ms — the same resolution a director works at. The step is applied
   * to the draft's CURRENT position (not the closure's snapshot) so a fast
   * repeat never double-applies a stale start. */
  function onBlockKey(e: KeyboardEvent, ev: TimelineEventOut) {
    if (!canEdit || !onEdit) return;
    if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
    e.preventDefault();
    const step = e.altKey ? NUDGE_FINE_S : e.shiftKey ? NUDGE_COARSE_S : NUDGE_S;
    const dir = e.key === "ArrowLeft" ? -1 : 1;
    const idx = ev.index;
    onEdit((d) => {
      const cur = d.events.find((x) => x.index === idx);
      if (!cur) return d;
      return moveEvent(d, idx, cur.start_time + dir * step, { snap: false });
    });
  }

  // -- inspector: rename ---------------------------------------------------
  let renaming = $state(false);
  let renameValue = $state("");

  function startRename() {
    if (!selectedEvent || !canEdit) return;
    const frozen = selectedEvent.source_duration_s != null;
    if (frozen) return; // the commit refuses renames of generated content
    renameValue = selectedEvent.name;
    renaming = true;
  }

  function applyRename() {
    if (!onEdit || selected == null) return;
    const idx = selected;
    const value = renameValue;
    onEdit((d) => renameEvent(d, idx, value) ?? d);
    renaming = false;
  }

  // -- resize grip (a real separator; the action attaches listeners and
  //     ARIA programmatically — see timeline-grip.ts) -----------------------
  function onGripKey(e: KeyboardEvent) {
    const coarse = e.shiftKey ? 4 : 1;
    if (e.key === "ArrowUp") {
      e.preventDefault();
      height = Math.max(MIN_H, height - 12 * coarse);
    } else if (e.key === "ArrowDown") {
      e.preventDefault();
      height += 12 * coarse;
    }
  }

  const nextVersion = $derived(
    timeline ? timeline.version + 1 : null,
  );

  function draftChangedText(d: Draft): string {
    return draftChanged(d)
      ? "unsaved changes — commit writes one new version"
      : "unchanged";
  }</script>

<section class="timeline" style={`height:${height}px`} aria-label="Timeline">
  <div
    class="grip"
    use:timelineGrip={{
      min: MIN_H,
      value: height,
      onKey: onGripKey,
      onDrag: (h) => (height = Math.max(MIN_H, Math.round(h))),
    }}
  ></div>

  <div class="head">
    <span class="title">Timeline</span>

    {#if versions && versions.length > 0}
      <div class="vers" role="group" aria-label="Timeline versions">
        {#each versions as v (v)}
          <button
            type="button"
            class="vchip num"
            class:pressed={viewing === v ||
              (viewing === "draft" && !draft && v === (versions[versions.length - 1]))}
            aria-pressed={viewing === v ||
              (viewing === "draft" && !draft && v === (versions[versions.length - 1]))}
            onclick={() => onView?.(v)}
          >
            <Tooltip
              text={v === versions[versions.length - 1]
                ? `v${v} — latest committed version`
                : `v${v} — earlier version (undo: view it, then edit from here)`}
            >
              <span>v{v}</span>
            </Tooltip>
          </button>
        {/each}
        {#if draft}
          <button
            type="button"
            class="vchip draft"
            class:pressed={viewing === "draft"}
            aria-pressed={viewing === "draft"}
            onclick={() => onView?.("draft")}
          >
            <Tooltip text={`Draft — ${draftChangedText(draft)}`}>
              <span>draft +1</span>
            </Tooltip>
          </button>
        {/if}
      </div>
    {/if}

    <span class="spacer"></span>

    {#if isProposalView}
      <span class="note proposal">proposal — ghosted old positions · nothing applied</span>
    {:else if isHistorical}
      <span class="note historic">viewing v{viewing} — historical</span>
      <button
        type="button"
        class="head-btn"
        disabled={locked}
        onclick={() => onRestoreFrom?.(viewing as number)}
      >
        <Tooltip
          text={locked
            ? "A run is in flight — editing is locked"
            : `Restore v${viewing} as the working draft (undo to this version)`}
        >
          <span>Edit from here</span>
        </Tooltip>
      </button>
    {:else if canEdit}
      <span class="note snap">snap 10 ms · ⌥ drag to override</span>
      <span class="note legend">
        <Term term="transient" /> = start + 50 ms bias
      </span>
      <span class="note free">move · resize · rename · delete free</span>
    {:else if locked}
      <span class="note lock">run in flight — locked</span>
    {:else}
      <span class="note">committed</span>
    {/if}

    {#if draft}
      {#if isDraftView && !isProposalView}
        <button type="button" class="head-btn primary" disabled={locked || commitPending} onclick={onCommit}>
          <Tooltip text={commitPending ? "Committing…" : "Write this draft as one new immutable version"}>
            <span>{commitPending ? "Committing…" : `Commit v${nextVersion ?? ""}`}</span>
          </Tooltip>
        </button>
        <button type="button" class="head-btn ghost" onclick={onDiscardDraft}>
          <Tooltip text="Discard this draft — the committed versions are untouched">
            <span>Discard</span>
          </Tooltip>
        </button>
      {/if}
    {/if}
  </div>

  {#if isProposalView && proposal}
    <div class="headline proposal" role="status">
      <span class="word">Director proposal</span>
      <span class="detail">
        {proposal.edits.length} {proposal.edits.length === 1 ? "event" : "events"} at their proposed positions
        (hollow blocks = current positions). The preview still plays the current document; nothing is
        applied until you approve in the Director tab.
      </span>
    </div>
  {/if}

  {#if isDraftView && commitError}
    <div class="headline err" role="alert">
      <span class="word">Commit refused</span>
      <span class="detail">{commitError}</span>
    </div>
  {/if}
  {#if isDraftView && draft}
    <div class="headline note">
      <span class="detail">
        Unsaved changes to <span class="num">v{draft.baseVersion}</span>
        {#if staleCount > 0}
          · the timeline moved underneath — latest is
          <span class="num">v{versions?.[versions.length - 1] ?? "?"}</span> and
          records {staleCount} frozen {staleCount === 1 ? "duration" : "durations"} this
          draft is missing; the commit would be refused.
          <button type="button" class="head-btn mini" onclick={onRebase}>
            Merge recorded freezes from latest
          </button>
        {/if}
        — commits write one new version; nothing is billed.
      </span>
    </div>
  {/if}

  <div class="wrap">
    <div class="grid" style={`width:${Math.max(widthPx, 480)}px`}>
      <div class="ruler" style={`width:${Math.max(widthPx, 480)}px`}>
        {#if display}
          {#each ticks as i}
            {@const major = i % 3 === 0}
            {@const left = ((i / display.duration) * 100).toFixed(3)}
            <i class="tick" class:major={major} style={`left:${left}%`}></i>
            {#if major && i < display.duration}
              <span class="tlabel num" style={`left:${left}%`}>{i}s</span>
            {/if}
          {/each}
          <!-- Hit points at the TRANSIENT, not start_time (control-room.md §7,
               unit 7.9): discrete events are placed at start + 50 ms bias −
               onset in the mix; onset is per-clip and not exposed, so the
               marker is bias-adjusted only and says so. -->
          {#each lanes.flatMap((l) => l.events) as ev (ev.index)}
            {#if ev.kind === "discrete"}
              {@const t = (transientOffset(ev) / display.duration) * 100}
              <i
                class="transient"
                style={`left:${t.toFixed(3)}%`}
                aria-hidden="true"
              ></i>
            {/if}
          {/each}
        {/if}
      </div>

      {#if lanes.length === 0}
        <div class="empty">
          {#if isDraftView}
            <p>
              Every event was deleted — commit writes an empty document
              (refused while the current version still has generated content;
              deleting it explicitly is free).
            </p>
          {:else}
            <p>
              {failed
                ? "The timeline could not be loaded."
                : "No timeline yet — analysis has not run."}
            </p>
          {/if}
        </div>
      {:else}
        {#each lanes as lane}
          <div class="lane-row" style={`width:${Math.max(widthPx, 480)}px`}>
            <div class="tname">
              <span class="n">{lane.label}</span>
              <span class="k">{lane.kind}</span>
            </div>
            <div class="lane">
              {#each lane.events as ev}
                {@const p = placeEvent(ev, PPS)}
                {#if isProposalView}
                  {@const ghost = ghosts.find((g) => g.index === ev.index)}
                  {#if ghost && (ghost.leftPx !== p.leftPx || ghost.widthPx !== p.widthPx)}
                    <div
                      class="ghost"
                      style={`left:${ghost.leftPx}px;width:${ghost.widthPx}px`}
                      aria-hidden="true"
                    ></div>
                  {/if}
                {/if}
                <div
                  class="block"
                  class:selected={selected === ev.index}
                  class:draft={isDraftView}
                  class:proposed={isProposalView}
                  style={`left:${p.leftPx}px;width:${p.widthPx}px`}
                  role="button"
                  tabindex="0"
                  aria-label={`${ev.name} — index ${ev.index}, starts at ${ev.start_time.toFixed(3)}s${canEdit ? " — drag to move, arrow keys to nudge" : ""}`}
                  aria-pressed={selected === ev.index}
                  onpointerdown={(e) => beginMove(e, ev)}
                  onpointermove={onGestureMove}
                  onpointerup={endGesture}
                  onpointercancel={endGesture}
                  onkeydown={(e) => onBlockKey(e, ev)}
                  onclick={() => select(ev.index)}
                >
                  <EnvelopeCanvas kind={ev.kind} seed={envelopeSeed(ev)} />
                  <span class="bname">{ev.name}</span>
                  {#if canEdit}
                    <span
                      class="handle h-l"
                      aria-label={`Resize ${ev.name} start — arrow keys to adjust, ⌥ for 1 ms`}
                      use:resizeHandle={{
                        value: ev.start_time,
                        min: 0,
                        max: display?.duration ?? 0,
                        onKey: (e) => onResizeKey(e, ev, "start"),
                        onDown: (e) => beginResize(e, ev, "start"),
                        onMove: onGestureMove,
                        onUp: endGesture,
                      }}
                    ></span>
                    <span
                      class="handle h-r"
                      aria-label={`Resize ${ev.name} end — arrow keys to adjust, ⌥ for 1 ms`}
                      use:resizeHandle={{
                        value: ev.start_time + ev.duration,
                        min: 0,
                        max: display?.duration ?? 0,
                        onKey: (e) => onResizeKey(e, ev, "end"),
                        onDown: (e) => beginResize(e, ev, "end"),
                        onMove: onGestureMove,
                        onUp: endGesture,
                      }}
                    ></span>
                  {/if}
                </div>
              {/each}
            </div>
          </div>
        {/each}
      {/if}
    </div>
  </div>

  <div class="inspector">
    {#if selectedEvent}
      <div class="field">
        <span class="flabel">Event</span>
        {#if renaming}
          <input
            class="rename-input"
            bind:value={renameValue}
            aria-label="Event name"
            onkeydown={(e) => {
              if (e.key === "Enter") {
                e.stopPropagation();
                applyRename();
              } else if (e.key === "Escape") {
                e.stopPropagation();
                renaming = false;
              }
            }}
          />
        {:else}
          <span class="fval name">{selectedEvent.name}</span>
        {/if}
        {#if canEdit}
          <button
            type="button"
            class="mini"
            disabled={selectedEvent.source_duration_s != null}
            onclick={startRename}
          >
            <Tooltip
              text={selectedEvent.source_duration_s != null
                ? "Frozen content — the commit refuses renames (a different generation); Regenerate re-rolls the audio instead"
                : "Rename — free to commit; the next generation re-routes and re-prompts by the new name"}
            >
              <span>rename</span>
            </Tooltip>
          </button>
        {/if}
      </div>
      <div class="field"><span class="flabel">Index</span><span class="fval num">{selectedEvent.index}</span></div>
      <div class="field"><span class="flabel">Start</span><span class="fval num">{selectedEvent.start_time.toFixed(3)}</span></div>
      <div class="field"><span class="flabel">Span</span><span class="fval num">{selectedEvent.duration.toFixed(3)}</span></div>
      <div class="field">
        <span class="flabel">Generated</span>
        <span class="fval num">{selectedEvent.source_duration_s == null ? "—" : selectedEvent.source_duration_s.toFixed(3)}</span>
      </div>
      <div class="field"><span class="flabel">Kind</span><span class="fval">{selectedEvent.kind}</span></div>
      <div class="field"><span class="flabel">Intensity</span><span class="fval">{selectedEvent.intensity}</span></div>
      {#if selectedEvent.kind === "rhythmic" && selectedEvent.seed != null}
        <div class="field"><span class="flabel">Mix seed</span><span class="fval num">{selectedEvent.seed}</span></div>
      {/if}
      {#if canEdit}
        <span class="cost-tag">move · resize free</span>
        <button
          type="button"
          class="mini danger"
          onclick={() => {
            if (selected == null || !onEdit) return;
            const idx = selected;
            onEdit((d) => deleteEvent(d, idx));
            select(-1);
            onselect?.(null);
          }}
        >
          <Tooltip text="Delete this event — free: the money for generated content is already spent">
            <span>delete</span>
          </Tooltip>
        </button>
      {/if}
      {#if inspectorActions}
        <span class="insp-actions">
          {@render inspectorActions(selectedEvent)}
        </span>
      {/if}
    {:else}
      <p class="empty">No event selected — click a block.</p>
    {/if}
  </div>
</section>

<style>
  .timeline {
    background: var(--surface);
    border-top: 1px solid var(--line);
    display: flex;
    flex-direction: column;
    position: relative;
    min-height: 0;
  }

  .grip {
    position: absolute;
    top: -3px;
    left: 0;
    right: 0;
    height: 7px;
    cursor: row-resize;
    z-index: 5;
    border: 0;
    padding: 0;
    background: transparent;
    display: grid;
    place-items: center;
  }

  .grip::after {
    content: "";
    width: 44px;
    height: 3px;
    border-radius: 2px;
    background: var(--line);
  }

  .grip:hover::after,
  .grip:focus-visible::after {
    background: var(--accent);
  }

  .head {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 7px 14px;
    border-bottom: 1px solid var(--line-soft);
    flex-wrap: wrap;
  }

  .title {
    font-size: var(--text-12);
    font-weight: var(--w-590);
  }

  .vers {
    display: flex;
    align-items: center;
    gap: 4px;
  }

  .vchip {
    font-family: var(--font-mono);
    font-variant-numeric: tabular-nums;
    font-size: 10.5px;
    padding: 2px 8px;
    border-radius: 999px;
    border: 1px solid var(--line);
    background: var(--raised);
    color: var(--dim);
    cursor: pointer;
  }

  .vchip:hover {
    border-color: var(--faint);
  }

  .vchip.pressed {
    color: var(--text);
    border-color: var(--accent);
    background: var(--accent-q);
  }

  .vchip.draft {
    border-style: dashed;
    color: var(--warn);
  }

  .vchip.draft.pressed {
    border-color: var(--warn);
    background: var(--warn-q);
    color: var(--text);
  }

  .spacer {
    flex: 1;
  }

  .note {
    font-size: 10px;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    color: var(--faint);
  }

  .note.lock {
    color: var(--warn);
  }

  .note.historic {
    color: var(--warn);
  }

  .note.proposal {
    color: var(--accent);
  }

  .note.snap,
  .note.free {
    text-transform: none;
    letter-spacing: 0.01em;
  }

  .note.free {
    color: var(--ok);
  }

  .head-btn {
    font: inherit;
    font-size: var(--text-11);
    font-weight: var(--w-560);
    padding: 4px 10px;
    border-radius: var(--r);
    border: 1px solid var(--line);
    background: var(--raised);
    color: var(--dim);
    cursor: pointer;
  }

  .head-btn:hover:not(:disabled) {
    border-color: var(--faint);
    color: var(--text);
  }

  .head-btn:disabled {
    opacity: 0.55;
    cursor: not-allowed;
  }

  .head-btn.primary {
    background: var(--accent);
    border-color: var(--accent);
    color: var(--surface);
  }

  .head-btn.ghost {
    background: transparent;
  }

  .headline {
    display: flex;
    align-items: baseline;
    gap: 8px;
    padding: 4px 14px;
    font-size: var(--text-11);
    border-bottom: 1px solid var(--line-soft);
  }

  .headline.err {
    background: color-mix(in srgb, var(--stop) 8%, var(--surface));
  }

  .headline.err .word {
    font-weight: var(--w-590);
    color: var(--stop);
    flex: 0 0 auto;
  }

  .headline.proposal {
    background: var(--accent-q);
  }

  .headline.proposal .word {
    font-weight: var(--w-590);
    color: var(--accent);
    flex: 0 0 auto;
  }

  .headline.note .detail {
    color: var(--dim);
  }

  .headline .detail {
    min-width: 0;
    line-height: 1.4;
  }

  .insp-actions {
    margin-left: auto;
    display: inline-flex;
    align-items: center;
    gap: 6px;
  }

  .wrap {
    overflow: auto;
    flex: 1 1 auto;
    min-height: 0;
  }

  .grid {
    min-width: 480px;
    padding-bottom: 6px;
  }

  .ruler {
    position: relative;
    height: 22px;
    margin-left: 116px;
  }

  .tick {
    position: absolute;
    top: 14px;
    width: 1px;
    height: 5px;
    background: var(--line);
  }

  .tick.major {
    top: 11px;
    height: 8px;
    background: var(--faint);
  }

  .transient {
    position: absolute;
    top: 2px;
    width: 1.5px;
    height: 7px;
    background: var(--accent);
  }

  .tlabel {
    position: absolute;
    top: 2px;
    transform: translateX(-50%);
    font-size: 9.5px;
    color: var(--faint);
  }

  .lane-row {
    display: flex;
    height: 44px;
    border-top: 1px solid var(--line-soft);
  }

  .tname {
    width: 116px;
    flex: 0 0 auto;
    display: flex;
    flex-direction: column;
    justify-content: center;
    padding: 0 12px;
    gap: 2px;
    overflow: hidden;
  }

  .tname .n {
    font-size: var(--text-11);
    font-weight: var(--w-560);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .tname .k {
    font-size: 9.5px;
    color: var(--faint);
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }

  .lane {
    position: relative;
    flex: 1 1 auto;
    min-width: 0;
  }

  .block {
    position: absolute;
    top: 7px;
    bottom: 7px;
    border: 1px solid var(--line);
    border-radius: 5px;
    background: var(--sunken);
    overflow: hidden;
    padding: 0;
    cursor: pointer;
    display: flex;
    align-items: flex-end;
    user-select: none;
    touch-action: none;
  }

  .block:hover {
    border-color: var(--faint);
  }

  .block.selected {
    border-color: var(--accent);
    box-shadow: 0 0 0 1px var(--accent);
  }

  .block.draft {
    border-style: dashed;
    border-color: var(--accent-q);
  }

  .block.draft.selected {
    border-color: var(--accent);
  }

  .block.proposed {
    border-style: dashed;
    border-color: var(--accent);
    background: color-mix(in srgb, var(--accent-q) 55%, var(--sunken));
  }

  .ghost {
    position: absolute;
    top: 7px;
    bottom: 7px;
    border: 1px dashed var(--faint);
    border-radius: 5px;
    background: transparent;
    opacity: 0.55;
    pointer-events: none;
    box-shadow: none;
  }

  .block .bname {
    position: absolute;
    left: 4px;
    right: 4px;
    bottom: 2px;
    font-size: 8.5px;
    color: var(--text);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    text-align: left;
    pointer-events: none;
  }

  .handle {
    position: absolute;
    top: 0;
    bottom: 0;
    width: 7px;
    cursor: ew-resize;
    z-index: 2;
  }

  .handle.h-l {
    left: 0;
  }

  .handle.h-r {
    right: 0;
  }

  .handle::after {
    content: "";
    position: absolute;
    top: 50%;
    left: 2px;
    width: 3px;
    height: 16px;
    margin-top: -8px;
    border-radius: 2px;
    background: transparent;
    transition: background 0.08s;
  }

  @media (prefers-reduced-motion: reduce) {
    .handle::after {
      transition: none;
    }
  }

  .handle.h-r::after {
    left: auto;
    right: 2px;
  }

  .block:hover .handle::after,
  .handle:hover::after {
    background: var(--faint);
  }

  .block.selected .handle::after {
    background: var(--accent);
  }

  .empty {
    margin: 0;
    padding: 24px 14px;
    color: var(--faint);
    font-size: var(--text-12);
  }

  .inspector {
    display: flex;
    align-items: center;
    gap: 14px;
    padding: 7px 14px;
    border-top: 1px solid var(--line-soft);
    overflow-x: auto;
    white-space: nowrap;
  }

  .field {
    display: flex;
    align-items: baseline;
    gap: 6px;
  }

  .flabel {
    font-size: 10px;
    letter-spacing: 0.05em;
    text-transform: uppercase;
    color: var(--faint);
  }

  .fval {
    font-size: var(--text-12);
  }

  .fval.name {
    font-weight: var(--w-560);
  }

  .num {
    font-family: var(--font-mono);
    font-variant-numeric: tabular-nums;
  }

  .rename-input {
    font: inherit;
    font-size: var(--text-12);
    padding: 1px 5px;
    border: 1px solid var(--accent);
    border-radius: 4px;
    background: var(--raised);
    color: var(--text);
    width: 180px;
  }

  .mini {
    font: inherit;
    font-size: 10px;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    color: var(--dim);
    background: transparent;
    border: 1px solid var(--line);
    border-radius: 4px;
    padding: 1px 6px;
    cursor: pointer;
  }

  .mini:hover:not(:disabled) {
    color: var(--text);
    border-color: var(--faint);
  }

  .mini:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }

  .mini.danger {
    color: var(--stop);
  }

  .cost-tag {
    font-size: 10px;
    letter-spacing: 0.03em;
    color: var(--ok);
    border: 1px solid var(--ok-q);
    background: var(--ok-q);
    border-radius: 4px;
    padding: 2px 6px;
  }

  .inspector .empty {
    padding: 0;
    color: var(--faint);
    font-size: var(--text-11);
  }
</style>
