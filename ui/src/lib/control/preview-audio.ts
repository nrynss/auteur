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
   * Call on the video's play event; stop() on pause/seek. */
  schedule(): void {
    const ctx = this.ensureCtx();
    const videoTime = this.video.currentTime;
    for (const item of this.items) {
      const buf = this.buffers.get(item.key);
      if (!buf) continue;
      if (item.offset + buf.duration < videoTime) continue; // already passed
      const when = ctx.currentTime + Math.max(0, item.offset - videoTime);

      const src = ctx.createBufferSource();
      src.buffer = buf;
      const gain = ctx.createGain();
      // 8 ms attack/release so stems do not click; nothing else is shaped —
      // the server's envelopes are not replicated.
      gain.gain.setValueAtTime(0, when);
      gain.gain.linearRampToValueAtTime(PREVIEW_GAIN, when + 0.008);
      gain.gain.setValueAtTime(PREVIEW_GAIN, when + buf.duration - 0.008);
      gain.gain.linearRampToValueAtTime(0, when + buf.duration);

      src.connect(gain).connect(ctx.destination);
      src.start(when);
      this.sources.push(src);
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
