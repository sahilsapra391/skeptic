"use client";

/**
 * Landing §4 — receipts strip (design 2a lines 190–209 / 2b 481–491).
 * Every coverage-fed value renders LIVE from /api/data/coverage via the same
 * shared client cache the app composer uses; while loading (or on failure)
 * those values show "—" — a fake number never renders. The two config tiles
 * (0DTE, bid/ask) are engine-guardrail copy, not data, so they render
 * regardless of fetch state. Trust hue only on this surface — no P/L tokens.
 */

import clsx from "clsx";
import { useEffect, useState, type ReactNode } from "react";

import { getCoverage } from "@/lib/api";
import { daysOnRecord, oldestDataFirst } from "@/lib/coverage-facts";
import { monthYear, shortDate } from "@/lib/format";
import type { CoveragePayload, CoverageRange } from "@/lib/types";

function daysBetween(a: string, b: string): number {
  return Math.max(1, (new Date(b).getTime() - new Date(a).getTime()) / 86_400_000);
}

/** MetricTile "?" badge + hover tooltip (DS bundle §6 Hint, CSS hover instead
 * of the bundle's JS mouseenter). Hover-only affordance, so hidden below md —
 * the mobile design carries no hints. */
function Hint({ text, align = "center" }: { text: string; align?: "center" | "right" }) {
  return (
    <span className="group/hint relative hidden shrink-0 md:inline-flex">
      <span className="flex h-[15px] w-[15px] cursor-help select-none items-center justify-center rounded-full border border-line text-[9.5px] font-semibold leading-none text-ink-4 transition-colors duration-100 group-hover/hint:border-line-hover group-hover/hint:text-ink-2">
        ?
      </span>
      <span
        className={clsx(
          "pointer-events-none absolute top-[calc(100%+7px)] z-30 w-[230px] rounded-[9px] border border-line bg-raised px-3 py-2 text-left font-sans text-[11.5px] font-normal normal-case leading-[1.55] tracking-normal text-ink-2 opacity-0 shadow-pop transition-opacity duration-100 group-hover/hint:opacity-100",
          align === "right" ? "right-0" : "left-1/2 -translate-x-1/2",
        )}
      >
        {text}
      </span>
    </span>
  );
}

function MetricTile({
  value,
  label,
  hint,
  hintAlign,
  className,
}: {
  value: string;
  label: string;
  hint?: string;
  hintAlign?: "center" | "right";
  className?: string;
}) {
  return (
    <div className={clsx("rounded-[12px] border border-line bg-panel p-4", className)}>
      <div className="font-mono text-[24px] font-semibold text-ink">{value}</div>
      <div className="mt-[6px] flex items-center justify-between gap-1">
        <span className="font-mono text-[10.5px] font-medium tracking-[.08em] text-ink-4">{label}</span>
        {hint && <Hint text={hint} align={hintAlign} />}
      </div>
    </div>
  );
}

/** Same pill as the app home's coverage chips (coverage-chips.tsx) — thin
 * coverage gets the trust hue on purpose; deep coverage stays quiet ink. */
function Chip({ label, fill, range }: { label: string; fill: number; range: string }) {
  return (
    <span className="inline-flex items-center gap-[7px] rounded-full border border-line-soft px-3 py-[5px] font-mono text-[11px] text-ink-3">
      {label}
      <span className="inline-block h-[5px] w-9 overflow-hidden rounded-[3px] bg-line-soft">
        <span
          className={clsx("block h-full", fill > 0.5 ? "bg-ink-4" : "bg-trust")}
          style={{ width: `${Math.max(3, Math.round(fill * 100))}%` }}
        />
      </span>
      {range}
    </span>
  );
}

export function Receipts() {
  const [coverage, setCoverage] = useState<CoveragePayload | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getCoverage()
      .then(setCoverage)
      .catch((e) => setError(e instanceof Error ? e.message : "coverage unavailable"));
  }, []);

  // "SESSIONS ON TAP" = distinct SPY daily chain sessions — same fact the
  // window picker's "all available" offers, from the one coverage fetch
  const sessionsOnTap = coverage?.chains.SPY
    ? coverage.chains.SPY.sessions.toLocaleString("en-US")
    : "—";

  // "CHAINS SINCE" = earliest chain session across the tickers. Live that is
  // QQQ 2009-10-12 (SPY starts 2012-07-09) — derived, never hand-pinned.
  const chainYears = coverage
    ? Object.values(coverage.chains)
        .filter((c): c is CoverageRange => c !== null)
        .map((c) => Number(c.first.slice(0, 4)))
    : [];
  const chainsSince = chainYears.length > 0 ? String(Math.min(...chainYears)) : "—";

  // owner 2026-07-17: DAYS ON RECORD counts from the OLDEST banked session
  // (QQQ chains reach Oct '09) and grows daily — computed from the payload,
  // never a constant. The Observatory shows the same number.
  const recordDaysN = coverage ? daysOnRecord(coverage) : null;
  const recordDays = recordDaysN != null ? recordDaysN.toLocaleString("en-US") : "—";
  const recordSince = coverage ? oldestDataFirst(coverage) : null;

  let chips: ReactNode;
  if (error) {
    chips = (
      <span className="inline-flex items-center rounded-full border border-warn/50 px-3 py-[5px] font-mono text-[11px] text-warn">
        coverage unavailable — {error.includes("backend unreachable") ? "backend offline" : "lake unreadable"}
      </span>
    );
  } else if (!coverage) {
    chips = (
      <span className="inline-flex animate-pin-pulse items-center rounded-full border border-line-soft px-3 py-[5px] font-mono text-[11px] text-ink-4 motion-reduce:animate-none">
        reading the lake…
      </span>
    );
  } else {
    const spy = coverage.chains.SPY;
    const qqq = coverage.chains.QQQ;
    const minute = coverage.minute_bars.SPY;
    const und = coverage.underlying.SPY;
    const today = coverage.generated_at.slice(0, 10);
    const undSpan = und ? daysBetween(und.first, today) : 1;
    chips = (
      <>
        {spy && (
          <Chip
            label="SPY chains"
            fill={daysBetween(spy.first, today) / undSpan}
            range={`${monthYear(spy.first)} → now`}
          />
        )}
        {qqq && (
          <Chip
            label="QQQ/IWM chains"
            fill={daysBetween(qqq.first, today) / undSpan}
            range={`${monthYear(qqq.first)} → now`}
          />
        )}
        {minute && (
          <Chip
            label="minute bars"
            fill={daysBetween(minute.first, today) / undSpan}
            range={`${monthYear(minute.first)} → now`}
          />
        )}
        <span className="font-mono text-[11px] text-ink-4">
          nightly lake
          {coverage.record_latest ? ` · through ${shortDate(coverage.record_latest)}` : ""} · asymmetric
          on purpose →
        </span>
      </>
    );
  }

  return (
    <section id="receipts" className="border-t border-line-softer">
      <div className="mx-auto max-w-[1440px] px-6 pb-[44px] pt-[52px] md:px-14 md:pb-[64px] md:pt-[84px] xl:px-[120px]">
        <div className="flex items-baseline justify-between">
          <span className="font-mono text-[10.5px] font-medium tracking-[.14em] text-trust md:text-[11.5px]">
            RECEIPTS
          </span>
          <span className="hidden font-mono text-[11px] text-ink-4 md:inline">
            every fallback disclosed · gaps stay gaps, never interpolated
          </span>
        </div>
        <h2 className="mt-3 max-w-[760px] font-serif text-[27px] font-medium leading-[1.2] md:mt-[14px] md:text-[40px] md:leading-[1.15]">
          The data earns the verdicts.
        </h2>
        <div className="mt-[22px] grid grid-cols-2 gap-[10px] md:mt-[38px] md:grid-cols-3 md:gap-[14px] xl:grid-cols-5">
          <MetricTile
            value={sessionsOnTap}
            label="SESSIONS ON TAP"
            hint="every distinct SPY chain session — the window picker’s ‘all available’"
          />
          <MetricTile
            value="0DTE"
            label="MINUTE-LEVEL FILLS"
            hint="intraday chains down to same-day expiry"
          />
          <MetricTile
            value="bid/ask"
            label="NEVER MID"
            hint="slip 0.85/0.9 · $0.65/ct — measured, not assumed"
          />
          <MetricTile
            value={chainsSince}
            label="CHAINS SINCE"
            hint="earliest chain session across SPY, QQQ, IWM — read live from the coverage endpoint"
          />
          <MetricTile
            value={recordDays}
            label="DAYS ON RECORD"
            hint={
              recordSince
                ? `since the oldest banked session — ${shortDate(recordSince)}, growing daily`
                : "since the oldest banked session — live from the lake"
            }
            hintAlign="right"
            className="hidden md:block"
          />
        </div>
        <div className="mt-5 hidden flex-wrap items-center gap-2 md:flex">{chips}</div>
        <div className="mt-[14px] font-mono text-[10.5px] leading-[1.7] text-ink-4 md:hidden">
          {recordDaysN != null ? `day ${recordDaysN.toLocaleString("en-US")} of the record · ` : ""}
          nightly lake · every fallback disclosed · gaps stay gaps, never interpolated
        </div>
      </div>
    </section>
  );
}
