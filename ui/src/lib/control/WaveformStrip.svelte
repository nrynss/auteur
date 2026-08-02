<script lang="ts">
  /** Real peak envelope from unit 7.5 (the mix's measured waveform), drawn
   * on canvas. Repaints on theme change and resize. This is measured data,
   * never synthesised. */
  import type { PeaksResponse } from "../api/projects";

  interface Props {
    peaks: PeaksResponse;
    height?: number;
  }

  let { peaks, height = 26 }: Props = $props();
  let canvas = $state<HTMLCanvasElement | null>(null);

  function paint() {
    const el = canvas;
    if (!el) return;
    const dpr = window.devicePixelRatio || 1;
    const w = el.clientWidth;
    el.width = Math.max(1, Math.floor(w * dpr));
    el.height = Math.floor(height * dpr);
    const ctx = el.getContext("2d");
    if (!ctx) return;
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, w, height);

    const { min, max } = peaks;
    const n = Math.max(min.length, max.length);
    if (n === 0) return;
    const wave = getComputedStyle(document.documentElement)
      .getPropertyValue("--wave")
      .trim();
    ctx.fillStyle = wave;
    const mid = height / 2;
    for (let x = 0; x < w; x += 1) {
      const i = Math.floor((x / w) * n);
      const lo = (min[i] ?? 0) * mid;
      const hi = (max[i] ?? 0) * mid;
      ctx.fillRect(x, mid - hi, 1, Math.max(1, hi - lo));
    }
  }

  $effect(() => {
    paint();
    const onResize = () => paint();
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  });
</script>

<canvas bind:this={canvas} style={`width:100%;height:${height}px`} aria-hidden="true"></canvas>
