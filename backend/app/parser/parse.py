"""English → StrategySpec-or-questions (TECH-SPEC §2–3, BUILD-PLAN M4).

The one rule that outranks convenience: missing exit rules, strike
selection, underlying, or an undefined trigger ("when it dips") produce
QUESTIONS — never fabricated parameters. The single allowed convention:
unstated tenor on a premium trade whose time-stop implies one (e.g.
"close at 21 DTE") may use the standard 45-DTE cycle, because the spec
screen shows every dial and nothing runs unconfirmed.

description_raw is overwritten server-side with the user's verbatim
text, so the LLM cannot paraphrase it. No key → None (the route 501s).
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from app.honesty.verdict import OPENROUTER_URL, PARSER_MODEL, _extract_json
from app.models.spec import (
    V2_INDICATORS,
    V5_INDICATORS,
    V6_INDICATORS,
    V7_INDICATORS,
    V8_INDICATORS,
    StrategySpec,
)

_V2_INDICATOR_NAMES = {i.value for i in V2_INDICATORS}
_V5_INDICATOR_NAMES = {i.value for i in V5_INDICATORS}
_V6_INDICATOR_NAMES = {i.value for i in V6_INDICATORS}
_V7_INDICATOR_NAMES = {i.value for i in V7_INDICATORS}
_V8_INDICATOR_NAMES = {i.value for i in V8_INDICATORS}


def _required_spec_version(raw_spec: dict[str, Any]) -> int:
    """Server-computed from the vocabulary actually used — the version is a
    contract, never trusted from the LLM."""
    entry = raw_spec.get("entry") or {}
    exit_rules = raw_spec.get("exit") or {}
    position = raw_spec.get("position") or {}
    conds = list(entry.get("conditions") or []) + list(exit_rules.get("conditions") or [])
    schedule = entry.get("schedule") or {}
    expiration = position.get("expiration_selection") or {}
    backtest = raw_spec.get("backtest") or {}
    # v5 (F4): vol-surface indicators — checked first, the version is the MAX
    # the vocabulary needs (a skew condition on a finest-resolution spec is 5).
    # Ladder rungs and the rearm are conditions too (review finding).
    scale_in = entry.get("scale_in") or {}
    ladder_conds = list(scale_in.get("rungs") or [])
    if isinstance(scale_in.get("rearm"), dict):
        ladder_conds.append(scale_in["rearm"])
    all_conds = conds + ladder_conds
    # v8 (parity Tier 3): the standardized IVX form — checked first, max wins
    if any(isinstance(c, dict) and c.get("indicator") in _V8_INDICATOR_NAMES
           for c in all_conds):
        return 8
    # v7 (F2/F3): flow/sentiment/pin
    if any(isinstance(c, dict) and c.get("indicator") in _V7_INDICATOR_NAMES
           for c in all_conds):
        return 7
    # v6 (F1): dealer positioning
    if any(isinstance(c, dict) and c.get("indicator") in _V6_INDICATOR_NAMES
           for c in all_conds):
        return 6
    if any(isinstance(c, dict) and c.get("indicator") in _V5_INDICATOR_NAMES
           for c in all_conds):
        return 5
    # v4 (FX.1/FX.2): per-session resolution + continuous scanning
    if (backtest.get("resolution") is not None
            or entry.get("intraday_scan") is not None):
        return 4
    # v3 (D5): the scale-in ladder and the session force-flat
    if entry.get("scale_in") is not None or exit_rules.get("close_at_time") is not None:
        return 3
    uses_v2 = (
        exit_rules.get("delta_stop_abs") is not None
        or exit_rules.get("theta_harvest") is not None
        or position.get("max_vega_per_contract") is not None
        or backtest.get("clock") not in (None, "daily")
        or schedule.get("time_of_day") is not None
        or expiration.get("min_dte") == 0
        or expiration.get("target_dte") == 0
        or any(
            isinstance(c, dict)
            and (c.get("indicator") in _V2_INDICATOR_NAMES or c.get("timeframe") == "5min")
            for c in conds
        )
    )
    return 2 if uses_v2 else 1

log = logging.getLogger("parser")

MAX_TEXT_CHARS = 1200

# The frontend proxy gives LLM routes a 100s leash (frontend/app/api/
# [...path]/route.ts) — the WHOLE attempt loop must answer inside it, or a
# healthy engine gets reported as a 504 mid-retry. The budget bounds both
# attempts together: each upstream call gets what's left of it, and a retry
# that can't get a useful slice is refused honestly instead. requests applies
# its timeout PER PHASE (connect, then read), so each phase is bounded
# separately — a single float would let one attempt run ~2x its nominal
# bound on a connect stall and blow the leash anyway.
PARSE_BUDGET_SECONDS = 90.0
_ATTEMPT_TIMEOUT_SECONDS = 60.0
_CONNECT_TIMEOUT_SECONDS = 10.0
# clarify re-parses regularly pass 30s — a thinner read slice than this is a
# retry that almost certainly times out, burning the budget to say less
_MIN_RETRY_SECONDS = 20.0

_monotonic = time.monotonic  # patchable in tests — the budget clock


def _attempt_timeout(remaining: float) -> tuple[float, float]:
    """(connect, read) bounds that keep the whole attempt inside what's left
    of the budget."""
    read = min(_ATTEMPT_TIMEOUT_SECONDS, max(1.0, remaining - _CONNECT_TIMEOUT_SECONDS))
    return (_CONNECT_TIMEOUT_SECONDS, read)


class ParserUnavailableError(Exception):
    """The upstream LLM failed or the parse budget ran out. The /parse route
    maps this to an honest 503 — it must never masquerade as a clarifying
    question (a fake question polluted the run's provenance record and put
    words in the product's mouth: "I don't guess")."""


class _UpstreamHTTPError(Exception):
    """Non-200 from the LLM gateway — transient, worth ONE retry within the
    budget; a second one is an outage, not a parsing problem, and must
    become the 503 (never the could-not-compile question)."""


class Question(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    question: str
    options: list[str] = []


class ParseOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str  # "spec" | "questions"
    spec: dict[str, Any] | None = None
    questions: list[Question] = []


_SYSTEM = """You compile plain-English options strategies into a strict JSON spec for a
backtesting research tool, or you ask clarifying questions. You NEVER guess.
Write every clarifying question and any prose you emit in English.

Respond with JSON only, one of:
  {"result": "spec", "spec": { ...full spec... }}
  {"result": "questions",
   "questions": [{"id": "kebab-id", "question": "...", "options": ["...", ...]}]}

THE SPEC (all fields required unless noted):
{
 "spec_version": 1,
 "meta": {"name": "<= 80 chars, e.g. 'SPY .30Δ short put'",
          "description_raw": "<verbatim user text>"},
 "underlying": {"ticker": "SPY" | "QQQ" | "IWM"},
 "position": {
   "structure": "short_put" | "put_credit_spread" | "call_credit_spread" | "iron_condor"
                | "covered_call" | "long_call" | "long_put",
   "legs": [{"right": "call"|"put", "side": "long"|"short", "ratio": 1,
             "strike_selection": {"method": "delta"|"offset_pct"|"width_from_leg",
                                  "value": <number>, "reference_leg": <int, width_from_leg only>}}],
   "expiration_selection": {"target_dte": <1-90>, "min_dte": <int>, "max_dte": <int>},
   "max_vega_per_contract": <number, OPTIONAL — dollars of NET position vega
                             per contract-set per vol point>
 },
 "entry": {"schedule": {"frequency": "daily"|"weekly"|"monthly"|"signal_only",
                        "day_of_week": "monday"..."friday" (weekly only),
                        "time_of_day": "HH:MM" (OPTIONAL, ET, clock "5min" only —
                                       earliest bar an entry may fill)},
           "conditions": [{"indicator": "rsi"|"sma"|"ema"|"price_vs_sma_pct"|"price_vs_ema_pct"
                          |"ema_cross_state"|"iv_percentile_1y"|"vix_level"
                          |"realized_vol_20d"|"drawdown_from_high_pct"
                          |"ivx_rank_1y"|"ivx_level_30d"|"hv_iv_spread_30d"
                          |"price_vs_vwap_pct"|"skew_25d"|"term_structure_slope"
                          |"gex_level"|"gex_rank_1y"|"dex_level"|"dex_rank_1y"
                          |"net_premium_level"|"net_premium_rank_1y"
                          |"market_tide_level"|"market_tide_rank_1y"
                          |"nope_level"|"nope_rank_1y"|"put_call_flow_ratio"
                          |"max_pain_distance_pct"|"ivx_zscore_1y",
                           "period": <int, optional>, "params": {..optional..},
                           "timeframe": "daily" (default) | "5min" (intraday bars;
                                        price-series indicators only),
                           "operator": "<"|"<="|">"|">="|"above"|"below"
                                     |"crosses_above"|"crosses_below",
                           "value": <number>}],
           "max_concurrent_positions": <1-10>,
           "intraday_scan": "every_setup" (OPTIONAL, clock "5min" only — continuous
                            opportunity scanning: one entry per signal episode,
                            re-entry after intraday exits; omit for the default
                            one-entry-per-session behavior; NEVER with scale_in),
           "scale_in": {  // OPTIONAL — a scale-in ladder (add size as a signal deepens)
             "mode": "signal_ladder", "basket": true,
             "rungs": [{<a condition: indicator/operator/value/period/timeframe>,
                        "add_contracts": <int>}],  // ordered shallow → deep
             "rearm": {<a condition — the signal LEAVING the zone>},
             "stop_adding_on": {"mode": "next_rung_not_reached"},  // only mode supported
             "max_total_contracts": <int>  // REQUIRED with scale_in — the ruin cap
           }},
 "exit": {"profit_target_pct": <number>, "stop_loss_pct": <number>,
          "time_exit_dte": <int>, "conditions": [...],
          "delta_stop_abs": <decimal in (0,1), OPTIONAL>,
          "theta_harvest": {"dte_from": <int>, "dte_to": <int>,
                            "profit_pct": <number>} (OPTIONAL),
          "close_at_time": "HH:MM" (OPTIONAL, ET, clock "5min" only — flatten every open
                           position at/after this bar; "no overnight")},
 "sizing": {"method": "fixed_contracts", "value": 1},
 "costs": {"commission_per_contract": 0.65, "slippage_half_spread_fraction": 0.85,
           "slippage_half_spread_fraction_sell": 0.90},
 "backtest": {"start": null, "end": null, "initial_capital": 25000, "seed": 42,
              "clock": "daily" (default) | "5min",
              "resolution": "finest" (OPTIONAL, clock "5min" only — per-session
                            finest-honest bar grid; ONLY on explicit phrasing)}
}

CONVENTIONS:
- A single stated slippage ("slippage 30%", "0.3 slippage") sets BOTH
  slippage_half_spread_fraction AND slippage_half_spread_fraction_sell to that
  number — never leave one side at its default when the user gave one value.
  Distinct buy/sell values only on an explicit two-value request.
- "30 delta" → {"method": "delta", "value": 0.30}. Delta values are decimals in (0, 1).
- "5% below spot" (a put) → {"method": "offset_pct", "value": -0.05}; above spot → positive.
- "ATM" / "at the money" → {"method": "delta", "value": 0.50} — an at-the-money
  option IS the 50-delta strike; never emit method "atm".
- "$5 wide" spread long leg →
  {"method": "width_from_leg", "value": 5, "reference_leg": <short leg index>}.
- Iron condor leg order: short put, long put (width ref 0), short call, long call (width ref 2).
- min_dte/max_dte: a sensible window around target
  (about target-10 floored at 1, target+15 capped at 120).
- "stop at 2x credit" → stop_loss_pct 200.
- "exit at expiration" / "hold to expiry" → time_exit_dte 0. NEVER include
  time_exit_dte unless the user states a time-based exit — untriggered positions
  are handled by the engine's expiration model; encoding an unstated rule is
  fabrication.
- The exit object contains ONLY rules the user stated. ONE stated rule (a profit
  target, OR a stop, OR a time exit) is a COMPLETE exit — do not ask for the
  others and do not add them.
- "close at X% profit or N days" → profit_target_pct X AND time_exit_dte N;
  a bare "or N days" / "at N days" / "N days left" in an exit clause means a
  time exit at N DTE — don't ask about those forms.
  BUT exits counted FROM ENTRY ("sell it after 10 days", "hold for two
  weeks") are NOT expressible as DTE without knowing the tenor relationship —
  ask ONE question offering the DTE equivalent (e.g. "exit at 35 DTE, i.e.
  10 days after entering a 45 DTE position?"). Never silently convert.
- "9 EMA below the 20 EMA" → {"indicator": "ema_cross_state", "operator": "below",
  "value": 0, "params": {"fast": 9, "slow": 20}}.
- Entry conditions present but no cadence stated → frequency "signal_only";
  a stated evaluation cadence (e.g. "daily signal") keeps that frequency with the
  condition attached.
- "weekly" with no day named → day_of_week "monday" (the standard cycle);
  do NOT ask which day.
- The number attached to an indicator IS its period and must be included:
  "RSI(14)" → period 14; "its 50 SMA" / "50-day SMA" → period 50;
  "9 EMA below the 20 EMA" → params {"fast": 9, "slow": 20}.
- "one at a time" → max_concurrent_positions 1; otherwise 5 unless stated. Never
  ask about position count or sizing — the defaults cover them.
- Percent profit/stop numbers are percents (50 = 50%). The same for percent
  indicators: price_vs_sma_pct / price_vs_ema_pct / drawdown_from_high_pct
  values are percents ("3% below its SMA" → value 3, never 0.03). Only delta
  (0.30), delta_stop_abs (0.60) and offset_pct (-0.05) take decimal values.
- A DELTA-based stop ("close/stop out if the short strike reaches/hits
  60 delta", "close when it goes to 60 delta") → delta_stop_abs 0.60 —
  NEVER stop_loss_pct (that is a percent-of-credit stop; a delta trigger is
  not a percent, and substituting one is fabrication). Rolling is NOT
  supported: "roll at X delta / X DTE" → ask whether CLOSING there is
  acceptable (offer delta_stop_abs / time_exit_dte as options).
- Distinguish the two profit-taking forms precisely:
    "close at 50% profit or 21 DTE" (single DTE bound, an OR)
        → profit_target_pct 50 AND time_exit_dte 21 — NOT theta_harvest.
    "take profits at 50% BETWEEN 21 and 7 DTE" (a DTE WINDOW, two bounds)
        → theta_harvest {"dte_from": 21, "dte_to": 7, "profit_pct": 50},
          dte_from always the HIGHER number; emit NO separate
          profit_target_pct/time_exit_dte for it.
  theta_harvest is ONLY valid on defined-max-profit structures (short_put,
  put/call credit spreads, iron_condor, covered_call). On long_call/long_put
  it is INVALID — you must ask instead (offer a plain profit target or a
  time exit). Never emit theta_harvest on a long option.
- "IV rank above 50" / "IVR > 50" → {"indicator": "ivx_rank_1y",
  "operator": ">", "value": 50} (a percentile, 0-100). "IVX above 25" (a
  LEVEL) → ivx_level_30d with value 25 (percentage points, like vix_level).
  "IV rich vs realized by 4 points" → hv_iv_spread_30d with value 4.
- IV Z-SCORE (the SAME 30d IVX series as ivx_rank_1y, standardized over the
  trailing year — σ units, raw thresholds LEGAL): "IV z-score above 1.5" /
  "IV 2 sigma above its 1-year mean" / "IV two standard deviations rich" →
  {"indicator": "ivx_zscore_1y", "operator": ">", "value": 1.5 (or 2)};
  "z-score below -1" / "IV a sigma cheap" → "<" with value -1. PERCENTILE
  phrasing (rank/IVR/percentile) stays ivx_rank_1y — never convert between
  the two forms. Vague "IV stretched/extreme" with NO number → ask for the
  threshold (offer e.g. 1.5, 2).
- VARIANCE/VOL RISK PREMIUM is the SAME quantity: "VRP above 4", "variance risk
  premium positive", "vol premium rich vs realized" → hv_iv_spread_30d (IV minus
  realized, percentage points). NEVER invent a separate "vrp" indicator.
- VOL-SURFACE SIGNALS (EOD surface fits, timeframe "daily" ONLY — never "5min";
  usable at any clock): "25-delta skew above 5" / "put skew over 5 (vols/points)"
  → {"indicator": "skew_25d", "operator": ">", "value": 5} — IV(25Δ put) −
  IV(25Δ call) at the 30d tenor, VOL POINTS; positive = puts rich. "term
  structure inverted" / "vol curve in backwardation" → {"indicator":
  "term_structure_slope", "operator": "<", "value": 0} — ATM IV(90d) − ATM
  IV(30d), vol points; "30/90 slope below −1" → value -1. Vague "when skew is
  high/extreme/steep" with NO number → ask for the threshold (offer e.g. 4, 6).
  Other tenors/deltas ("10-delta skew", "60-day skew", "1-week vs 6-month
  slope") are NOT in the vocabulary → ask, offering the two supported signals.
- DEALER POSITIONING (UW daily EOD series, timeframe "daily" ONLY — never
  "5min"; usable at any clock). The values are VENDOR UNITS, meaningful in
  SIGN and RANK only:
    "when dealers are long gamma" / "positive gamma regime" / "dealer gamma
    positive" → {"indicator": "gex_level", "operator": ">", "value": 0};
    "dealers short gamma" / "negative gamma" → operator "<", value 0. There
    is NO separate dealer_gamma_regime indicator — the sign of gex_level IS
    the regime.
    "GEX in the top quartile (of the past year)" → {"indicator":
    "gex_rank_1y", "operator": ">", "value": 75} (a percentile, 0-100, like
    ivx_rank_1y). "bottom decile" → gex_rank_1y < 10. Vague magnitude
    ("unusually high gamma", "extreme GEX") with NO stated
    percentile/quantile → ask for the threshold (offer e.g. 75, 90).
    "dealers net long delta" → {"indicator": "dex_level", "operator": ">",
    "value": 0}; net short → "<" 0. Percentile phrasing → dex_rank_1y.
    RAW-UNIT thresholds ("GEX above 5 billion", "gamma exposure over 2M")
    are NEVER emitted — the vendor's units are opaque and unstable → ask,
    offering the sign form ("long/short gamma") or a percentile rank.
- FLOW / SENTIMENT / PIN (UW daily EOD reductions, timeframe "daily" ONLY;
  usable at any clock):
    "net premium positive" / "bullish options flow" / "flow skewed to calls"
    → {"indicator": "net_premium_level", "operator": ">", "value": 0};
    bearish flow → "<" 0. DOLLAR sums are vendor magnitudes → raw thresholds
    ("net premium above $50M") are NEVER emitted — ask, offering the sign
    form or a percentile rank (net_premium_rank_1y, like ivx_rank).
    "market tide risk-on" / "market-wide flow bullish" → {"indicator":
    "market_tide_level", "operator": ">", "value": 0} — MARKET-WIDE (the
    whole tape, not the ticker); percentile phrasing → market_tide_rank_1y.
    "NOPE positive/negative" → nope_level >/< 0; "NOPE unusually high/top
    decile" → nope_rank_1y. RAW NOPE thresholds ("NOPE above 20") are NEVER
    emitted even though the metric is dimensionless — we ingest the
    VENDOR'S implementation and raw values can silently rescale → ask,
    offering sign or rank.
    "put/call ratio above 1" / "more puts than calls trading" →
    {"indicator": "put_call_flow_ratio", "operator": ">", "value": 1} —
    unit-free ratio, raw thresholds LEGAL.
    "within 1% of max pain" → TWO conditions: max_pain_distance_pct < 1 AND
    > -1 (signed % distance, front expiry — the expiry where pin dynamics
    operate). "spot below max pain" → max_pain_distance_pct > 0 (max pain
    ABOVE spot). Another expiry's max pain ("next month's max pain") is NOT
    in the vocabulary → ask.
- "keep position vega under $30 per contract" → max_vega_per_contract 30.
- INTRADAY (clock "5min"): "0DTE"/"same-day expiry" → target_dte 0, min_dte 0,
  max_dte 0-1, clock "5min". "1DTE" → target_dte 1 (TRADING days at this clock:
  Friday 1DTE correctly finds Monday). Any 0/1/2-DTE strategy, a time-of-day
  entry, or a 5-minute indicator ⇒ set backtest.clock "5min". Intraday quote
  coverage is SPY, 0-2 trading-DTE, near the money — that is the engine's
  problem to disclose, not yours to block.
- "enter at/after 10am" / "wait for the first 30 minutes" →
  schedule.time_of_day "10:00" (ET, on the 5-minute grid). Sub-5-minute times
  ("10:02") do not exist in the record → ask (offer the nearest 5-min bars).
- "5-minute RSI(14) under 30" → {"indicator": "rsi", "period": 14,
  "operator": "<", "value": 30, "timeframe": "5min"}. Same for 5-min SMA/EMA.
- "below VWAP" → {"indicator": "price_vs_vwap_pct", "operator": "<", "value": 0,
  "timeframe": "5min"}; "1% above VWAP" → value 1, operator ">". VWAP is
  session-anchored and intraday-only — NEVER emit it with timeframe "daily".
- A 1-minute chart/indicator request → ask: only the 5-minute record exists.
- SCALE-IN LADDER ("add 2 contracts at RSI 30, 3 at 25, 5 at 20, ...", "scale in as it
  falls", "buy more as the signal deepens", "average down in steps") → entry.scale_in.
  This is SUPPORTED now — RUN IT AS WRITTEN, never flatten it to a single entry and never
  say it isn't supported. Rules:
    * Only on single-leg long_call / long_put. Each rung is a full condition PLUS
      add_contracts (an ABSOLUTE contract count); rungs ordered shallow → deep.
    * The rungs ARE the entry signal: put them in scale_in.rungs and leave entry.conditions
      EMPTY. Do NOT also duplicate the first rung as an entry condition.
    * rearm = the SAME indicator leaving the zone: rungs firing on "RSI <= 30/25/20/15" →
      rearm {"indicator":"rsi", "operator":">", "value":30, ...same period/timeframe} (the
      shallowest threshold). Carry the rung indicator's period/timeframe onto every rung and
      the rearm ("5-minute RSI(14)" → period 14, timeframe "5min" on all of them).
    * A 5-minute ladder indicator ⇒ backtest.clock "5min" (like any intraday indicator).
    * max_total_contracts is REQUIRED — the ruin cap. If the user states one ("cap at 20",
      "max 20 contracts total") use it. If NO cap is stated, ASK for it: a scale-in with no
      hard cap is unbounded ruin — NEVER default or invent it.
    * "stop adding when it reverses" / "add until the move reverses, then stop" / "stop
      deepening if it doesn't keep falling" → stop_adding_on {"mode":"next_rung_not_reached"}
      (adds simply stop when the signal doesn't reach the next rung — the ONLY supported
      mode; never emit "reversal_signal").
    * sizing stays fixed_contracts (rung counts are absolute) — never risk_pct with a ladder.
- "flatten by 3:45" / "close everything by 3:45pm" / "no overnight, out by 15:45" (intraday)
  → exit.close_at_time "15:45" (ET, clock "5min"). It is a COMPLETE exit on its own.
- CONTINUOUS SCANNING — INTRADAY strategies only (0-2 DTE, intraday indicators, or
  session language like "all day"/"all session"/"through the day"): phrasing like
  "take every setup", "re-enter after I take profit", "keep selling all day",
  "trade it all day" → entry.intraday_scan "every_setup" (clock "5min"). One entry
  per SIGNAL EPISODE — the engine handles episode/re-entry mechanics; do NOT emit a
  ladder for this. Condition-less continuous scanning ("take every setup all day",
  "keep selling", "get right back in after each exit" with NO stated trigger) is
  COMPLETE as written: emit intraday_scan "every_setup" with "conditions": [] and
  frequency "daily" — the position LIFECYCLE is the setup (the engine re-enters
  after each exit); NEVER ask what defines a setup. On a LONGER-TENOR strategy
  ("every time RSI dips below 30, buy a 45 DTE call") the same words are a plain
  signal_only DAILY strategy — do NOT emit intraday_scan or clock "5min" for them.
  "once a day" / "each morning" / one entry per session phrasing → OMIT the field
  entirely (the default). FOR INTRADAY STRATEGIES (0-2 DTE / 5-min), a session
  cycle is the natural reading: when no cadence is stated use frequency "daily"
  WITHOUT asking — do not ask "how often should we enter" there (the daily-clock
  rule below still applies to everything else). NEVER combine intraday_scan with
  scale_in — a ladder is its own multi-entry semantic (if the user asks for both,
  ASK which they mean).
- RESOLUTION ("use the finest data", "minute-level where you have it", "best/highest
  resolution available", "minute resolution") → backtest.resolution "finest" (clock
  "5min"). This is a DATA POLICY, never inferred from strategy shape: a plain 0DTE
  request WITHOUT this phrasing gets NO resolution field — never guess it.
- sizing/costs/backtest: use the defaults shown unless the user states otherwise.
  spec_version: always emit 1 — the server recomputes it (ivx_zscore_1y
  lifts it to 8; flow/tide/NOPE/put-call/max-pain vocabulary lifts it to 7;
  gex/dex vocabulary
  lifts it to 6; skew_25d or term_structure_slope lifts it to 5; intraday_scan
  or backtest.resolution lifts it to 4; a scale_in ladder or a close_at_time
  lifts it to 3; v2 vocabulary lifts it to 2).

WHEN TO ASK (result "questions") — the tool's identity depends on this:
- ZERO exit rules stated → ask. No strike selection (delta/offset/ATM) stated → ask.
  For an exit-less 0DTE SELLING strategy (short premium, same-day expiry), the ask
  must OFFER the concrete choices: a force-flat time (e.g. "flatten by 15:45"), a
  profit target, or holding to settlement (0DTE settles at the close: ITM = assignment,
  OTM = expires worthless). Suggest — NEVER default one in. Entry TIME is never a
  required question (0DTE included): without time_of_day the entry window is simply
  the whole session — only ask about entry timing when the user's own words are
  ambiguous about a time they stated.
- Underlying missing or not one of SPY/QQQ/IWM → ask (offer the three).
- Vague triggers ("when it dips", "when it looks oversold") → ask what defines them
  (offer concrete options like "drawdown_from_high_pct >= 2" or "rsi(14) < 30").
- Unsupported structures (wheel, strangles, calendars, ratio spreads) → ask, offering
  the nearest supported structures as options.
- theta_harvest semantics requested on a long_call/long_put (no defined max
  profit) → ask; offer a profit target or time exit instead. Never guess.
- A scale-in ladder with NO stated max-contracts cap → ask for the cap (offer a couple of
  concrete totals). The ladder is supported; the missing RUIN CAP is the only blocker.
- No entry cadence AND no entry condition → ask — EXCEPT intraday strategies
  (0-2 DTE / 5-min), where a session cycle (frequency "daily") is the natural
  reading and is used without asking. Worked example: "Sell a 30-delta put on
  SPY, close at 50% profit" is a DAILY-clock strategy stating neither tenor nor
  cadence → ask for BOTH; emitting frequency "daily" as filler there is
  fabrication.
Ask AT MOST 4 questions, each answerable in a word or two, most important first.
Include 2-4 concrete "options" per question whenever sensible.

THE ONE ALLOWED CONVENTION: if tenor is unstated but the exit ITSELF references a
DTE ("close at 21 DTE", "exit at 10 DTE"), that implies a longer tenor on a
premium-selling structure — use target_dte 45 (the standard monthly cycle). It
applies ONLY when a DTE number appears in the exit: a bare profit target
("close at 50% profit") implies NOTHING about tenor — tenor stays a question.
Do not invent anything else.

If the user supplied ANSWERS to earlier questions, merge them with the original
text and re-evaluate: emit the spec if now unambiguous, or ask ONLY what is
still genuinely missing."""


def _call_llm(
    messages: list[dict[str, str]], api_key: str, timeout: tuple[float, float]
) -> dict[str, Any] | None:
    import requests

    body: dict[str, Any] = {
        # the parser stays on the pro model (its own override) — flash does not
        # clear the parser eval (it fabricates exits); guardrail #3
        "model": os.environ.get("OPENROUTER_PARSER_MODEL", PARSER_MODEL),
        "messages": messages,
        "temperature": 0.0,
        "response_format": {"type": "json_object"},
    }
    resp = requests.post(
        OPENROUTER_URL,
        headers={"Authorization": f"Bearer {api_key}"},
        json=body,
        timeout=timeout,
    )
    if resp.status_code != 200:
        log.warning("parser LLM HTTP %s", resp.status_code)
        raise _UpstreamHTTPError(f"HTTP {resp.status_code}")
    content = str(resp.json()["choices"][0]["message"]["content"])
    return _extract_json(content)


def _user_message(text: str, answers: dict[str, str] | None) -> str:
    payload: dict[str, Any] = {"strategy_text": text}
    if answers:
        payload["answers_to_your_questions"] = answers
    return json.dumps(payload)


def parse_strategy(text: str, answers: dict[str, str] | None = None) -> ParseOutcome | None:
    """Parse NL → spec or questions. None when no LLM key is configured.

    Raises ParserUnavailableError when the upstream LLM fails or the parse
    budget runs out — an error the route reports as a retryable 503, never
    dressed up as a clarifying question."""
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        return None

    text = text.strip()[:MAX_TEXT_CHARS]
    messages = [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": _user_message(text, answers)},
    ]

    deadline = _monotonic() + PARSE_BUDGET_SECONDS
    retry_note: str | None = None
    for attempt in range(2):
        remaining = deadline - _monotonic()  # one snapshot per iteration
        if attempt and remaining - _CONNECT_TIMEOUT_SECONDS < _MIN_RETRY_SECONDS:
            # the earned retry can't get a useful read slice inside the
            # proxy's leash — refuse it here, while we can still say so
            raise ParserUnavailableError(
                "The parser ran out of time before it could finish — "
                "try again, or rephrase the strategy in one sentence."
            )
        if retry_note:
            messages = [
                messages[0],
                {"role": "user", "content": _user_message(text, answers) + retry_note},
            ]
        try:
            data = _call_llm(messages, api_key, timeout=_attempt_timeout(remaining))
        except _UpstreamHTTPError as exc:
            if attempt:
                raise ParserUnavailableError(
                    "The parser hit an upstream error — try again, or "
                    "rephrase the strategy in one sentence."
                ) from exc
            continue  # transient gateway error — one plain retry within budget
        except Exception as exc:
            log.exception("parser LLM failed")
            raise ParserUnavailableError(
                "The parser hit an upstream error — try again, or "
                "rephrase the strategy in one sentence."
            ) from exc
        if data is None:
            retry_note = "\n\nRespond with the JSON object ONLY — no prose, no code fences."
            continue

        if data.get("result") == "questions":
            qs = [
                Question(
                    id=str(q.get("id", f"q{i}")),
                    question=str(q.get("question", "")).strip(),
                    options=[str(o) for o in q.get("options", [])][:4],
                )
                for i, q in enumerate(data.get("questions", []))
                if str(q.get("question", "")).strip()
            ][:4]
            if qs:
                return ParseOutcome(status="questions", questions=qs)
            retry_note = "\n\nYour questions list was empty. Ask real questions or emit the spec."
            continue

        raw_spec = data.get("spec")
        if not isinstance(raw_spec, dict):
            retry_note = "\n\nYour reply had neither a spec nor questions. Follow the contract."
            continue

        # guardrail: the user's words are the record, never a paraphrase
        raw_spec.setdefault("meta", {})
        raw_spec["meta"]["description_raw"] = text
        raw_spec["spec_version"] = _required_spec_version(raw_spec)
        # (ATM → .50Δ normalization lives on StrikeSelection itself — every
        # ingress that validates a spec gets it, not just this one)
        try:
            spec = StrategySpec.model_validate(raw_spec)
        except ValidationError as exc:
            log.warning("parser spec failed validation: %s", exc.errors()[:3])
            retry_note = (
                "\n\nYour spec failed schema validation: "
                + json.dumps(
                    [f"{e.get('loc')}: {e.get('msg')}" for e in exc.errors()[:5]]
                )
                + ". Fix these exact fields (or ask questions if the information is missing)."
            )
            continue
        return ParseOutcome(status="spec", spec=json.loads(spec.model_dump_json()))

    return ParseOutcome(
        status="questions",
        questions=[
            Question(
                id="could-not-compile",
                question=(
                    "I couldn't compile that into a valid spec without guessing. "
                    "Which structure is this closest to?"
                ),
                options=["short put", "put credit spread", "iron condor", "long call"],
            )
        ],
    )


# --------------------------------------------------------- UI draft mapping
def spec_to_draft(spec: dict[str, Any], text: str) -> dict[str, Any]:
    """Project a validated spec onto the UI's editable dial surface."""
    position = spec["position"]
    lead = position["legs"][0]
    sel = lead["strike_selection"]
    method = sel["method"]
    if method == "delta":
        # method "atm" can't reach here — StrikeSelection normalizes it to
        # delta 0.5 during validation, so the dial is always a real .XXΔ
        delta = int(round(abs(sel["value"]) * 100 / 5.0) * 5) or 5
        strike_label = None
    else:  # offset_pct / anything non-delta keeps its honest label
        delta = 30
        pct = sel["value"] * 100
        strike_label = f"{abs(pct):g}% {'below' if pct < 0 else 'above'} spot"

    schedule = spec["entry"]["schedule"]
    freq = schedule["frequency"]
    if freq == "weekly":
        cadence = f"weekly · {(schedule.get('day_of_week') or 'monday')[:3]}"
    elif freq == "signal_only":
        cadence = "on signal"
    else:
        cadence = freq
    if schedule.get("time_of_day"):
        cadence += f" · {schedule['time_of_day']} ET"

    exit_rules = spec["exit"]
    parts: list[str] = []
    if exit_rules.get("profit_target_pct") is not None:
        parts.append(f"{exit_rules['profit_target_pct']:g}% profit")
    if exit_rules.get("stop_loss_pct") is not None:
        parts.append(f"stop {exit_rules['stop_loss_pct']:g}%")
    if exit_rules.get("time_exit_dte") is not None:
        t = exit_rules["time_exit_dte"]
        parts.append("hold to expiry" if t == 0 else f"{t} DTE")
    if exit_rules.get("close_at_time"):
        parts.append(f"flat {exit_rules['close_at_time']}")
    if not parts and exit_rules.get("conditions"):
        parts.append("on exit signal")

    def _cond_view(c: dict[str, Any]) -> dict[str, Any]:
        return {
            "indicator": c["indicator"],
            "operator": c["operator"],
            "value": c["value"],
            **({"period": c["period"]} if c.get("period") is not None else {}),
        }

    conditions = spec["entry"].get("conditions") or []
    trigger_spec = _cond_view(conditions[0]) if conditions else None

    # Read-only projections (2026-07-07): a scale-in ladder and any condition
    # beyond the first were INVISIBLE on the pre-run screen — the dials showed
    # a strategy with no entry logic, and the SCANNING dial happily built the
    # scan+ladder combination the spec model refuses. Dials cannot edit these;
    # the rebuild passes them through whole (FX.5).
    condition_list = [_cond_view(c) for c in conditions]
    scale_in = spec["entry"].get("scale_in")
    ladder = (
        {
            "rungs": [
                {**_cond_view(r), "add": r["add_contracts"]} for r in scale_in["rungs"]
            ],
            "cap": scale_in["max_total_contracts"],
            "rearm": _cond_view(scale_in["rearm"]),
        }
        if scale_in
        else None
    )

    sizing = spec["sizing"]
    size = (
        f"{int(sizing['value'])} contract{'s' if sizing['value'] != 1 else ''}"
        if sizing["method"] == "fixed_contracts"
        else f"{sizing['value']:g}% risk"
    )

    backtest = spec.get("backtest") or {}
    # explicit dates in the strategy text pre-fill a custom window; the
    # pre-run screen still requires the user to CONFIRM a window before
    # any run (owner directive 2026-07-06) — null forces that choice
    window = (
        {"kind": "custom", "start": backtest["start"], "end": backtest.get("end")}
        if backtest.get("start")
        else None
    )

    return {
        "ticker": spec["underlying"]["ticker"],
        "structure": position["structure"],
        "strikeDelta": delta,
        "strikeLabel": strike_label,
        "dte": position["expiration_selection"]["target_dte"],
        "cadence": cadence,
        "size": size,
        # structured dials (2026-07-06): the pre-run screen edits THESE and
        # the outgoing spec is rebuilt from them — display strings above
        # stay for compatibility
        "cadenceSel": {
            "frequency": freq,
            "day_of_week": schedule.get("day_of_week"),
        },
        "sizeMethod": sizing["method"],
        "sizeValue": sizing["value"],
        "capital": backtest.get("initial_capital", 25_000),
        "clock": backtest.get("clock", "daily"),
        # FX.5 (v4 dials): surfaced so the pre-run screen shows and edits them
        "intradayScan": spec["entry"].get("intraday_scan"),
        "resolution": backtest.get("resolution"),
        # read-only entry logic (see above) — shown, never dial-edited
        "ladder": ladder,
        "conditionList": condition_list,
        "window": window,
        "exit": " · ".join(parts) if parts else None,
        "fromChart": False,
        "quote": text,
        **({"triggerSpec": trigger_spec, "trigger": None} if trigger_spec else {}),
    }
