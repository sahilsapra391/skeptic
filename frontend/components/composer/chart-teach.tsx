"use client";

/**
 * "Show it on the chart" — teach-by-example on the full MarketChart.
 * Click = entry, click again = exit, up to 10 examples.
 *
 * STRUCTURE INFERENCE: each pinned move is scored against the series' own
 * volatility over the same bar span (a z-score), so "big move" means big
 * for THIS timeframe, not an absolute percent:
 *   consistent gentle drift up   → short put        (selling premium)
 *   consistent strong move up    → long call        (paying for direction)
 *   consistent gentle drift down → call credit spread
 *   consistent strong move down  → long put
 *   mixed directions / flat      → iron condor      (range-bound)
 * The inference is a starting point — every dial is editable on the spec
 * screen, and nothing runs on an unconfirmed spec.
 */

import { useMemo, useState } from "react";
import clsx from "clsx";

import type { Bar, SpecDraft, Structure, Ticker } from "@/lib/types";
import { STRUCTURE_LABEL } from "@/lib/types";

import { ChartPin, MarketChart } from "@/components/charts/market-chart";

const TICKERS: Ticker[] = ["SPY", "QQQ", "IWM"];
const MAX_EXAMPLES = 10;

// z-score thresholds: below DIRECTION the move is noise; above STRONG the
// move is conviction-sized for this timeframe. DIRECTION leans permissive —
// a deliberately pinned move is an intent signal even when statistically
// mild; only near-sideways pins read as range-bound.
const Z_DIRECTION = 0.35;
const Z_STRONG = 1.8;

const DEFAULTS: Record<string, { delta: number; exit: string }> = {
  short_put: { delta: 30, exit: "50% profit · 21 DTE" },
  call_credit_spread: { delta: 30, exit: "50% profit · stop 150%" },
  long_call: { delta: 50, exit: "100% profit · stop 50%" },
  long_put: { delta: 50, exit: "100% profit · stop 50%" },
  iron_condor: { delta: 20, exit: "50% profit · 21 DTE" },
};

function fmtPin(iso: string): string {
  const d = new Date(iso);
  const hasTime = iso.includes("T") && !iso.startsWith(iso.slice(0, 10) + "T00:00");
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    ...(hasTime ? { hour: "2-digit", minute: "2-digit", hour12: false } : { year: "2-digit" }),
    timeZone: "America/New_York",
  }).format(d);
}

interface Inference {
  structure: Structure;
  avgZ: number;
}

/** Score each move against same-span moves everywhere else in the series. */
function inferStructure(pins: ChartPin[], bars: Bar[]): Inference {
  const complete = pins.filter((p) => p.b != null);
  if (!complete.length) return { structure: "short_put", avgZ: 0 };

  const index = new Map(bars.map((b, i) => [b.t, i]));
  const zs: number[] = [];
  for (const pin of complete) {
    const move = (pin.b!.c - pin.a.c) / pin.a.c;
    const ia = index.get(pin.a.t);
    const ib = index.get(pin.b!.t);
    let z: number;
    if (ia != null && ib != null && ib > ia && bars.length > ib - ia + 4) {
      const n = ib - ia;
      const step = Math.max(1, Math.floor(n / 2));
      const rets: number[] = [];
      for (let i = 0; i + n < bars.length; i += step) {
        if (bars[i].c > 0) rets.push(bars[i + n].c / bars[i].c - 1);
      }
      const mean = rets.reduce((s, r) => s + r, 0) / Math.max(rets.length, 1);
      const sd = Math.sqrt(
        rets.reduce((s, r) => s + (r - mean) ** 2, 0) / Math.max(rets.length - 1, 1),
      );
      z = sd > 0 ? move / sd : 0;
    } else {
      z = move / 0.02; // series context unavailable — assume ~2% typical move
    }
    zs.push(z);
  }

  const ups = zs.filter((z) => z > Z_DIRECTION).length;
  const downs = zs.filter((z) => z < -Z_DIRECTION).length;
  const avgZ = zs.reduce((s, z) => s + z, 0) / zs.length;

  let structure: Structure;
  if ((ups > 0 && downs > 0) || (ups === 0 && downs === 0)) {
    structure = "iron_condor"; // mixed directions or all noise → range-bound
  } else if (ups > 0) {
    structure = avgZ >= Z_STRONG ? "long_call" : "short_put";
  } else {
    structure = avgZ <= -Z_STRONG ? "long_put" : "call_credit_spread";
  }
  return { structure, avgZ };
}

export function ChartTeach({ onCompile }: { onCompile: (draft: SpecDraft) => void }) {
  const [ticker, setTicker] = useState<Ticker>("SPY");
  const [pins, setPins] = useState<ChartPin[]>([]);
  const [bars, setBars] = useState<Bar[]>([]);
  // default width matches the Describe It box; expanded fills the page
  const [expanded, setExpanded] = useState(false);

  const complete = pins.filter((p) => p.b != null);
  const pending = pins.find((p) => p.b == null) ?? null;

  const inference = useMemo(() => inferStructure(pins, bars), [pins, bars]);

  function handleBarClick(t: string, close: number) {
    if (pending) {
      if (pending.a.t === t) return;
      setPins(pins.map((p) => (p === pending ? { ...p, b: { t, c: close } } : p)));
    } else {
      if (complete.length >= MAX_EXAMPLES) return;
      setPins([...pins, { a: { t, c: close }, b: null }]);
    }
  }

  function clearPins() {
    setPins([]);
  }

  function compile() {
    if (!complete.length) return;
    const n = complete.length;
    const defaults = DEFAULTS[inference.structure] ?? DEFAULTS.short_put;
    onCompile({
      ticker,
      structure: inference.structure,
      strikeDelta: defaults.delta,
      dte: 45,
      cadence: "signal",
      size: "1 contract",
      exit: defaults.exit,
      fromChart: true,
      quote: `taught by ${n} pinned example${n === 1 ? "" : "s"} on the ${ticker} chart`,
      anchor: fmtPin(complete[0].a.t),
      trigger: "pullback ≥ 2% from high",
      triggerSpec: { indicator: "drawdown_from_high_pct", operator: ">=", value: 2 },
      examples: n,
    });
  }

  const pendingNote = pending
    ? `entry pinned at ${fmtPin(pending.a.t)} — now click where you’d exit`
    : complete.length
      ? `${complete.length} example${complete.length > 1 ? "s" : ""} pinned · add up to ${MAX_EXAMPLES}, or continue →`
      : "Show me 1–10 trades you’d have taken. Click where you’d enter.";

  return (
    <div
      className={clsx(
        "rounded-[14px] border border-line bg-panel px-5 py-4",
        expanded ? "w-full" : "mx-auto max-w-[960px]",
      )}
    >
      <div className="mb-3 flex items-center gap-3">
        <div className="inline-flex gap-[2px]">
          {TICKERS.map((t) => (
            <button
              key={t}
              onClick={() => {
                setTicker(t);
                clearPins();
              }}
              className={clsx(
                "rounded-[8px] px-3.5 py-1.5 font-mono text-[14px] font-semibold",
                ticker === t ? "bg-raised-3 text-ink" : "text-ink-4 hover:text-ink-3",
              )}
            >
              {t}
            </button>
          ))}
        </div>
        <span className="ml-auto font-mono text-[12.5px] text-ink-4">
          click = entry · click again = exit · up to {MAX_EXAMPLES} examples
        </span>
        <button
          onClick={() => setExpanded((v) => !v)}
          aria-label={expanded ? "Shrink chart" : "Expand chart"}
          title={expanded ? "Shrink chart" : "Expand chart"}
          className="flex h-[32px] w-[32px] flex-none items-center justify-center rounded-[8px] border border-line text-ink-4 hover:border-line-hover hover:text-ink"
        >
          {expanded ? (
            <svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
              <path d="M6.5 2v4.5H2" />
              <path d="M9.5 14V9.5H14" />
              <path d="M6.5 6.5L1.5 1.5" />
              <path d="M9.5 9.5l5 5" />
            </svg>
          ) : (
            <svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
              <path d="M9.5 2H14v4.5" />
              <path d="M6.5 14H2V9.5" />
              <path d="M14 2L9.5 6.5" />
              <path d="M2 14l4.5-4.5" />
            </svg>
          )}
        </button>
      </div>

      <MarketChart
        ticker={ticker}
        pinMode
        pins={pins}
        onBarClick={handleBarClick}
        onViewChange={clearPins}
        onDataChange={setBars}
      />

      <div className="mx-0.5 mb-1 mt-2.5 font-mono text-[13px] text-trust">{pendingNote}</div>

      {complete.map((pin, i) => {
        const movePct = ((pin.b!.c - pin.a.c) / pin.a.c) * 100;
        return (
          <div
            key={pin.a.t + String(i)}
            className="mt-1.5 flex items-center justify-between rounded-[9px] border border-line bg-raised px-3 py-2"
          >
            <span className="font-mono text-[13.5px] text-ink-2">
              example {i + 1} — {fmtPin(pin.a.t)} → {pin.b ? fmtPin(pin.b.t) : ""} ·{" "}
              {movePct >= 0 ? "+" : ""}
              {movePct.toFixed(1)}%
            </span>
            <button
              onClick={() => setPins(pins.filter((p) => p !== pin))}
              className="text-[13px] text-ink-4 hover:text-ink"
            >
              ✕
            </button>
          </div>
        );
      })}

      <div className="mt-3 flex items-center gap-2.5">
        <button
          onClick={clearPins}
          className="rounded-full border border-line px-3.5 py-1.5 text-[13px] text-ink-4 hover:text-ink-3"
        >
          clear pins
        </button>
        <span className="font-mono text-[12.5px] text-ink-4">
          {complete.length
            ? `structure: ${STRUCTURE_LABEL[inference.structure]} · inferred from your pins · changing view clears pins`
            : "structure inferred from your pins · changing view clears pins"}
        </span>
        <button
          onClick={compile}
          disabled={!complete.length}
          className={clsx(
            "ml-auto rounded-[10px] px-5 py-2.5 text-[14.5px]",
            complete.length
              ? "bg-trust font-bold text-on-accent"
              : "cursor-not-allowed bg-raised-2 text-ink-4",
          )}
        >
          That's the idea →
        </button>
      </div>
    </div>
  );
}
