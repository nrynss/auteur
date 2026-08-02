<script lang="ts">
  /** Spinner — the only animation in the system; disabled under
   * prefers-reduced-motion. Never carries meaning alone. */

  interface Props {
    size?: number;
    label?: string;
  }

  let { size = 14, label = "working" }: Props = $props();
</script>

<span class="spinner" style={`width:${size}px;height:${size}px`} role="img" aria-label={label}>
  <i></i>
</span>

<style>
  .spinner {
    display: inline-block;
    position: relative;
  }

  .spinner i {
    position: absolute;
    inset: 0;
    border-radius: 50%;
    border: 2px solid var(--line);
    border-top-color: var(--accent);
    animation: spin 0.8s linear infinite;
  }

  @keyframes spin {
    to {
      transform: rotate(360deg);
    }
  }

  @media (prefers-reduced-motion: reduce) {
    .spinner i {
      animation-duration: 1.6s;
    }
  }
</style>
