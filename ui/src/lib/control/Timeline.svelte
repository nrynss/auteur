<script lang="ts">
  /** Read-only timeline (unit 7.8): ruler, kind-grouped lanes with event
   * blocks (structural envelopes), and an inspector for the selected event.
   * Editing is unit 7.10; nothing here mutates. The frame is resizable via
   * the real separator grip (control-room.md §5). */
  import EnvelopeCanvas from "./EnvelopeCanvas.svelte";
  import { groupLanes, placeEvent } from "./helpers";
  import { timelineGrip } from "./timeline-grip";
  import { transientOffset } from "./preview";
  import type { TimelineEventOut } from "../api/projects";

  interface Props {
    timeline: {
      version: number;
      duration: number;
      ambience: string;
      events: TimelineEventOut[];
    } | null;
  }

  let { timeline }: Props = $props();

  const PPS = 64; // px per second — the ruler's working density

  let selected = $state<number | null>(null);
  let height = $state(236);
  const MIN_H = 148;

  const lanes = $derived(timeline ? groupLanes(timeline.events) : []);
  const widthPx = $derived((timeline?.duration ?? 0) * PPS);
  const ticks = $derived(
    timeline
      ? Array.from({ length: Math.ceil(timeline.duration) + 1 }, (_, i) => i)
      : [],
  );
  const selectedEvent = $derived(
    selected == null || !timeline ? null : (timeline.events.find((e) => e.index === selected) ?? null),
  );

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
</script>

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
    {#if timeline}
      <span class="ver num">v{timeline.version}</span>
    {/if}
    <span class="spacer"></span>
    <span class="note">read-only — edits land in 7.10</span>
  </div>

  <div class="wrap">
    <div class="grid" style={`width:${Math.max(widthPx, 480)}px`}>
      <div class="ruler" style={`width:${Math.max(widthPx, 480)}px`}>
        {#if timeline}
          {#each ticks as i}
            {@const major = i % 3 === 0}
            {@const left = ((i / timeline.duration) * 100).toFixed(3)}
            <i class="tick" class:major={major} style={`left:${left}%`}></i>
            {#if major && i < timeline.duration}
              <span class="tlabel num" style={`left:${left}%`}>{i}s</span>
            {/if}
          {/each}
          <!-- Hit points at the TRANSIENT, not start_time (control-room.md §7,
               unit 7.9): discrete events are placed at start + 50 ms bias −
               onset in the mix; onset is per-clip and not exposed, so the
               marker is bias-adjusted only and says so. -->
          {#each lanes.flatMap((l) => l.events) as ev (ev.index)}
            {#if ev.kind === "discrete"}
              {@const t = (transientOffset(ev) / timeline.duration) * 100}
              <i
                class="transient"
                style={`left:${t.toFixed(3)}%`}
                title="transient — start + 50 ms bias (onset trim not exposed)"
              ></i>
            {/if}
          {/each}
        {/if}
      </div>

      {#if lanes.length === 0}
        <div class="empty">
          <p>No timeline yet — analysis has not run.</p>
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
                <button
                  class="block"
                  class:selected={selected === ev.index}
                  style={`left:${p.leftPx}px;width:${p.widthPx}px`}
                  aria-label={`${ev.name} — index ${ev.index}, starts at ${ev.start_time.toFixed(3)}s`}
                  aria-pressed={selected === ev.index}
                  onclick={() => (selected = ev.index)}
                >
                  <EnvelopeCanvas kind={ev.kind} seed={ev.index} />
                  <span class="bname">{ev.name}</span>
                </button>
              {/each}
            </div>
          </div>
        {/each}
      {/if}
    </div>
  </div>

  <div class="inspector">
    {#if selectedEvent}
      <div class="field"><span class="flabel">Event</span><span class="fval name">{selectedEvent.name}</span></div>
      <div class="field"><span class="flabel">Index</span><span class="fval num">{selectedEvent.index}</span></div>
      <div class="field"><span class="flabel">Start</span><span class="fval num">{selectedEvent.start_time.toFixed(3)}</span></div>
      <div class="field"><span class="flabel">Span</span><span class="fval num">{selectedEvent.duration.toFixed(3)}</span></div>
      <div class="field">
        <span class="flabel">Generated</span>
        <span class="fval num">{selectedEvent.source_duration_s == null ? "—" : selectedEvent.source_duration_s.toFixed(3)}</span>
      </div>
      <div class="field"><span class="flabel">Kind</span><span class="fval">{selectedEvent.kind}</span></div>
      <div class="field"><span class="flabel">Intensity</span><span class="fval">{selectedEvent.intensity}</span></div>
      {#if selectedEvent.seed != null}
        <div class="field"><span class="flabel">Mix seed</span><span class="fval num">{selectedEvent.seed}</span></div>
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
  }

  .title {
    font-size: var(--text-12);
    font-weight: var(--w-590);
  }

  .ver {
    color: var(--faint);
    font-size: var(--text-11);
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
  }

  .block:hover {
    border-color: var(--faint);
  }

  .block.selected {
    border-color: var(--accent);
    box-shadow: 0 0 0 1px var(--accent);
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

  .inspector .empty {
    padding: 0;
    color: var(--faint);
    font-size: var(--text-11);
  }
</style>
