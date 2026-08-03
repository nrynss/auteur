<script lang="ts">
  /** A/B player + browser preview (unit 7.9). One transport, two sources
   * (Original / Foley mix) with the playhead preserved across toggles, and a
   * preview mode that schedules the real stems over the original video via
   * Web Audio. The preview is a labelled approximation: rms_normalize,
   * ducking and true-peak are the server's, not recreated here — QA numbers
   * only ever come from the measured mix (7.8's EvidencePanel). */
  import { onDestroy } from "svelte";

  import Icon from "../components/Icon.svelte";
  import { PreviewEngine } from "./preview-audio";
  import { buildSchedule } from "./preview";
  import { formatTimecode } from "../format";
  import type { TimelineEventOut } from "../api/projects";

  interface Props {
    id: string;
    sourceUrl: string | null;
    mixUrl: string | null;
    events: TimelineEventOut[];
  }

  let { id, sourceUrl, mixUrl, events }: Props = $props();

  type Source = "original" | "mix" | "preview";
  let active = $state<Source>("original");
  let playing = $state(false);
  let currentTime = $state(0);
  let duration = $state(0);
  let previewFailed = $state(0);

  const previewItems = $derived(buildSchedule(events));
  // A project without a source video cannot show Original as the initial
  // view; fall back to the mix without lying about which is pressed.
  const shownSource = $derived(
    active === "original" && !sourceUrl ? "mix" : active,
  );

  let origEl: HTMLVideoElement | undefined = $state();
  let mixEl: HTMLVideoElement | undefined = $state();
  let engine: PreviewEngine | null = null;

  const activeVideo = $derived(
    shownSource === "mix" ? mixEl : origEl,
  );
  const isPreview = $derived(active === "preview");

  function otherVideo(): HTMLVideoElement | undefined {
    return active === "mix" ? origEl : mixEl;
  }

  // -- transport -----------------------------------------------------------
  function togglePlay() {
    const el = activeVideo;
    if (!el) return;
    if (el.paused) void el.play();
    else el.pause();
  }

  function seekBy(delta: number) {
    const el = activeVideo;
    if (!el) return;
    el.currentTime = Math.max(0, Math.min(el.currentTime + delta, duration));
  }

  function onSeek(e: Event) {
    const el = activeVideo;
    if (!el) return;
    el.currentTime = Number((e.target as HTMLInputElement).value);
  }

  function onTimeUpdate(el: HTMLVideoElement) {
    currentTime = el.currentTime;
    if (el.duration && Number.isFinite(el.duration)) duration = el.duration;
  }

  // -- A/B switching, preserving the playhead -------------------------------
  function switchTo(next: Source) {
    if (next === active) return;
    const t = activeVideo ? activeVideo.currentTime : currentTime;
    const wasPlaying = playing;
    const from = activeVideo;
    if (from) {
      from.pause();
    }
    active = next;
    if (next === "preview") {
      void startPreview();
    } else {
      stopPreview();
    }
    const to = activeVideo;
    if (to) {
      const seek = () => {
        try {
          to.currentTime = t;
        } catch {
          // not ready; ignore
        }
      };
      if (to.readyState >= 1) seek();
      else to.addEventListener("loadedmetadata", seek, { once: true });
      if (wasPlaying) void to.play();
    }
  }

  // -- preview -------------------------------------------------------------
  async function startPreview() {
    const el = origEl;
    if (!el) return;
    if (!engine) {
      engine = new PreviewEngine(
        el,
        id,
        previewItems,
        () => (previewFailed += 1),
      );
      // Attached once, on engine creation: toggling preview off and on must
      // not stack duplicate play/pause listeners (overlapping schedules).
      el.addEventListener("play", () => engine?.schedule());
      el.addEventListener("pause", () => engine?.stop());
      el.addEventListener("seeking", () => engine?.stop());
      el.addEventListener("seeked", () => {
        if (!el.paused) engine?.schedule();
      });
      await engine.loadAll();
      if (!el.paused) engine.schedule();
    }
  }

  function stopPreview() {
    engine?.stop();
  }

  // -- keyboard ------------------------------------------------------------
  // On the window (not a markup listener): the player must react to K/Space
  // and arrow keys even when focus is in the rail, and svelte's a11y rules
  // would flag a section with listeners. Typing contexts are skipped, so the
  // future director composer (an input) is unaffected.
  $effect(() => {
    const onKeydown = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" ||
                t.isContentEditable)) {
        return;
      }
      if (e.key === "k" || e.key === "K" || e.key === " ") {
        e.preventDefault();
        togglePlay();
      } else if (e.key === "ArrowRight") {
        e.preventDefault();
        seekBy(5);
      } else if (e.key === "ArrowLeft") {
        e.preventDefault();
        seekBy(-5);
      }
    };
    window.addEventListener("keydown", onKeydown);
    return () => window.removeEventListener("keydown", onKeydown);
  });

  // keep `playing` in sync with whatever video is live
  $effect(() => {
    const el = activeVideo;
    if (!el) return;
    const onPlay = () => (playing = true);
    const onPause = () => (playing = false);
    el.addEventListener("play", onPlay);
    el.addEventListener("pause", onPause);
    return () => {
      el.removeEventListener("play", onPlay);
      el.removeEventListener("pause", onPause);
    };
  });

  // The preview follows the draft (unit 7.10): moving an event in the
  // timeline re-schedules the real stems at the new offsets instantly, free,
  // no server round trip. The engine's buffer cache is keyed by stem key, so
  // this swaps the schedule without re-downloading audio. Mid-playback the
  // stems restart at the current playhead so the move is heard, not deferred.
  $effect(() => {
    const items = previewItems;
    if (active !== "preview" || !engine) return;
    engine.setItems(items);
    if (origEl && !origEl.paused) {
      engine.stop();
      engine.schedule();
    }
  });

  onDestroy(() => {
    engine?.dispose();
  });
</script>

<section class="player" aria-label="Player">
  <div class="frame">
    {#if sourceUrl}
      <video
        bind:this={origEl}
        src={sourceUrl}
        playsinline
        preload="auto"
        aria-label="Original source video"
        class:off={shownSource !== "original" && shownSource !== "preview"}
        ontimeupdate={(e) => onTimeUpdate(e.currentTarget)}
      >
        <!-- The director's own footage: no captions exist, and the track
             declares that state rather than the player guessing. -->
        <track kind="captions" label="None" />
      </video>
    {/if}
    {#if mixUrl}
      <video
        bind:this={mixEl}
        src={mixUrl}
        playsinline
        preload="auto"
        aria-label="Foley mix video"
        class:off={shownSource !== "mix"}
        ontimeupdate={(e) => onTimeUpdate(e.currentTarget)}
      >
        <track kind="captions" label="None" />
      </video>
    {/if}
  </div>

  <div class="transport">
    <button class="play" aria-label={playing ? "Pause" : "Play"} onclick={togglePlay}>
      <Icon name={playing ? "pause" : "play"} size={11} />
    </button>
    <div class="tc">
      <div class="tcv num">{formatTimecode(currentTime)}</div>
      <div class="tc-sub">/ {formatTimecode(duration)}</div>
    </div>
    <input
      class="scrub"
      type="range"
      min="0"
      max={duration || 0}
      step="0.001"
      value={currentTime}
      aria-label="Seek"
      oninput={onSeek}
    />
    <div class="seg" role="group" aria-label="Compare audio">
      <button
        class:on={shownSource === "original"}
        aria-pressed={shownSource === "original"}
        disabled={!sourceUrl}
        onclick={() => switchTo("original")}
      >
        Original
      </button>
      <button
        class:on={shownSource === "mix"}
        aria-pressed={shownSource === "mix"}
        disabled={!mixUrl}
        onclick={() => switchTo("mix")}
      >
        Foley mix
      </button>
      <button
        class:on={shownSource === "preview"}
        aria-pressed={shownSource === "preview"}
        disabled={previewItems.length === 0 || !sourceUrl}
        onclick={() => switchTo("preview")}
      >
        Preview
      </button>
    </div>
    {#if isPreview}
      <span class="preview-note" role="status">
        <span class="dot" aria-hidden="true"></span>
        Browser preview — not the measured mix
        {#if previewFailed > 0}
          · {previewFailed} stem{previewFailed === 1 ? "" : "s"} unavailable
        {/if}
      </span>
    {/if}
    <span class="spacer"></span>
    <span class="hint">K play · ← → seek</span>
  </div>
</section>

<style>
  .player {
    flex: 1 1 auto;
    min-height: 0;
    display: flex;
    flex-direction: column;
  }

  .frame {
    flex: 1 1 auto;
    min-height: 0;
    background: var(--sunken);
    display: grid;
    place-items: center;
    padding: 16px;
    position: relative;
  }

  video {
    position: absolute;
    height: 100%;
    aspect-ratio: 16/9;
    max-width: 100%;
    border-radius: var(--r);
    overflow: hidden;
    box-shadow: var(--shadow-frame);
    background: #05080a;
    object-fit: contain;
  }

  video.off {
    visibility: hidden;
    pointer-events: none;
  }

  .transport {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 8px 14px;
    background: var(--surface);
    border-top: 1px solid var(--line);
  }

  .play {
    width: 30px;
    height: 30px;
    border-radius: 50%;
    display: grid;
    place-items: center;
    padding: 0;
    background: var(--raised);
    border: 1px solid var(--line);
    color: var(--text);
    cursor: pointer;
  }

  .tc {
    display: flex;
    align-items: baseline;
    gap: 6px;
  }

  .tcv {
    font: 500 13px/1 var(--font-mono);
    font-variant-numeric: tabular-nums;
    letter-spacing: -0.01em;
  }

  .tc-sub {
    font-size: 10px;
    color: var(--faint);
    letter-spacing: 0.04em;
    text-transform: uppercase;
  }

  .scrub {
    flex: 1 1 auto;
    min-width: 60px;
    accent-color: var(--accent);
  }

  .seg {
    display: inline-flex;
    background: var(--sunken);
    border-radius: var(--r-sm);
    padding: 2px;
    gap: 2px;
    border: 1px solid var(--line-soft);
  }

  .seg button {
    border: 0;
    background: transparent;
    border-radius: 4px;
    padding: 3px 11px;
    color: var(--dim);
    font-size: var(--text-12);
    font-weight: var(--w-500);
    cursor: pointer;
  }

  .seg button.on {
    background: var(--surface);
    color: var(--text);
    box-shadow: 0 1px 2px rgba(0, 0, 0, 0.14);
  }

  .seg button:disabled {
    opacity: 0.4;
    cursor: not-allowed;
  }

  .preview-note {
    font-size: 11px;
    color: var(--faint);
    display: inline-flex;
    align-items: center;
    gap: 5px;
    white-space: nowrap;
  }

  .preview-note .dot {
    width: 5px;
    height: 5px;
    border-radius: 50%;
    background: var(--warn);
  }

  .spacer {
    flex: 1;
  }

  .hint {
    font-size: 10px;
    color: var(--faint);
    white-space: nowrap;
  }

  .num {
    font-family: var(--font-mono);
    font-variant-numeric: tabular-nums;
  }
</style>
