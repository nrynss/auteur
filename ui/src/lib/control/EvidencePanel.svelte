<script lang="ts">
  /** Evidence tab: loudness measurements against their targets (with a
   * display-scale bar) and visual continuity as comparisons. Every number
   * here is the server's measured value with its source — the browser never
   * approximates (that rule lands with 7.9's preview). */
  import ContinuityCard from "./ContinuityCard.svelte";
  import Evidence from "../components/Evidence.svelte";
  import { loudnessDelta } from "./helpers";
  import type { QaReportResponse, SceneOut } from "../api/projects";

  interface Props {
    qa: QaReportResponse | null;
    scenes: SceneOut[];
  }

  let { qa, scenes }: Props = $props();

  // Display scale for the bar, not a measured claim: LUFS over [-30, -10],
  // true peak over [-6, 0] dBTP. The numbers the screen shows are the real
  // values; the bar is a position on that scale.
  const toPct = (v: number, lo: number, hi: number) =>
    Math.max(0, Math.min(100, ((v - lo) / (hi - lo)) * 100));

  const loudness = $derived(qa?.audio ?? null);
  const integrated = $derived(loudness?.integrated_lufs ?? null);
  const peak = $derived(loudness?.true_peak_dbtp ?? null);
  const measuredRun = $derived(loudness?.measured_run_id ?? qa?.run_id ?? null);
</script>

<div class="panel-body">
  {#if !qa || !qa.audio.measured}
    <p class="empty">No measurements yet — the mix has not been QA-measured.</p>
  {:else}
    <p class="eyebrow">Loudness</p>

  {#if integrated != null}
    {@const loudWarn = loudnessDelta(integrated, loudness!.target_lufs) !== "at target"}
    <Evidence
      label="Integrated loudness"
      value={`${integrated.toFixed(1)} LUFS`}
      source={measuredRun ? `runs/${measuredRun}/mix.wav` : "mix.wav"}
      warn={loudWarn ? loudnessDelta(integrated, loudness!.target_lufs) : null}
    >
      <div class="bar" aria-hidden="true">
        <i class:warn={loudWarn} style={`width:${toPct(integrated, -30, -10)}%`}></i>
        <u style={`left:${toPct(loudness!.target_lufs, -30, -10)}%`}></u>
      </div>
    </Evidence>
      <p class="scale-note">
        bar scale −30…−10 LUFS · target {loudness!.target_lufs.toFixed(1)} marked
      </p>
    {/if}

  {#if peak != null}
    <div style="height:9px"></div>
    {@const peakWarn = peak > loudness!.target_true_peak_dbtp}
    <Evidence
      label="True peak"
      value={`${peak.toFixed(1)} dBTP`}
      source={measuredRun ? `runs/${measuredRun}/mix.wav` : "mix.wav"}
      warn={peakWarn ? `${(peak - loudness!.target_true_peak_dbtp).toFixed(1)} dB over target` : null}
    >
      <div class="bar" aria-hidden="true">
        <i class:warn={peakWarn} style={`width:${toPct(Math.min(peak, 0), -6, 0)}%`}></i>
        <u style={`left:${toPct(loudness!.target_true_peak_dbtp, -6, 0)}%`}></u>
      </div>
    </Evidence>
  {/if}
  {/if}

  {#if qa && (qa.visual.issues ?? []).length > 0}
    <p class="eyebrow">Visual continuity</p>
    <div class="cards">
      {#each qa.visual.issues ?? [] as issue (issue.code + (issue.scene_id ?? "") + (issue.compared_with ?? ""))}
        <ContinuityCard
          issue={issue}
          threshold={qa.visual.luma_delta_threshold}
          {scenes}
          runId={qa.run_id ?? null}
        />
      {/each}
    </div>
  {/if}
</div>

<style>
  .panel-body {
    display: flex;
    flex-direction: column;
  }

  .eyebrow {
    margin: 0 0 8px;
  }

  .eyebrow + .eyebrow {
    margin-top: 18px;
  }

  .empty {
    color: var(--faint);
    font-size: var(--text-12);
  }

  .cards {
    display: flex;
    flex-direction: column;
    gap: 9px;
  }

  .bar {
    height: 3px;
    background: var(--sunken);
    position: relative;
    margin: 0 11px;
  }

  .bar > i {
    position: absolute;
    inset: 0 auto 0 0;
    background: var(--ok);
  }

  .bar > i.warn {
    background: var(--warn);
  }

  .bar > u {
    position: absolute;
    top: -3px;
    bottom: -3px;
    width: 1px;
    background: var(--text);
    opacity: 0.5;
  }

  .scale-note {
    font-size: 10px;
    color: var(--faint);
    margin: 4px 11px 0;
  }
</style>
