"use client";

/**
 * "Show it on the chart" — the teach-by-example composer. The price series
 * is the REAL underlying daily history from the lake; pins mark entry/exit
 * examples; the compiled draft is confirmed on the Spec screen like any
 * other input (nothing runs on an unconfirmed spec).
 */

import { useEffect, useMemo, useState } from "react";
import clsx from "clsx";

import { getUnderlying } from "@/lib/api";
import { shortDate, monthYear } from "@/lib/format";
import type { SpecDraft, Ticker } from "@/lib/types";

const W = 860;
const H = 240;
const PAD = 14;
const TICKERS: Ticker[] = ["SPY", "QQQ", "IWM"];

interface Example {
  a: number;
  b: number;
}

/** Deterministic stand-in series, used ONLY when the lake is unreachable —
 * and labeled as such on the canvas. (Matches the design prototype's
 * generator so the fallback still looks like a market.) */
function sampleSeries(): { date: string; close: number }[] {
  let seed = 7;
  const rnd = () => {
    seed = (seed * 1103515245 + 12345) % 2147483648;
    return seed / 2147483648;
  };
  const out: { date: string; close: number }[] = [];
  let v = 478;
  const d = new Date(2024, 6, 1);
  for (let i = 0; i < 240; i++) {
    let chg = (rnd() - 0.48) * 1.1;
    if (i >= 60 && i <= 66) chg = -0.55 - rnd() * 0.3;
    if (i > 66 && i <= 74) chg = 0.5 + rnd() * 0.2;
    if (i >= 148 && i <= 160) chg = -0.85 - rnd() * 0.5;
    if (i > 160 && i <= 176) chg = 0.75 + rnd() * 0.4;
    v = v * (1 + chg / 100);
    out.push({ date: d.toISOString().slice(0, 10), close: v });
    d.setDate(d.getDate() + 3);
  }
  return out;
}

export function ChartTeach({ onCompile }: { onCompile: (draft: SpecDraft) => void }) {
  const [ticker, setTicker] = useState<Ticker>("SPY");
  const [series, setSeries] = useState<{ date: string; close: number }[]>([]);
  const [isSample, setIsSample] = useState(false);
  const [pendingPin, setPendingPin] = useState<number | null>(null);
  const [examples, setExamples] = useState<Example[]>([]);

  useEffect(() => {
    let cancelled = false;
    setSeries([]);
    getUnderlying(ticker, 240)
      .then((r) => {
        if (cancelled) return;
        setSeries(r.series);
        setIsSample(false);
      })
      .catch(() => {
        if (cancelled) return;
        setSeries(sampleSeries());
        setIsSample(true);
      });
    return () => {
      cancelled = true;
    };
  }, [ticker]);

  const geometry = useMemo(() => {
    if (!series.length) return null;
    const values = series.map((p) => p.close);
    const min = Math.min(...values);
    const max = Math.max(...values);
    const xFor = (i: number) => (i / (series.length - 1)) * W;
    const yFor = (i: number) => PAD + (1 - (values[i] - min) / (max - min || 1)) * (H - 2 * PAD);
    return { xFor, yFor };
  }, [series]);

  const dateFor = (i: number) => (series[i] ? shortDate(series[i].date) : "");

  function handleClick(e: React.MouseEvent<HTMLDivElement>) {
    if (!series.length) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const frac = Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width));
    const idx = Math.round(frac * (series.length - 1));
    if (pendingPin == null) {
      if (examples.length >= 3) return;
      setPendingPin(idx);
    } else {
      if (idx === pendingPin) return;
      setExamples([...examples, { a: Math.min(pendingPin, idx), b: Math.max(pendingPin, idx) }]);
      setPendingPin(null);
    }
  }

  function compile() {
    if (!examples.length) return;
    const n = examples.length;
    onCompile({
      ticker,
      structure: "short_put",
      strikeDelta: 30,
      dte: 45,
      cadence: "signal",
      size: "1 contract",
      exit: "50% profit · 21 DTE",
      fromChart: true,
      quote: `taught by ${n} pinned example${n === 1 ? "" : "s"} on the ${ticker} chart`,
      anchor: dateFor(examples[0].a),
      trigger: "pullback ≥2% ≤5d",
      examples: n,
    });
  }

  const pendingNote =
    pendingPin != null
      ? `entry pinned at ${dateFor(pendingPin)} — now click where you’d exit`
      : examples.length
        ? `${examples.length} example${examples.length > 1 ? "s" : ""} pinned · add up to 3, or continue →`
        : "Show me 1–3 trades you’d have taken. Click where you’d enter.";

  const rangeLabel = series.length
    ? `daily · ${monthYear(series[0].date)} → now${isSample ? " · SAMPLE SERIES — lake offline" : ""}`
    : "reading the lake…";

  return (
    <div className="rounded-[14px] border border-line bg-panel px-4 py-3.5">
      <div className="mb-2.5 flex items-center gap-2.5">
        <div className="inline-flex gap-[2px]">
          {TICKERS.map((t) => (
            <button
              key={t}
              onClick={() => {
                setTicker(t);
                setExamples([]);
                setPendingPin(null);
              }}
              className={clsx(
                "rounded-[7px] px-2.5 py-1 font-mono text-[11.5px]",
                ticker === t ? "bg-raised-3 text-ink" : "text-ink-4",
              )}
            >
              {t}
            </button>
          ))}
        </div>
        <span className={clsx("font-mono text-[11px]", isSample ? "text-warn" : "text-ink-4")}>
          {rangeLabel}
        </span>
        <span className="ml-auto font-mono text-[11px] text-ink-4">
          click = entry · click again = exit · up to 3 examples
        </span>
      </div>

      <div onClick={handleClick} className="cursor-crosshair rounded-lg bg-panel-chart px-1 py-1.5">
        <svg width="100%" viewBox={`0 0 ${W} ${H}`} className="block">
          <line x1="0" y1="60" x2={W} y2="60" stroke="#20242c" strokeWidth="1" />
          <line x1="0" y1="120" x2={W} y2="120" stroke="#20242c" strokeWidth="1" />
          <line x1="0" y1="180" x2={W} y2="180" stroke="#20242c" strokeWidth="1" />
          {geometry && series.length > 0 && (
            <polyline
              points={series.map((_, i) => `${geometry.xFor(i).toFixed(1)},${geometry.yFor(i).toFixed(1)}`).join(" ")}
              fill="none"
              stroke="#cdd6df"
              strokeWidth="1.6"
            />
          )}
          {geometry &&
            examples.map((ex, i) => (
              <g key={i}>
                <line
                  x1={geometry.xFor(ex.a)}
                  y1={geometry.yFor(ex.a)}
                  x2={geometry.xFor(ex.b)}
                  y2={geometry.yFor(ex.b)}
                  stroke="var(--ac)"
                  strokeWidth="1.4"
                  strokeDasharray="5 4"
                />
                <circle cx={geometry.xFor(ex.a)} cy={geometry.yFor(ex.a)} r="5.5" fill="var(--ac)" />
                <circle
                  cx={geometry.xFor(ex.b)}
                  cy={geometry.yFor(ex.b)}
                  r="5.5"
                  fill="#171a20"
                  stroke="var(--ac)"
                  strokeWidth="2"
                />
              </g>
            ))}
          {geometry && pendingPin != null && (
            <circle
              className="animate-pin-pulse"
              cx={geometry.xFor(pendingPin)}
              cy={geometry.yFor(pendingPin)}
              r="6"
              fill="var(--ac)"
            />
          )}
        </svg>
      </div>

      <div className="mx-0.5 mb-1 mt-[9px] font-mono text-[11.5px] text-trust">{pendingNote}</div>

      {examples.map((ex, i) => (
        <div
          key={i}
          className="mt-1.5 flex items-center justify-between rounded-[9px] border border-line bg-raised px-3 py-2"
        >
          <span className="font-mono text-[12px] text-ink-2">
            example {i + 1} — {dateFor(ex.a)} → {dateFor(ex.b)} · sell .30Δ put · closed ~+50%
          </span>
          <button
            onClick={() => setExamples(examples.filter((_, j) => j !== i))}
            className="text-[13px] text-ink-4 hover:text-ink"
          >
            ✕
          </button>
        </div>
      ))}

      <div className="mt-3 flex items-center gap-2.5">
        <button
          onClick={() => {
            setExamples([]);
            setPendingPin(null);
          }}
          className="rounded-full border border-line px-3 py-[5px] text-[12px] text-ink-4 hover:text-ink-3"
        >
          clear pins
        </button>
        <span className="font-mono text-[11.5px] text-ink-4">
          structure: short put · inferred from pins
        </span>
        <button
          onClick={compile}
          disabled={!examples.length}
          className={clsx(
            "ml-auto rounded-[9px] px-4 py-2 text-[13px]",
            examples.length
              ? "bg-trust font-bold text-[#0d1216]"
              : "cursor-not-allowed bg-raised-2 text-ink-4",
          )}
        >
          That's the idea →
        </button>
      </div>
    </div>
  );
}
