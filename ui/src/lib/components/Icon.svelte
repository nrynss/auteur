<script lang="ts">
  /** Inline SVG glyphs — no emoji, per control-room.md §1. Stroke-based,
   * currentColor, so icons inherit text colour and never carry meaning alone
   * (semantic states pair an icon with a word — see Chip). */
  import type { Snippet } from "svelte";
  import { ICON_PATHS, type IconName } from "../icons";

  interface Props {
    name: IconName;
    /** 1em default; icons in tight spots pass explicit px. */
    size?: number;
    /** Decorative by default (a11y: the word travels beside the icon). */
    "aria-hidden"?: boolean;
    children?: Snippet;
  }

  let {
    name,
    size = 14,
    "aria-hidden": ariaHidden = true,
    ...rest
  }: Props = $props();

  const pick = $derived(ICON_PATHS[name]);
</script>

<svg
  width={size}
  height={size}
  viewBox="0 0 24 24"
  aria-hidden={ariaHidden}
  {...rest}
>{@html pick}</svg
>
