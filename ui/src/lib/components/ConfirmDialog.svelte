<script module lang="ts">
  // Module-scope counter: ids must be document-unique across every instance
  // (two dialogs mounted together would otherwise both be "dialog-title-1"
  // and aria-labelledby would resolve to the wrong one).
  let uidCounter = 0;
  function uid(prefix: string) {
    uidCounter += 1;
    return `${prefix}-${uidCounter}`;
  }
</script>

<script lang="ts">
  /** ConfirmDialog — cost confirmation for anything that spends money
   * (7.11's contract: the UI cannot spend money it never displayed, so the
   * amount is always shown when one exists, and `confirm_cost_usd` is echoed
   * verbatim). Modal, focus-managed, Escape/backdrop cancel. No drop shadow:
   * control-room.md §4 reserves the one shadow for the video frame, so this
   * separates with border + backdrop. */
  import Button from "./Button.svelte";
  import Icon from "./Icon.svelte";

  interface Props {
    open: boolean;
    title: string;
    /** What will happen — active voice (control-room.md §8). */
    body?: string;
    /** The cost, pre-formatted (screens pass formatCost of the exact
     * confirm_cost_usd they will echo back). null hides the cost line. */
    amount?: string | null;
    confirmLabel?: string;
    cancelLabel?: string;
    danger?: boolean;
    busy?: boolean;
    onconfirm?: () => void;
    oncancel?: () => void;
  }

  let {
    open,
    title,
    body = "",
    amount = null,
    confirmLabel = "Confirm",
    cancelLabel = "Cancel",
    danger = false,
    busy = false,
    onconfirm,
    oncancel,
  }: Props = $props();

  const titleId = uid("dialog-title");
  const descId = uid("dialog-desc");
  let dialogEl = $state<HTMLElement | null>(null);

  $effect(() => {
    if (!open) return;
    const el = dialogEl;
    const previouslyFocused = document.activeElement as HTMLElement | null;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    // After the DOM for the open dialog is in place, move focus to the
    // confirm button so Enter confirms and Tab starts inside the dialog.
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
    if (busy) return; // a pending spend path must not be cancellable
    if (e.key === "Escape") {
      e.preventDefault();
      oncancel?.();
      return;
    }
    if (e.key === "Tab") {
      trapTab(e);
    }
  }

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
    if (busy) return; // same rule as Escape
    if (e.target === e.currentTarget) oncancel?.();
  }
</script>

{#if open}
  <!-- Backdrop has no keydown: Escape is handled once, on the dialog. A
       second handler here would fire twice via bubbling. -->
  <div class="backdrop" role="presentation" onclick={onBackdrop}>
    <div
      class="dialog"
      role="dialog"
      aria-modal="true"
      aria-labelledby={titleId}
      aria-describedby={body ? descId : undefined}
      tabindex="-1"
      bind:this={dialogEl}
      onkeydown={onKeydown}
    >
      <header>
        <h2 id={titleId}>{title}</h2>
        <button class="x" aria-label="Close" onclick={oncancel} disabled={busy}>
          <Icon name="close" size={12} />
        </button>
      </header>
      {#if body}<p id={descId}>{body}</p>{/if}
      {#if amount != null}
        <div class="cost">
          <span class="k">This will cost</span>
          <span class="amt num">{amount}</span>
        </div>
      {/if}
      <footer>
        <Button variant="ghost" onclick={oncancel} disabled={busy}>
          {cancelLabel}
        </Button>
        <Button
          variant={danger ? "danger" : "primary"}
          pending={busy}
          data-autofocus
          onclick={onconfirm}
        >
          {confirmLabel}
        </Button>
      </footer>
    </div>
  </div>
{/if}

<style>
  .backdrop {
    position: fixed;
    inset: 0;
    z-index: var(--z-dialog);
    display: grid;
    place-items: center;
    padding: 24px;
    background: color-mix(in srgb, var(--ground) 74%, transparent);
    backdrop-filter: blur(2px);
  }

  .dialog {
    width: min(420px, 100%);
    background: var(--surface);
    border: 1px solid var(--line);
    border-radius: var(--r-lg);
    padding: 16px 16px 14px;
    outline: none;
  }

  header {
    display: flex;
    align-items: baseline;
    gap: 10px;
  }

  h2 {
    margin: 0;
    font-size: var(--text-13);
    font-weight: var(--w-590);
    flex: 1;
  }

  .x {
    border: 0;
    background: transparent;
    color: var(--faint);
    cursor: pointer;
    padding: 2px;
    display: grid;
    place-items: center;
    border-radius: 4px;
  }

  .x:hover {
    color: var(--text);
  }

  p {
    margin: 10px 0 0;
    font-size: var(--text-12);
    line-height: 1.5;
    color: var(--dim);
  }

  .cost {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 10px;
    margin-top: 12px;
    padding: 9px 11px;
    border: 1px solid var(--line);
    border-radius: var(--r);
    background: var(--raised);
  }

  .cost .k {
    font-size: var(--text-11);
    color: var(--dim);
  }

  .cost .amt {
    font-size: var(--text-13);
    font-weight: var(--w-590);
    color: var(--accent);
  }

  footer {
    display: flex;
    justify-content: flex-end;
    gap: 8px;
    margin-top: 14px;
  }
</style>
