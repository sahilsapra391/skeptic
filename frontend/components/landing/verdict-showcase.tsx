import Link from "next/link";
import clsx from "clsx";

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
  name: string;
  meta: string;
  /* warn is the only non-default meta color the design uses */
  metaWarn?: boolean;
  band: Band | null;
  marker?: string;
  withheld?: boolean;
  quote: string;
  /* rows without an href are display-only by design (runs not linked publicly) */
  href?: string;
};

const RANGE_ROWS: RangeRow[] = [
  {
    // run 612905835dca
    name: "SPY .25Δ put credit spread",
    meta: "505 fills · 5/5 survived",
    band: { left: "70%", width: "30%" },
    marker: "90%",
    quote:
      "Your strategy’s profit depends on a handful of days — 126 days produced half of all gains.",
    href: "/runs/612905835dca",
  },
  {
    // run 7bbf2837653f
    name: "SPY .50Δ long put",
    meta: "1/5 survived",
    metaWarn: true,
    band: { left: "0%", width: "30%" },
    marker: "10%",
    quote:
      "This strategy loses money in every single test — it is not just bad, it is reliably destructive.",
  },
  {
    // run 42ce700a376f
    name: "SPY .15Δ short put",
    meta: "12 trades · withheld",
    band: null,
    withheld: true,
    quote:
      "This strategy has only 12 closed trades, which is too few to trust any of its numbers.",
  },
];

/* TrustBand, card variant — geometry verbatim from the DS bundle
 * (_ds_bundle.js:1322-1437): 14px wrap, 2px track at top 6, 10px band at
 * top 2, 3px marker full-height; withheld = dashed empty track. */
function TrustBand({ band, marker, withheld }: Pick<RangeRow, "band" | "marker" | "withheld">) {
  return (
    <div className="relative mb-2.5 h-[14px]">
      <div className="absolute inset-x-0 top-[6px] h-[2px] bg-band-track" />
      {withheld ? (
        <div className="absolute left-[4%] top-[2px] h-[10px] w-[92%] rounded-[4px] border border-dashed border-line-hover" />
      ) : (
        <>
          {band && (
            <div
              className="absolute top-[2px] h-[10px] rounded-[4px] border border-trust-border bg-trust-dim"
              style={{ left: `min(${band.left}, calc(100% - ${band.width}))`, width: band.width }}
            />
          )}
          {marker && (
            <div
              className="absolute top-0 h-[14px] w-[3px] rounded-[2px] bg-trust"
              style={{ left: `min(${marker}, calc(100% - 4px))` }}
            />
          )}
        </>
      )}
    </div>
  );
}

function RangeRowCard({ row }: { row: RangeRow }) {
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
      <TrustBand band={row.band} marker={row.marker} withheld={row.withheld} />
      <div className="font-serif text-[14px] italic leading-[1.45] text-ink-2 md:text-[15px]">
        {row.quote}
      </div>
    </>
  );
  const shell =
    "block rounded-[14px] border border-line bg-panel px-3.5 pb-2.5 pt-3 md:px-[18px] md:pb-3 md:pt-3.5";
  /* hover affordance only on the row that actually links somewhere */
  return row.href ? (
    <Link
      href={row.href}
      className={clsx(shell, "transition-colors hover:border-line-hover hover:bg-raised")}
    >
      {body}
    </Link>
  ) : (
    <div className={shell}>{body}</div>
  );
}

export function VerdictShowcase() {
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

        <div className="mt-[22px] grid items-start gap-[26px] md:mt-11 md:grid-cols-[58fr_42fr]">
          {/* refusal card — run 42ce700a376f, copy verbatim from the stored verdict */}
          <div>
            <div
              className="rounded-2xl border border-dashed border-trust-border p-5 md:p-7"
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
              <div className="mt-3.5 rounded-[10px] border border-dashed border-trust-border px-3 py-2.5 md:mt-[18px] md:px-3.5 md:py-3">
                <div className="mb-2 font-mono text-[10px] tracking-[.12em] text-trust md:mb-2.5 md:text-[11px]">
                  TWO HONEST WAYS TO A VERDICT
                </div>
                {/* trustpill-styled spans, not buttons — the landing can't
                    re-run anything, so no interactive affordance */}
                <div className="flex flex-col gap-1.5 md:flex-row md:flex-wrap md:gap-2">
                  <span className="rounded-full border border-trust-border px-3.5 py-1.5 text-[13px] text-trust">
                    re-run on a longer window — unlocks at ≥ 15 trades (has 12)
                  </span>
                  <span className="rounded-full border border-trust-border px-3.5 py-1.5 text-[13px] text-trust">
                    edit the spec — make the entry fire more often
                  </span>
                </div>
              </div>
            </div>
            <div className="mt-3 flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1 font-mono text-[11px] text-ink-4">
              <span>SPY .15Δ short put · 12 trades in a 1-year window · ran Jul 14 ’26</span>
              <Link
                href="/runs/42ce700a376f"
                className="text-ink-3 transition-colors hover:text-ink"
              >
                read the full run →
              </Link>
            </div>
          </div>

          {/* range rail — recent verdicts across the whole trust spectrum */}
          <div className="flex flex-col gap-2.5">
            <div className="mb-0.5 font-mono text-[11px] tracking-[.12em] text-ink-3">
              THE RANGE — RECENT VERDICTS
            </div>
            {RANGE_ROWS.map((row) => (
              <RangeRowCard key={row.name} row={row} />
            ))}
            <div className="mt-1 font-mono text-[11px] leading-[1.7] text-ink-4">
              refused, damned, or blessed — whatever the evidence supports.
            </div>
          </div>
        </div>

        <div className="mx-auto mt-10 max-w-[640px] text-center font-mono text-[11px] leading-[1.8] text-ink-4">
          <div>rendered from real stored runs — every number computed, none decorative</div>
          <div className="text-ink-5">
            Research tool, not financial advice. Backtests overstate live results.
          </div>
        </div>
      </div>
    </section>
  );
}
