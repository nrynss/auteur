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
    const onTheme = () => paint();
    window.addEventListener("resize", onResize);
    window.addEventListener("ff:theme-change", onTheme);
    // A canvas mounted inside a hidden tab (the History panel is rendered
    // hidden at load) paints at clientWidth 0 and never re-paints when the
    // tab opens — opening a tab is not a resize. Re-paint whenever the
    // element becomes visible, so the measured peaks are there when the
    // director looks.
    let io: IntersectionObserver | null = null;
    if (typeof IntersectionObserver !== "undefined" && canvas) {
      io = new IntersectionObserver(
        (entries) => {
          if (entries.some((e) => e.isIntersecting)) paint();
        },
        { threshold: 0.01 },
      );
      io.observe(canvas);
    }
    return () => {
      window.removeEventListener("resize", onResize);
      window.removeEventListener("ff:theme-change", onTheme);
      io?.disconnect();
    };
  });
</script>

<canvas bind:this={canvas} style={`width:100%;height:${height}px`} aria-hidden="true"></canvas>
