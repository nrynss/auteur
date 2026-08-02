<script lang="ts">
  /** Dev-only showcase of the 7.7 primitives — rendered only under
   * import.meta.env.DEV, never shipped. Values are honest samples from the
   * real standoff_kling project (docs/design/control-room-mockup.html), so
   * what it shows is the shape of real data, not invented numbers. */
  import Button from "./components/Button.svelte";
  import Chip from "./components/Chip.svelte";
  import ConfirmDialog from "./components/ConfirmDialog.svelte";
  import Evidence from "./components/Evidence.svelte";
  import Kbd from "./components/Kbd.svelte";
  import Meter from "./components/Meter.svelte";
  import Panel from "./components/Panel.svelte";
  import SceneThumb from "./components/SceneThumb.svelte";
  import StageRow from "./components/StageRow.svelte";
  import Timecode from "./components/Timecode.svelte";

  let dialogOpen = $state(false);
  let busy = $state(false);
  let confirmCount = $state(0);
  const confirm = () => {
    busy = true;
    setTimeout(() => {
      busy = false;
      dialogOpen = false;
      confirmCount += 1;
    }, 600);
  };
</script>

<div class="gallery">
  <h1 class="eyebrow">Primitives — dev only</h1>

  <section>
    <h2 class="eyebrow">Chips</h2>
    <div class="row">
      <Chip state="ok" />
      <Chip state="warn" detail="7.4 LU under target" />
      <Chip state="blocked" detail="worker unreachable" />
      <Chip state="pending" />
      <Chip state="ok" label="Running" live />
    </div>
  </section>

  <section>
    <h2 class="eyebrow">Evidence — value + required source</h2>
    <Evidence
      label="Integrated loudness"
      value="−21.4 LUFS"
      source="runs/run_01KZ0JYN9M6Y4Q2WQTV3X8ZHD0/mix.wav"
    />
    <div style="height:9px"></div>
    <Evidence
      label="True peak"
      value="−2.1 dBTP"
      warn="7.4 LU under target"
      source="runs/run_01KZ0JYN9M6Y4Q2WQTV3X8ZHD0/mix.wav"
    />
  </section>

  <section>
    <h2 class="eyebrow">Timecodes &amp; numbers</h2>
    <div class="row">
      <Timecode seconds={10.62} />
      <Timecode seconds={15.04} />
      <Timecode seconds={3661.25} />
      <Timecode seconds={null} />
      <span class="num">$0.30</span>
    </div>
  </section>

  <section>
    <h2 class="eyebrow">Scene thumbs</h2>
    <div class="row">
      <SceneThumb index={0} src={null} alt="scene 1" start={0} end={3.2} current />
      <SceneThumb index={3} src={null} alt="scene 4" start={8.4} end={10.62} />
      <SceneThumb index={4} src={null} alt="scene 5" start={10.62} end={15.04} />
    </div>
  </section>

  <section>
    <h2 class="eyebrow">Stage rows (live run)</h2>
    <Panel>
      <StageRow name="scene detection" status="succeeded" elapsed_s={4.1} provider="local" />
      <StageRow name="timeline analysis" status="succeeded" elapsed_s={12.6} retries={1} provider="gemini-2.5-flash" />
      <StageRow name="stem generation" status="running" elapsed_s={23.9} provider="openmoss/moss-soundeffect-v2" />
      <StageRow name="mix" status="pending" />
    </Panel>
  </section>

  <section>
    <h2 class="eyebrow">Meter &amp; buttons</h2>
    <div class="row">
      <Meter percent={67} />
      <Button variant="secondary">Secondary</Button>
      <Button variant="ghost">Ghost</Button>
      <Button variant="primary">Render final</Button>
      <Button variant="danger">Regenerate <span class="num">$0.30</span></Button>
      <Button pending>Working…</Button>
    </div>
  </section>

  <section>
    <h2 class="eyebrow">Keyboard hint</h2>
    <div class="row">
      <span class="hint">press <Kbd>K</Kbd> to play, <Kbd>Shift</Kbd>+<Kbd>←</Kbd> to nudge</span>
    </div>
  </section>

  <section>
    <h2 class="eyebrow">Cost confirmation dialog</h2>
    <div class="row">
      <Button variant="primary" onclick={() => (dialogOpen = true)}>
        Open confirm ({confirmCount} confirmed)
      </Button>
    </div>
    <ConfirmDialog
      open={dialogOpen}
      title="Regenerate stem 4"
      body="Re-runs the generation for “leather holster creaking” at a new variant. The new take replaces the current one; the old take stays in the run history."
      amount="$0.30"
      confirmLabel="Regenerate"
      danger
      busy={busy}
      onconfirm={confirm}
      oncancel={() => (dialogOpen = false)}
    />
  </section>
</div>

<style>
  .gallery {
    display: grid;
    gap: 22px;
    padding: 18px 20px 40px;
    max-width: 860px;
    margin: 0 auto;
  }

  .gallery section {
    display: grid;
    gap: 8px;
  }

  .gallery h2 {
    margin: 0;
  }

  .row {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 8px;
  }

  .hint {
    font-size: var(--text-12);
    color: var(--dim);
  }
</style>
