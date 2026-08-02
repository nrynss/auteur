<script lang="ts">
  /** Timecode — mono, tabular numerics for any seconds value. The transport
   * rule (control-room.md §7) is a *caller* contract: this renders whatever
   * seconds it is given; screens pass the transient, not start_time. */
  import { formatTimecode } from "../format";

  interface Props {
    seconds: number | null | undefined;
    ms?: boolean;
    hours?: boolean;
    class?: string;
  }

  let { seconds, ms = true, hours = false, class: klass }: Props = $props();
</script>

<span class={klass ? `num timecode ${klass}` : "num timecode"}>
  {formatTimecode(seconds, { ms, hours })}
</span>

<style>
  .timecode {
    font-size: inherit;
    letter-spacing: -0.01em;
  }
</style>
