/**
 * SpecDraft → full StrategySpec IR (docs/strategy-spec.schema.json).
 * The backend re-validates with the pydantic models; an invalid spec is a
 * 422 there, so this builder failing loudly beats it guessing quietly.
 */

import type { SpecDraft } from "./types";

type Json = Record<string, unknown>;

const SPREAD_WIDTH_DOLLARS = 5;

function legs(draft: SpecDraft): Json[] {
  const delta = draft.strikeDelta / 100;
  switch (draft.structure) {
    case "short_put":
      return [
        { right: "put", side: "short", ratio: 1, strike_selection: { method: "delta", value: delta } },
      ];
    case "put_credit_spread":
      return [
        { right: "put", side: "short", ratio: 1, strike_selection: { method: "delta", value: delta } },
        {
          right: "put",
          side: "long",
          ratio: 1,
          strike_selection: { method: "width_from_leg", value: SPREAD_WIDTH_DOLLARS, reference_leg: 0 },
        },
      ];
    case "call_credit_spread":
      return [
        { right: "call", side: "short", ratio: 1, strike_selection: { method: "delta", value: delta } },
        {
          right: "call",
          side: "long",
          ratio: 1,
          strike_selection: { method: "width_from_leg", value: SPREAD_WIDTH_DOLLARS, reference_leg: 0 },
        },
      ];
    case "iron_condor":
      return [
        { right: "put", side: "short", ratio: 1, strike_selection: { method: "delta", value: delta } },
        {
          right: "put",
          side: "long",
          ratio: 1,
          strike_selection: { method: "width_from_leg", value: SPREAD_WIDTH_DOLLARS, reference_leg: 0 },
        },
        { right: "call", side: "short", ratio: 1, strike_selection: { method: "delta", value: delta } },
        {
          right: "call",
          side: "long",
          ratio: 1,
          strike_selection: { method: "width_from_leg", value: SPREAD_WIDTH_DOLLARS, reference_leg: 2 },
        },
      ];
    case "covered_call":
      return [
        { right: "call", side: "short", ratio: 1, strike_selection: { method: "delta", value: delta } },
      ];
    case "long_call":
      return [
        { right: "call", side: "long", ratio: 1, strike_selection: { method: "delta", value: delta } },
      ];
    case "long_put":
      return [
        { right: "put", side: "long", ratio: 1, strike_selection: { method: "delta", value: delta } },
      ];
  }
}

function schedule(draft: SpecDraft): Json {
  if (draft.fromChart) return { frequency: "signal_only" };
  if (draft.cadence === "daily") return { frequency: "daily" };
  if (draft.cadence === "monthly") return { frequency: "monthly" };
  const day = draft.cadence.includes("fri") ? "friday" : "monday";
  return { frequency: "weekly", day_of_week: day };
}

function conditions(draft: SpecDraft): Json[] {
  if (!draft.fromChart) return [];
  // chart-teach trigger per the approved design: "pullback ≥2% ≤5d"
  return [{ indicator: "drawdown_from_high_pct", operator: ">=", value: 2 }];
}

function exitRules(draft: SpecDraft): Json {
  const out: Json = {};
  const label = draft.exit ?? "";
  const profit = label.match(/(\d+)%\s*profit/);
  if (profit) out.profit_target_pct = Number(profit[1]);
  const dte = label.match(/(\d+)\s*DTE/);
  if (dte) out.time_exit_dte = Number(dte[1]);
  if (label.includes("expiry")) out.time_exit_dte = 0;
  const stop = label.match(/stop\s*(\d+)(×|%)/);
  if (stop) out.stop_loss_pct = stop[2] === "×" ? Number(stop[1]) * 100 : Number(stop[1]);
  return out;
}

export function draftToSpec(draft: SpecDraft): Json {
  if (!draft.exit) {
    throw new Error("exit is unset — the spec screen must ask, never default");
  }
  return {
    spec_version: 1,
    meta: {
      name: `${draft.ticker} .${draft.strikeDelta}Δ ${draft.structure.replace(/_/g, " ")}`.slice(0, 80),
      description_raw: draft.quote,
    },
    underlying: { ticker: draft.ticker },
    position: {
      structure: draft.structure,
      legs: legs(draft),
      expiration_selection: {
        target_dte: draft.dte,
        min_dte: Math.max(1, draft.dte - 10),
        max_dte: Math.min(120, draft.dte + 15),
      },
    },
    entry: {
      schedule: schedule(draft),
      conditions: conditions(draft),
      max_concurrent_positions: 5,
    },
    exit: exitRules(draft),
    sizing: { method: "fixed_contracts", value: 1 },
    costs: { commission_per_contract: 0.65, slippage_half_spread_fraction: 0.5 },
    backtest: { start: null, end: null, initial_capital: 25000, seed: 42 },
  };
}
