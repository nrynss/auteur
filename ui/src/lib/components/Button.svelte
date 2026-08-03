<script lang="ts">
  /** Button — the only interactive element the screens share. Variants:
   * secondary (default, raised), primary (accent), ghost (flat), danger
   * (semantic stop). `pending` shows a spinner and disables. */

  import Icon from "./Icon.svelte";
  import Spinner from "./Spinner.svelte";
  import Tooltip from "../help/Tooltip.svelte";

  interface Props {
    variant?: "secondary" | "primary" | "ghost" | "danger";
    size?: "sm" | "md";
    pending?: boolean;
    disabled?: boolean;
    type?: "button" | "submit";
    /** Help text — rendered as a real tooltip (task 22), never a native
     * title= attribute. A disabled button still gets its explanation: the
     * tooltip wrapper becomes the keyboard handle. */
    help?: string;
    "aria-label"?: string;
    onclick?: (event: MouseEvent) => void;
    children?: import("svelte").Snippet;
    /** data-* passthrough (e.g. data-autofocus for dialogs). */
    [key: `data-${string}`]: unknown;
  }

  let {
    variant = "secondary",
    size = "md",
    pending = false,
    disabled = false,
    type = "button",
    help,
    "aria-label": ariaLabel,
    onclick,
    children,
    ...rest
  }: Props = $props();
</script>

{#if help}
  <Tooltip text={help}>
    <button
      class="btn"
      class:primary={variant === "primary"}
      class:ghost={variant === "ghost"}
      class:danger={variant === "danger"}
      class:sm={size === "sm"}
      class:pending={pending}
      type={type}
      disabled={disabled || pending}
      aria-label={ariaLabel}
      aria-busy={pending || undefined}
      onclick={onclick}
      {...rest}
    >
      {#if pending}
        <Spinner size={11} />
      {/if}
      {@render children?.()}
    </button>
  </Tooltip>
{:else}
  <button
    class="btn"
    class:primary={variant === "primary"}
    class:ghost={variant === "ghost"}
    class:danger={variant === "danger"}
    class:sm={size === "sm"}
    class:pending={pending}
    type={type}
    disabled={disabled || pending}
    aria-label={ariaLabel}
    aria-busy={pending || undefined}
    onclick={onclick}
    {...rest}
  >
    {#if pending}
      <Spinner size={11} />
    {/if}
    {@render children?.()}
  </button>
{/if}

<style>
  .btn {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    font: inherit;
    color: var(--text);
    background: var(--raised);
    border: 1px solid var(--line);
    border-radius: var(--r-sm);
    padding: 5px 11px;
    cursor: pointer;
    white-space: nowrap;
  }

  .btn:hover:not(:disabled) {
    border-color: var(--faint);
  }

  .btn:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }

  .btn.primary {
    background: var(--accent);
    border-color: transparent;
    font-weight: var(--w-560);
  }

  .btn.primary:not(:disabled) {
    color: var(--surface);
  }

  .btn.primary:hover:not(:disabled) {
    filter: brightness(1.06);
  }

  .btn.ghost {
    background: transparent;
  }

  .btn.danger {
    border-color: var(--stop);
    color: var(--stop);
    background: transparent;
  }

  .btn.sm {
    padding: 3px 9px;
    font-size: var(--text-12);
  }
</style>
