const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "2026-07-02" → "Jul 2 ’26" (the design's date style) */
export function shortDate(iso: string): string {
  const [y, m, d] = iso.split("-").map(Number);
  return `${MONTHS[m - 1]} ${d} ’${String(y).slice(2)}`;
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
