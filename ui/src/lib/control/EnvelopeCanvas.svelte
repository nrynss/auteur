<script lang="ts">
  /** Envelope for a timeline event block — STRUCTURAL, not measured
   * (control-room.md §6: discrete one-shots get a transient-led decay, beds
   * and rhythmic events a sustained one; per-event peak data is not reachable
   * until the edit model lands in 7.10). Seeded from the event index so the
   * same timeline always draws the same shape — determinism, like the mix. */
  interface Props {
    kind: string;
    /** Deterministic seed — the event's index. */
    seed: number;
  }

  let { kind, seed }: Props = $props();
  let canvas = $state<HTMLCanvasElement | null>(null);

  function rng(t: number) {
    let s = (t >>> 0) || 1;
    return () => {
      s += 0x6d2b79f5;
      let x = Math.imul(s ^ (s >>> 15), 1 | s);
      x ^= x + Math.imul(x ^ (x >>> 7), 61 | x);
      return ((x ^ (x >>> 14)) >>> 0) / 4294967296;
    };
  }

  function paint() {
    const el = canvas;
    if (!el) return;
    const dpr = window.devicePixelRatio || 1;
    const w = el.clientWidth;
    const h = el.clientHeight;
    if (w < 2 || h < 2) return;
    el.width = Math.floor(w * dpr);
    el.height = Math.floor(h * dpr);
    const ctx = el.getContext("2d");
    if (!ctx) return;
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = getComputedStyle(document.documentElement)
      .getPropertyValue("--wave")
      .trim();

    const rand = rng(seed + 7);
    const mid = h / 2;
    const amp = h * 0.42;
    const step = Math.max(1, Math.floor(w / 40));
    for (let x = 0; x < w; x += step) {
      const t = x / w;
      let env: number;
      if (kind === "discrete") {
        env = Math.exp(-5.5 * t); // transient-led decay
      } else if (kind === "rhythmic") {
        const phase = (t * 4) % 1; // tiled loop bursts
        env = Math.exp(-8 * phase);
      } else {
        env = 0.55 + 0.45 * Math.sin(t * 9); // sustained bed
      }
      const v = Math.max(0.04, env) * amp * (0.75 + 0.5 * rand());
      ctx.fillRect(x, mid - v, step, Math.max(1, v * 2));
    }
  }

  $effect(() => {
    paint();
    const onResize = () => paint();
    const onTheme = () => paint();
    window.addEventListener("resize", onResize);
    window.addEventListener("ff:theme-change", onTheme);
    return () => {
      window.removeEventListener("resize", onResize);
      window.removeEventListener("ff:theme-change", onTheme);
    };
  });
</script>

<canvas bind:this={canvas} style="width:100%;height:100%;display:block" aria-hidden="true"></canvas>
