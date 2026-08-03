/** The keyboard map (handoff.md task 22) — the single source the help
 * overlay renders, so the documented map and the bindings cannot drift by
 * hand. The bindings themselves are implemented in the components; this
 * registry is the map's truth, and help.test.ts cross-checks it against the
 * constants the handlers actually use (steps, key names).
 */

export interface Binding {
  /** Stable id — the help overlay keys on it. */
  id: string;
  /** Where the binding is live (tab or component). */
  scope: string;
  keys: string;
  action: string;
}

export const BINDINGS: Binding[] = [
  {
    id: "timeline-nudge",
    scope: "Timeline",
    keys: "← / →",
    action: "nudge the selected event 10 ms (Shift 100 ms, Alt 1 ms)",
  },
  {
    id: "resize-edge",
    scope: "Timeline",
    keys: "← / →",
    action: "resize the focused edge 10 ms (Shift 100 ms, Alt 1 ms)",
  },
  {
    id: "timeline-grip",
    scope: "Timeline",
    keys: "↑ / ↓",
    action: "resize the timeline panel (Shift for coarse)",
  },
  {
    id: "rename-enter",
    scope: "Timeline",
    keys: "Enter",
    action: "apply the inline rename",
  },
  {
    id: "rename-escape",
    scope: "Timeline",
    keys: "Escape",
    action: "cancel the inline rename",
  },
  {
    id: "dialog-escape",
    scope: "Dialogs",
    keys: "Escape",
    action: "cancel the dialog",
  },
  {
    id: "dialog-tab",
    scope: "Dialogs",
    keys: "Tab",
    action: "move within the dialog: focus is contained while it is open",
  },
  {
    id: "player-toggle",
    scope: "Player",
    keys: "K / Space",
    action: "play / pause the transport",
  },
  {
    id: "player-seek",
    scope: "Player",
    keys: "← / →",
    action: "step the playhead back / forward",
  },
  {
    id: "help-overlay",
    scope: "Everywhere",
    keys: "?",
    action: "open this keyboard map and the glossary",
  },
];
