"use client";

/**
 * "Show it on the chart" — the teach-by-example composer, now on the full
 * MarketChart: real lake bars at any interval, candles or line, indicator
 * overlays, live tail when available. Click = entry, click again = exit,
 * up to 3 examples; the compiled draft is confirmed on the Spec screen like
 * any other input (nothing runs on an unconfirmed spec).
 */

import { useState } from "react";
import clsx from "clsx";

import type { SpecDraft, Ticker } from "@/lib/types";

import { ChartPin, MarketChart } from "@/components/charts/market-chart";

const TICKERS: Ticker[] = ["SPY", "QQQ", "IWM"];

function fmtPin(iso: string): string {
  const d = new Date(iso);
  const hasTime = iso.includes("T") && !iso.startsWith(iso.slice(0, 10) + "T00:00");
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    ...(hasTime
      ? { hour: "2-digit", minute: "2-digit", hour12: false }
      : { year: "2-digit" }),
    timeZone: "America/New_York",
  }).format(d);
}

export function ChartTeach({ onCompile }: { onCompile: (draft: SpecDraft) => void }) {
  const [ticker, setTicker] = useState<Ticker>("SPY");
  const [pins, setPins] = useState<ChartPin[]>([]);

  const complete = pins.filter((p) => p.b != null);
  const pending = pins.find((p) => p.b == null) ?? null;

  function handleBarClick(t: string, close: number) {
    if (pending) {
      if (pending.a.t === t) return;
      setPins(pins.map((p) => (p === pending ? { ...p, b: { t, c: close } } : p)));
    } else {
      if (complete.length >= 3) return;
      setPins([...pins, { a: { t, c: close }, b: null }]);
    }
  }

  function clearPins() {
    setPins([]);
  }

  function compile() {
    if (!complete.length) return;
    const n = complete.length;
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
      anchor: fmtPin(complete[0].a.t),
      trigger: "pullback ≥2% ≤5d",
      examples: n,
    });
  }

  const pendingNote = pending
    ? `entry pinned at ${fmtPin(pending.a.t)} — now click where you’d exit`
    : complete.length
      ? `${complete.length} example${complete.length > 1 ? "s" : ""} pinned · add up to 3, or continue →`
      : "Show me 1–3 trades you’d have taken. Click where you’d enter.";

  return (
    <div className="rounded-[14px] border border-line bg-panel px-4 py-3.5">
      <div className="mb-2.5 flex items-center gap-2.5">
        <div className="inline-flex gap-[2px]">
          {TICKERS.map((t) => (
            <button
              key={t}
              onClick={() => {
                setTicker(t);
                clearPins();
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
        <span className="ml-auto font-mono text-[11px] text-ink-4">
          click = entry · click again = exit · up to 3 examples
        </span>
      </div>

      <MarketChart
        ticker={ticker}
        pinMode
        pins={pins}
        onBarClick={handleBarClick}
        onViewChange={clearPins}
      />

      <div className="mx-0.5 mb-1 mt-[9px] font-mono text-[11.5px] text-trust">{pendingNote}</div>

      {complete.map((pin, i) => (
        <div
          key={pin.a.t}
          className="mt-1.5 flex items-center justify-between rounded-[9px] border border-line bg-raised px-3 py-2"
        >
          <span className="font-mono text-[12px] text-ink-2">
            example {i + 1} — {fmtPin(pin.a.t)} → {pin.b ? fmtPin(pin.b.t) : ""} · sell .30Δ put ·
            closed ~+50%
          </span>
          <button
            onClick={() => setPins(pins.filter((p) => p !== pin))}
            className="text-[13px] text-ink-4 hover:text-ink"
          >
            ✕
          </button>
        </div>
      ))}

      <div className="mt-3 flex items-center gap-2.5">
        <button
          onClick={clearPins}
          className="rounded-full border border-line px-3 py-[5px] text-[12px] text-ink-4 hover:text-ink-3"
        >
          clear pins
        </button>
        <span className="font-mono text-[11.5px] text-ink-4">
          structure: short put · inferred from pins · changing view clears pins
        </span>
        <button
          onClick={compile}
          disabled={!complete.length}
          className={clsx(
            "ml-auto rounded-[9px] px-4 py-2 text-[13px]",
            complete.length
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
