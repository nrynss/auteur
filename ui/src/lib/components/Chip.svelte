<script lang="ts">
  /** State chip — the vocabulary's four states. The design rule that has
   * teeth (control-room.md §2): colour never carries meaning alone, so every
   * chip renders a glyph AND the word, and the accessible name is the word
   * plus detail. `toggle` renders a real, keyboard-activatable `<button>`
   * (the timeline version selector, 7.10); `pressed` is its state. A
   * non-toggle chip is a status region. */
  import Icon from "./Icon.svelte";
  import type { IconName } from "../icons";

  export type ChipState = "ok" | "warn" | "blocked" | "pending";

  interface Props {
    state: ChipState;
    /** Overrides the derived word (e.g. stage row says "Running"). */
    label?: string;
    detail?: string;
    /** Render as a toggle button (selectable chip); pressed is its state. */
    toggle?: boolean;
    /** Toggle state — only meaningful with toggle. */
    pressed?: boolean;
    live?: boolean;
    onclick?: (event: MouseEvent) => void;
    [key: `data-${string}`]: unknown;
  }

  let {
    state,
    label,
    detail,
    toggle = false,
    pressed = false,
    live = false,
    onclick,
    ...rest
  }: Props = $props();

  const WORDS: Record<ChipState, string> = {
    ok: "OK",
    warn: "Warning",
    blocked: "Blocked",
    pending: "Pending",
  };

  const ICONS: Record<ChipState, IconName> = {
    ok: "check",
    warn: "alert",
    blocked: "stop",
    pending: "dot",
  };

  const word = $derived(label ?? WORDS[state]);
  const accessible = $derived(detail ? `${word} — ${detail}` : word);
  const cls = $derived(
    [
      "chip",
      state,
      ...(live ? ["live"] : []),
      ...(pressed ? ["pressed"] : []),
    ].join(" "),
  );
</script>

{#if toggle}
  <!-- A toggle chip is a real button: keyboard-activatable, focusable,
       announces via aria-pressed. An OFF toggle is still a button — that is
       the one you click to turn on. -->
  <button
    type="button"
    class={cls}
    aria-label={accessible}
    aria-pressed={pressed}
    onclick={onclick}
    {...rest}
  >
    <Icon name={ICONS[state]} size={11} />
    <span class="word">{word}</span>
    {#if detail}<span class="detail">{detail}</span>{/if}
  </button>
{:else}
  <span class={cls} aria-label={accessible} role="status">
    <Icon name={ICONS[state]} size={11} />
    <span class="word">{word}</span>
    {#if detail}<span class="detail">{detail}</span>{/if}
  </span>
{/if}

<style>
  .chip {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    font-size: var(--text-11);
    font-weight: var(--w-500);
    padding: 3px 9px;
    border-radius: var(--r-pill);
    border: 1px solid var(--line);
    color: var(--dim);
    background: var(--raised);
  }

  .chip .word {
    font-weight: var(--w-560);
  }

  .chip .detail {
    color: var(--faint);
  }

  .chip.ok {
    color: var(--ok);
    border-color: var(--ok-q);
    background: var(--ok-q);
  }

  .chip.warn {
    color: var(--warn);
    border-color: var(--warn-q);
    background: var(--warn-q);
  }

  .chip.blocked {
    color: var(--stop);
    border-color: var(--stop-q);
    background: var(--stop-q);
  }

  .chip.pending {
    color: var(--dim);
    border-color: var(--line);
    background: var(--raised);
  }

  .chip.live {
    color: var(--ok);
    border-color: transparent;
    background: var(--ok-q);
  }

  .chip[aria-pressed="true"] {
    color: var(--text);
    border-color: var(--accent);
    background: var(--accent-q);
  }
</style>
