"use client";

/**
 * The honest data-coverage line on the composer (design brief: per-ticker
 * asymmetric, fed live from /api/data/coverage). SPY reaches 2020 via the
 * verified archive; QQQ/IWM begin 2026-07 — the asymmetry is the product
 * being honest, not a bug state.
 */

import Link from "next/link";
import { useEffect, useState } from "react";

import { getCoverage } from "@/lib/api";
import { monthYear, year } from "@/lib/format";
import type { CoveragePayload } from "@/lib/types";

function daysBetween(a: string, b: string): number {
  return Math.max(1, (new Date(b).getTime() - new Date(a).getTime()) / 86_400_000);
}

function Chip({ label, fill, range }: { label: string; fill: number; range: string }) {
  return (
    <span className="inline-flex items-center gap-[7px] rounded-full border border-line-soft px-3 py-[5px] font-mono text-[11px] text-ink-3">
      {label}
      <span className="inline-block h-[5px] w-9 overflow-hidden rounded-[3px] bg-line-soft">
        <span
          className={`block h-full ${fill > 0.5 ? "bg-ink-4" : "bg-trust"}`}
          style={{ width: `${Math.max(3, Math.round(fill * 100))}%` }}
        />
      </span>
      {range}
    </span>
  );
}

export function CoverageChips() {
  const [coverage, setCoverage] = useState<CoveragePayload | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getCoverage()
      .then(setCoverage)
      .catch((e) => setError(e instanceof Error ? e.message : "coverage unavailable"));
  }, []);

  if (error) {
    return (
      <div className="mb-11 flex items-center gap-2">
        <span className="inline-flex items-center rounded-full border border-warn/50 px-3 py-[5px] font-mono text-[11px] text-warn">
          coverage unavailable — {error.includes("backend unreachable") ? "backend offline" : "lake unreadable"}
        </span>
      </div>
    );
  }

  if (!coverage) {
    return (
      <div className="mb-11 flex items-center gap-2">
        <span className="inline-flex animate-pin-pulse items-center rounded-full border border-line-soft px-3 py-[5px] font-mono text-[11px] text-ink-4">
          reading the lake…
        </span>
      </div>
    );
  }

  const spy = coverage.chains.SPY;
  const qqq = coverage.chains.QQQ;
  const und = coverage.underlying.SPY;
  const today = coverage.generated_at.slice(0, 10);
  const undSpan = und ? daysBetween(und.first, today) : 1;

  return (
    <div className="mb-11 flex flex-wrap items-center gap-2">
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
      {und && (
        <Chip
          label="underlying"
          fill={undSpan / daysBetween("1990-01-01", today)}
          range={`${year(und.first)} → now`}
        />
      )}
      <Link href="/data" className="ml-auto font-mono text-[11px] text-ink-4 hover:text-ink-3">
        day {coverage.record_days} of collection →
      </Link>
    </div>
  );
}
