/** Icon catalog — name → inline SVG path. Kept in plain TS (not a .svelte
 * module) so other components can import the IconName type without tripping
 * over component type exports. Stroke-based, currentColor, no emoji
 * (control-room.md §1). */

export type IconName =
  | "check"
  | "alert"
  | "stop"
  | "dot"
  | "retry"
  | "play"
  | "pause"
  | "close"
  | "plus"
  | "clock"
  | "key"
  | "upload";

export const ICON_PATHS: Record<IconName, string> = {
  check: '<path d="M4 12.5 L9 17.5 L20 6.5" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/>',
  alert:
    '<path d="M12 3.5 L21.5 20 H2.5 Z" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/><path d="M12 9.5 v4.5" stroke="currentColor" stroke-width="2" stroke-linecap="round"/><circle cx="12" cy="17" r="1.2" fill="currentColor"/>',
  stop: '<path d="M12 2.5 L21.5 12 L12 21.5 L2.5 12 Z" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/><path d="M8.5 12 H15.5" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>',
  dot: '<circle cx="12" cy="12" r="5" fill="currentColor"/>',
  retry:
    '<path d="M20 12 a8 8 0 1 1 -3 -6.2" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/><path d="M20 3 v4 h-4" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>',
  play: '<path d="M9.5 7.5 L17 12 L9.5 16.5 Z" fill="currentColor"/>',
  pause:
    '<path d="M9 7.5 h2.4 v9 H9 Z M12.6 7.5 H15 v9 h-2.4 Z" fill="currentColor"/>',
  close:
    '<path d="M6 6 L18 18 M18 6 L6 18" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>',
  plus: '<path d="M12 5 V19 M5 12 H19" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>',
  clock:
    '<circle cx="12" cy="12" r="8.5" fill="none" stroke="currentColor" stroke-width="2"/><path d="M12 7.5 V12 L15 14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>',
  key: '<circle cx="8.5" cy="12" r="4.5" fill="none" stroke="currentColor" stroke-width="2"/><path d="M12 12 H20 M17 12 V15 M19 12 V15" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>',
  upload:
    '<path d="M12 16 V4 M7.5 8.5 L12 4 L16.5 8.5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/><path d="M4.5 19.5 H19.5" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>',
};
