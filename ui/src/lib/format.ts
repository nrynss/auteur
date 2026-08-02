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

/** "$0.30", "$1,234.56". Null → "—". Two decimals for budgets and totals. */
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

/** Cost shown in a spend ConfirmDialog — never rounds under the echoed value.
 * Up to 6 fraction digits so 0.0375 stays "$0.0375", not "$0.04", and a
 * sub-cent billable amount never reads as "$0.00". */
export function formatConfirmCost(usd: number | null | undefined): string {
  if (usd == null || !Number.isFinite(usd)) return "—";
  if (usd === 0) return "$0.00";
  // Prefer the shortest string that still equals the number when re-parsed,
  // but never fewer than 2 decimals for whole-cent amounts.
  const fixed = usd.toFixed(6).replace(/\.?0+$/, "");
  const withMin = fixed.includes(".") ? fixed : `${fixed}.00`;
  const parts = withMin.split(".");
  if (parts[1].length < 2) parts[1] = parts[1].padEnd(2, "0");
  const [whole, frac] = parts;
  const wholeFmt = Number(whole).toLocaleString("en-US");
  return `$${wholeFmt}.${frac}`;
}

/** Raw seconds at two decimals, zero-padded to five chars — the scene-rail
 * format the mockup uses ("10.62", "03.20"). Scene spans are short, so
 * m:ss would be noise; this is the honest value the API reports. */
export function formatSeconds(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds)) return "—";
  return seconds.toFixed(2).padStart(5, "0");
}
