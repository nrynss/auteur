/** Formatting for the numerics the control room lives on — timecodes at the
 * precision a director works at (tens of milliseconds), and dollar costs.
 * Pure functions; every consumer renders through them so digits never jitter
 * between components. */

/** "mm:ss.mmm" ("h:mm:ss.mmm" past the hour) — minutes always zero-padded
 * like the mockup's transport (00:11.420), so digits never jitter as the
 * playhead moves. Null/non-finite → "—" so a number that does not exist
 * renders as an absent value, never a fake one. */
export function formatTimecode(
  seconds: number | null | undefined,
  opts: { ms?: boolean; hours?: boolean } = {},
): string {
  const { ms = true, hours = false } = opts;
  if (seconds == null || !Number.isFinite(seconds)) return "—";
  const neg = seconds < 0 ? "-" : "";
  // Work in thousandths so rounding carries correctly (59.9996 → 1:00.000,
  // never 0:59.1000).
  const totalMs = Math.round(Math.abs(seconds) * 1000);
  const milli = totalMs % 1000;
  const totalSec = Math.floor(totalMs / 1000);
  const sec = totalSec % 60;
  const min = Math.floor(totalSec / 60) % 60;
  const hr = Math.floor(totalSec / 3600);
  const showH = hours || hr > 0;
  const minPart = String(min).padStart(2, "0");
  const out = `${showH ? `${hr}:` : ""}${minPart}:${String(sec).padStart(2, "0")}`;
  return neg + out + (ms ? "." + String(milli).padStart(3, "0") : "");
}

/** "$0.30", "$1,234.56". Null → "—". */
export function formatCost(
  usd: number | null | undefined,
): string {
  if (usd == null || !Number.isFinite(usd)) return "—";
  return (
    "$" +
    usd.toLocaleString("en-US", {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    })
  );
}

/** Raw seconds at two decimals, zero-padded to five chars — the scene-rail
 * format the mockup uses ("10.62", "03.20"). Scene spans are short, so
 * m:ss would be noise; this is the honest value the API reports. */
export function formatSeconds(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds)) return "—";
  return seconds.toFixed(2).padStart(5, "0");
}
