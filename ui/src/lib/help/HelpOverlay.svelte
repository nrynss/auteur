<script module lang="ts">
  let uidCounter = 0;
  function uid() {
    uidCounter += 1;
    return `help-${uidCounter}`;
  }
</script>

<script lang="ts">
  /** Help overlay (handoff.md task 22) — one help surface, opened with `?`
   * from anywhere: the keyboard map and the glossary on one screen, so a
   * binding is documented where it can be learned and a term is defined in
   * the same place the tooltips read from. Modal and focus-managed like
   * ConfirmDialog: Escape closes, Tab is contained. */
  import Kbd from "../components/Kbd.svelte";
  import { BINDINGS } from "./bindings";
  import { GLOSSARY } from "./glossary";

  interface Props {
    open: boolean;
    onclose: () => void;
  }

  let { open, onclose }: Props = $props();

  const titleId = uid();
  let dialogEl = $state<HTMLElement | null>(null);

  $effect(() => {
    if (!open) return;
    const el = dialogEl;
    const previouslyFocused = document.activeElement as HTMLElement | null;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const t = setTimeout(() => {
      el?.querySelector<HTMLElement>("[data-autofocus]")?.focus();
    }, 0);
    return () => {
      clearTimeout(t);
      document.body.style.overflow = previousOverflow;
      previouslyFocused?.focus?.();
    };
  });

  function onKeydown(e: KeyboardEvent) {
    if (e.key === "Escape") {
      e.preventDefault();
      onclose();
      return;
    }
    if (e.key === "Tab") {
      trapTab(e);
    }
  }

  // Escape must close the overlay even when focus has not yet landed inside
  // it (autofocus is one macrotask behind the open) or sits on the scrim —
  // a modal that only closes when the right element happens to be focused
  // is a trap. Window-level, like Tooltip's Escape. Tab stays contained by
  // the dialog's own handler.
  $effect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onclose();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  function trapTab(e: KeyboardEvent) {
    const el = dialogEl;
    if (!el) return;
    const focusables = el.querySelectorAll<HTMLElement>(
      'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
    );
    if (focusables.length === 0) return;
    const first = focusables[0];
    const last = focusables[focusables.length - 1];
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault();
      first.focus();
    }
  }

  function onBackdrop(e: MouseEvent) {
    if (e.target === e.currentTarget) onclose();
  }
</script>

{#if open}
  <div class="scrim" role="presentation" onclick={onBackdrop}>
    <div
      bind:this={dialogEl}
      class="help"
      role="dialog"
      aria-modal="true"
      aria-labelledby={titleId}
      tabindex="-1"
      onkeydown={onKeydown}
    >
      <header class="head">
        <h2 id={titleId}>Help: keys and terms</h2>
        <button type="button" class="close" data-autofocus onclick={onclose} aria-label="Close help">
          ✕
        </button>
      </header>

      <div class="cols">
        <div class="col">
          <p class="eyebrow">Keyboard map</p>
          <ul class="map">
            {#each BINDINGS as b (b.id)}
              <li>
                <span class="keys">{b.keys}</span>
                <span class="what">
                  <span class="action">{b.action}</span>
                  <span class="scope">{b.scope}</span>
                </span>
              </li>
            {/each}
          </ul>
          <p class="note">
            <Kbd>?</Kbd> opens this overlay; <Kbd>Escape</Kbd> closes it and
            every dialog. Arrows nudge at 10 ms. Hold <Kbd>Shift</Kbd> for
            100 ms, <Kbd>Alt</Kbd> for 1 ms.
          </p>
        </div>

        <div class="col">
          <p class="eyebrow">Glossary</p>
          <ul class="terms">
            {#each Object.entries(GLOSSARY) as [id, t] (id)}
              <li>
                <span class="term">{t.label}</span>
                <span class="def">{t.def}</span>
              </li>
            {/each}
          </ul>
        </div>
      </div>
    </div>
  </div>
{/if}

<style>
  .scrim {
    position: fixed;
    inset: 0;
    z-index: 50;
    background: rgba(0, 0, 0, 0.45);
    display: grid;
    place-items: center;
  }

  .help {
    width: min(860px, calc(100vw - 32px));
    max-height: calc(100vh - 48px);
    overflow-y: auto;
    background: var(--surface);
    border: 1px solid var(--line);
    border-radius: var(--r-lg);
  }

  .head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 12px 16px;
    border-bottom: 1px solid var(--line);
  }

  .head h2 {
    margin: 0;
    font-size: var(--text-13);
    font-weight: var(--w-590);
  }

  .close {
    font: inherit;
    border: 0;
    background: transparent;
    color: var(--dim);
    cursor: pointer;
    padding: 4px 8px;
    border-radius: 4px;
  }

  .close:hover {
    color: var(--text);
    background: var(--line-soft);
  }

  .cols {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 4px;
    padding: 8px 16px 16px;
  }

  .eyebrow {
    font-size: 10px;
    letter-spacing: 0.07em;
    text-transform: uppercase;
    color: var(--faint);
    font-weight: 600;
    margin: 12px 0 6px;
  }

  .map,
  .terms {
    list-style: none;
    margin: 0;
    padding: 0;
    border: 1px solid var(--line);
    border-radius: var(--r);
    overflow: hidden;
  }

  .map li {
    display: grid;
    grid-template-columns: 86px 1fr;
    gap: 10px;
    padding: 6px 10px;
    border-bottom: 1px solid var(--line-soft);
    font-size: var(--text-12);
    align-items: baseline;
  }

  .map li:last-child,
  .terms li:last-child {
    border-bottom: 0;
  }

  .map .keys {
    font-family: var(--font-mono);
    font-size: 10.5px;
    color: var(--accent);
    font-variant-numeric: tabular-nums;
  }

  .map .what {
    display: flex;
    flex-direction: column;
    gap: 1px;
  }

  .map .scope {
    font-size: 9.5px;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: var(--faint);
  }

  .note {
    font-size: var(--text-11);
    line-height: 1.6;
    color: var(--dim);
    margin: 10px 2px 0;
  }

  .terms li {
    display: grid;
    grid-template-columns: 110px 1fr;
    gap: 10px;
    padding: 6px 10px;
    border-bottom: 1px solid var(--line-soft);
    font-size: var(--text-12);
  }

  .terms .term {
    font-weight: var(--w-560);
  }

  .terms .def {
    color: var(--dim);
    line-height: 1.45;
  }

  @media (max-width: 700px) {
    .cols {
      grid-template-columns: 1fr;
    }
  }
</style>
