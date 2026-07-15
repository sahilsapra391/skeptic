"use client";

/**
 * Strategy Library — sorted by trust, not by return. Each card carries the
 * miniaturized signature element. The empty state teaches the philosophy in
 * one line and routes to New Analysis.
 */

import Link from "next/link";
import { useEffect, useState } from "react";

import { listRuns } from "@/lib/api";
import { useSettings } from "@/lib/settings";
import type { RunSummary } from "@/lib/types";

import { DemoBadge, Disclaimer } from "@/components/disclaimer";
import { TrustBandCard } from "@/components/verdict/trust-band";

export default function LibraryPage() {
  const settings = useSettings();
  const [runs, setRuns] = useState<RunSummary[] | null>(null);
  const [demo, setDemo] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const load = (fresh: boolean) => {
      listRuns(fresh)
        .then((r) => {
          if (cancelled) return;
          setRuns(r.runs);
          setDemo(r.demo);
          // while anything is still running, keep the listing live so the
          // card flips to its verdict without a reload
          if (r.runs.some((x) => x.status === "running")) {
            timer = setTimeout(() => load(true), 4000);
          }
        })
        .catch((e) => {
          if (!cancelled) setError(e instanceof Error ? e.message : "library unavailable");
        });
    };
    load(false);
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, []);

  return (
    <div>
      <h1 className="mb-1 font-serif text-[32px] font-medium">Library</h1>
      <div className="mb-[22px] flex items-center gap-2.5">
        <p className="text-[15px] text-ink-3">Sorted by trust, not by return.</p>
        {demo && <DemoBadge text="demo entries — engine lands at M2" />}
      </div>

      {error && (
        <div className="rounded-xl border border-warn/50 px-3.5 py-3 font-mono text-[12px] text-warn">
          {error}
        </div>
      )}

      {runs === null && !error && (
        // loading skeleton — the header used to sit over a BLANK page for
        // the whole cold fetch (several seconds through the proxy on a cold
        // backend); pulse cards mirror the real card geometry
        <div className="grid grid-cols-2 gap-3.5" aria-hidden data-testid="library-skeleton">
          {[0, 1, 2, 3].map((i) => (
            <div
              key={i}
              className="animate-pulse rounded-[14px] border border-line bg-panel p-5"
            >
              <div className="h-[15px] w-2/5 rounded bg-line" />
              <div className="mb-3.5 mt-2 h-[12px] w-3/5 rounded bg-line" />
              <div className="mb-3 h-[14px] w-full rounded bg-line" />
              <div className="h-[13px] w-4/5 rounded bg-line" />
            </div>
          ))}
        </div>
      )}

      {runs && runs.length === 0 && (
        <div className="mt-10 rounded-[14px] border border-dashed border-line-hover px-6 py-10 text-center">
          <p className="text-[14.5px] text-ink-2">
            Nothing here yet. The first thing this tool will do with your idea is try to break it.
          </p>
          <Link
            href="/"
            className="mt-4 inline-block rounded-[10px] bg-trust px-5 py-2.5 text-[14px] font-bold text-on-accent"
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
              <div className="flex items-center gap-2 font-mono text-[15px] font-medium">
                {r.status === "running" && (
                  <span className="inline-block h-[8px] w-[8px] shrink-0 animate-pin-pulse rounded-full bg-trust" />
                )}
                {r.name}
              </div>
              <div className="mb-3.5 mt-1 font-mono text-[12px] text-ink-4">{r.meta}</div>
              {r.autoNote && (
                <div className="mb-2 inline-block rounded-full border border-trust-border px-2.5 py-0.5 font-mono text-[10.5px] text-trust">
                  ↻ {r.autoNote}
                </div>
              )}
              {r.supersededBy && (
                <div className="mb-2 inline-block rounded-full border border-line px-2.5 py-0.5 font-mono text-[10.5px] text-ink-4">
                  superseded — re-ran automatically on new data
                </div>
              )}
              {r.status === "running" ? (
                <div className="flex min-h-[72px] flex-col justify-center gap-1.5">
                  <div className="font-mono text-[12px] tracking-[.1em] text-trust">
                    GAUNTLET IN PROGRESS — STAGE {Math.min((r.stage ?? 0) + 1, 6)} OF 6
                  </div>
                  <div className="text-[13px] text-ink-4">Open to watch it live.</div>
                </div>
              ) : (
                <>
                  <TrustBandCard band={r.band} marker={r.marker} withheld={r.kind === "refusal"} />
                  <div className="text-[13.5px] italic leading-[1.55] text-ink-2">
                    {settings.verbiage === "retail" && r.quoteRetail ? r.quoteRetail : r.quote}
                  </div>
                </>
              )}
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
