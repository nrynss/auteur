<script lang="ts">
  /** ThemeToggle — explicit theme switch; the tokens redefine under
   * data-theme so the toggle wins over the system preference in both
   * directions (control-room.md §2). */
  import { currentTheme, toggleTheme } from "../theme";

  let theme = $state(currentTheme());
  const label = $derived(
    theme === "dark" ? "Switch to light theme" : "Switch to dark theme",
  );

  function onClick() {
    theme = toggleTheme();
  }
</script>

<button class="t" aria-label={label} onclick={onClick}>◐</button>

<style>
  .t {
    font: inherit;
    color: var(--dim);
    background: transparent;
    border: 1px solid var(--line);
    border-radius: var(--r-sm);
    padding: 4px 7px;
    line-height: 1;
    cursor: pointer;
  }

  .t:hover {
    color: var(--text);
    border-color: var(--faint);
  }
</style>
