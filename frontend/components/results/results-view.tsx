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

import { useState } from "react";
import clsx from "clsx";

import { ApiError, askRun } from "@/lib/api";
import type { RunPayload, SeriesPoint } from "@/lib/types";

import { DemoBadge, Disclaimer } from "@/components/disclaimer";
import { VerdictBlock } from "@/components/verdict/verdict-block";

const PANEL = "rounded-[14px] border border-line bg-panel";
const PANEL_TITLE = "font-mono text-[10.5px] font-medium tracking-[.12em] text-ink-4";

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
      {run.mtiles.map((m) => (
        <div key={m.l} className="rounded-xl border border-line bg-panel p-3">
          <div
            className={clsx(
              "font-mono text-[19px] font-semibold",
              m.neg ? "text-pl-neg" : "text-ink",
            )}
          >
            {m.v}
          </div>
          <div className="mt-1 font-mono text-[9.5px] font-medium tracking-[.1em] text-ink-4">
            {m.l}
          </div>
        </div>
      ))}
    </div>
  );
}

function EquityChart({ run }: { run: RunPayload }) {
  const equityPoints = run.equitySeries?.length
    ? seriesToPoints(run.equitySeries, 200, 14, false)
    : run.equityPoints;
  const drawdownPoints = run.drawdownSeries?.length
    ? seriesToPoints(run.drawdownSeries, 54, 5, true)
    : run.drawdownPoints;
  const startLabel = run.equitySeries?.length
    ? `$${Math.round(run.equitySeries[0].v).toLocaleString()} start · net of costs`
    : "$25k start · net of costs";
  return (
    <div className={clsx(PANEL, "mt-3 px-4 py-3.5")}>
      <div className="mb-2 flex justify-between">
        <span className={PANEL_TITLE}>
          {run.oosShadeX < 860 ? "EQUITY — OUT-OF-SAMPLE SHADED" : "EQUITY"}
        </span>
        <span className="font-mono text-[11px] text-ink-4">{startLabel}</span>
      </div>
      <svg width="100%" viewBox="0 0 860 200" className="block">
        {run.oosShadeX < 860 && (
          <rect x={run.oosShadeX} y="0" width={860 - run.oosShadeX} height="200" fill="rgba(255,255,255,.035)" />
        )}
        <line x1="0" y1="50" x2="860" y2="50" stroke="#20242c" />
        <line x1="0" y1="100" x2="860" y2="100" stroke="#20242c" />
        <line x1="0" y1="150" x2="860" y2="150" stroke="#20242c" />
        <polyline points={equityPoints} fill="none" stroke="#d7dde3" strokeWidth="1.8" />
      </svg>
      <svg width="100%" viewBox="0 0 860 54" className="mt-1.5 block">
        <line x1="0" y1="6" x2="860" y2="6" stroke="#20242c" />
        <polyline points={drawdownPoints} fill="none" stroke="#e0604f" strokeWidth="1.3" />
      </svg>
      <div className="mt-1.5 font-mono text-[10.5px] text-ink-4">
        drawdown — P/L red lives only here, never in the verdict
      </div>
    </div>
  );
}

function HonestyPanels({ run }: { run: RunPayload }) {
  const h = run.honesty;
  return (
    <div className="mt-3 grid grid-cols-2 gap-2.5">
      <div className={clsx(PANEL, "px-[15px] py-[13px]")}>
        <div className={clsx(PANEL_TITLE, "mb-2.5")}>IN-SAMPLE VS OUT-OF-SAMPLE</div>
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
        <div className={clsx(PANEL_TITLE, "mb-2.5")}>
          WALK-FORWARD{h.wf.length ? ` — ${h.wf.length} WINDOWS` : ""}
        </div>
        {h.wf.length > 0 ? (
          <div className="flex h-14 items-end gap-[5px]">
            {h.wf.map((w, i) => (
              <div
                key={i}
                className={clsx("w-3.5 rounded-t-[3px]", w.pos ? "bg-pl-pos" : "bg-pl-neg")}
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
        <div className={clsx(PANEL_TITLE, "mb-2.5")}>
          {run.mc.p50 ? "MONTE CARLO — 1,000 RESAMPLES" : "MONTE CARLO"}
        </div>
        <svg width="100%" viewBox="0 0 400 100" className="block">
          <polyline points={run.mc.p95} fill="none" stroke="#4a545f" strokeWidth="1.2" />
          <polyline points={run.mc.p50} fill="none" stroke="#cdd6df" strokeWidth="1.7" />
          <polyline points={run.mc.p05} fill="none" stroke="#4a545f" strokeWidth="1.2" />
        </svg>
        <div className="mt-2 text-[12.5px] text-ink-2">{h.notes[2]}</div>
      </div>

      <div className={clsx(PANEL, "px-[15px] py-[13px]")}>
        <div className={clsx(PANEL_TITLE, "mb-2.5")}>
          {run.sensitivity.length ? "SENSITIVITY — Δ 15 → 45" : "SENSITIVITY"}
        </div>
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
        <div className="mt-2 text-[12.5px] text-ink-2">{h.notes[3]}</div>
      </div>
    </div>
  );
}

function TradeLog({ run }: { run: RunPayload }) {
  const [open, setOpen] = useState(false);
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
        <span>{run.tradeHeader}</span>
      </button>
      {open && (
        <div className="rounded-b-xl border border-t-0 border-line bg-panel-deep px-4 pb-2.5 pt-1.5">
          {run.trades.map((t, i) => (
            <div
              key={i}
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
          ))}
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
        <div className="ml-auto flex gap-2">
          {onEditSpec && (
            <button
              onClick={onEditSpec}
              className="rounded-[9px] border border-line px-[13px] py-[7px] text-[12.5px] text-ink-3 hover:border-line-hover hover:text-ink"
            >
              edit spec
            </button>
          )}
          <button
            onClick={onNew}
            className="rounded-[9px] border border-line px-[13px] py-[7px] text-[12.5px] text-ink-3 hover:border-line-hover hover:text-ink"
          >
            new
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
          className="rounded-[9px] border border-trust-border bg-trust-dim px-[13px] py-1.5 text-[13px] font-semibold text-trust"
        >
          ↵
        </button>
      </div>
      <Disclaimer />
    </div>
  );
}
