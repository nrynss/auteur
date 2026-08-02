<script lang="ts">
  /** State chip — the vocabulary's four states. The design rule that has
   * teeth (control-room.md §2): colour never carries meaning alone, so every
   * chip renders a glyph AND the word, and the accessible name is the word
   * plus detail. `aria-pressed` opt-in makes it a toggle chip (timeline
   * version selector, 7.10). */
  import Icon from "./Icon.svelte";
  import type { IconName } from "../icons";

  export type ChipState = "ok" | "warn" | "blocked" | "pending";

  interface Props {
    state: ChipState;
    /** Overrides the derived word (e.g. stage row says "Running"). */
    label?: string;
    detail?: string;
    pressed?: boolean;
    live?: boolean;
  }

  let { state, label, detail, pressed = false, live = false }: Props = $props();

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

{#if pressed}
  <!-- A toggle chip is a real button: keyboard-activatable, focusable,
       announces via aria-pressed. A display chip is a status region. -->
  <button type="button" class={cls} aria-label={accessible} aria-pressed={pressed}>
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
