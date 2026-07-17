/**
 * Facts derived from the coverage payload, shared by the landing receipts,
 * the landing footer, and the Data Observatory (owner 2026-07-17: "days on
 * record" counts from the OLDEST banked data — SPY/QQQ chains reach 2009 —
 * and grows daily; the young nightly-record streak reads as a different,
 * smaller thing and confused the story).
 */

import type { CoveragePayload, CoverageRange } from "./types";

/** Earliest first-session across every banked per-ticker range (chains +
 * EOD sources + minute bars) — the true start of the record we hold. */
export function oldestDataFirst(coverage: CoveragePayload): string | null {
  const firsts: string[] = [];
  const collect = (ranges?: Record<string, CoverageRange | null>) => {
    for (const r of Object.values(ranges ?? {})) {
      if (r?.first) firsts.push(r.first);
    }
  };
  collect(coverage.chains);
  collect(coverage.minute_bars);
  for (const source of Object.values(coverage.eod ?? {})) {
    collect(source ?? undefined);
  }
  return firsts.length ? firsts.reduce((a, b) => (a < b ? a : b)) : null;
}

/** Whole days from the oldest banked session to the payload's own
 * generation stamp — computed, never a build-time constant. */
export function daysOnRecord(coverage: CoveragePayload): number | null {
  const first = oldestDataFirst(coverage);
  if (!first) return null;
  const start = Date.parse(`${first}T00:00:00Z`);
  const end = Date.parse(coverage.generated_at);
  if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) return null;
  return Math.floor((end - start) / 86_400_000);
}
