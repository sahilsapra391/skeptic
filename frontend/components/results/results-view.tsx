"use client";

/**
 * Results / verdict screen body (design: "Results / verdict"). Verdict-first:
 * the Verdict Block owns the top; the equity curve lives below the fold of
 * attention. In the refusal state everything below the block renders dimmed
 * under the "UNBLESSED OUTPUT" rule.
 *
 * P/L green/red appears ONLY in: trade-log P/L column, walk-forward window
 * bars, the drawdown subchart and the MAX DD tile. Never in the verdict.
 */

import { useRef, useState } from "react";
import clsx from "clsx";

import { ApiError, askRun } from "@/lib/api";
import { useSettings } from "@/lib/settings";
import type { NullableSeriesPoint, RunPayload, SeriesPoint } from "@/lib/types";

import { DemoBadge, Disclaimer } from "@/components/disclaimer";
import { Hint } from "@/components/hint";
import { VerdictBlock } from "@/components/verdict/verdict-block";

const PANEL = "rounded-[14px] border border-line bg-panel";
const PANEL_TITLE = "font-mono text-[11.5px] font-medium tracking-[.12em] text-ink-4";

/** Plain-English one-liners for every stat surface (* = unblessed);
 * [institutional, retail] — the verbiage setting picks the register. */
type HintPair = [string, string];

const METRIC_HINTS: Record<string, HintPair> = {
  CAGR: [
    "Compound annual growth rate — how fast the account grew per year, on average.",
    "How fast the account grew per year, on average.",
  ],
  SHARPE: [
    "Return earned per unit of risk taken. Under ~1 is weak; higher is better.",
    "Reward earned for the risk taken. Under about 1 is weak; higher is better.",
  ],
  SORTINO: [
    "Like Sharpe, but only counts downside swings as risk — upside isn't punished.",
    "Like the risk score, but only the bad swings count against it.",
  ],
  "MAX DD": [
    "Max drawdown — the deepest peak-to-trough loss the account suffered.",
    "The deepest fall from a high point — how far down you'd have been at the worst moment.",
  ],
  "WIN RATE": [
    "Share of closed trades that made money.",
    "How many finished trades made money.",
  ],
  "P·FACTOR": [
    "Profit factor — total gains divided by total losses. Above 1 = net profitable.",
    "All the wins divided by all the losses. Above 1 means net profitable.",
  ],
};

const HINT_EQUITY: HintPair = [
  "Account value over time, after commissions and slippage. The shaded strip is " +
    "out-of-sample history the strategy wasn't tuned on; the red line below is " +
    "drawdown — how far the account sat below its previous peak.",
  "Your account value over time, after costs. The shaded part is data the strategy " +
    "never saw during testing; the red line shows how far below its best the account was.",
];
const HINT_OOS: HintPair = [
  "The last 30% of history is judged separately from the first 70%. A real edge " +
    "holds up on data it never saw; a curve-fit one collapses there.",
  "We hide the most recent 30% of history, then check whether the strategy still " +
    "works there. A real edge does; an over-tuned one falls apart.",
];
const HINT_WF: HintPair = [
  "P/L in rolling ~2-month windows. A real edge wins in most windows — not just " +
    "one lucky stretch that dominates the total.",
  "Profit and loss in rolling two-month chunks. A real edge wins in most chunks — " +
    "not one lucky streak carrying everything.",
];
const HINT_MC: HintPair = [
  "The trade order reshuffled 1,000 times to show the range of outcomes luck alone " +
    "could produce. If many reshuffles lose money, the original path was fortunate.",
  "We shuffled the order of the trades 1,000 times to see how much was luck. " +
    "If lots of shuffles lose money, the original run got lucky.",
];
const HINT_SENS: HintPair = [
  "Each parameter nudged ±20% and the backtest re-run. Brighter = better Sharpe. " +
    "A real edge survives nudges (plateau); a fragile one collapses (cliff).",
  "We nudged every setting up and down 20% and re-ran the whole test. Brighter = " +
    "better result. A real edge survives nudges; a fragile one falls apart.",
];
/** Retail register: same tiles, everyday names. */
const RETAIL_METRIC_LABEL: Record<string, string> = {
  CAGR: "YEARLY GROWTH",
  SHARPE: "RISK SCORE",
  SORTINO: "DOWNSIDE SCORE",
  "MAX DD": "WORST DIP",
  "WIN RATE": "WIN RATE",
  "P·FACTOR": "WIN/LOSS RATIO",
};

const HINT_TRADES: HintPair = [
  "Every simulated fill, priced at bid/ask plus slippage — never mid. Skipped " +
    "entries are listed separately with the reason each was refused.",
  "Every simulated trade, priced at real buy/sell quotes plus slippage. Skipped " +
    "entries are listed separately with the reason each one was refused.",
];

const HINT_RECS: HintPair = [
  "Each suggestion comes from this run's own gauntlet numbers — the ±20% sweeps " +
    "really re-ran the engine. Nothing here is opinion, and acting on one starts " +
    "a new trial that the deflated Sharpe will count against you.",
  "Every suggestion comes from tests we actually ran on this exact strategy — " +
    "nothing is opinion. But each retry makes good-looking numbers a little less " +
    "trustworthy, and the math keeps score.",
];

const pick = (pair: HintPair, retail: boolean) => (retail ? pair[1] : pair[0]);

/** Shape a raw series into SVG polyline points (chart shaping only —
 * the numbers come from the backend untouched). */
function seriesToPoints(
  series: SeriesPoint[],
  height: number,
  pad: number,
  invert: boolean,
): string {
  if (series.length < 2) return "";
  const values = series.map((p) => p.v);
  const lo = Math.min(...values);
  const hi = Math.max(...values);
  const span = hi - lo || 1;
  return series
    .map((p, i) => {
      const x = (i / (series.length - 1)) * 860;
      const frac = (p.v - lo) / span;
      const y = invert ? pad + frac * (height - 2 * pad) : pad + (1 - frac) * (height - 2 * pad);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
}

function MetricTiles({ run, retailMode }: { run: RunPayload; retailMode: boolean }) {
  return (
    <div className="mt-5 grid grid-cols-6 gap-3">
      {run.mtiles.map((m, i) => {
        const bare = m.l.replace(/\*$/, "");
        const star = m.l.endsWith("*") ? "*" : "";
        const label = retailMode ? (RETAIL_METRIC_LABEL[bare] ?? bare) + star : m.l;
        return (
          <div key={m.l} className="rounded-xl border border-line bg-panel p-4">
            <div
              className={clsx(
                "font-mono text-[24px] font-semibold",
                m.neg ? "text-pl-neg" : "text-ink",
              )}
            >
              {m.v}
            </div>
            <div className="mt-1.5 flex items-center justify-between gap-1">
              <span className="font-mono text-[10.5px] font-medium tracking-[.08em] text-ink-4">
                {label}
              </span>
              <Hint
                text={METRIC_HINTS[bare] ? pick(METRIC_HINTS[bare], retailMode) : m.l}
                align={i >= 4 ? "right" : "center"}
              />
            </div>
          </div>
        );
      })}
    </div>
  );
}

function fmtDollars(v: number): string {
  return `$${Math.round(v).toLocaleString()}`;
}

function fmtDate(iso: string): string {
  const d = new Date(iso + "T00:00:00");
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "2-digit" });
}

function EquityChart({ run, retailMode }: { run: RunPayload; retailMode: boolean }) {
  const series = run.equitySeries ?? [];
  const dd = run.drawdownSeries ?? [];
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const [hover, setHover] = useState<number | null>(null);

  const equityPoints = series.length
    ? seriesToPoints(series, 200, 14, false)
    : run.equityPoints;
  const drawdownPoints = dd.length ? seriesToPoints(dd, 54, 5, true) : run.drawdownPoints;
  const startLabel = series.length
    ? `${fmtDollars(series[0].v)} start · net of costs`
    : "$25k start · net of costs";

  const values = series.map((p) => p.v);
  const lo = values.length ? Math.min(...values) : 0;
  const hi = values.length ? Math.max(...values) : 0;
  const span = hi - lo || 1;
  const xFor = (i: number) => (i / Math.max(series.length - 1, 1)) * 860;
  const yFor = (v: number) => 14 + (1 - (v - lo) / span) * (200 - 28);

  const onMove = (e: React.MouseEvent) => {
    if (!series.length || !wrapRef.current) return;
    const rect = wrapRef.current.getBoundingClientRect();
    const frac = Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width));
    setHover(Math.round(frac * (series.length - 1)));
  };

  const h = hover !== null && series[hover] ? hover : null;
  const hoverFrac = h !== null ? h / Math.max(series.length - 1, 1) : 0;
  const inOos = h !== null && run.oosSplitDate ? series[h].t > run.oosSplitDate : false;

  return (
    <div className={clsx(PANEL, "mt-3.5 px-5 py-4")}>
      <div className="mb-2.5 flex justify-between">
        <span className={clsx(PANEL_TITLE, "flex items-center gap-2")}>
          {run.oosShadeX < 860
            ? retailMode
              ? "ACCOUNT VALUE — UNSEEN DATA SHADED"
              : "EQUITY — OUT-OF-SAMPLE SHADED"
            : retailMode
              ? "ACCOUNT VALUE"
              : "EQUITY"}
          <Hint text={pick(HINT_EQUITY, retailMode)} />
        </span>
        <span className="font-mono text-[12px] text-ink-4">{startLabel}</span>
      </div>
      <div
        ref={wrapRef}
        className="relative cursor-crosshair"
        onMouseMove={onMove}
        onMouseLeave={() => setHover(null)}
      >
        <svg width="100%" viewBox="0 0 860 200" className="block">
          {run.oosShadeX < 860 && (
            <>
              <rect x={run.oosShadeX} y="0" width={860 - run.oosShadeX} height="200" fill="var(--oos-shade)" />
              <text x={run.oosShadeX + 8} y="14" fill="var(--ink-5)" fontSize="10" fontFamily="var(--font-plex-mono)">
                {retailMode ? "UNSEEN DATA →" : "OUT-OF-SAMPLE →"}
              </text>
            </>
          )}
          <line x1="0" y1="50" x2="860" y2="50" stroke="var(--grid)" />
          <line x1="0" y1="100" x2="860" y2="100" stroke="var(--grid)" />
          <line x1="0" y1="150" x2="860" y2="150" stroke="var(--grid)" />
          {series.length > 0 && (
            <>
              <text x="4" y="12" fill="var(--ink-5)" fontSize="10" fontFamily="var(--font-plex-mono)">
                {fmtDollars(hi)}
              </text>
              <text x="4" y="196" fill="var(--ink-5)" fontSize="10" fontFamily="var(--font-plex-mono)">
                {fmtDollars(lo)}
              </text>
            </>
          )}
          <polyline points={equityPoints} fill="none" stroke="var(--chart-bright)" strokeWidth="1.8" />
          {h !== null && (
            <>
              <line x1={xFor(h)} y1="0" x2={xFor(h)} y2="200" stroke="var(--crosshair)" strokeWidth="1" />
              <circle cx={xFor(h)} cy={yFor(series[h].v)} r="3.5" fill="var(--chart-bright)" />
            </>
          )}
        </svg>
        <svg width="100%" viewBox="0 0 860 54" className="mt-1.5 block">
          <line x1="0" y1="6" x2="860" y2="6" stroke="var(--grid)" />
          <polyline points={drawdownPoints} fill="none" stroke="var(--pl-neg)" strokeWidth="1.3" />
          {h !== null && (
            <line x1={xFor(h)} y1="0" x2={xFor(h)} y2="54" stroke="var(--crosshair)" strokeWidth="1" />
          )}
        </svg>
        {h !== null && (
          <div
            className="pointer-events-none absolute top-1 z-10 rounded-[9px] border border-line bg-raised px-3 py-2 font-mono text-[11.5px] leading-[1.6] shadow-[var(--shadow-pop)]"
            style={
              hoverFrac > 0.62
                ? { right: `${(1 - hoverFrac) * 100}%`, marginRight: 10 }
                : { left: `${hoverFrac * 100}%`, marginLeft: 10 }
            }
          >
            <div className="text-ink-4">
              {fmtDate(series[h].t)}
              {inOos && <span className="ml-1.5 text-trust">OOS</span>}
            </div>
            <div className="text-ink">{fmtDollars(series[h].v)}</div>
            {dd[h] && <div className="text-pl-neg">drawdown −{dd[h].v.toFixed(1)}%</div>}
          </div>
        )}
      </div>
      <div className="mt-1.5 flex items-baseline justify-between">
        <span className="font-mono text-[10.5px] text-ink-4">
          drawdown — P/L red lives only here, never in the verdict
        </span>
        {series.length > 0 && (
          <span className="font-mono text-[10px] text-ink-4">
            {fmtDate(series[0].t)}
            {run.oosSplitDate ? ` · split ${fmtDate(run.oosSplitDate)} · ` : " · "}
            {fmtDate(series[series.length - 1].t)}
          </span>
        )}
      </div>
    </div>
  );
}

const HINT_GREEKS: HintPair = [
  "Aggregate exposure of all open positions at each day's marks: delta/gamma in " +
    "share-equivalents, theta in $/day, vega in $ per vol point. Gaps are days " +
    "the data carried no greek — shown as holes, never interpolated.",
  "How exposed the open positions were each day, in plain units. Gaps mean the " +
    "data had no greeks that day — we show a hole instead of making one up.",
];

/** Null-gap polylines: one segment per contiguous run of known values, so a
 * missing-greeks day renders as a hole in the line (honest gap). */
function nullableSegments(
  series: NullableSeriesPoint[],
  height: number,
  pad: number,
): { points: string[]; dots: { x: number; y: number }[]; last: number | null } {
  const known = series.filter((p): p is SeriesPoint => p.v !== null);
  if (known.length === 0) return { points: [], dots: [], last: null };
  const values = known.map((p) => p.v);
  const lo = Math.min(...values, 0);
  const hi = Math.max(...values, 0);
  const span = hi - lo || 1;
  const y = (v: number) => pad + (1 - (v - lo) / span) * (height - 2 * pad);
  const x = (i: number) => (i / Math.max(series.length - 1, 1)) * 860;

  const points: string[] = [];
  const dots: { x: number; y: number }[] = [];
  let current: string[] = [];
  let currentStart = -1;
  const flush = (endIdx: number) => {
    if (current.length > 1) points.push(current.join(" "));
    else if (current.length === 1) {
      // an isolated known day between gaps still deserves a mark
      const v = series[currentStart].v as number;
      dots.push({ x: x(endIdx - 1), y: y(v) });
    }
    current = [];
    currentStart = -1;
  };
  series.forEach((p, i) => {
    if (p.v === null) {
      flush(i);
    } else {
      if (currentStart < 0) currentStart = i;
      current.push(`${x(i).toFixed(1)},${y(p.v).toFixed(1)}`);
    }
  });
  flush(series.length);
  return { points, dots, last: known[known.length - 1].v };
}

function GreeksPanel({ run, retailMode }: { run: RunPayload; retailMode: boolean }) {
  const gs = run.greeksSeries;
  const liq = run.liquidity;
  const conc = run.concentration;
  if (!gs || gs.delta.length === 0) return null;

  const rows: { key: keyof typeof gs; label: string; unit: string }[] = [
    { key: "delta", label: "Δ DELTA", unit: "share-eq" },
    { key: "gamma", label: "Γ GAMMA", unit: "share-eq /$" },
    { key: "theta", label: "Θ THETA", unit: "$/day" },
    { key: "vega", label: "V VEGA", unit: "$/vol-pt" },
  ];

  const pct = (v: number | null) => (v === null ? "—" : `${Math.round(v * 100)}%`);

  return (
    <div className={clsx(PANEL, "mt-3.5 px-5 py-4")}>
      <div className="mb-2.5 flex justify-between">
        <span className={clsx(PANEL_TITLE, "flex items-center gap-2")}>
          {retailMode ? "OPEN-POSITION EXPOSURE, DAY BY DAY" : "PORTFOLIO GREEKS — DAILY MARKS"}
          <Hint text={pick(HINT_GREEKS, retailMode)} />
        </span>
        <span className="font-mono text-[10.5px] text-ink-4">
          gaps = no greeks that day, never interpolated
        </span>
      </div>
      <div className="flex flex-col gap-1">
        {rows.map(({ key, label, unit }) => {
          const { points, dots, last } = nullableSegments(gs[key], 40, 5);
          return (
            <div key={key} className="grid grid-cols-[108px_1fr_120px] items-center gap-2.5">
              <span className="font-mono text-[10.5px] tracking-[.08em] text-ink-4">{label}</span>
              <svg width="100%" viewBox="0 0 860 40" className="block" preserveAspectRatio="none">
                <line x1="0" y1="20" x2="860" y2="20" stroke="var(--grid)" />
                {points.map((seg) => (
                  <polyline
                    key={seg.slice(0, 24)}
                    points={seg}
                    fill="none"
                    stroke="var(--chart-bright)"
                    strokeWidth="1.2"
                  />
                ))}
                {dots.map((d) => (
                  <circle
                    key={`${d.x}-${d.y}`}
                    cx={d.x.toFixed(1)}
                    cy={d.y.toFixed(1)}
                    r="1.6"
                    fill="var(--chart-bright)"
                  />
                ))}
              </svg>
              <span className="text-right font-mono text-[11px] text-ink-3">
                {last === null ? "—" : last.toLocaleString()}{" "}
                <span className="text-ink-4">{unit}</span>
              </span>
            </div>
          );
        })}
      </div>
      {liq && (
        <div className="mt-3 border-t border-line-softer pt-2.5 font-mono text-[11px] text-ink-3">
          fills {liq.option_leg_fills} · median spread{" "}
          {liq.median_spread_pct === null
            ? "—"
            : `${(liq.median_spread_pct * 100).toFixed(1)}% of mid`}{" "}
          · {pct(liq.penalized_share)} thin-penalized · {pct(liq.unknown_liquidity_share)}{" "}
          liquidity unknown · {liq.skipped_illiquid} refused ·{" "}
          <span className="text-ink-4">
            gates: spread ≤ {liq.max_spread_pct}% · OI ≥ {liq.min_open_interest} · mode{" "}
            {liq.mode}
          </span>
        </div>
      )}
      {conc?.flagged && conc.note && (
        <div className="mt-2 font-mono text-[11px] text-warn">⚠ concentration: {conc.note}</div>
      )}
    </div>
  );
}

function ReceiptBanner({ run }: { run: RunPayload }) {
  const [busy, setBusy] = useState(false);
  const receipts = run.receipts ?? [];
  const latest = receipts.length ? receipts[receipts.length - 1] : null;

  const replay = async () => {
    setBusy(true);
    try {
      const { replayRun } = await import("@/lib/api");
      const r = await replayRun(run.id);
      window.location.href = `/runs/${r.run_id}`;
    } catch {
      setBusy(false);
    }
  };

  if (!latest && !run.replayEligible) return null;

  const fmtS = (v: number | null) => (v === null ? "—" : v.toFixed(2));

  return (
    <div className="mt-3">
      {latest && (
        <div
          className={clsx(
            "rounded-[14px] border px-5 py-3.5",
            latest.worse ? "border-warn/60 bg-panel" : "border-line bg-panel",
          )}
        >
          {latest.worse && (
            // owner amendment 4: the disagreement is PROMINENT and lowers
            // the SHOWN confidence — the stored verdict above is untouched
            <div className="mb-1.5 font-mono text-[11.5px] font-medium tracking-[.12em] text-warn">
              ⚠ THE 5-MIN REPLAY DISAGREES — READ THE VERDICT ABOVE WITH REDUCED CONFIDENCE
            </div>
          )}
          <div className="font-mono text-[12.5px] text-ink-2">
            RECEIPT — the daily backtest promised Sharpe {fmtS(latest.daily_sharpe)};
            the 5-min replay says {fmtS(latest.five_min_sharpe)}.{" "}
            <a href={`/runs/${latest.replay_run_id}`} className="text-trust underline">
              see the replay
            </a>
            {Object.keys(latest.fill_sources ?? {}).length > 0 && (
              <span className="text-ink-4">
                {" "}· replay fills:{" "}
                {Object.entries(latest.fill_sources)
                  .map(([k, v]) => `${k} ${v}`)
                  .join(", ")}
              </span>
            )}
          </div>
        </div>
      )}
      {!latest && run.replayEligible && (
        <button
          onClick={replay}
          disabled={busy}
          className="rounded-full border border-line px-4 py-1.5 font-mono text-[11.5px] text-ink-3 hover:border-trust-border hover:text-trust disabled:opacity-50"
        >
          {busy ? "queuing replay…" : "↻ replay at 5-min (verdict receipt)"}
        </button>
      )}
    </div>
  );
}

function HonestyPanels({ run, retailMode }: { run: RunPayload; retailMode: boolean }) {
  const h = run.honesty;
  const notes =
    retailMode && run.retail ? run.retail.notes : h.notes;
  return (
    <div className="mt-3.5 grid grid-cols-2 gap-3">
      <div className={clsx(PANEL, "px-5 py-4")}>
        <div className={clsx(PANEL_TITLE, "mb-3 flex items-center gap-2")}>
          {retailMode ? "TRAINING DATA VS UNSEEN DATA" : "IN-SAMPLE VS OUT-OF-SAMPLE"}
          <Hint text={pick(HINT_OOS, retailMode)} />
        </div>
        <div className="mb-1 font-mono text-[12.5px] text-ink-3">
          {retailMode ? "training score" : "IS sharpe"} {h.isSharpe}
        </div>
        <div className="mb-2 h-[9px] overflow-hidden rounded-[3px] bg-line-softer">
          <div className="h-full rounded-[3px] bg-chart" style={{ width: h.bar1 }} />
        </div>
        <div className="mb-1 font-mono text-[12.5px] text-ink-3">
          {retailMode ? "unseen-data score" : "OOS sharpe"} {h.oosSharpe}
        </div>
        <div className="h-[9px] overflow-hidden rounded-[3px] bg-line-softer">
          <div className="h-full rounded-[3px] bg-chart" style={{ width: h.bar2 }} />
        </div>
        <div className="mt-3 text-[14px] text-ink-2">{notes[0]}</div>
      </div>

      <div className={clsx(PANEL, "px-5 py-4")}>
        <div className={clsx(PANEL_TITLE, "mb-3 flex items-center gap-2")}>
          {retailMode ? "TIME PERIODS" : "WALK-FORWARD"}
          {h.wf.length ? (retailMode ? ` — ALL ${h.wf.length}` : ` — ${h.wf.length} WINDOWS`) : ""}
          <Hint text={pick(HINT_WF, retailMode)} />
        </div>
        {h.wf.length > 0 ? (
          // bars flex to span the panel whatever the window count — the full
          // tested history reads as complete rather than half-empty
          <div className="flex h-16 items-end gap-[3px]">
            {h.wf.map((w, i) => (
              <div
                key={i}
                title={w.t}
                className={clsx(
                  "min-w-[2px] flex-1 rounded-t-[2px] transition-opacity",
                  w.pos ? "bg-pl-pos" : "bg-pl-neg",
                  w.t && "cursor-help hover:opacity-75",
                )}
                style={{ height: `${w.h}px` }}
              />
            ))}
          </div>
        ) : (
          <div className="flex h-16 items-center font-mono text-[12px] text-ink-4">—</div>
        )}
        <div className="mt-3 text-[14px] text-ink-2">{notes[1]}</div>
      </div>

      <div className={clsx(PANEL, "px-5 py-4")}>
        <div className={clsx(PANEL_TITLE, "mb-3 flex items-center gap-2")}>
          {retailMode
            ? run.mc.p50
              ? "LUCK TEST — 1,000 RESHUFFLES"
              : "LUCK TEST"
            : run.mc.p50
              ? "MONTE CARLO — 1,000 RESAMPLES"
              : "MONTE CARLO"}
          <Hint text={pick(HINT_MC, retailMode)} />
        </div>
        <svg width="100%" viewBox="0 0 400 100" className="block overflow-visible">
          {/* three of the 1,000 reshuffled equity paths: the lucky one, the
              middle one, the unlucky one. Labels live in the legend below, so
              nothing is written over the lines. */}
          <polyline points={run.mc.p95} fill="none" stroke="var(--chart-mid)" strokeWidth="1.2" />
          <polyline points={run.mc.p50} fill="none" stroke="var(--chart)" strokeWidth="1.7" />
          <polyline points={run.mc.p05} fill="none" stroke="var(--chart-mid)" strokeWidth="1.2" />
          {([run.mc.p95, run.mc.p50, run.mc.p05] as const).map((pts, i) => {
            const [x, y] = (pts.trim().split(" ").pop() ?? "").split(",").map(Number);
            if (Number.isNaN(x) || Number.isNaN(y)) return null;
            return (
              <circle key={i} cx={x} cy={y} r="2.4" fill={i === 1 ? "var(--chart)" : "var(--chart-mid)"} />
            );
          })}
        </svg>
        {run.mcTerm && run.mc.p50 && (
          // plain-language legend: what $ each of the three paths ended at, so
          // a reader who's never seen a percentile can still read the range
          <div className="mt-3 grid grid-cols-3 gap-2 font-mono text-[11px]">
            {(
              [
                [retailMode ? "good case" : "95th pct", run.mcTerm.p95, "var(--chart-mid)"],
                [retailMode ? "most likely" : "median", run.mcTerm.p50, "var(--chart)"],
                [retailMode ? "bad case" : "5th pct", run.mcTerm.p05, "var(--chart-mid)"],
              ] as const
            ).map(([label, val, color]) => (
              <div key={label} className="flex flex-col gap-1">
                <span className="flex items-center gap-1.5 text-ink-4">
                  <span
                    className="inline-block h-[3px] w-3.5 rounded-full"
                    style={{ background: color }}
                  />
                  {label}
                </span>
                <span className="text-[13px] text-ink">{val}</span>
              </div>
            ))}
          </div>
        )}
        <div className="mt-2.5 text-[14px] text-ink-2">{notes[2]}</div>
      </div>

      <div className={clsx(PANEL, "px-5 py-4")}>
        <div className={clsx(PANEL_TITLE, "mb-3 flex items-center gap-2")}>
          {retailMode
            ? "NUDGE TEST — SETTINGS ±20%"
            : run.sensitivityRows?.length
              ? "SENSITIVITY — ±20% PER PARAMETER"
              : run.sensitivity.length
                ? "SENSITIVITY — Δ 15 → 45"
                : "SENSITIVITY"}
          <Hint text={pick(HINT_SENS, retailMode)} align="right" />
        </div>
        {run.sensitivityDetail?.length ? (
          // real runs: labelled rows, every cell explains itself on hover,
          // the as-specced column is ringed
          <div className="flex flex-col gap-1">
            {run.sensitivityDetail.map((row) => (
              <div key={row.name} className="flex items-center gap-2">
                <span
                  className="w-[110px] shrink-0 truncate font-mono text-[10.5px] text-ink-4"
                  title={row.cls ? `${row.name} — ${row.cls}` : row.name}
                >
                  {row.name}
                  {row.cls ? ` · ${row.cls}` : ""}
                </span>
                <div
                  className="grid flex-1 gap-1"
                  style={{ gridTemplateColumns: `repeat(${row.cells.length}, minmax(0, 1fr))` }}
                >
                  {row.cells.map((cell, ci) => (
                    <div
                      key={ci}
                      title={`${row.name} ${cell.label} → Sharpe ${cell.sharpe}`}
                      className={clsx(
                        "flex h-7 cursor-help items-center justify-center rounded font-mono text-[10px] transition-transform hover:scale-[1.06]",
                        ci === row.base && "ring-1 ring-trust-border",
                        cell.o > 0.55 ? "text-on-accent" : "text-ink-3",
                      )}
                      style={{ background: `rgb(var(--heat-rgb) / ${cell.o})` }}
                    >
                      {cell.label}
                    </div>
                  ))}
                </div>
              </div>
            ))}
            <div className="mt-0.5 flex justify-between pl-[112px] font-mono text-[9.5px] text-ink-4">
              <span>−20%</span>
              <span>as specced (ringed)</span>
              <span>+20%</span>
            </div>
          </div>
        ) : run.sensitivityRows?.length ? (
          // older stored runs: opacity rows without cell detail
          <div className="flex flex-col gap-1">
            {run.sensitivity.map((row, ri) => (
              <div key={ri} className="flex items-center gap-2">
                <span className="w-[104px] shrink-0 truncate font-mono text-[9.5px] text-ink-4">
                  {run.sensitivityRows?.[ri] ?? ""}
                </span>
                <div
                  className="grid flex-1 gap-1"
                  style={{ gridTemplateColumns: `repeat(${row.length}, minmax(0, 1fr))` }}
                >
                  {row.map((opacity, ci) => (
                    <div
                      key={ci}
                      className="h-5 rounded"
                      style={{ background: `rgb(var(--heat-rgb) / ${opacity})` }}
                    />
                  ))}
                </div>
              </div>
            ))}
            <div className="mt-0.5 flex justify-between pl-[112px] font-mono text-[9.5px] text-ink-4">
              <span>−20%</span>
              <span>as specced</span>
              <span>+20%</span>
            </div>
          </div>
        ) : (
          <>
            <div className="grid grid-cols-9 gap-1">
              {run.sensitivity.flatMap((row, ri) =>
                row.map((opacity, ci) => (
                  <div
                    key={`${ri}-${ci}`}
                    className="h-5 rounded"
                    style={{ background: `rgb(var(--heat-rgb) / ${opacity})` }}
                  />
                )),
              )}
            </div>
            {run.sensitivity.length > 0 && (
              <div className="mt-1 flex justify-between font-mono text-[9.5px] text-ink-4">
                <span>Δ.15</span>
                <span>Δ.30</span>
                <span>Δ.45</span>
              </div>
            )}
          </>
        )}
        <div className="mt-2.5 text-[14px] text-ink-2">{notes[3]}</div>
      </div>
    </div>
  );
}

function Recommendations({ run, retailMode }: { run: RunPayload; retailMode: boolean }) {
  const recs =
    retailMode && run.retail ? run.retail.recommendations : run.recommendations;
  if (!recs?.length) return null;
  return (
    <div className={clsx(PANEL, "mt-3.5 px-5 py-4")}>
      <div className={clsx(PANEL_TITLE, "mb-3 flex items-center gap-2")}>
        WHAT WOULD IMPROVE IT — COMPUTED FROM THIS RUN
        <Hint text={pick(HINT_RECS, retailMode)} />
      </div>
      <ul className="flex flex-col gap-2.5">
        {recs.map((rec, i) => (
          <li key={i} className="flex gap-3 text-[14.5px] leading-[1.6] text-ink-2">
            <span className="font-mono text-trust">{String(i + 1).padStart(2, "0")}</span>
            <span>{rec}</span>
          </li>
        ))}
      </ul>
      <div className="mt-3 border-t border-grid pt-2.5 font-mono text-[10.5px] text-ink-4">
        backtest-fit observations, not trading advice — every change re-enters the gauntlet
        as a new trial
      </div>
    </div>
  );
}

function TradeRowLine({ t }: { t: RunPayload["trades"][number] }) {
  return (
    <div
      className={clsx(
        "grid items-baseline gap-2.5 border-t border-grid py-2 font-mono text-[13px]",
        t.skip && "opacity-55",
      )}
      style={{ gridTemplateColumns: "84px 58px 1.3fr 74px 1fr" }}
    >
      <span className="text-ink-4">{t.d}</span>
      <span
        className={clsx(
          t.a === "SKIP" ? "italic text-ink-4" : t.a === "OPEN" ? "text-ink" : "text-ink-3",
        )}
      >
        {t.a}
      </span>
      <span className="text-ink-3">{t.det}</span>
      <span
        className={clsx(
          "text-right",
          t.plSign === "pos" ? "text-pl-pos" : t.plSign === "neg" ? "text-pl-neg" : "text-ink-3",
        )}
      >
        {t.pl}
      </span>
      <span className="text-ink-4">{t.n}</span>
    </div>
  );
}

function TradeLog({ run, retailMode }: { run: RunPayload; retailMode: boolean }) {
  const [open, setOpen] = useState(false);
  const [showSkipped, setShowSkipped] = useState(false);
  const filled = run.trades.filter((t) => !t.skip);
  const skipped = run.trades.filter((t) => t.skip);
  return (
    <div className="mt-3">
      <button
        onClick={() => setOpen((v) => !v)}
        className={clsx(
          PANEL,
          "flex w-full items-center gap-2.5 px-5 py-3.5 text-left font-mono text-[13.5px] text-ink-3 hover:border-line-hover hover:text-ink",
          open && "rounded-b-none",
        )}
      >
        <span>{open ? "▾" : "▸"}</span>
        <span className="flex-1">{run.tradeHeader}</span>
        <Hint text={pick(HINT_TRADES, retailMode)} align="right" />
      </button>
      {open && (
        <div className="rounded-b-xl border border-t-0 border-line bg-panel-deep px-4 pb-2.5 pt-1.5">
          {filled.map((t, i) => (
            <TradeRowLine key={i} t={t} />
          ))}
          {skipped.length > 0 && (
            <button
              onClick={() => setShowSkipped((v) => !v)}
              className="mt-1 flex w-full items-center gap-2 border-t border-grid py-2.5 text-left font-mono text-[11.5px] text-ink-4 hover:text-ink-2"
            >
              <span>{showSkipped ? "▾" : "▸"}</span>
              <span>
                {skipped.length} skipped entr{skipped.length === 1 ? "y" : "ies"} — with reasons
              </span>
            </button>
          )}
          {showSkipped && skipped.map((t, i) => <TradeRowLine key={`s${i}`} t={t} />)}
        </div>
      )}
    </div>
  );
}

export function ResultsView({
  run,
  onEditSpec,
  onNew,
  onBack,
  backLabel,
}: {
  run: RunPayload;
  onEditSpec?: () => void;
  onNew: () => void;
  onBack?: () => void;
  backLabel?: string;
}) {
  const [askText, setAskText] = useState("");
  const [answer, setAnswer] = useState<string | null>(null);
  const [asking, setAsking] = useState(false);
  const settings = useSettings();
  // retail register: same computed numbers, everyday words. Static UI text
  // (titles, tooltips, tile names) follows the setting alone; run-computed
  // text (verdict, notes, recommendations) additionally needs the stored
  // retail block — older runs fall back to institutional there.
  const retailMode = settings.verbiage === "retail";
  const verdict = retailMode && run.retail
    ? {
        ...run.verdict,
        headline: run.retail.headline,
        evidence: run.retail.evidence,
        breaks: run.retail.breaks,
        caveat: run.retail.caveat,
        refusalBody: run.retail.refusalBody ?? run.verdict.refusalBody,
        refusalUnlock: run.retail.refusalUnlock ?? run.verdict.refusalUnlock,
      }
    : run.verdict;

  async function submitAsk() {
    if (!askText.trim() || asking) return;
    setAsking(true);
    try {
      const res = await askRun(run.id, askText);
      setAnswer(res.answer);
    } catch (e) {
      setAnswer(e instanceof ApiError ? e.detail : e instanceof Error ? e.message : "ask failed");
    } finally {
      setAsking(false);
    }
  }

  return (
    <div>
      {onBack && (
        <button
          onClick={onBack}
          className="mb-[18px] text-[13px] text-ink-4 hover:text-ink-3"
        >
          ‹ {backLabel ?? "back"}
        </button>
      )}
      <div className="mb-4 flex items-start gap-3">
        <div>
          <h1 className="mb-1.5 text-[21px] font-[650]">{run.name}</h1>
          <div className="flex items-center gap-2.5">
            <div className="font-mono text-[11.5px] text-ink-4">{run.meta}</div>
            {run.demo && <DemoBadge />}
          </div>
        </div>
        <div className="ml-auto flex shrink-0 gap-2">
          {onEditSpec && (
            <button
              onClick={onEditSpec}
              className="flex items-center gap-1.5 whitespace-nowrap rounded-[10px] border border-line bg-raised-2 px-4 py-2 text-[13px] font-semibold text-ink-2 hover:border-trust-border hover:bg-raised-3 hover:text-ink"
            >
              <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round">
                <path d="M11.1 2.2l2.7 2.7L5.4 13.3l-3.2.5.5-3.2z" />
              </svg>
              Edit spec
            </button>
          )}
          <button
            onClick={onNew}
            className="flex items-center gap-1.5 whitespace-nowrap rounded-[10px] border border-trust-border bg-trust-dim px-4 py-2 text-[13px] font-semibold text-trust hover:bg-trust/15"
          >
            <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round">
              <line x1="8" y1="3" x2="8" y2="13" />
              <line x1="3" y1="8" x2="13" y2="8" />
            </svg>
            New analysis
          </button>
        </div>
      </div>

      <VerdictBlock verdict={verdict} />
      <ReceiptBanner run={run} />

      {run.verdict.refusal && (
        <div className="mb-1 mt-[18px] text-center font-mono text-[10.5px] font-medium tracking-[.18em] text-ink-4">
          — UNBLESSED OUTPUT · MACHINERY CHECK ONLY —
        </div>
      )}

      <div className={clsx(run.verdict.refusal && "opacity-[.38]")}>
        <MetricTiles run={run} retailMode={retailMode} />
        <EquityChart run={run} retailMode={retailMode} />
        <GreeksPanel run={run} retailMode={retailMode} />
        <HonestyPanels run={run} retailMode={retailMode} />
        <Recommendations run={run} retailMode={retailMode} />
        <TradeLog run={run} retailMode={retailMode} />
      </div>

      {answer && (
        <div className={clsx(PANEL, "mt-3.5 rounded-xl px-4 py-3")}>
          <div className="mb-1.5 font-mono text-[10.5px] font-medium tracking-[.12em] text-trust">
            GROUNDED ANSWER
          </div>
          <div className="text-[13.5px] leading-[1.6] text-ink-2">{answer}</div>
        </div>
      )}

      <div className="sticky bottom-2.5 mt-3.5 flex items-center gap-2.5 rounded-[13px] border border-line bg-[var(--overlay-panel)] py-[9px] pl-4 pr-2.5 backdrop-blur-lg">
        <input
          className="flex-1 font-mono text-[15px]"
          placeholder='Ask about this result… "is this just 2020?" · "widen the spread" · "worst month"'
          value={askText}
          onChange={(e) => setAskText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") submitAsk();
          }}
        />
        <button
          onClick={submitAsk}
          disabled={asking}
          className={clsx(
            "rounded-[9px] border px-[13px] py-1.5 text-[13px] font-semibold",
            asking
              ? "cursor-wait border-line text-ink-4"
              : "border-trust-border bg-trust-dim text-trust",
          )}
        >
          {asking ? "thinking…" : "↵"}
        </button>
      </div>
      <Disclaimer />
    </div>
  );
}
