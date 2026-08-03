/** Browser preview audio engine (unit 7.9). Decodes stems once (cached by
 * key) and schedules them against the video's playhead via Web Audio.
 *
 * Honesty rules, structurally:
 * - Stems come from the same-origin preview route (§3.13), never a guessed
 *   address — the key is the event's stamped stem_key.
 * - The mix's real processing (rms_normalize, ducking, true-peak) is NOT
 *   replicated; this is a labelled approximation, and QA numbers only ever
 *   come from the server's measured mix.
 * - A stem that fails to load is skipped and counted, never substituted. */

import type { PreviewItem } from "./preview";

const PREVIEW_GAIN = 0.8; // flat; the server's per-intensity gains are not replicated

export class PreviewEngine {
  private ctx: AudioContext | null = null;
  private buffers = new Map<string, AudioBuffer>();
  private sources: AudioBufferSourceNode[] = [];
  private items: PreviewItem[];
  private video: HTMLVideoElement;
  private projectId: string;
  private onFail: (key: string) => void;
  private loaded = new Set<string>();

  constructor(
    video: HTMLVideoElement,
    projectId: string,
    items: PreviewItem[],
    onFail: (key: string) => void,
  ) {
    this.video = video;
    this.projectId = projectId;
    this.items = items;
    this.onFail = onFail;
  }

  /** Swap the schedule in place (unit 7.10: the draft moved an event). The
   * decoded-stem cache is keyed by stem key and survives — a nudge must not
   * re-download audio, it must translate. The caller re-schedules after. */
  setItems(items: PreviewItem[]): void {
    this.items = items;
  }

  private ensureCtx(): AudioContext {
    if (!this.ctx) {
      this.ctx = new AudioContext();
    }
    if (this.ctx.state === "suspended") {
      void this.ctx.resume();
    }
    return this.ctx;
  }

  /** Fetch + decode every stem, cached by key. Failures are reported per key
   * and do not stop the rest. */
  async loadAll(): Promise<void> {
    const tasks = this.items
      .filter((it) => !this.loaded.has(it.key))
      .map(async (it) => {
        try {
          const r = await fetch(
            `/api/v1/projects/${this.projectId}/preview/stems/${it.key}`,
          );
          if (!r.ok) throw new Error(`HTTP ${r.status}`);
          const data = await r.arrayBuffer();
          const buf = await this.ensureCtx().decodeAudioData(data);
          this.buffers.set(it.key, buf);
          this.loaded.add(it.key);
        } catch (e) {
          this.onFail(it.key);
        }
      });
    await Promise.all(tasks);
  }

  /** Schedule every stem whose window is still ahead of the playhead.
   * Call on the video's play event; stop() on pause/seek.
   *
   * A stem that straddles the playhead starts at the offset INTO its buffer
   * (start(when, into)), never from its beginning — a 15 s bed resumed at
   * t=10 must continue at its 10 s mark, not restart and run past the end
   * of the video. The release ramp uses the REMAINING duration for the same
   * reason: shaping past the source's actual end would never fire and the
   * stem would click. */
  schedule(): void {
    const ctx = this.ensureCtx();
    const videoTime = this.video.currentTime;
    for (const item of this.items) {
      const buf = this.buffers.get(item.key);
      if (!buf) continue;
      // How far the playhead is INTO this stem's window; negative = ahead.
      const behind = videoTime - item.offset;
      if (behind >= buf.duration) continue; // fully in the past
      const into = Math.max(0, behind); // offset into the buffer
      const remaining = buf.duration - into;
      const when = ctx.currentTime + Math.max(0, -behind);

      const src = ctx.createBufferSource();
      src.buffer = buf;
      const gain = ctx.createGain();
      // 8 ms attack/release so stems do not click; nothing else is shaped —
      // the server's envelopes are not replicated.
      gain.gain.setValueAtTime(0, when);
      gain.gain.linearRampToValueAtTime(
        PREVIEW_GAIN,
        when + Math.min(0.008, remaining / 2),
      );
      if (remaining > 0.016) {
        gain.gain.setValueAtTime(PREVIEW_GAIN, when + remaining - 0.008);
        gain.gain.linearRampToValueAtTime(0, when + remaining);
      }

      src.connect(gain).connect(ctx.destination);
      src.start(when, into);
      this.sources.push(src);
    }
  }

  /** Stop everything and release the context. Call on unmount: browsers cap
   * concurrent AudioContexts (Chrome historically ~6), so navigating between
   * projects must not leak one per visit. */
  dispose(): void {
    this.stop();
    if (this.ctx) {
      void this.ctx.close();
      this.ctx = null;
    }
  }

  stop(): void {
    for (const s of this.sources) {
      try {
        s.stop();
      } catch {
        // already stopped
      }
    }
    this.sources = [];
  }
}
