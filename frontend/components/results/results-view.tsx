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
import type { RunPayload, SeriesPoint } from "@/lib/types";

import { DemoBadge, Disclaimer } from "@/components/disclaimer";
import { Hint } from "@/components/hint";
import { VerdictBlock } from "@/components/verdict/verdict-block";

const PANEL = "rounded-[14px] border border-line bg-panel";
const PANEL_TITLE = "font-mono text-[10.5px] font-medium tracking-[.12em] text-ink-4";

/** Plain-English one-liners for every stat surface (* = unblessed). */
const METRIC_HINTS: Record<string, string> = {
  CAGR: "Compound annual growth rate — how fast the account grew per year, on average.",
  SHARPE: "Return earned per unit of risk taken. Under ~1 is weak; higher is better.",
  SORTINO: "Like Sharpe, but only counts downside swings as risk — upside isn't punished.",
  "MAX DD": "Max drawdown — the deepest peak-to-trough loss the account suffered.",
  "WIN RATE": "Share of closed trades that made money.",
  "P·FACTOR": "Profit factor — total gains divided by total losses. Above 1 = net profitable.",
};

const HINT_EQUITY =
  "Account value over time, after commissions and slippage. The shaded strip is " +
  "out-of-sample history the strategy wasn't tuned on; the red line below is " +
  "drawdown — how far the account sat below its previous peak.";
const HINT_OOS =
  "The last 30% of history is judged separately from the first 70%. A real edge " +
  "holds up on data it never saw; a curve-fit one collapses there.";
const HINT_WF =
  "P/L in rolling ~2-month windows. A real edge wins in most windows — not just " +
  "one lucky stretch that dominates the total.";
const HINT_MC =
  "The trade order reshuffled 1,000 times to show the range of outcomes luck alone " +
  "could produce. If many reshuffles lose money, the original path was fortunate.";
const HINT_SENS =
  "Each parameter nudged ±20% and the backtest re-run. Brighter = better Sharpe. " +
  "A real edge survives nudges (plateau); a fragile one collapses (cliff).";
const HINT_TRADES =
  "Every simulated fill, priced at bid/ask plus slippage — never mid. Skipped " +
  "entries are listed separately with the reason each was refused.";

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

function MetricTiles({ run }: { run: RunPayload }) {
  return (
    <div className="mt-[18px] grid grid-cols-6 gap-2.5">
      {run.mtiles.map((m, i) => (
        <div key={m.l} className="rounded-xl border border-line bg-panel p-3">
          <div
            className={clsx(
              "font-mono text-[19px] font-semibold",
              m.neg ? "text-pl-neg" : "text-ink",
            )}
          >
            {m.v}
          </div>
          <div className="mt-1 flex items-center justify-between gap-1">
            <span className="font-mono text-[9.5px] font-medium tracking-[.1em] text-ink-4">
              {m.l}
            </span>
            <Hint
              text={METRIC_HINTS[m.l.replace(/\*$/, "")] ?? m.l}
              align={i >= 4 ? "right" : "center"}
            />
          </div>
        </div>
      ))}
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

function EquityChart({ run }: { run: RunPayload }) {
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
    <div className={clsx(PANEL, "mt-3 px-4 py-3.5")}>
      <div className="mb-2 flex justify-between">
        <span className={clsx(PANEL_TITLE, "flex items-center gap-2")}>
          {run.oosShadeX < 860 ? "EQUITY — OUT-OF-SAMPLE SHADED" : "EQUITY"}
          <Hint text={HINT_EQUITY} />
        </span>
        <span className="font-mono text-[11px] text-ink-4">{startLabel}</span>
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
              <rect x={run.oosShadeX} y="0" width={860 - run.oosShadeX} height="200" fill="rgba(255,255,255,.035)" />
              <text x={run.oosShadeX + 8} y="14" fill="#4a545f" fontSize="10" fontFamily="var(--font-plex-mono)">
                OUT-OF-SAMPLE →
              </text>
            </>
          )}
          <line x1="0" y1="50" x2="860" y2="50" stroke="#20242c" />
          <line x1="0" y1="100" x2="860" y2="100" stroke="#20242c" />
          <line x1="0" y1="150" x2="860" y2="150" stroke="#20242c" />
          {series.length > 0 && (
            <>
              <text x="4" y="12" fill="#4a545f" fontSize="10" fontFamily="var(--font-plex-mono)">
                {fmtDollars(hi)}
              </text>
              <text x="4" y="196" fill="#4a545f" fontSize="10" fontFamily="var(--font-plex-mono)">
                {fmtDollars(lo)}
              </text>
            </>
          )}
          <polyline points={equityPoints} fill="none" stroke="#d7dde3" strokeWidth="1.8" />
          {h !== null && (
            <>
              <line x1={xFor(h)} y1="0" x2={xFor(h)} y2="200" stroke="#3a424d" strokeWidth="1" />
              <circle cx={xFor(h)} cy={yFor(series[h].v)} r="3.5" fill="#d7dde3" />
            </>
          )}
        </svg>
        <svg width="100%" viewBox="0 0 860 54" className="mt-1.5 block">
          <line x1="0" y1="6" x2="860" y2="6" stroke="#20242c" />
          <polyline points={drawdownPoints} fill="none" stroke="#e0604f" strokeWidth="1.3" />
          {h !== null && (
            <line x1={xFor(h)} y1="0" x2={xFor(h)} y2="54" stroke="#3a424d" strokeWidth="1" />
          )}
        </svg>
        {h !== null && (
          <div
            className="pointer-events-none absolute top-1 z-10 rounded-[9px] border border-line bg-raised px-3 py-2 font-mono text-[11.5px] leading-[1.6] shadow-[0_8px_24px_rgba(0,0,0,.45)]"
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

function HonestyPanels({ run }: { run: RunPayload }) {
  const h = run.honesty;
  return (
    <div className="mt-3 grid grid-cols-2 gap-2.5">
      <div className={clsx(PANEL, "px-[15px] py-[13px]")}>
        <div className={clsx(PANEL_TITLE, "mb-2.5 flex items-center gap-2")}>
          IN-SAMPLE VS OUT-OF-SAMPLE
          <Hint text={HINT_OOS} />
        </div>
        <div className="mb-[3px] font-mono text-[11px] text-ink-3">IS sharpe {h.isSharpe}</div>
        <div className="mb-2 h-[9px] overflow-hidden rounded-[3px] bg-line-softer">
          <div className="h-full rounded-[3px] bg-chart" style={{ width: h.bar1 }} />
        </div>
        <div className="mb-[3px] font-mono text-[11px] text-ink-3">OOS sharpe {h.oosSharpe}</div>
        <div className="h-[9px] overflow-hidden rounded-[3px] bg-line-softer">
          <div className="h-full rounded-[3px] bg-chart" style={{ width: h.bar2 }} />
        </div>
        <div className="mt-2.5 text-[12.5px] text-ink-2">{h.notes[0]}</div>
      </div>

      <div className={clsx(PANEL, "px-[15px] py-[13px]")}>
        <div className={clsx(PANEL_TITLE, "mb-2.5 flex items-center gap-2")}>
          WALK-FORWARD{h.wf.length ? ` — LAST ${h.wf.length} WINDOWS` : ""}
          <Hint text={HINT_WF} />
        </div>
        {h.wf.length > 0 ? (
          <div className="flex h-14 items-end gap-[5px]">
            {h.wf.map((w, i) => (
              <div
                key={i}
                title={w.t}
                className={clsx(
                  "w-3.5 rounded-t-[3px] transition-opacity",
                  w.pos ? "bg-pl-pos" : "bg-pl-neg",
                  w.t && "cursor-help hover:opacity-75",
                )}
                style={{ height: `${w.h}px` }}
              />
            ))}
          </div>
        ) : (
          <div className="flex h-14 items-center font-mono text-[11px] text-ink-4">—</div>
        )}
        <div className="mt-2.5 text-[12.5px] text-ink-2">{h.notes[1]}</div>
      </div>

      <div className={clsx(PANEL, "px-[15px] py-[13px]")}>
        <div className={clsx(PANEL_TITLE, "mb-2.5 flex items-center gap-2")}>
          {run.mc.p50 ? "MONTE CARLO — 1,000 RESAMPLES" : "MONTE CARLO"}
          <Hint text={HINT_MC} />
        </div>
        <svg width="100%" viewBox="0 0 400 100" className="block overflow-visible">
          <polyline points={run.mc.p95} fill="none" stroke="#4a545f" strokeWidth="1.2" />
          <polyline points={run.mc.p50} fill="none" stroke="#cdd6df" strokeWidth="1.7" />
          <polyline points={run.mc.p05} fill="none" stroke="#4a545f" strokeWidth="1.2" />
          {run.mcTerm &&
            (
              [
                ["p95", run.mc.p95, run.mcTerm.p95, "#7b8794"],
                ["med", run.mc.p50, run.mcTerm.p50, "#cdd6df"],
                ["p5", run.mc.p05, run.mcTerm.p05, "#7b8794"],
              ] as const
            ).map(([tag, pts, dollars, color]) => {
              const last = pts.trim().split(" ").pop();
              if (!last || !dollars) return null;
              const y = parseFloat(last.split(",")[1] ?? "");
              if (Number.isNaN(y)) return null;
              return (
                <text
                  key={tag}
                  x="398"
                  y={Math.min(Math.max(y + 3, 8), 98)}
                  textAnchor="end"
                  fill={color}
                  fontSize="8.5"
                  fontFamily="var(--font-plex-mono)"
                >
                  {tag} {dollars}
                </text>
              );
            })}
        </svg>
        <div className="mt-2 text-[12.5px] text-ink-2">{h.notes[2]}</div>
      </div>

      <div className={clsx(PANEL, "px-[15px] py-[13px]")}>
        <div className={clsx(PANEL_TITLE, "mb-2.5 flex items-center gap-2")}>
          {run.sensitivityRows?.length
            ? "SENSITIVITY — ±20% PER PARAMETER"
            : run.sensitivity.length
              ? "SENSITIVITY — Δ 15 → 45"
              : "SENSITIVITY"}
          <Hint text={HINT_SENS} align="right" />
        </div>
        {run.sensitivityDetail?.length ? (
          // real runs: labelled rows, every cell explains itself on hover,
          // the as-specced column is ringed
          <div className="flex flex-col gap-1">
            {run.sensitivityDetail.map((row) => (
              <div key={row.name} className="flex items-center gap-2">
                <span
                  className="w-[104px] shrink-0 truncate font-mono text-[9.5px] text-ink-4"
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
                        "flex h-6 cursor-help items-center justify-center rounded font-mono text-[9px] transition-transform hover:scale-[1.06]",
                        ci === row.base && "ring-1 ring-trust-border",
                        cell.o > 0.55 ? "text-[#0d1216]" : "text-ink-3",
                      )}
                      style={{ background: `rgba(205,214,223,${cell.o})` }}
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
                      style={{ background: `rgba(205,214,223,${opacity})` }}
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
                    style={{ background: `rgba(205,214,223,${opacity})` }}
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
        <div className="mt-2 text-[12.5px] text-ink-2">{h.notes[3]}</div>
      </div>
    </div>
  );
}

function Recommendations({ run }: { run: RunPayload }) {
  if (!run.recommendations?.length) return null;
  return (
    <div className={clsx(PANEL, "mt-3 px-[15px] py-[13px]")}>
      <div className={clsx(PANEL_TITLE, "mb-2.5 flex items-center gap-2")}>
        WHAT WOULD IMPROVE IT — COMPUTED FROM THIS RUN
        <Hint
          text={
            "Each suggestion comes from this run's own gauntlet numbers — the ±20% " +
            "sweeps really re-ran the engine. Nothing here is opinion, and acting on " +
            "one starts a new trial that the deflated Sharpe will count against you."
          }
        />
      </div>
      <ul className="flex flex-col gap-2">
        {run.recommendations.map((rec, i) => (
          <li key={i} className="flex gap-2.5 text-[13px] leading-[1.55] text-ink-2">
            <span className="font-mono text-trust">{String(i + 1).padStart(2, "0")}</span>
            <span>{rec}</span>
          </li>
        ))}
      </ul>
      <div className="mt-2.5 border-t border-grid pt-2 font-mono text-[10px] text-ink-4">
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
        "grid items-baseline gap-2.5 border-t border-grid py-[7px] font-mono text-[12px]",
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

function TradeLog({ run }: { run: RunPayload }) {
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
          "flex w-full items-center gap-2.5 px-4 py-3 text-left font-mono text-[12.5px] text-ink-3 hover:border-line-hover hover:text-ink",
          open && "rounded-b-none",
        )}
      >
        <span>{open ? "▾" : "▸"}</span>
        <span className="flex-1">{run.tradeHeader}</span>
        <Hint text={HINT_TRADES} align="right" />
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
}: {
  run: RunPayload;
  onEditSpec?: () => void;
  onNew: () => void;
}) {
  const [askText, setAskText] = useState("");
  const [answer, setAnswer] = useState<string | null>(null);
  const [asking, setAsking] = useState(false);

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

      <VerdictBlock verdict={run.verdict} />

      {run.verdict.refusal && (
        <div className="mb-1 mt-[18px] text-center font-mono text-[10.5px] font-medium tracking-[.18em] text-ink-4">
          — UNBLESSED OUTPUT · MACHINERY CHECK ONLY —
        </div>
      )}

      <div className={clsx(run.verdict.refusal && "opacity-[.38]")}>
        <MetricTiles run={run} />
        <EquityChart run={run} />
        <HonestyPanels run={run} />
        <Recommendations run={run} />
        <TradeLog run={run} />
      </div>

      {answer && (
        <div className={clsx(PANEL, "mt-3.5 rounded-xl px-4 py-3")}>
          <div className="mb-1.5 font-mono text-[10.5px] font-medium tracking-[.12em] text-trust">
            GROUNDED ANSWER
          </div>
          <div className="text-[13.5px] leading-[1.6] text-ink-2">{answer}</div>
        </div>
      )}

      <div className="sticky bottom-2.5 mt-3.5 flex items-center gap-2.5 rounded-[13px] border border-line bg-[rgba(27,31,38,.92)] py-[9px] pl-4 pr-2.5 backdrop-blur-lg">
        <input
          className="flex-1 font-mono text-[13.5px]"
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
