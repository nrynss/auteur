/** Legacy mix-seed computation (unit 7.10), mirroring assemble.py's
 * `_event_seed` byte for byte.
 *
 * Why this exists: a rhythmic event's mix texture (the per-tile gain jitter)
 * is drawn from `sha256(f"{name}|{start_time:.3f}")`. Events on timelines
 * mixed before render.py stamped the seed used carry a stem but no seed; the
 * editor freezes the seed such an event was *always going to render with* so
 * a nudge translates instead of re-rolling the texture. The frozen value must
 * equal Python's exactly — a different integer means a different texture on
 * the next mix.
 *
 * Two parity traps, both handled:
 * - `sha256` must be the real SHA-256 (Python's hashlib), so this file ships
 *   a self-contained implementation rather than reaching for a hash that is
 *   "close enough". Verified against Python for every value it will see.
 * - Python formats floats with round-half-**even**; JS `toFixed` rounds
 *   half-up. They diverge only on the exact dyadic ties (q/16 for odd q —
 *   e.g. 0.3125 → Python "0.312", JS "0.313"), which ARE realistic start
 *   times. `pyToFixed3` reproduces Python's formatting on those.
 */
import { sha256 } from "./sha256";

/** Python's `f"{x:.3f}"`, including round-half-even on exact dyadic ties. */
export function pyToFixed3(x: number): string {
  const sign = x < 0 ? "-" : "";
  const a = Math.abs(x);
  const q = a * 16;
  if (Number.isInteger(q) && q % 2 === 1) {
    // a = q/16 exactly, so a*1000 = 62.5q — an exact tie at the 3rd decimal.
    // floor = 62q + (q-1)/2 in exact integer arithmetic (no float rounding).
    const floorT = 62 * q + (q - 1) / 2;
    // Python rounds half to even on the retained (3rd) digit.
    const r = floorT % 2 === 0 ? floorT : floorT + 1;
    const s = String(r).padStart(4, "0");
    return sign + (r < 1000 ? "0." + s.slice(-3) : s.slice(0, -3) + "." + s.slice(-3));
  }
  // Not a tie: Python and JS round the same binary value identically.
  return sign + a.toFixed(3);
}

/** assemble._event_seed: `int(sha256(f"{name}|{start:.3f}").hexdigest()[:8], 16)`. */
export function legacySeed(name: string, startTime: number): number {
  const raw = `${name}|${pyToFixed3(startTime)}`;
  return parseInt(sha256(raw).slice(0, 8), 16);
}
