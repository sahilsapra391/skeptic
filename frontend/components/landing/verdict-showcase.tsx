import clsx from "clsx";

import { TrustBandCard } from "@/components/verdict/trust-band";

/**
 * Landing §3 — the verdict showcase ("Refusal is a feature.").
 * Design: docs/design/landing/Skeptic Landing.dc.html option 2a lines 146-188
 * (desktop) / 2b lines 452-479 (mobile). Every number below comes from a REAL
 * stored run — nothing is a fixture:
 *   refusal card + range row 3 → run 42ce700a376f
 *   range row 1               → run 612905835dca
 *   range row 2               → run 7bbf2837653f
 * Trust hue only on this surface — P/L tokens are forbidden here.
 */

type Band = { left: string; width: string };

type RangeRow = {
  /* every row is a real pinned example run and opens the full-run popup */
  runId: string;
  name: string;
  meta: string;
  /* warn is the only non-default meta color the design uses */
  metaWarn?: boolean;
  band: Band | null;
  marker?: string;
  withheld?: boolean;
  quote: string;
};

// the instrument's full range, all real (owner 2026-07-17): a solid pass,
// a big winner, a destructive one, a withheld verdict. Geometry/quotes
// verbatim from each run's stored summary.
const RANGE_ROWS: RangeRow[] = [
  {
    runId: "612905835dca",
    name: "SPY .25Δ put credit spread",
    meta: "505 fills · 5/5 survived",
    band: { left: "70%", width: "30%" },
    marker: "90%",
    quote:
      "Your strategy’s profit depends on a handful of days — 126 days produced half of all gains.",
  },
  {
    runId: "2a4f48d6178e",
    name: "SPY .45Δ short put",
    meta: "281 fills · 5/5 survived · +$71k",
    band: { left: "70%", width: "30%" },
    marker: "90%",
    quote:
      "In the worst 5% of reshuffled trade sequences, the account dropped by 85% at some point.",
  },
  {
    runId: "7bbf2837653f",
    name: "SPY .50Δ long put",
    meta: "1/5 survived",
    metaWarn: true,
    band: { left: "0%", width: "30%" },
    marker: "10%",
    quote:
      "This strategy loses money in every single test — it is not just bad, it is reliably destructive.",
  },
  {
    runId: "42ce700a376f",
    name: "SPY .15Δ short put",
    meta: "12 trades · withheld",
    band: null,
    withheld: true,
    quote:
      "This strategy has only 12 closed trades, which is too few to trust any of its numbers.",
  },
];

function RangeRowCard({ row, onOpen }: { row: RangeRow; onOpen: (runId: string) => void }) {
  const body = (
    <>
      <div className="mb-1.5 flex items-baseline justify-between gap-3 md:mb-2">
        <span className="font-mono text-[11.5px] text-ink-2 md:text-[12px]">{row.name}</span>
        <span
          className={clsx(
            "shrink-0 font-mono text-[10px] md:text-[10.5px]",
            row.metaWarn ? "text-warn" : "text-ink-4",
          )}
        >
          {row.meta}
        </span>
      </div>
      {/* the app's own band component — the landing must never drift from
          the verdict surfaces it's advertising */}
      <TrustBandCard
        band={row.band ?? undefined}
        marker={row.marker ?? undefined}
        withheld={row.withheld}
      />
      <div className="font-serif text-[14px] italic leading-[1.45] text-ink-2 md:text-[15px]">
        {row.quote}
      </div>
    </>
  );
  const shell =
    "block w-full text-left rounded-[14px] border border-line bg-panel px-3.5 pb-2.5 pt-3 md:px-[18px] md:pb-3 md:pt-3.5";
  /* every row opens the full stored run in the landing's popup */
  return (
    <button
      onClick={() => onOpen(row.runId)}
      title="open the full run"
      className={clsx(shell, "transition-colors hover:border-line-hover hover:bg-raised")}
    >
      {body}
    </button>
  );
}

export function VerdictShowcase({ onOpenRun }: { onOpenRun: (runId: string) => void }) {
  return (
    <section
      id="verdict"
      className="border-t border-line-softer px-6 pb-11 pt-[52px] md:px-14 md:pb-16 xl:px-[120px] xl:pt-[88px]"
    >
      <div className="mx-auto max-w-[1440px]">
        <div className="font-mono text-[10.5px] font-medium tracking-[.14em] text-trust md:text-[11.5px]">
          MOST BACKTESTERS TELL YOU WHAT YOU WANT TO HEAR
        </div>
        <h2 className="mb-2.5 mt-3 font-serif text-[27px] font-medium leading-[1.2] md:mb-3 md:mt-3.5 md:max-w-[760px] md:text-[40px] md:leading-[1.15]">
          Refusal is a feature.
        </h2>
        <p className="max-w-[730px] text-[13.5px] leading-[1.6] text-ink-2 md:text-[15px] md:leading-[1.65]">
          Twelve closed trades in a year isn’t evidence — it’s anecdotes. Skeptic ships the
          numbers, withholds the blessing, and shows exactly what unlocks a verdict.
        </p>

        <div className="mt-[22px] grid items-stretch gap-[26px] md:mt-11 md:grid-cols-[58fr_42fr]">
          {/* refusal card — run 42ce700a376f, copy verbatim from the stored verdict.
              the column stretches to the range rail's height and the card grows
              to fill it, so the two sides match (owner 2026-07-17) */}
          <div className="flex flex-col">
            <div
              className="flex flex-1 flex-col rounded-2xl border border-dashed border-trust-border p-5 md:p-7"
              style={{ background: "linear-gradient(180deg,var(--acd),var(--ac-faint))" }}
            >
              <div className="mb-2 flex items-center justify-between gap-3 md:mb-2.5">
                <span className="font-mono text-[10px] font-medium tracking-[.1em] text-trust md:text-[11.5px] md:tracking-[.14em]">
                  VERDICT — THE HONEST READ
                </span>
                <span className="shrink-0 font-mono text-[10px] font-medium text-trust md:text-[12px]">
                  VERDICT WITHHELD
                </span>
              </div>
              <div className="font-serif text-[21px] font-medium leading-[1.3] md:max-w-[640px] md:text-[32px] md:leading-[1.25]">
                12 closed trades is not a sample, yet the strategy claims a perfect win rate and
                zero drawdown.
              </div>
              <p className="mt-3.5 max-w-[640px] text-[14px] leading-[1.65] text-ink-2 md:mt-4 md:text-[15.5px]">
                The spec is valid. The engine ran it. But only 12 trades closed inside the 1-year
                window — below the 15-trade evidence bar. Numbers shown, blessing withheld.
              </p>
              <div className="mt-3.5 rounded-[10px] border border-dashed border-trust-border px-3 py-2.5 md:mt-auto md:px-3.5 md:py-3">
                <div className="mb-2 font-mono text-[10px] tracking-[.12em] text-trust md:mb-2.5 md:text-[11px]">
                  TWO HONEST WAYS TO A VERDICT
                </div>
                {/* trustpill-styled spans, not buttons — the landing can't
                    re-run anything, so no interactive affordance */}
                <div className="flex flex-col gap-1.5 md:flex-row md:flex-wrap md:gap-2">
                  <span className="rounded-full border border-trust-border px-3.5 py-1.5 text-[13px] text-trust">
                    re-run on a longer window — unlocks at ≥ 15 trades (has 12)
                  </span>
                  {/* neutral pill per the mockup — editing the spec is the
                      user's move, not one of the trust-hued re-run offers */}
                  <span className="rounded-full border border-line-hover px-3.5 py-1.5 font-mono text-[12px] text-ink-3">
                    edit the spec — make the entry fire more often
                  </span>
                </div>
              </div>
            </div>
            <div className="mt-3 flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1 font-mono text-[11px] text-ink-4">
              <span>SPY .15Δ short put · 12 trades in a 1-year window · ran Jul 14 ’26</span>
              <button
                onClick={() => onOpenRun("42ce700a376f")}
                className="text-ink-3 transition-colors hover:text-ink"
              >
                read the full run →
              </button>
            </div>
          </div>

          {/* range rail — recent verdicts across the whole trust spectrum */}
          <div className="flex flex-col gap-2.5">
            <div className="mb-0.5 font-mono text-[11px] tracking-[.12em] text-ink-3">
              THE RANGE — RECENT VERDICTS
            </div>
            {RANGE_ROWS.map((row) => (
              <RangeRowCard key={row.runId} row={row} onOpen={onOpenRun} />
            ))}
            <div className="mt-1 font-mono text-[11px] leading-[1.7] text-ink-4">
              refused, damned, or blessed — whatever the evidence supports.
            </div>
          </div>
        </div>

        <div className="mx-auto mt-10 max-w-[640px] text-center font-mono text-[11px] leading-[1.8] text-ink-4">
          <div>rendered from real stored runs — every number computed, none decorative</div>
          <div className="text-ink-5">
            Research tool, not financial advice.
          </div>
        </div>
      </div>
    </section>
  );
}
