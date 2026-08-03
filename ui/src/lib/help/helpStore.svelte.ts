/** Help overlay store (handoff.md task 22) — the `?` help surface is global
 * (reachable from every route), so the open state lives here rather than in
 * one screen. The chrome hint buttons and the global `?` key handler both
 * call openHelp(); App.svelte renders the overlay. Mutations go through the
 * functions — `help` is a state object so property assignment is legal
 * across module boundaries. */
export const help = $state({ open: false });

export function openHelp(): void {
  help.open = true;
}

export function closeHelp(): void {
  help.open = false;
}
