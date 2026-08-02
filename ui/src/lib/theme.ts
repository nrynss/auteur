/** Theme resolution + persistence (unit 7.7, control-room.md §2).
 *
 * Precedence: explicit `data-theme` attribute (set before first paint by the
 * inline script in index.html, then by ThemeToggle) → saved preference →
 * system `prefers-color-scheme`. The tokens in theme.css redefine under both
 * the media query and the attribute so an explicit toggle wins in both
 * directions. */

export type Theme = "light" | "dark";

export const THEME_KEY = "foleyflow-theme";

/** Pure decision, no DOM — unit-testable. */
export function resolveTheme(
  explicit: string | null | undefined,
  systemPrefersDark: boolean,
): Theme {
  if (explicit === "light" || explicit === "dark") return explicit;
  return systemPrefersDark ? "dark" : "light";
}

export function currentTheme(): Theme {
  const explicit = document.documentElement.dataset.theme ?? null;
  const systemPrefersDark = window.matchMedia(
    "(prefers-color-scheme: dark)",
  ).matches;
  return resolveTheme(explicit, systemPrefersDark);
}

export function setTheme(theme: Theme): void {
  document.documentElement.dataset.theme = theme;
  try {
    localStorage.setItem(THEME_KEY, theme);
  } catch {
    // Private mode / storage disabled: the attribute still applies for this
    // session; persistence is best-effort.
  }
  // Canvas-drawn components repaint on theme change (control-room.md §6).
  window.dispatchEvent(new Event("ff:theme-change"));
}

export function toggleTheme(): Theme {
  const next = currentTheme() === "dark" ? "light" : "dark";
  setTheme(next);
  return next;
}

// System-tracking users (no saved preference) follow their OS live — the
// pre-paint script sets data-theme only when a preference exists. When the OS
// flips, the CSS tokens (and --wave with them) change but no toggle ran, so
// canvas-drawn components would keep the old theme's ink. Bridge the system
// path to the same repaint event setTheme dispatches.
if (typeof window !== "undefined") {
  window
    .matchMedia("(prefers-color-scheme: dark)")
    .addEventListener("change", () => {
      window.dispatchEvent(new Event("ff:theme-change"));
    });
}
