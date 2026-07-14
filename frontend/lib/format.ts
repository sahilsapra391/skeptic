const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "2026-07-02" (or a full ISO datetime) → "Jul 2 ’26" (the design's date
 * style). Unparseable input passes through rather than rendering "NaN". */
export function shortDate(iso: string): string {
  const [y, m, d] = iso.slice(0, 10).split("-").map(Number);
  if (!y || !m || !d) return iso;
  return `${MONTHS[m - 1]} ${d} ’${String(y).slice(2)}`;
}

// module-level: Intl.DateTimeFormat construction is expensive and pinLabel
// renders inside per-pin loops
const PIN_TIME_FMT = new Intl.DateTimeFormat("en-US", {
  month: "short",
  day: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
  timeZone: "America/New_York",
});

/** Pinned-bar time → "Mar 4 ’26", or "Apr 7, 10:35" (ET) when the pin is
 * intraday. Date-only inputs format as plain dates — a timezone conversion
 * would parse them as UTC midnight and slide the label back a day in ET.
 * Shared by chart-teach (pinning) and the provenance story (replaying the
 * record) so the two surfaces can never label the same bar differently. */
export function pinLabel(iso: string): string {
  const hasTime = iso.includes("T") && !iso.startsWith(iso.slice(0, 10) + "T00:00");
  if (!hasTime) return shortDate(iso);
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return PIN_TIME_FMT.format(d);
}

/** "2026-07-02" → "Jul ’26" */
export function monthYear(iso: string): string {
  const [y, m] = iso.split("-").map(Number);
  return `${MONTHS[m - 1]} ’${String(y).slice(2)}`;
}

/** "1993-01-29" → "1993" */
export function year(iso: string): string {
  return iso.slice(0, 4);
}

export function minutesAgo(isoTs: string): number {
  return Math.max(0, Math.round((Date.now() - new Date(isoTs).getTime()) / 60000));
}
