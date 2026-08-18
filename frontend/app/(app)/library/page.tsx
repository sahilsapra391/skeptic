"use client";

/**
 * Strategy Library — sorted by trust, not by return. Each card carries the
 * miniaturized signature element. The empty state teaches the philosophy in
 * one line and routes to New Analysis.
 */

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import { listRuns } from "@/lib/api";
import { useSettings } from "@/lib/settings";
import type { RunSummary } from "@/lib/types";

import { DemoBadge, Disclaimer } from "@/components/disclaimer";
import { TrustBandCard } from "@/components/verdict/trust-band";

/** V-12: group a family — each root followed immediately by its variants in
 * ordinal order. Roots keep the listing's own (newest-first) order; a variant
 * whose root is absent from the listing stands alone, badge intact.
 *
 * LOAD-BEARING INVARIANT (V-45): a chain never re-roots, so every descendant
 * of R carries `rootRunId === R.id` regardless of depth and `byRoot` is flat.
 * Family expansion therefore happens only in the main loop's push branch and
 * never recurses. If re-rooting is ever introduced — a "promote to root"
 * action, a repair script, a migration — variants of a variant would be
 * skipped by the guard and pushed by no family pass, vanishing from the
 * Library with no error. Any change there must revisit this function.
 */
function groupFamilies(runs: RunSummary[]): RunSummary[] {
  const byRoot = new Map<string, RunSummary[]>();
  // one pass, and one id set — the membership test below was an O(n) scan
  // inside the loop, on a list that re-renders every 4s while a run is live
  const ids = new Set(runs.map((r) => r.id));
  for (const r of runs) {
    if (r.variantOrdinal != null && r.rootRunId) {
      const list = byRoot.get(r.rootRunId) ?? [];
      list.push(r);
      byRoot.set(r.rootRunId, list);
    }
  }
  for (const list of byRoot.values()) {
    list.sort((a, b) => (a.variantOrdinal ?? 0) - (b.variantOrdinal ?? 0));
  }
  const out: RunSummary[] = [];
  const placed = new Set<string>();
  for (const r of runs) {
    if (placed.has(r.id)) continue;
    // a variant renders under its root's pass — unless the root is not in
    // this listing at all, in which case it stands alone. (The old
    // `byRoot.has(r.rootRunId)` guard here was dead: a variant with a
    // rootRunId was itself just inserted under that key.)
    if (r.variantOrdinal != null && r.rootRunId && ids.has(r.rootRunId)) continue;
    out.push(r);
    placed.add(r.id);
    for (const v of byRoot.get(r.id) ?? []) {
      if (!placed.has(v.id)) {
        out.push(v);
        placed.add(v.id);
      }
    }
  }
  return out;
}

export default function LibraryPage() {
  const settings = useSettings();
  const [runs, setRuns] = useState<RunSummary[] | null>(null);
  // V-180: grouping is derived, not recomputed per render — the listing polls
  // every 4s while any run is in progress
  const grouped = useMemo(() => (runs ? groupFamilies(runs) : []), [runs]);
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
            href="/new"
            className="mt-4 inline-block rounded-[10px] bg-trust px-5 py-2.5 text-[14px] font-bold text-on-accent"
          >
            New analysis →
          </Link>
        </div>
      )}

      {runs && runs.length > 0 && (
        <div className="grid grid-cols-2 gap-3.5">
          {grouped.map((r) => (
            <div
              key={r.id}
              className="group relative rounded-[14px] border border-line bg-panel p-5 hover:border-line-hover"
            >
              {/* the whole card navigates, but as an overlay SIBLING of the
                  action rather than its ancestor: a <button> inside an <a> is
                  invalid HTML and leaves the accessibility tree malformed —
                  screen readers announce a link containing a button, and the
                  inner activation gets swallowed. Card content sits above the
                  overlay; only the action needs its own stacking context. */}
              <Link
                href={`/runs/${r.id}`}
                aria-label={`Open ${r.name}`}
                className="absolute inset-0 z-0 rounded-[14px]"
              />
              <div className="relative z-10 flex items-center gap-2 font-mono text-[15px] font-medium">
                {r.status === "running" && (
                  <span className="inline-block h-[8px] w-[8px] shrink-0 animate-pin-pulse rounded-full bg-trust" />
                )}
                {r.name}
              </div>
              <div className="relative z-10 mb-3.5 mt-1 flex items-baseline justify-between gap-2">
                <span className="font-mono text-[12px] text-ink-4">{r.meta}</span>
                {r.status !== "running" && !r.example && !r.demo && (
                  // V-173: a sibling of the card's overlay link, so it needs no
                  // click interception — and a Link, not window.location, so it
                  // routes client-side like every other navigation here rather
                  // than tearing down the SPA and the 30s listing cache
                  <Link
                    href={`/new?variant=${r.id}`}
                    className="shrink-0 rounded-full border border-line px-2.5 py-0.5 font-mono text-[10.5px] text-ink-4 hover:border-trust-border hover:text-trust"
                  >
                    run a variant ›
                  </Link>
                )}
              </div>
              {r.example && (
                <div className="relative z-10 mb-2 inline-block rounded-full border border-trust-border bg-trust-dim px-2.5 py-0.5 font-mono text-[10.5px] text-trust">
                  EXAMPLE RUN — a showcase result, not yours
                </div>
              )}
              {r.autoNote && (
                <div className="relative z-10 mb-2 inline-block rounded-full border border-trust-border px-2.5 py-0.5 font-mono text-[10.5px] text-trust">
                  ↻ {r.autoNote}
                </div>
              )}
              {r.supersededBy && (
                <div className="relative z-10 mb-2 inline-block rounded-full border border-line px-2.5 py-0.5 font-mono text-[10.5px] text-ink-4">
                  superseded — re-ran automatically on new data
                </div>
              )}
              {/* V-12: the ordinal badge is what tells same-name variants
                  apart (V-80/V-174) — lineage register, navigation only */}
              {r.variantOrdinal != null && (
                <div className="relative z-10 mb-2 inline-block rounded-full border border-trust-border px-2.5 py-0.5 font-mono text-[10.5px] text-trust">
                  ↳ variant {r.variantOrdinal}
                </div>
              )}
              {r.status === "running" ? (
                <div className="relative z-10 flex min-h-[72px] flex-col justify-center gap-1.5">
                  <div className="font-mono text-[12px] tracking-[.1em] text-trust">
                    GAUNTLET IN PROGRESS — STAGE {Math.min((r.stage ?? 0) + 1, 6)} OF 6
                  </div>
                  <div className="text-[13px] text-ink-4">Open to watch it live.</div>
                </div>
              ) : (
                <div className="relative z-10">
                  <TrustBandCard band={r.band} marker={r.marker} withheld={r.kind === "refusal"} />
                  <div className="text-[13.5px] italic leading-[1.55] text-ink-2">
                    {settings.verbiage === "retail" && r.quoteRetail ? r.quoteRetail : r.quote}
                  </div>
                </div>
              )}
            </div>
          ))}
          <Link
            href="/new"
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
