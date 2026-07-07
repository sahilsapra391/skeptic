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
  // the structured dial wins when present (pre-run cadence tile)
  if (draft.cadenceSel) {
    return {
      frequency: draft.cadenceSel.frequency,
      day_of_week:
        draft.cadenceSel.frequency === "weekly"
          ? (draft.cadenceSel.day_of_week ?? "monday")
          : null,
    };
  }
  if (draft.fromChart || draft.cadence === "on signal") return { frequency: "signal_only" };
  if (draft.cadence === "daily") return { frequency: "daily" };
  if (draft.cadence === "monthly") return { frequency: "monthly" };
  const day = draft.cadence.includes("fri") ? "friday" : "monday";
  return { frequency: "weekly", day_of_week: day };
}

function conditions(draft: SpecDraft, baseConds: Json[] = []): Json[] {
  const t = draft.triggerSpec;
  if (t) {
    const first = baseConds[0];
    const unedited =
      first != null &&
      first.indicator === t.indicator &&
      first.operator === t.operator &&
      first.value === t.value &&
      (t.period == null || first.period === t.period);
    // trigger dial untouched → ALL base conditions pass through whole
    // (multi-condition specs and their timeframes survive the rebuild)
    if (unedited) return baseConds;
    const cond: Json = { indicator: t.indicator, operator: t.operator, value: t.value };
    if (t.period != null) cond.period = t.period;
    // an EDITED trigger keeps the base condition's timeframe (the dial
    // cannot express it; dropping it breaks intraday-only indicators)
    if (first != null && first.indicator === t.indicator && first.timeframe != null) {
      cond.timeframe = first.timeframe;
    }
    return [cond];
  }
  if (draft.fromChart) {
    // chart drafts always carry a pin-derived trigger — one missing is
    // corrupted state, and failing loudly beats silently backtesting a
    // canned trigger nobody set
    throw new Error("chart draft lost its trigger — recompile from the chart");
  }
  return baseConds.length > 0 ? baseConds : [];
}

function exitRules(draft: SpecDraft): Json {
  const out: Json = {};
  const label = draft.exit ?? "";
  // decimals are legal everywhere the label grammar carries percents —
  // "12.5% profit" must never parse as 5%
  const profit = label.match(/(\d+(?:\.\d+)?)%\s*profit/);
  if (profit) out.profit_target_pct = Number(profit[1]);
  const dte = label.match(/(\d+)\s*DTE/);
  if (dte) out.time_exit_dte = Number(dte[1]);
  if (label.includes("expiry")) out.time_exit_dte = 0;
  const stop = label.match(/stop\s*(\d+(?:\.\d+)?)(×|%)/);
  if (stop) out.stop_loss_pct = stop[2] === "×" ? Number(stop[1]) * 100 : Number(stop[1]);
  // "flat 15:45" — the session force-flat is a complete exit on its own
  const flat = label.match(/flat\s*(\d{2}:\d{2})/);
  if (flat) out.close_at_time = flat[1];
  return out;
}

/** Mirror of the server's _required_spec_version — the version is a
 * contract computed from the vocabulary actually used, so a dial-rebuilt
 * spec must carry the same version the server would compute. */
function computeSpecVersion(spec: Json): number {
  const entry = (spec.entry ?? {}) as Json;
  const exit = (spec.exit ?? {}) as Json;
  const backtest = (spec.backtest ?? {}) as Json;
  const position = (spec.position ?? {}) as Json;
  const sel = (position.expiration_selection ?? {}) as Json;
  const sched = (entry.schedule ?? {}) as Json;
  const conds = [
    ...((entry.conditions as Json[] | undefined) ?? []),
    ...((exit.conditions as Json[] | undefined) ?? []),
  ];
  const V2_INDICATORS = new Set([
    "ivx_rank_1y", "ivx_level_30d", "hv_iv_spread_30d", "price_vs_vwap_pct",
  ]);
  // F4: vol-surface vocabulary — checked first, the version is the MAX
  // the vocabulary needs (a skew condition on a finest-resolution spec is 5).
  // Ladder rungs and the rearm are conditions too (review finding).
  const V5_INDICATORS = new Set(["skew_25d", "term_structure_slope"]);
  // F1: dealer-positioning vocabulary lifts to 6 — checked before v5,
  // the version is the MAX the vocabulary needs
  const V6_INDICATORS = new Set([
    "gex_level", "gex_rank_1y", "dex_level", "dex_rank_1y",
  ]);
  const scaleIn = (entry.scale_in ?? {}) as Json;
  const ladderConds = [
    ...((scaleIn.rungs as Json[] | undefined) ?? []),
    ...(scaleIn.rearm ? [scaleIn.rearm as Json] : []),
  ];
  const allConds = [...conds, ...ladderConds];
  if (allConds.some((c) => V6_INDICATORS.has(String(c.indicator)))) return 6;
  if (allConds.some((c) => V5_INDICATORS.has(String(c.indicator)))) return 5;
  if (backtest.resolution != null || entry.intraday_scan != null) return 4;
  if (entry.scale_in != null || exit.close_at_time != null) return 3;
  if (
    exit.delta_stop_abs != null ||
    exit.theta_harvest != null ||
    position.max_vega_per_contract != null ||
    (backtest.clock != null && backtest.clock !== "daily") ||
    sched.time_of_day != null ||
    sel.min_dte === 0 ||
    sel.target_dte === 0 ||
    conds.some(
      (c) => V2_INDICATORS.has(String(c.indicator)) || c.timeframe === "5min",
    )
  )
    return 2;
  return 1;
}

export function draftToSpec(draft: SpecDraft, base?: Json | null): Json {
  if (!draft.exit) {
    throw new Error("exit is unset — the spec screen must ask, never default");
  }
  // FX.5: 0DTE runs on the 5-minute intraday engine (shipped) — a 0DTE
  // dial now emits an intraday spec instead of refusing. The DTE band is
  // the intraday slice (0–2 trading DTE).
  const zeroDte = draft.dte === 0;
  const baseEntry = ((base?.entry ?? {}) as Json) ?? {};
  const baseExit = ((base?.exit ?? {}) as Json) ?? {};
  const baseBacktest = ((base?.backtest ?? {}) as Json) ?? {};
  const baseSchedule = (baseEntry.schedule ?? {}) as Json;
  const intraday =
    zeroDte ||
    draft.clock === "5min" ||
    baseBacktest.clock === "5min" ||
    draft.resolution === "finest" ||
    draft.intradayScan === "every_setup";

  // Parser-only vocabulary the dials cannot express survives a dial edit
  // (the D5d hole: the rebuild silently DROPPED the ladder). What survives:
  // scale_in, intraday_scan, resolution, time_of_day, clock, ALL entry
  // conditions when the trigger dial is unedited (multi-condition specs +
  // timeframes), the edited trigger's timeframe/period extras, label-
  // inexpressible exit fields (delta stops, theta harvest, exit
  // conditions), max_concurrent and max_vega. close_at_time lives in the
  // exit LABEL and is removed by replacing the exit — visibly.
  const scaleIn = baseEntry.scale_in ?? null;
  const baseConds = (baseEntry.conditions as Json[] | undefined) ?? [];
  const entry: Json = {
    schedule: {
      ...schedule(draft),
      ...(baseSchedule.time_of_day != null
        ? { time_of_day: baseSchedule.time_of_day }
        : {}),
    },
    // a ladder IS the entry signal — its conditions stay empty; dials
    // cannot edit rungs, so the base ladder passes through whole
    conditions: scaleIn != null ? [] : conditions(draft, baseConds),
    max_concurrent_positions:
      (baseEntry.max_concurrent_positions as number | undefined) ?? 5,
  };
  if (scaleIn != null) entry.scale_in = scaleIn;
  // the dial's OFF state is null and must OVERRIDE the base; only a draft
  // that never carried the key (undefined) defers to the parsed spec —
  // ?? here would silently run every_setup while the dial showed "once /
  // session" (review blocker)
  const scan =
    draft.intradayScan !== undefined
      ? draft.intradayScan
      : (baseEntry.intraday_scan as string | undefined);
  if (scan === "every_setup") entry.intraday_scan = "every_setup";

  // close_at_time is owned by the exit LABEL grammar ("flat 15:45"): it
  // round-trips while present and is REMOVED when the user replaces the
  // exit — display and run always agree (review: invisible re-attachment
  // was silent divergence). Exit fields the label can NEVER express
  // (delta stops, theta harvest, exit conditions) pass through from the
  // base — matching what an untouched verbatim run would do.
  const exit: Json = exitRules(draft);
  if (baseExit.delta_stop_abs != null && exit.delta_stop_abs == null) {
    exit.delta_stop_abs = baseExit.delta_stop_abs;
  }
  if (baseExit.theta_harvest != null && exit.theta_harvest == null) {
    exit.theta_harvest = baseExit.theta_harvest;
  }
  if (baseExit.conditions != null && exit.conditions == null) {
    exit.conditions = baseExit.conditions;
  }

  const backtest: Json = {
    // start/end are set by startBacktest from the CONFIRMED window —
    // building a spec without one is a bug it will throw on
    start: null,
    end: null,
    initial_capital: draft.capital ?? 25000,
    seed: 42,
  };
  if (intraday) backtest.clock = "5min";
  const resolution =
    draft.resolution !== undefined
      ? draft.resolution
      : (baseBacktest.resolution as string | undefined);
  if (resolution === "finest" && intraday) backtest.resolution = "finest";

  const spec: Json = {
    spec_version: 1,
    meta: {
      name: `${draft.ticker} .${draft.strikeDelta}Δ ${draft.structure.replace(/_/g, " ")}`.slice(0, 80),
      description_raw: draft.quote,
    },
    underlying: { ticker: draft.ticker },
    position: {
      structure: draft.structure,
      legs: legs(draft),
      ...(((base?.position ?? {}) as Json).max_vega_per_contract != null
        ? {
            max_vega_per_contract: ((base?.position ?? {}) as Json)
              .max_vega_per_contract,
          }
        : {}),
      // 0DTE band matches the parser's convention (max 1: same-day intent,
      // next-day fallback only — review: band drift between ingresses)
      expiration_selection: zeroDte
        ? { target_dte: 0, min_dte: 0, max_dte: 1 }
        : {
            target_dte: draft.dte,
            min_dte: Math.max(intraday ? 0 : 1, draft.dte - 10),
            max_dte: Math.min(120, draft.dte + 15),
          },
    },
    entry,
    exit,
    sizing: {
      method: draft.sizeMethod ?? "fixed_contracts",
      value: draft.sizeValue ?? 1,
    },
    costs: { commission_per_contract: 0.65, slippage_half_spread_fraction: 0.5 },
    backtest,
  };
  spec.spec_version = computeSpecVersion(spec);
  return spec;
}
