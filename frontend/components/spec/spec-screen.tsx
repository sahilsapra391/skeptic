"use client";

/**
 * Spec confirmation — every dial the engine will use, editable in place:
 * ticker, structure, strike (Δ.05 steps), DTE (0–50), anchor, the entry
 * trigger (any indicator / direction / level), and the exit. A missing
 * exit becomes a focused question (never a default), 0DTE is refused
 * honestly (no minute engine yet), and nothing runs on an unconfirmed spec.
 */

import { useEffect, useState } from "react";
import clsx from "clsx";

import { Hint } from "@/components/hint";
import { getEstimate } from "@/lib/api";
import { useSettings } from "@/lib/settings";
import type { EstimatePayload, SpecDraft, Structure, Ticker, TriggerSpec, WindowKind } from "@/lib/types";
import { STRUCTURE_LABEL } from "@/lib/types";

/** Plain-English one-liners for every dial — [institutional, retail]. */
const SPEC_HINTS: Record<string, [string, string]> = {
  TICKER: [
    "Which ETF to trade options on. Coverage differs — SPY has the longest record.",
    "Which fund to trade options on. SPY has the most history to test against.",
  ],
  STRUCTURE: [
    "The option position type — what gets bought or sold at entry.",
    "The kind of options trade to place.",
  ],
  STRIKE: [
    "How far from the money, in delta. .05Δ is far out (rarely hit), .50Δ is at the money.",
    "How far from the current price. .05Δ is far away (rarely reached), .50Δ is right at the price.",
  ],
  DTE: [
    "Days to expiration when the trade opens. 0DTE needs minute data and is refused for now.",
    "How many days until the option expires when the trade opens. Same-day (0) isn't supported yet.",
  ],
  SCANNING: [
    "How often the intraday clock may open a position. Default is once per session; every setup takes one entry per signal episode and re-enters after intraday exits.",
    "How many trades can open in a day. Default is one; every setup lets it re-enter each time the signal fires again after an exit.",
  ],
  RESOLUTION: [
    "Bar resolution per session. Default simulates on the 5-minute grid; finest uses the minute grid where minute data is banked — the run discloses its mix.",
    "How fine-grained each day's simulation is. Default checks every 5 minutes; finest checks minute by minute where that data exists.",
  ],
  ANCHOR: [
    "The first pinned example on your chart — where the pattern was taught from.",
    "The first example you pinned on the chart.",
  ],
  TRIGGER: [
    "The market condition that must be true for a trade to enter.",
    "The condition that has to be true before a trade opens.",
  ],
  CADENCE: [
    "How often a new trade is considered.",
    "How often a new trade is considered.",
  ],
  SIZE: [
    "How many contracts each trade uses. Editable — sizing scales P/L and risk together.",
    "How many contracts each trade buys or sells. You can change it.",
  ],
  WINDOW: [
    "How much history to test on — required. Session counts are real coverage; time estimates are medians of measured runs on this server. Shorter windows run faster but see fewer regimes, so verdicts earn less trust.",
    "How many years to test against — you must pick one. Less history is faster but the verdict is based on less evidence.",
  ],
  CAPITAL: [
    "Starting cash for the simulated account.",
    "How much money the test account starts with.",
  ],
  EXIT: [
    "When the trade closes — a profit target, a stop loss, a time exit, or a combination.",
    "When the trade closes — take profit, cut losses, a time limit, or a mix.",
  ],
  FILLS: [
    "How fills are priced: buys toward the ask, sells toward the bid, plus slippage — never at mid.",
    "Trades are priced like real life: buy a bit above fair value, sell a bit below, plus costs.",
  ],
  LADDER: [
    "The scale-in ladder: each rung adds contracts as its signal fires, capped at the ruin limit. Rungs are the entry logic — dials can't edit them; re-compile to change the ladder.",
    "The plan for buying in steps as the signal deepens, with a hard cap. To change the steps, edit your strategy text and re-compile.",
  ],
};

const TILE = "rounded-xl border border-line bg-panel px-4 py-3.5";
const TILE_LABEL = "mb-[7px] font-mono text-[11px] font-medium tracking-[.1em] text-ink-4";

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

// crosses_* need a bar series (yesterday's value); everything else is read
// as a single point-in-time observation and the spec refuses the pair —
// mirrors CROSS_CAPABLE_INDICATORS in backend/app/models/spec.py
const CROSS_CAPABLE = new Set([
  "rsi",
  "sma",
  "ema",
  "price_vs_sma_pct",
  "price_vs_ema_pct",
  "ema_cross_state",
  "drawdown_from_high_pct",
]);

export function triggerLabel(t: TriggerSpec): string {
  const ind = INDICATORS.find((i) => i.id === t.indicator);
  const op = OPERATORS.find((o) => o.id === t.operator);
  const name = ind?.label ?? t.indicator;
  const period = ind?.period && t.period ? `(${t.period})` : "";
  return `${name}${period} ${op?.sym ?? t.operator} ${t.value}`;
}

/** Tile header: the dial's name plus its tooltip in the chosen register.
 * A tile with no SPEC_HINTS entry renders NO hint — echoing the name back
 * as its own tooltip is the bug, not a fallback (SCANNING/RESOLUTION
 * shipped that way once). */
function TileLabel({ name, warn = false }: { name: string; warn?: boolean }) {
  const { verbiage } = useSettings();
  const pair = SPEC_HINTS[name.replace(/ [▾✎⌖]$/, "")];
  const text = pair ? (verbiage === "retail" ? pair[1] : pair[0]) : null;
  return (
    <div className={clsx(TILE_LABEL, "flex items-center justify-between gap-1", warn && "!text-warn")}>
      <span>{name}</span>
      {text && <Hint text={text} align="right" />}
    </div>
  );
}

const SELECT_CLS =
  "rounded-[8px] border border-line bg-panel-deep px-2.5 py-1.5 font-mono text-[13px] text-ink";

/** Tile-sized dropdown — dial values pick from the full legal range. */
const TILE_SELECT_CLS =
  "w-full cursor-pointer appearance-none rounded-[7px] border border-transparent " +
  "bg-transparent py-[2px] font-mono text-[17px] font-semibold text-ink " +
  "hover:border-line focus:border-line focus:outline-none [&>option]:bg-panel-deep";

// Strike: every .05Δ from .05 to .95 (stored as whole-number delta, 5..95)
const STRIKE_DELTAS = Array.from({ length: 19 }, (_, i) => (i + 1) * 5);
// DTE: 0–50, every day (0 = 0DTE, refused at run until the minute engine)
const DTE_CHOICES = Array.from({ length: 51 }, (_, i) => i);

const WINDOW_KEYS: WindowKind[] = ["1y", "3y", "5y", "10y", "all"];
const WINDOW_LABEL: Record<string, string> = {
  "1y": "last 1 year",
  "3y": "last 3 years",
  "5y": "last 5 years",
  "10y": "last 10 years",
  all: "all available",
};

/** Honest time label: measured medians only — unmeasured says so. */
function fmtEst(seconds: number | null): string {
  if (seconds == null) return "unmeasured";
  if (seconds < 90) return `~${Math.max(1, Math.round(seconds))}s`;
  if (seconds < 5400) return `~${Math.round(seconds / 60)}m`;
  return `~${(seconds / 3600).toFixed(1)}h`;
}

const CADENCE_CHOICES: { v: string; label: string }[] = [
  { v: "daily", label: "daily" },
  { v: "weekly:monday", label: "weekly · mon" },
  { v: "weekly:friday", label: "weekly · fri" },
  { v: "monthly", label: "monthly" },
  { v: "signal_only", label: "on signal" },
];

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
  const settings = useSettings();
  // V-36: what the FILLS tile shows is the spec's own confirmed costs. Settings
  // are the fallback only for a draft built before they were stamped on.
  const fills = {
    commission: draft.costs?.commission_per_contract ?? settings.commission,
    slippage: draft.costs?.slippage_half_spread_fraction ?? settings.slippage,
    slippageSell:
      draft.costs?.slippage_half_spread_fraction_sell ?? settings.slippageSell,
  };
  const [exitEditing, setExitEditing] = useState(false);
  const [customProfit, setCustomProfit] = useState("");
  const [customStop, setCustomStop] = useState("");
  const [customDte, setCustomDte] = useState("");
  const exitSet = !!draft.exit;
  const zeroDte = draft.dte === 0;
  const windowSet = !!draft.window;
  const set = (patch: Partial<SpecDraft>) => onChange({ ...draft, ...patch });

  // real session counts + measured time estimates per window (per ticker+clock)
  const [estimate, setEstimate] = useState<EstimatePayload | null>(null);
  // a 0DTE dial runs intraday — the window estimates must price THAT clock
  const clock = draft.dte === 0 ? "5min" : (draft.clock ?? "daily");
  useEffect(() => {
    let alive = true;
    setEstimate(null);
    getEstimate(draft.ticker, clock)
      .then((e) => {
        if (alive) setEstimate(e);
      })
      .catch(() => undefined); // estimates degrade to "…", never invented
    return () => {
      alive = false;
    };
  }, [draft.ticker, clock]);

  // F1: dealer-positioning indicators are coverage-capped — windows
  // starting before the signal's first session are REFUSED at run time,
  // so the bound is shown here while composing (owner decision). This
  // surface is DELIBERATELY partial: it keys off the trigger dial only;
  // a v6 condition arriving via exit conditions or ladder rungs shows no
  // note here, and the run-time refusal is the guard that always holds.
  const V6_INDICATORS = ["gex_level", "gex_rank_1y", "dex_level", "dex_rank_1y"];
  const V7_FLOW = [
    "net_premium_level", "net_premium_rank_1y", "nope_level", "nope_rank_1y",
    "put_call_flow_ratio", "max_pain_distance_pct",
  ];
  const V7_TIDE = ["market_tide_level", "market_tide_rank_1y"];
  const dealerBound = draft.triggerSpec
    ? V6_INDICATORS.includes(draft.triggerSpec.indicator)
      ? estimate?.signal_windows?.dealer_positioning
      : V7_FLOW.includes(draft.triggerSpec.indicator)
        ? estimate?.signal_windows?.options_flow
        : V7_TIDE.includes(draft.triggerSpec.indicator)
          ? estimate?.signal_windows?.market_tide
          : undefined
    : undefined;
  const dealerRankBound =
    draft.triggerSpec?.indicator.endsWith("_rank_1y") && dealerBound?.rank_first
      ? dealerBound.rank_first
      : undefined;

  const windowOption = (key: WindowKind): string => {
    const opt = estimate?.options.find((o) => o.key === key);
    if (!opt) return WINDOW_LABEL[key];
    return `${WINDOW_LABEL[key]} · ${opt.sessions} sessions · ${fmtEst(opt.est_seconds)}`;
  };

  const cadenceValue = draft.cadenceSel
    ? draft.cadenceSel.frequency === "weekly"
      ? `weekly:${draft.cadenceSel.day_of_week ?? "monday"}`
      : draft.cadenceSel.frequency
    : "weekly:monday";

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
        <div className="max-w-[70%] rounded-[12px_12px_4px_12px] border border-line bg-raised px-4 py-3 font-mono text-[14px] leading-[1.55] text-ink-2">
          {draft.fromChart ? `◉ ${draft.quote}` : `“${draft.quote}”`}
        </div>
      </div>
      <p className="mb-3.5 text-[16px] text-ink-3">Here's what I heard — every dial is adjustable:</p>

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
            title={
              draft.ladder
                ? "The scale-in ladder runs on single-leg long calls / long puts only — other structures can't carry it"
                : "Position type"
            }
          >
            {STRUCTURES.map((s) => (
              // same trap class as scan+ladder: the ladder survives the
              // rebuild whole, and the spec model refuses it on anything
              // but a single-leg long — don't offer what can't run
              <option
                key={s}
                value={s}
                disabled={!!draft.ladder && s !== "long_call" && s !== "long_put"}
              >
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
        <div className={TILE}>
          <TileLabel name="DTE ▾" />
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

        {(clock === "5min" || zeroDte) && (
          <>
            <div className={TILE}>
              <TileLabel name="SCANNING ▾" />
              <select
                value={draft.intradayScan ?? ""}
                onChange={(e) =>
                  set({
                    intradayScan: e.target.value === "every_setup" ? "every_setup" : null,
                  })
                }
                className={TILE_SELECT_CLS}
                title={
                  draft.ladder
                    ? "The ladder is its own multi-entry semantic — rungs already add on each signal, so continuous scanning can't combine with it"
                    : "How often the intraday clock may open positions — every_setup takes one entry per signal episode and re-enters after intraday exits"
                }
              >
                <option value="">once / session</option>
                {/* scan + ladder is refused by the spec model — offering it
                    here just manufactures a refusal (incident 2026-07-07) */}
                <option value="every_setup" disabled={!!draft.ladder}>
                  {draft.ladder ? "every setup (n/a with ladder)" : "every setup"}
                </option>
              </select>
            </div>

            <div className={TILE}>
              <TileLabel name="RESOLUTION ▾" />
              <select
                value={draft.resolution ?? ""}
                onChange={(e) =>
                  set({ resolution: e.target.value === "finest" ? "finest" : null })
                }
                className={TILE_SELECT_CLS}
                title="Bar resolution per session — finest uses the minute grid where minute data is banked (the run discloses its mix); default is the 5-minute grid everywhere"
              >
                <option value="">5-min grid</option>
                <option value="finest">finest available</option>
              </select>
            </div>
          </>
        )}

        {draft.fromChart ? (
          <>
            <div className={clsx(TILE, "!border-trust-border")}>
              <div className={clsx(TILE_LABEL, "flex items-center justify-between gap-1 !text-trust")}>
                <span>ANCHOR ⌖</span>
                <Hint text={settings.verbiage === "retail" ? SPEC_HINTS.ANCHOR[1] : SPEC_HINTS.ANCHOR[0]} align="right" />
              </div>
              <input
                value={draft.anchor ?? ""}
                onChange={(e) => set({ anchor: e.target.value })}
                className="w-full bg-transparent font-mono text-[15px] font-semibold focus:outline-none"
              />
            </div>
            <div className={TILE}>
              <TileLabel name="TRIGGER" />
              <div className="pt-[3px] font-mono text-[13.5px] font-semibold">
                {trig ? triggerLabel(trig) : draft.trigger ?? "—"}
              </div>
            </div>
          </>
        ) : (
          <div className={TILE}>
            <TileLabel name="CADENCE ▾" />
            <select
              value={cadenceValue}
              onChange={(e) => {
                const v = e.target.value;
                const [freq, day] = v.split(":");
                const sel = { frequency: freq, day_of_week: day ?? null };
                const label = CADENCE_CHOICES.find((c) => c.v === v)?.label ?? freq;
                set({ cadenceSel: sel, cadence: label });
              }}
              className={TILE_SELECT_CLS}
              title="Entry cadence"
            >
              {CADENCE_CHOICES.map((c) => (
                <option key={c.v} value={c.v}>
                  {c.label}
                </option>
              ))}
            </select>
          </div>
        )}

        <div className={TILE}>
          <TileLabel name="SIZE ✎" />
          <div className="flex items-baseline gap-1.5">
            <input
              type="number"
              min={1}
              max={1000}
              value={draft.sizeValue ?? 1}
              onChange={(e) =>
                set({
                  sizeValue: Math.max(1, Math.min(1000, Math.round(Number(e.target.value) || 1))),
                })
              }
              className="w-[64px] bg-transparent py-[2px] font-mono text-[17px] font-semibold text-ink focus:outline-none [appearance:textfield] [&::-webkit-inner-spin-button]:appearance-none"
              title="Contracts per trade"
            />
            <span className="font-mono text-[11px] text-ink-4">
              {draft.sizeMethod === "risk_pct_of_equity" ? "% risk" : "contracts"}
            </span>
          </div>
        </div>

        <div className={clsx(TILE, !windowSet && "!border-trust-border !bg-trust-dim")}>
          <div
            className={clsx(
              TILE_LABEL,
              "flex items-center justify-between gap-1",
              !windowSet && "!text-trust",
            )}
          >
            <span>WINDOW ▾</span>
            <Hint
              text={settings.verbiage === "retail" ? SPEC_HINTS.WINDOW[1] : SPEC_HINTS.WINDOW[0]}
              align="right"
            />
          </div>
          <select
            value={draft.window ? draft.window.kind : "__unset"}
            onChange={(e) => {
              const v = e.target.value as WindowKind | "__unset" | "__custom";
              if (v === "__unset") return;
              if (v === "__custom" || v === "custom") {
                // confirm the text-supplied dates as the window
                set({ window: { ...(draft.window ?? {}), kind: "custom" } });
                return;
              }
              set({ window: { kind: v } });
            }}
            className={TILE_SELECT_CLS}
            title={estimate?.basis.note ?? "Data window — required before running"}
          >
            {!windowSet && <option value="__unset">choose…</option>}
            {draft.window?.kind === "custom" || draft.window?.start ? (
              <option value="custom">
                {draft.window?.start ?? "?"} → {draft.window?.end ?? "today"}
              </option>
            ) : null}
            {WINDOW_KEYS.map((k) => (
              <option key={k} value={k}>
                {windowOption(k)}
              </option>
            ))}
          </select>
          {dealerBound && (
            <div className="mt-1 font-mono text-[10.5px] leading-[1.4] text-ink-4">
              dealer-positioning data starts {dealerBound.first} — earlier
              windows are refused
              {dealerRankBound
                ? `; rank filters evaluable from ${dealerRankBound}`
                : null}
            </div>
          )}
        </div>

        <div className={TILE}>
          <TileLabel name="CAPITAL ✎" />
          <div className="flex items-baseline gap-1.5">
            <span className="font-mono text-[15px] font-semibold text-ink-4">$</span>
            <input
              type="number"
              min={1000}
              max={10_000_000}
              step={1000}
              value={draft.capital ?? 25000}
              onChange={(e) =>
                set({
                  capital: Math.max(
                    1000,
                    Math.min(10_000_000, Math.round(Number(e.target.value) || 25000)),
                  ),
                })
              }
              className="w-full bg-transparent py-[2px] font-mono text-[17px] font-semibold text-ink focus:outline-none [appearance:textfield] [&::-webkit-inner-spin-button]:appearance-none"
              title="Starting capital"
            />
          </div>
        </div>

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
            <Hint text={settings.verbiage === "retail" ? SPEC_HINTS.EXIT[1] : SPEC_HINTS.EXIT[0]} align="right" />
          </div>
          <div className="pt-0.5 font-mono text-[15px] font-semibold">
            {draft.exit ?? "not set"}
          </div>
        </button>
        <div className={TILE}>
          <TileLabel name="FILLS" />
          <div className="pt-[3px] font-mono text-[13.5px] font-semibold">
            {/* V-36: the CONFIRMED costs, not a live Settings read. These are
                what will run; Settings only seeded them at parse time. */}
            bid/ask + slip {fills.slippage}/{fills.slippageSell} · ${fills.commission}/ct
          </div>
        </div>
      </div>

      {draft.ladder && (
        <div className="mt-3 flex flex-wrap items-center gap-2 rounded-xl border border-line bg-panel px-3.5 py-3">
          <span className="flex items-center gap-1.5 font-mono text-[10.5px] font-medium tracking-[.1em] text-ink-4">
            LADDER — ADDS ON SIGNAL
            <Hint
              text={settings.verbiage === "retail" ? SPEC_HINTS.LADDER[1] : SPEC_HINTS.LADDER[0]}
            />
          </span>
          {draft.ladder.rungs.map((r, i) => (
            <span
              key={i}
              className="rounded-[8px] border border-line bg-panel-deep px-2.5 py-1.5 font-mono text-[13px] text-ink"
            >
              {triggerLabel(r)} → +{r.add}
            </span>
          ))}
          <span className="font-mono text-[11px] text-ink-4">
            cap {draft.ladder.cap} · re-arm {triggerLabel(draft.ladder.rearm)}
          </span>
        </div>
      )}

      {(draft.fromChart || draft.triggerSpec) && (
        <div className="mt-3 flex flex-wrap items-center gap-2 rounded-xl border border-line bg-panel px-3.5 py-3">
          <span className="font-mono text-[10.5px] font-medium tracking-[.1em] text-ink-4">
            TRIGGER — ENTER WHEN
          </span>
          <select
            value={trig?.indicator ?? "drawdown_from_high_pct"}
            onChange={(e) => {
              const ind = INDICATORS.find((i) => i.id === e.target.value);
              const op = trig?.operator;
              setTrig({
                indicator: e.target.value,
                period: ind?.period ? (trig?.period ?? 14) : undefined,
                // crosses need a bar series — switching to a point-in-time
                // indicator keeps the direction but drops to the level form
                ...(op?.startsWith("crosses_") && !CROSS_CAPABLE.has(e.target.value)
                  ? { operator: op === "crosses_above" ? ">" : "<" }
                  : {}),
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
            {OPERATORS.filter(
              // hide crosses for point-in-time indicators, but never hide
              // the operator a loaded draft actually carries — the dial
              // shows the stored truth; the run's 422 names the fix
              (o) =>
                !o.id.startsWith("crosses_") ||
                o.id === trig?.operator ||
                CROSS_CAPABLE.has(trig?.indicator ?? "drawdown_from_high_pct"),
            ).map((o) => (
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
          {(draft.conditionList ?? []).length > 1 ? (
            // conditions beyond the first survive the rebuild whole (FX.5)
            // but the dial can't edit them — show them so the screen never
            // hides part of the entry logic
            <span className="font-mono text-[13px] text-ink-3">
              {(draft.conditionList ?? [])
                .slice(1)
                .map((c) => `& ${triggerLabel(c)}`)
                .join("  ")}
            </span>
          ) : (
            <span className="font-mono text-[11px] text-ink-4">
              e.g. RSI below 30 · pullback ≥ 2% · VIX above 25
            </span>
          )}
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
              className="rounded-full border border-trust-border px-4 py-[7px] text-[13.5px] text-trust hover:bg-trust-dim"
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
                  ? "bg-trust text-on-accent"
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
        <div className="mt-3 rounded-xl border border-line px-3.5 py-3 text-[13px] leading-[1.5] text-ink-3">
          0DTE runs on the 5-minute intraday engine — sessions with intraday coverage only
          (the results page shows the exact window and resolution mix it was computed on).
        </div>
      )}

      {!windowSet && (
        <div className="mt-3 rounded-xl border border-trust-border bg-trust-dim px-3.5 py-3 text-[13.5px] leading-[1.5] text-ink">
          <b className="text-trust">Pick a data window</b> — how much history should this run test
          against? Shorter is faster; longer sees more market regimes and earns more trust.
        </div>
      )}

      <div className="mt-5 flex items-center justify-between">
        <span className="text-[12.5px] text-ink-4">
          {estimate
            ? `Nothing runs on an unconfirmed spec. Time estimates: ${estimate.basis.note}.`
            : "Nothing runs on an unconfirmed spec."}
        </span>
        <button
          onClick={onRun}
          disabled={!exitSet || !windowSet}
          className={clsx(
            "rounded-[11px] px-6 py-3 text-[15.5px]",
            exitSet && windowSet
              ? "bg-trust font-bold text-on-accent"
              : "cursor-not-allowed bg-raised-2 text-ink-4",
          )}
        >
          Run the gauntlet →
        </button>
      </div>
    </div>
  );
}
