"use client";

/**
 * Strategy Library — sorted by trust, not by return. Each card carries the
 * miniaturized signature element. The empty state teaches the philosophy in
 * one line and routes to New Analysis.
 */

import Link from "next/link";
import { useEffect, useState } from "react";

import { listRuns } from "@/lib/api";
import type { RunSummary } from "@/lib/types";

import { DemoBadge, Disclaimer } from "@/components/disclaimer";
import { TrustBandCard } from "@/components/verdict/trust-band";

export default function LibraryPage() {
  const [runs, setRuns] = useState<RunSummary[] | null>(null);
  const [demo, setDemo] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    listRuns()
      .then((r) => {
        setRuns(r.runs);
        setDemo(r.demo);
      })
      .catch((e) => setError(e instanceof Error ? e.message : "library unavailable"));
  }, []);

  return (
    <div>
      <h1 className="mb-1 text-[28px] font-[650]">Library</h1>
      <div className="mb-[22px] flex items-center gap-2.5">
        <p className="text-[15px] text-ink-3">Sorted by trust, not by return.</p>
        {demo && <DemoBadge text="demo entries — engine lands at M2" />}
      </div>

      {error && (
        <div className="rounded-xl border border-warn/50 px-3.5 py-3 font-mono text-[12px] text-warn">
          {error}
        </div>
      )}

      {runs && runs.length === 0 && (
        <div className="mt-10 rounded-[14px] border border-dashed border-line-hover px-6 py-10 text-center">
          <p className="text-[14.5px] text-ink-2">
            Nothing here yet. The first thing this tool will do with your idea is try to break it.
          </p>
          <Link
            href="/"
            className="mt-4 inline-block rounded-[10px] bg-trust px-5 py-2.5 text-[14px] font-bold text-[#0d1216]"
          >
            New analysis →
          </Link>
        </div>
      )}

      {runs && runs.length > 0 && (
        <div className="grid grid-cols-2 gap-3.5">
          {runs.map((r) => (
            <Link
              key={r.id}
              href={`/runs/${r.id}`}
              className="rounded-[14px] border border-line bg-panel p-5 hover:border-line-hover"
            >
              <div className="font-mono text-[15px] font-medium">{r.name}</div>
              <div className="mb-3.5 mt-1 font-mono text-[12px] text-ink-4">{r.meta}</div>
              <TrustBandCard band={r.band} marker={r.marker} withheld={r.kind === "refusal"} />
              <div className="text-[13.5px] italic leading-[1.55] text-ink-2">{r.quote}</div>
            </Link>
          ))}
          <Link
            href="/"
            className="flex min-h-[140px] items-center justify-center rounded-[14px] border border-dashed border-line-hover p-5 text-[14.5px] text-ink-4 hover:border-trust-border hover:text-trust"
          >
            + New Analysis
          </Link>
        </div>
      )}

      <Disclaimer short />
    </div>
  );
}
