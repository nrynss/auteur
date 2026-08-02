/** Ancillary-fetch guard for the control room.
 *
 * This lives in a `.ts` module, NOT inline in ControlRoom.svelte, and that is
 * load-bearing. Written as a generic arrow inside a `.svelte` script —
 *
 *     const quiet = async <T,>(p: Promise<T>): Promise<T | null> => { … }
 *
 * — Svelte's TypeScript-stripping step drops the whole parameter list and
 * emits `async () => { … return await p }`. The source type-checks (it is
 * valid TS, so svelte-check passes) but every call throws
 * `ReferenceError: p is not defined` at runtime, and every panel on the screen
 * silently renders its empty state. It shipped that way in both the dev server
 * and the production bundle. Plain `.ts` modules compile it correctly.
 *
 * Behaviour: a 404 is absence — an endpoint that has not produced yet — and
 * returns null quietly. Anything else is a failure: it returns null too, but
 * reports through `onFailure` so the screen can say the empty rows are a
 * failure rather than letting them read as "no data" (control-room.md §7). */

import { ApiError } from "../api/client";

export async function quiet<T>(
  p: Promise<T>,
  onFailure: (error: unknown) => void,
): Promise<T | null> {
  try {
    return await p;
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) return null;
    onFailure(e);
    return null;
  }
}
