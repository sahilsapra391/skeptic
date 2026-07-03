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

import { Hint } from "@/components/hint";
import type { SpecDraft, Structure, Ticker, TriggerSpec } from "@/lib/types";
import { STRUCTURE_LABEL } from "@/lib/types";

/** Plain-English one-liners for every dial. */
const SPEC_HINTS: Record<string, string> = {
  TICKER: "Which ETF to trade options on. Coverage differs — SPY has the longest record.",
  STRUCTURE: "The option position type — what gets bought or sold at entry.",
  STRIKE:
    "How far from the money, in delta. .05Δ is far out (rarely hit), .50Δ is at the money.",
  DTE: "Days to expiration when the trade opens. 0DTE needs minute data and is refused for now.",
  ANCHOR: "The first pinned example on your chart — where the pattern was taught from.",
  TRIGGER: "The market condition that must be true for a trade to enter.",
  CADENCE: "How often a new trade is considered.",
  SIZE: "How many contracts each trade uses.",
  EXIT: "When the trade closes — a profit target, a stop loss, a time exit, or a combination.",
  FILLS:
    "How fills are priced: buys toward the ask, sells toward the bid, plus slippage — never at mid.",
};

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

/** Tile header: the dial's name plus its plain-English tooltip. */
function TileLabel({ name, warn = false }: { name: string; warn?: boolean }) {
  return (
    <div className={clsx(TILE_LABEL, "flex items-center justify-between gap-1", warn && "!text-warn")}>
      <span>{name}</span>
      <Hint text={SPEC_HINTS[name.replace(/ [▾✎⌖]$/, "")] ?? name} align="right" />
    </div>
  );
}

const SELECT_CLS =
  "rounded-[7px] border border-line bg-panel-deep px-2 py-1 font-mono text-[11.5px] text-ink";

/** Tile-sized dropdown — dial values pick from the full legal range. */
const TILE_SELECT_CLS =
  "w-full cursor-pointer appearance-none rounded-[7px] border border-transparent " +
  "bg-transparent py-[1px] font-mono text-[15px] font-semibold text-ink " +
  "hover:border-line focus:border-line focus:outline-none [&>option]:bg-panel-deep";

// Strike: every .05Δ from .05 to .95 (stored as whole-number delta, 5..95)
const STRIKE_DELTAS = Array.from({ length: 19 }, (_, i) => (i + 1) * 5);
// DTE: 0–50, every day (0 = 0DTE, refused at run until the minute engine)
const DTE_CHOICES = Array.from({ length: 51 }, (_, i) => i);

/** Per-structure exit preset sets. Credit structures manage winners early
 * and cut at a DTE; debit structures think in profit multiples and stops. */
const CREDIT_EXITS = [
  "50% profit",
  "50% profit · 21 DTE",
  "25% profit",
  "75% profit",
  "21 DTE",
  "14 DTE",
  "stop 2× credit",
  "50% profit · stop 2×",
  "hold to expiry",
];
const DEBIT_EXITS = [
  "100% profit",
  "100% profit · stop 50%",
  "25% profit",
  "50% profit",
  "200% profit",
  "stop 50%",
  "50% profit · 7 DTE",
  "hold to expiry",
];

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
  const [customProfit, setCustomProfit] = useState("");
  const [customStop, setCustomStop] = useState("");
  const [customDte, setCustomDte] = useState("");
  const exitSet = !!draft.exit;
  const zeroDte = draft.dte === 0;
  const set = (patch: Partial<SpecDraft>) => onChange({ ...draft, ...patch });

  // structured custom exit → the same string grammar the presets use
  const customExitLabel = (): string | null => {
    const parts: string[] = [];
    const p = Number(customProfit);
    if (customProfit.trim() && p > 0) parts.push(`${p}% profit`);
    const s = Number(customStop);
    if (customStop.trim() && s > 0) parts.push(`stop ${s}%`);
    const d = Number(customDte);
    if (customDte.trim() && Number.isInteger(d) && d >= 0) parts.push(`${d} DTE`);
    return parts.length ? parts.join(" · ") : null;
  };
  const applyCustomExit = () => {
    const label = customExitLabel();
    if (!label) return;
    set({ exit: label });
    setCustomProfit("");
    setCustomStop("");
    setCustomDte("");
    setExitEditing(false);
  };

  const trig = draft.triggerSpec;
  const trigIndicator = trig ? INDICATORS.find((i) => i.id === trig.indicator) : undefined;
  const setTrig = (patch: Partial<TriggerSpec>) => {
    const next = { ...(trig ?? { indicator: "rsi", operator: "<", value: 30 }), ...patch };
    set({ triggerSpec: next, trigger: triggerLabel(next) });
  };

  const exitChoices =
    draft.structure === "long_call" || draft.structure === "long_put"
      ? DEBIT_EXITS
      : CREDIT_EXITS;

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
          <TileLabel name="TICKER ▾" />
          <select
            value={draft.ticker}
            onChange={(e) => set({ ticker: e.target.value as Ticker })}
            className={TILE_SELECT_CLS}
            title="Underlying ETF"
          >
            {TICKERS.map((k) => (
              <option key={k} value={k}>
                {k}
              </option>
            ))}
          </select>
        </div>
        <div className={TILE}>
          <TileLabel name="STRUCTURE ▾" />
          <select
            value={draft.structure}
            onChange={(e) => set({ structure: e.target.value as Structure })}
            className={TILE_SELECT_CLS}
            title="Position type"
          >
            {STRUCTURES.map((s) => (
              <option key={s} value={s}>
                {STRUCTURE_LABEL[s]}
              </option>
            ))}
          </select>
        </div>
        <div className={TILE}>
          <TileLabel name="STRIKE ▾" />
          <select
            value={draft.strikeLabel ? "__parsed" : draft.strikeDelta}
            onChange={(e) => {
              if (e.target.value === "__parsed") return;
              set({ strikeDelta: Number(e.target.value), strikeLabel: null });
            }}
            className={TILE_SELECT_CLS}
            title="Strike selection"
          >
            {draft.strikeLabel && <option value="__parsed">{draft.strikeLabel}</option>}
            {STRIKE_DELTAS.map((d) => (
              <option key={d} value={d}>
                .{String(d).padStart(2, "0")}Δ
              </option>
            ))}
          </select>
        </div>
        <div className={clsx(TILE, zeroDte && "!border-warn/50")}>
          <TileLabel name="DTE ▾" warn={zeroDte} />
          <select
            value={draft.dte}
            onChange={(e) => set({ dte: Number(e.target.value) })}
            className={TILE_SELECT_CLS}
            title="Days to expiration — 0 to 50"
          >
            {DTE_CHOICES.map((d) => (
              <option key={d} value={d}>
                {d === 0 ? "0 (0DTE)" : d}
              </option>
            ))}
          </select>
        </div>

        {draft.fromChart ? (
          <>
            <div className={clsx(TILE, "!border-trust-border")}>
              <div className={clsx(TILE_LABEL, "flex items-center justify-between gap-1 !text-trust")}>
                <span>ANCHOR ⌖</span>
                <Hint text={SPEC_HINTS.ANCHOR} align="right" />
              </div>
              <input
                value={draft.anchor ?? ""}
                onChange={(e) => set({ anchor: e.target.value })}
                className="w-full bg-transparent font-mono text-[13px] font-semibold focus:outline-none"
              />
            </div>
            <div className={TILE}>
              <TileLabel name="TRIGGER" />
              <div className="pt-[3px] font-mono text-[12px] font-semibold">
                {trig ? triggerLabel(trig) : draft.trigger ?? "—"}
              </div>
            </div>
          </>
        ) : (
          <>
            <div className={TILE}>
              <TileLabel name="CADENCE" />
              <div className="pt-0.5 font-mono text-[13px] font-semibold">{draft.cadence}</div>
            </div>
            <div className={TILE}>
              <TileLabel name="SIZE" />
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
          <div
            className={clsx(
              TILE_LABEL,
              "flex items-center justify-between gap-1",
              !exitSet && "!text-trust",
            )}
          >
            <span>EXIT ✎</span>
            <Hint text={SPEC_HINTS.EXIT} align="right" />
          </div>
          <div className="pt-0.5 font-mono text-[13px] font-semibold">
            {draft.exit ?? "not set"}
          </div>
        </button>
        <div className={TILE}>
          <TileLabel name="FILLS" />
          <div className="pt-[3px] font-mono text-[12px] font-semibold">bid/ask + slip 0.5</div>
        </div>
      </div>

      {(draft.fromChart || draft.triggerSpec) && (
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
          <div className="flex flex-wrap items-center gap-2 rounded-[9px] border border-trust-border px-2.5 py-[5px]">
            <span className="font-mono text-[10.5px] font-medium tracking-[.08em] text-ink-4">
              CUSTOM
            </span>
            {(
              [
                ["% profit", customProfit, setCustomProfit],
                ["% stop loss", customStop, setCustomStop],
                ["DTE", customDte, setCustomDte],
              ] as const
            ).map(([suffix, value, setter]) => (
              <label key={suffix} className="flex items-center gap-1">
                <input
                  type="number"
                  min={0}
                  value={value}
                  onChange={(e) => setter(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") applyCustomExit();
                  }}
                  placeholder="—"
                  className="w-[52px] rounded-[7px] border border-line bg-panel-deep px-2 py-[3px] text-center font-mono text-[12px] text-ink placeholder:text-ink-4 focus:border-trust-border focus:outline-none [appearance:textfield] [&::-webkit-inner-spin-button]:appearance-none [&::-webkit-outer-spin-button]:appearance-none"
                />
                <span className="font-mono text-[11px] text-ink-4">{suffix}</span>
              </label>
            ))}
            <button
              onClick={applyCustomExit}
              disabled={!customExitLabel()}
              className={clsx(
                "rounded-[7px] px-3 py-[4px] text-[12px] font-semibold",
                customExitLabel()
                  ? "bg-trust text-[#0d1216]"
                  : "cursor-not-allowed bg-raised-2 text-ink-4",
              )}
            >
              {customExitLabel() ?? "set exit"}
            </button>
          </div>
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
