"use client";

/**
 * Spec confirmation — every dial the engine will use, editable in place:
 * ticker, structure, strike (Δ.05 steps), DTE (0–50), anchor, the entry
 * trigger (any indicator / direction / level), and the exit. A missing
 * exit becomes a focused question (never a default), 0DTE is refused
 * honestly (no minute engine yet), and nothing runs on an unconfirmed spec.
 */

import { useState } from "react";
import clsx from "clsx";

import type { SpecDraft, Structure, Ticker, TriggerSpec } from "@/lib/types";
import { STRUCTURE_LABEL } from "@/lib/types";

const TILE = "rounded-xl border border-line bg-panel px-3 py-2.5";
const TILE_LABEL = "mb-[5px] font-mono text-[10px] font-medium tracking-[.1em] text-ink-4";

const TICKERS: Ticker[] = ["SPY", "QQQ", "IWM"];
const STRUCTURES: Structure[] = [
  "short_put",
  "put_credit_spread",
  "call_credit_spread",
  "iron_condor",
  "covered_call",
  "long_call",
  "long_put",
];

const INDICATORS: { id: string; label: string; period: boolean }[] = [
  { id: "rsi", label: "RSI", period: true },
  { id: "sma", label: "SMA level", period: true },
  { id: "ema", label: "EMA level", period: true },
  { id: "price_vs_sma_pct", label: "price vs SMA %", period: true },
  { id: "price_vs_ema_pct", label: "price vs EMA %", period: true },
  { id: "drawdown_from_high_pct", label: "pullback from high %", period: false },
  { id: "vix_level", label: "VIX level", period: false },
  { id: "realized_vol_20d", label: "realized vol 20d %", period: false },
  { id: "iv_percentile_1y", label: "IV percentile 1y", period: false },
];

const OPERATORS: { id: string; label: string; sym: string }[] = [
  { id: ">", label: "above", sym: ">" },
  { id: ">=", label: "at or above", sym: "≥" },
  { id: "<", label: "below", sym: "<" },
  { id: "<=", label: "at or below", sym: "≤" },
  { id: "crosses_above", label: "crosses above", sym: "↗" },
  { id: "crosses_below", label: "crosses below", sym: "↘" },
];

export function triggerLabel(t: TriggerSpec): string {
  const ind = INDICATORS.find((i) => i.id === t.indicator);
  const op = OPERATORS.find((o) => o.id === t.operator);
  const name = ind?.label ?? t.indicator;
  const period = ind?.period && t.period ? `(${t.period})` : "";
  return `${name}${period} ${op?.sym ?? t.operator} ${t.value}`;
}

function Stepper({
  render,
  onDec,
  onInc,
}: {
  render: () => string;
  onDec: () => void;
  onInc: () => void;
}) {
  return (
    <div className="flex items-center gap-2">
      <button onClick={onDec} className="text-[15px] text-ink-4 hover:text-ink">
        ‹
      </button>
      <span className="font-mono text-[15px] font-semibold">{render()}</span>
      <button onClick={onInc} className="text-[15px] text-ink-4 hover:text-ink">
        ›
      </button>
    </div>
  );
}

const SELECT_CLS =
  "rounded-[7px] border border-line bg-panel-deep px-2 py-1 font-mono text-[11.5px] text-ink";

export function SpecScreen({
  draft,
  onChange,
  onBack,
  onRun,
  earliestYear,
}: {
  draft: SpecDraft;
  onChange: (d: SpecDraft) => void;
  onBack: () => void;
  onRun: () => void;
  earliestYear: string;
}) {
  const [exitEditing, setExitEditing] = useState(false);
  const [customExit, setCustomExit] = useState("");
  const exitSet = !!draft.exit;
  const zeroDte = draft.dte === 0;
  const set = (patch: Partial<SpecDraft>) => onChange({ ...draft, ...patch });

  const cycle = <T,>(list: T[], current: T, dir: 1 | -1): T =>
    list[(list.indexOf(current) + dir + list.length) % list.length];

  const trig = draft.triggerSpec;
  const trigIndicator = trig ? INDICATORS.find((i) => i.id === trig.indicator) : undefined;
  const setTrig = (patch: Partial<TriggerSpec>) => {
    const next = { ...(trig ?? { indicator: "rsi", operator: "<", value: 30 }), ...patch };
    set({ triggerSpec: next, trigger: triggerLabel(next) });
  };

  const exitChoices =
    draft.structure === "long_call" || draft.structure === "long_put"
      ? ["100% profit", "stop 50%", "hold to expiry"]
      : ["50% profit", "21 DTE", "hold to expiry"];

  return (
    <div>
      <button onClick={onBack} className="mb-[18px] text-[12.5px] text-ink-4 hover:text-ink-3">
        ‹ edit input
      </button>

      <div className="mb-4 flex justify-end">
        <div className="max-w-[70%] rounded-[12px_12px_4px_12px] border border-line bg-raised px-3.5 py-2.5 font-mono text-[13px] leading-[1.55] text-ink-2">
          {draft.fromChart ? `◉ ${draft.quote}` : `“${draft.quote}”`}
        </div>
      </div>
      <p className="mb-3 text-[14.5px] text-ink-3">Here's what I heard — every dial is adjustable:</p>

      <div className="grid grid-cols-4 gap-2.5">
        <div className={TILE}>
          <div className={TILE_LABEL}>TICKER</div>
          <Stepper
            render={() => draft.ticker}
            onDec={() => set({ ticker: cycle(TICKERS, draft.ticker, -1) })}
            onInc={() => set({ ticker: cycle(TICKERS, draft.ticker, 1) })}
          />
        </div>
        <div className={TILE}>
          <div className={TILE_LABEL}>STRUCTURE</div>
          <Stepper
            render={() => STRUCTURE_LABEL[draft.structure]}
            onDec={() => set({ structure: cycle(STRUCTURES, draft.structure, -1) })}
            onInc={() => set({ structure: cycle(STRUCTURES, draft.structure, 1) })}
          />
        </div>
        <div className={TILE}>
          <div className={TILE_LABEL}>STRIKE</div>
          <Stepper
            render={() => `.${String(draft.strikeDelta).padStart(2, "0")}Δ`}
            onDec={() => set({ strikeDelta: Math.max(5, draft.strikeDelta - 5) })}
            onInc={() => set({ strikeDelta: Math.min(95, draft.strikeDelta + 5) })}
          />
        </div>
        <div className={clsx(TILE, zeroDte && "!border-warn/50")}>
          <div className={clsx(TILE_LABEL, zeroDte && "!text-warn")}>DTE</div>
          <div className="flex items-center gap-1.5">
            <button
              onClick={() => set({ dte: Math.max(0, draft.dte - 1) })}
              className="text-[15px] text-ink-4 hover:text-ink"
            >
              ‹
            </button>
            <input
              type="number"
              min={0}
              max={50}
              value={draft.dte}
              onChange={(e) => {
                const v = Math.max(0, Math.min(50, Number(e.target.value) || 0));
                set({ dte: v });
              }}
              className="w-[46px] rounded border border-transparent bg-transparent text-center font-mono text-[15px] font-semibold focus:border-line [appearance:textfield] [&::-webkit-inner-spin-button]:appearance-none [&::-webkit-outer-spin-button]:appearance-none"
            />
            <button
              onClick={() => set({ dte: Math.min(50, draft.dte + 1) })}
              className="text-[15px] text-ink-4 hover:text-ink"
            >
              ›
            </button>
          </div>
        </div>

        {draft.fromChart ? (
          <>
            <div className={clsx(TILE, "!border-trust-border")}>
              <div className={clsx(TILE_LABEL, "!text-trust")}>ANCHOR ⌖</div>
              <input
                value={draft.anchor ?? ""}
                onChange={(e) => set({ anchor: e.target.value })}
                className="w-full bg-transparent font-mono text-[13px] font-semibold focus:outline-none"
              />
            </div>
            <div className={TILE}>
              <div className={TILE_LABEL}>TRIGGER</div>
              <div className="pt-[3px] font-mono text-[12px] font-semibold">
                {trig ? triggerLabel(trig) : draft.trigger ?? "—"}
              </div>
            </div>
          </>
        ) : (
          <>
            <div className={TILE}>
              <div className={TILE_LABEL}>CADENCE</div>
              <div className="pt-0.5 font-mono text-[13px] font-semibold">{draft.cadence}</div>
            </div>
            <div className={TILE}>
              <div className={TILE_LABEL}>SIZE</div>
              <div className="pt-0.5 font-mono text-[13px] font-semibold">{draft.size}</div>
            </div>
          </>
        )}

        <button
          onClick={() => setExitEditing(true)}
          className={clsx(
            TILE,
            "text-left",
            !exitSet && "!border-trust-border !bg-trust-dim",
            exitSet && "hover:border-line-hover",
          )}
          title="Edit exit rules"
        >
          <div className={clsx(TILE_LABEL, !exitSet && "!text-trust")}>EXIT ✎</div>
          <div className="pt-0.5 font-mono text-[13px] font-semibold">
            {draft.exit ?? "not set"}
          </div>
        </button>
        <div className={TILE}>
          <div className={TILE_LABEL}>FILLS</div>
          <div className="pt-[3px] font-mono text-[12px] font-semibold">bid/ask + slip 0.5</div>
        </div>
      </div>

      {draft.fromChart && (
        <div className="mt-3 flex flex-wrap items-center gap-2 rounded-xl border border-line bg-panel px-3.5 py-3">
          <span className="font-mono text-[10.5px] font-medium tracking-[.1em] text-ink-4">
            TRIGGER — ENTER WHEN
          </span>
          <select
            value={trig?.indicator ?? "drawdown_from_high_pct"}
            onChange={(e) => {
              const ind = INDICATORS.find((i) => i.id === e.target.value);
              setTrig({
                indicator: e.target.value,
                period: ind?.period ? (trig?.period ?? 14) : undefined,
              });
            }}
            className={SELECT_CLS}
          >
            {INDICATORS.map((i) => (
              <option key={i.id} value={i.id}>
                {i.label}
              </option>
            ))}
          </select>
          {trigIndicator?.period && (
            <input
              type="number"
              min={2}
              max={400}
              value={trig?.period ?? 14}
              onChange={(e) => setTrig({ period: Math.max(2, Math.min(400, Number(e.target.value) || 14)) })}
              className={clsx(SELECT_CLS, "w-[64px]")}
              title="period"
            />
          )}
          <select
            value={trig?.operator ?? ">="}
            onChange={(e) => setTrig({ operator: e.target.value })}
            className={SELECT_CLS}
          >
            {OPERATORS.map((o) => (
              <option key={o.id} value={o.id}>
                {o.label}
              </option>
            ))}
          </select>
          <input
            type="number"
            step="any"
            value={trig?.value ?? 2}
            onChange={(e) => setTrig({ value: Number(e.target.value) || 0 })}
            className={clsx(SELECT_CLS, "w-[84px]")}
            title="threshold"
          />
          <span className="font-mono text-[11px] text-ink-4">
            e.g. RSI below 30 · pullback ≥ 2% · VIX above 25
          </span>
        </div>
      )}

      {(!exitSet || exitEditing) && (
        <div className="mt-3 flex flex-wrap items-center gap-3 rounded-xl border border-trust-border bg-trust-dim px-3.5 py-3">
          <span className="text-[13.5px] text-ink">
            {exitSet ? (
              <b className="text-trust">Edit exit</b>
            ) : (
              <>
                <b className="text-trust">One question</b> — you gave no exit. Close at:
              </>
            )}
          </span>
          {exitChoices.map((label) => (
            <button
              key={label}
              onClick={() => {
                set({ exit: label });
                setExitEditing(false);
              }}
              className="rounded-full border border-trust-border px-[13px] py-[5px] text-[12.5px] text-trust hover:bg-trust-dim"
            >
              {label}
            </button>
          ))}
          <input
            value={customExit}
            onChange={(e) => setCustomExit(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && customExit.trim()) {
                set({ exit: customExit.trim() });
                setCustomExit("");
                setExitEditing(false);
              }
            }}
            placeholder='custom: "60% profit · stop 2× · 14 DTE" ↵'
            className="min-w-[230px] flex-1 rounded-[9px] border border-trust-border bg-transparent px-3 py-[5px] font-mono text-[12px] text-ink placeholder:text-ink-4 focus:outline-none"
          />
        </div>
      )}

      {draft.fromChart && (
        <div className="mt-3 rounded-xl border border-trust-border bg-trust-dim px-3.5 py-3 text-[13.5px] leading-[1.5] text-ink">
          You showed me winners — that's what eyes do. I'll test{" "}
          <b>every look-alike since {earliestYear}</b>, losers included. If the edge lives only in
          your examples, the verdict will say exactly that.
        </div>
      )}

      {zeroDte && (
        <div className="mt-3 rounded-xl border border-warn/50 px-3.5 py-3 text-[13px] leading-[1.5] text-warn">
          0DTE needs minute-level simulation — refused until the minute engine milestone. Set DTE
          to 1 or more to run on EOD data.
        </div>
      )}

      <div className="mt-5 flex items-center justify-between">
        <span className="text-[12.5px] text-ink-4">Nothing runs on an unconfirmed spec.</span>
        <button
          onClick={onRun}
          disabled={!exitSet || zeroDte}
          className={clsx(
            "rounded-[10px] px-5 py-2.5 text-[14px]",
            exitSet && !zeroDte
              ? "bg-trust font-bold text-[#0d1216]"
              : "cursor-not-allowed bg-raised-2 text-ink-4",
          )}
        >
          Run the gauntlet →
        </button>
      </div>
    </div>
  );
}
