"""Strategy Spec IR — pydantic models for docs/strategy-spec.schema.json.

These models are maintained to match the JSON schema exactly (same enums,
same numeric bounds, same required fields). One deliberate strictness beyond
JSON Schema defaults: `extra="forbid"` on every model, not just the root, so
a malformed spec fails loudly instead of silently dropping keys (guardrail #3:
nothing about a spec is ever silent).

Any change here is a versioned migration via `spec_version`, never silent.
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Ticker(StrEnum):
    SPY = "SPY"
    QQQ = "QQQ"
    IWM = "IWM"


class Structure(StrEnum):
    SHORT_PUT = "short_put"
    PUT_CREDIT_SPREAD = "put_credit_spread"
    CALL_CREDIT_SPREAD = "call_credit_spread"
    IRON_CONDOR = "iron_condor"
    COVERED_CALL = "covered_call"
    LONG_CALL = "long_call"
    LONG_PUT = "long_put"


class Right(StrEnum):
    CALL = "call"
    PUT = "put"


class Side(StrEnum):
    LONG = "long"
    SHORT = "short"


class StrikeMethod(StrEnum):
    DELTA = "delta"
    OFFSET_PCT = "offset_pct"
    ATM = "atm"
    WIDTH_FROM_LEG = "width_from_leg"


class Frequency(StrEnum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    SIGNAL_ONLY = "signal_only"


class DayOfWeek(StrEnum):
    MONDAY = "monday"
    TUESDAY = "tuesday"
    WEDNESDAY = "wednesday"
    THURSDAY = "thursday"
    FRIDAY = "friday"


class Indicator(StrEnum):
    RSI = "rsi"
    SMA = "sma"
    EMA = "ema"
    PRICE_VS_SMA_PCT = "price_vs_sma_pct"
    PRICE_VS_EMA_PCT = "price_vs_ema_pct"
    EMA_CROSS_STATE = "ema_cross_state"
    IV_PERCENTILE_1Y = "iv_percentile_1y"
    VIX_LEVEL = "vix_level"
    REALIZED_VOL_20D = "realized_vol_20d"
    DRAWDOWN_FROM_HIGH_PCT = "drawdown_from_high_pct"
    # spec v2 (D1c): vendor IVX/HV analytics, 2005+ on all three tickers
    IVX_RANK_1Y = "ivx_rank_1y"
    IVX_LEVEL_30D = "ivx_level_30d"
    HV_IV_SPREAD_30D = "hv_iv_spread_30d"
    # spec v2 (D2c): intraday-only — % distance from session-anchored VWAP
    PRICE_VS_VWAP_PCT = "price_vs_vwap_pct"
    # spec v5 (F4): IVS-derived vol-surface signals, 2007+ (VOL POINTS)
    SKEW_25D = "skew_25d"
    TERM_STRUCTURE_SLOPE = "term_structure_slope"


class Timeframe(StrEnum):
    """Which bar series a condition's indicator reads (D2c). daily = the
    underlying daily closes (v1 semantics). 5min = the run's 5-minute
    underlying lasts — requires clock="5min"."""

    DAILY = "daily"
    FIVE_MIN = "5min"


# Indicators (and fields, checked separately) that require spec_version 2 —
# a v1 spec using v2 vocabulary is a versioning error, never silent.
V2_INDICATORS = {
    Indicator.IVX_RANK_1Y,
    Indicator.IVX_LEVEL_30D,
    Indicator.HV_IV_SPREAD_30D,
    Indicator.PRICE_VS_VWAP_PCT,
}

# F4: vol-surface indicators require spec_version 5 — a versioned
# migration like every vocabulary addition, never silent.
V5_INDICATORS = {
    Indicator.SKEW_25D,
    Indicator.TERM_STRUCTURE_SLOPE,
}

# Price-series indicators that can read the 5-minute timeframe; everything
# else (VIX, IVX/HV, chain IV percentile, realized vol, drawdown) is a
# daily series by construction.
INTRADAY_CAPABLE_INDICATORS = {
    Indicator.RSI,
    Indicator.SMA,
    Indicator.EMA,
    Indicator.PRICE_VS_SMA_PCT,
    Indicator.PRICE_VS_EMA_PCT,
    Indicator.EMA_CROSS_STATE,
    Indicator.PRICE_VS_VWAP_PCT,
}


class Operator(StrEnum):
    LT = "<"
    LTE = "<="
    GT = ">"
    GTE = ">="
    ABOVE = "above"
    BELOW = "below"
    CROSSES_ABOVE = "crosses_above"
    CROSSES_BELOW = "crosses_below"


class Meta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(max_length=80)
    description_raw: str = Field(
        description="The user's original natural-language description, verbatim."
    )


class Underlying(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ticker: Ticker


class StrikeSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    method: StrikeMethod
    value: float
    reference_leg: int | None = Field(default=None, ge=0)

    @model_validator(mode="before")
    @classmethod
    def _atm_is_50_delta(cls, data: Any) -> Any:
        # ATM IS the 50-delta strike (owner directive). Normalizing at the
        # model means EVERY ingress — parser, POST /api/backtest, stored
        # specs re-validated for a run — lands on the same editable .50Δ,
        # and a spread can never collide both legs onto the spot strike.
        if isinstance(data, dict) and data.get("method") == "atm":
            return {**data, "method": "delta", "value": 0.5}
        return data

    @model_validator(mode="after")
    def _width_needs_reference(self) -> StrikeSelection:
        if self.method is StrikeMethod.WIDTH_FROM_LEG:
            if self.reference_leg is None:
                raise ValueError(
                    "strike_selection.reference_leg is required when method=width_from_leg"
                )
            if self.value <= 0:
                raise ValueError(
                    "strike_selection.value must be a positive dollar width "
                    "when method=width_from_leg"
                )
        return self


class Leg(BaseModel):
    model_config = ConfigDict(extra="forbid")

    right: Right
    side: Side
    strike_selection: StrikeSelection
    ratio: int = Field(ge=1, le=4)


class ExpirationSelection(BaseModel):
    """DTE bounds. At clock="daily" these are CALENDAR days (unchanged
    v1 semantics). At clock="5min" they are TRADING days (owner-confirmed:
    Friday "1DTE" selects Monday's expiry), and 0 (same-day expiry) is
    legal — 0DTE requires spec_version 2."""

    model_config = ConfigDict(extra="forbid")

    target_dte: int = Field(ge=0, le=90)
    min_dte: int = Field(ge=0)
    max_dte: int = Field(le=120)


class Position(BaseModel):
    model_config = ConfigDict(extra="forbid")

    structure: Structure
    legs: list[Leg] = Field(min_length=1, max_length=4)
    expiration_selection: ExpirationSelection
    # spec v2 (D1c): entry-time cap on |NET vega| of the contract-set, in
    # DOLLARS per contract-set per vol point (leg vegas sum signed —
    # long +, short −, × ratio — then abs, × 100). Owner amendment 2.
    max_vega_per_contract: float | None = Field(default=None, gt=0)


class Condition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    indicator: Indicator
    operator: Operator
    value: float
    period: int | None = Field(default=None, ge=2, le=400)
    params: dict[str, Any] | None = None
    # spec v2 (D2c): which bar series the indicator reads
    timeframe: Timeframe = Timeframe.DAILY

    @model_validator(mode="after")
    def _timeframe_fits_indicator(self) -> Condition:
        if (
            self.timeframe is Timeframe.FIVE_MIN
            and self.indicator not in INTRADAY_CAPABLE_INDICATORS
        ):
            raise ValueError(
                f"indicator {self.indicator.value} is a daily series — it cannot "
                "read the 5min timeframe"
            )
        if self.indicator is Indicator.PRICE_VS_VWAP_PCT and (
            self.timeframe is not Timeframe.FIVE_MIN
        ):
            raise ValueError(
                "price_vs_vwap_pct is intraday-only — VWAP is session-anchored; "
                'set timeframe "5min"'
            )
        return self


class Schedule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    frequency: Frequency
    day_of_week: DayOfWeek | None = None
    day_of_month: int | None = Field(default=None, ge=1, le=28)
    # spec v2 (D2c): earliest ET bar an entry may fill (clock="5min" only)
    time_of_day: str | None = Field(
        default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$"
    )


class ScaleInMode(StrEnum):
    SIGNAL_LADDER = "signal_ladder"  # this phase; leaves room for future modes


class StopAddingMode(StrEnum):
    NEXT_RUNG_NOT_REACHED = "next_rung_not_reached"  # default; a rung fires iff reached
    REVERSAL_SIGNAL = "reversal_signal"  # reserved (D5a wires the field, not the logic)


class Rung(Condition):
    """One ladder step (spec v3, D5a): a full entry Condition plus the number
    of contracts to ADD when it fires. Each rung fires at most once per basket;
    add_contracts is an ABSOLUTE contract count, never a percent of capital."""

    add_contracts: int = Field(ge=1)


class StopAddingOn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # only next_rung_not_reached is implemented in D5a — the field exists so
    # the schema is forward-compatible when the reversal detector lands later
    mode: StopAddingMode = StopAddingMode.NEXT_RUNG_NOT_REACHED


class ScaleIn(BaseModel):
    """The scale-in ladder primitive (spec v3, D5a). Legs accumulate into ONE
    basket with a blended cost basis; the whole basket exits together. A run
    that uses scale_in is hard-capped at insufficient_evidence until the
    martingale defenses land (D5c) — the interlock lives in the honesty layer,
    not here, but every scale_in spec inherits it (docs/HONESTY.md)."""

    model_config = ConfigDict(extra="forbid")

    mode: ScaleInMode
    basket: bool
    rungs: list[Rung] = Field(min_length=1, max_length=8)
    rearm: Condition
    stop_adding_on: StopAddingOn = Field(default_factory=StopAddingOn)
    max_total_contracts: int = Field(ge=1)  # the ruin cap (required; parser Q4)

    @model_validator(mode="after")
    def _basket_must_be_true(self) -> ScaleIn:
        if self.basket is not True:
            raise ValueError(
                "scale_in.basket must be true — legs accumulate into one blended "
                "basket that exits together"
            )
        return self

    @model_validator(mode="after")
    def _cap_covers_largest_rung(self) -> ScaleIn:
        biggest = max(r.add_contracts for r in self.rungs)
        if self.max_total_contracts < biggest:
            raise ValueError(
                f"scale_in.max_total_contracts ({self.max_total_contracts}) must be "
                f">= the largest rung add_contracts ({biggest})"
            )
        return self


class IntradayScan(StrEnum):
    """FX.2 (spec v4): how often the intraday clock may open positions.

    ONCE_PER_SESSION is the D2 behavior (the first fill ends the session's
    entries). EVERY_SETUP is continuous opportunity scanning: one entry per
    SIGNAL EPISODE (entry conditions transitioning false→true arm exactly
    one entry — a persistent signal is one setup, never a burst), re-entry
    after intraday exits, and for condition-less strategies the position
    LIFECYCLE is the episode (re-arm when a position closes — the
    always-in-the-market pattern). Bounded by max_concurrent_positions and
    capital; every fill still requires a real liquid quote (guardrail #1)
    and every skip is counted with a reason."""

    ONCE_PER_SESSION = "once_per_session"
    EVERY_SETUP = "every_setup"


class Entry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schedule: Schedule
    conditions: list[Condition]
    max_concurrent_positions: int = Field(ge=1, le=10)
    # spec v3 (D5a): the scale-in ladder. Absent ⇒ every pre-D5a spec is
    # unchanged. Present ⇒ the basket engine runs and the D5c interlock applies.
    scale_in: ScaleIn | None = None
    # FX.2 (spec v4): continuous opportunity scanning at the intraday clock.
    # None = ONCE_PER_SESSION exactly (absent and explicit are identical).
    intraday_scan: IntradayScan | None = None


class ThetaHarvest(BaseModel):
    """DTE-band profit harvest (spec v2, owner-confirmed semantics): inside
    the DTE window [dte_to, dte_from], close as soon as profit reaches
    profit_pct of max — "take profits during peak decay"."""

    model_config = ConfigDict(extra="forbid")

    dte_from: int = Field(ge=1, le=90)  # window opens at this DTE (inclusive)
    dte_to: int = Field(ge=0)  # window closes at this DTE (inclusive)
    profit_pct: float = Field(gt=0)

    @model_validator(mode="after")
    def _window_runs_downward(self) -> ThetaHarvest:
        # owner amendment 4: DTE counts DOWN — the window must too
        if self.dte_from <= self.dte_to:
            raise ValueError(
                "theta_harvest.dte_from must be greater than dte_to "
                "(the window runs from higher DTE down to lower DTE)"
            )
        return self


class Exit(BaseModel):
    """At least one exit rule must be present. The parser must ASK rather
    than default when the user gave none (guardrail #3)."""

    model_config = ConfigDict(extra="forbid")

    profit_target_pct: float | None = Field(default=None, gt=0)
    stop_loss_pct: float | None = Field(default=None, gt=0)
    time_exit_dte: int | None = Field(default=None, ge=0)
    conditions: list[Condition] | None = None
    # spec v2 (D1c): close when any watched leg's |delta| reaches the
    # threshold (short legs when present, else all legs). 0.30 and 30 both
    # accepted; normalized to the decimal like StrikeSelection deltas.
    delta_stop_abs: float | None = Field(default=None, gt=0, lt=1)
    theta_harvest: ThetaHarvest | None = None
    # spec v3 (D5a): general intraday session force-flat — flatten every open
    # position at the first 5-min bar ≥ this ET time ("no overnight"),
    # symmetric with entry.schedule.time_of_day. Requires clock="5min".
    close_at_time: str | None = Field(
        default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$"
    )

    @model_validator(mode="before")
    @classmethod
    def _normalize_delta_stop(cls, data: Any) -> Any:
        if isinstance(data, dict):
            v = data.get("delta_stop_abs")
            if isinstance(v, int | float) and v > 1:
                return {**data, "delta_stop_abs": v / 100.0}
        return data

    @model_validator(mode="after")
    def _at_least_one_rule(self) -> Exit:
        if (
            self.profit_target_pct is None
            and self.stop_loss_pct is None
            and self.time_exit_dte is None
            and self.conditions is None
            and self.delta_stop_abs is None
            and self.theta_harvest is None
            and self.close_at_time is None
        ):
            raise ValueError("exit must contain at least one rule (schema minProperties: 1)")
        return self


class SizingMethod(StrEnum):
    FIXED_CONTRACTS = "fixed_contracts"
    RISK_PCT_OF_EQUITY = "risk_pct_of_equity"


class Sizing(BaseModel):
    model_config = ConfigDict(extra="forbid")

    method: SizingMethod
    value: float = Field(gt=0)


class LiquidityMode(StrEnum):
    SKIP = "skip"  # gated contracts are never filled; entry skips with a reason
    STRESS = "stress"  # gated contracts fill at the full adverse quote (slip 1.0)


class Costs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    commission_per_contract: float = Field(default=0.65, ge=0)
    # 0 (mid fills) is forbidden by the schema's exclusiveMinimum — guardrail #1
    slippage_half_spread_fraction: float = Field(default=0.5, gt=0, le=1)
    # Liquidity floors (D1b, owner-confirmed Moderate defaults). Defaulted
    # cost knobs like commission/slippage — never entry/strike/exit params,
    # so guardrail #3 (no silent parser defaults) does not apply.
    max_spread_pct: float = Field(default=25.0, gt=0)  # (ask−bid)/mid, in percent
    min_open_interest: int = Field(default=10, ge=0)  # 0 disables the floor
    min_volume: int = Field(default=0, ge=0)  # 0 disables the floor
    liquidity_mode: LiquidityMode = LiquidityMode.SKIP


class Clock(StrEnum):
    """The engine's declared decision cadence (D2b). daily = decisions at
    each session close (the original engine, bit-identical). 5min =
    decisions at each 5-minute bar, fills from REAL intraday NBBO only."""

    DAILY = "daily"
    FIVE_MIN = "5min"


class Resolution(StrEnum):
    """Intraday bar-resolution policy (FX.1, spec v4). FIXED_5MIN is the
    D2 behavior (every covered session steps 5-minute bars). FINEST selects
    the finest HONEST resolution PER SESSION from the F0 resolution map:
    minute where minute data is banked for that session, else 5-min — the
    mix is recorded on the run and disclosed (ENGINE-V4 owner decisions
    2-4). Fills quote from real NBBO at every resolution; minute bars
    between quote stamps cannot fill anything (guardrail #1)."""

    FIXED_5MIN = "5min"
    FINEST = "finest"


class BacktestWindow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: date | None = Field(default=None, description="null = earliest available coverage")
    end: date | None = Field(default=None, description="null = latest available")
    initial_capital: float = Field(default=10_000, ge=1000)
    seed: int = 42
    clock: Clock = Clock.DAILY  # spec v2 vocabulary when not daily
    # FX.1 (spec v4): per-session resolution policy at the intraday clock.
    # None = FIXED_5MIN exactly (absent and explicit "5min" are identical).
    resolution: Resolution | None = None


# Structures with a DEFINED maximum profit (the collected credit / capped
# appreciation). theta_harvest measures "profit % of max" — undefined on
# unlimited-upside longs, so it is forbidden there (owner amendment 1).
DEFINED_MAX_PROFIT_STRUCTURES = {
    Structure.SHORT_PUT,
    Structure.PUT_CREDIT_SPREAD,
    Structure.CALL_CREDIT_SPREAD,
    Structure.IRON_CONDOR,
    Structure.COVERED_CALL,
}


class StrategySpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    spec_version: int
    meta: Meta
    underlying: Underlying
    position: Position
    entry: Entry
    exit: Exit
    sizing: Sizing
    costs: Costs
    backtest: BacktestWindow

    @model_validator(mode="after")
    def _version_supported(self) -> StrategySpec:
        if self.spec_version not in (1, 2, 3, 4, 5):
            raise ValueError("spec_version must be 1, 2, 3, 4, or 5")
        return self

    @model_validator(mode="after")
    def _v5_vocabulary_needs_v5(self) -> StrategySpec:
        """v5 vocabulary on an older spec is a loud error, never silent
        (module contract: every change is a versioned migration)."""
        if self.spec_version >= 5:
            return self
        all_conditions = list(self.entry.conditions) + list(self.exit.conditions or [])
        # a Rung IS a Condition and the rearm is one too — the gate must see
        # the ladder's vocabulary or a v3 ladder smuggles v5 in silently
        # (review finding; the D5d wrong-answer-no-error class)
        if self.entry.scale_in is not None:
            all_conditions += list(self.entry.scale_in.rungs)
            if self.entry.scale_in.rearm is not None:
                all_conditions.append(self.entry.scale_in.rearm)
        used = sorted({c.indicator.value for c in all_conditions
                       if c.indicator in V5_INDICATORS})
        if used:
            raise ValueError(
                f"spec_version {self.spec_version} cannot use v5 vocabulary: "
                f"{', '.join(f'indicator {u}' for u in used)} — set spec_version 5"
            )
        return self

    @model_validator(mode="after")
    def _v4_vocabulary_needs_v4(self) -> StrategySpec:
        """v4 vocabulary on an older spec is a loud error, never silent
        (module contract: every change is a versioned migration)."""
        if self.spec_version >= 4:
            return self
        used: list[str] = []
        if self.backtest.resolution is not None:
            used.append("backtest.resolution")
        if self.entry.intraday_scan is not None:
            used.append("entry.intraday_scan")
        if used:
            raise ValueError(
                f"spec_version {self.spec_version} cannot use v4 vocabulary: "
                f"{', '.join(used)} — set spec_version 4"
            )
        return self

    @model_validator(mode="after")
    def _resolution_needs_intraday_clock(self) -> StrategySpec:
        """Resolution is a policy over intraday bars — meaningless at the
        daily clock, so it is refused there rather than silently ignored."""
        if self.backtest.resolution is not None and self.backtest.clock is Clock.DAILY:
            raise ValueError(
                'backtest.resolution requires clock "5min" — the daily clock '
                "has no intraday bars to resolve"
            )
        return self

    @model_validator(mode="after")
    def _intraday_scan_constraints(self) -> StrategySpec:
        """FX.2: continuous scanning is an intraday-clock policy, and the
        scale-in ladder is its OWN multi-entry semantic — combining them
        would double-manage the book, so it is refused loudly."""
        if self.entry.intraday_scan is None:
            return self
        if self.backtest.clock is Clock.DAILY:
            raise ValueError(
                'entry.intraday_scan requires clock "5min" — the daily clock '
                "has no intraday bars to scan"
            )
        if self.entry.scale_in is not None:
            raise ValueError(
                "entry.intraday_scan cannot combine with entry.scale_in — "
                "the ladder is its own multi-entry semantic (and "
                '"once_per_session" would misdescribe a rung-firing ladder)'
            )
        return self

    @model_validator(mode="after")
    def _v2_vocabulary_needs_v2(self) -> StrategySpec:
        """A v1 spec using v2 vocabulary is a versioning error, never silent
        (module contract: every change is a versioned migration)."""
        if self.spec_version >= 2:
            return self
        used: list[str] = []
        if self.exit.delta_stop_abs is not None:
            used.append("exit.delta_stop_abs")
        if self.exit.theta_harvest is not None:
            used.append("exit.theta_harvest")
        if self.position.max_vega_per_contract is not None:
            used.append("position.max_vega_per_contract")
        if self.backtest.clock is not Clock.DAILY:
            used.append("backtest.clock")
        if self.position.expiration_selection.min_dte == 0 or (
            self.position.expiration_selection.target_dte == 0
        ):
            used.append("0-DTE expiration selection")
        if self.entry.schedule.time_of_day is not None:
            used.append("schedule.time_of_day")
        all_conditions = list(self.entry.conditions) + list(self.exit.conditions or [])
        v2_used = {c.indicator.value for c in all_conditions if c.indicator in V2_INDICATORS}
        used += sorted(f"indicator {name}" for name in v2_used)
        if any(c.timeframe is Timeframe.FIVE_MIN for c in all_conditions):
            used.append('timeframe "5min"')
        if used:
            raise ValueError(
                f"spec_version 1 cannot use v2 vocabulary: {', '.join(used)} — set spec_version 2"
            )
        return self

    @model_validator(mode="after")
    def _v3_vocabulary_needs_v3(self) -> StrategySpec:
        """v3 vocabulary on a v1/v2 spec is a loud error, never silent
        (module contract: every change is a versioned migration)."""
        if self.spec_version >= 3:
            return self
        used: list[str] = []
        if self.entry.scale_in is not None:
            used.append("entry.scale_in")
        if self.exit.close_at_time is not None:
            used.append("exit.close_at_time")
        if used:
            raise ValueError(
                f"spec_version {self.spec_version} cannot use v3 vocabulary: "
                f"{', '.join(used)} — set spec_version 3"
            )
        return self

    @model_validator(mode="after")
    def _scale_in_constraints(self) -> StrategySpec:
        """D5a scope + coherence rules for the scale-in ladder."""
        si = self.entry.scale_in
        if si is None:
            return self
        # single-leg only in D5a: the blended-per-share math generalizes
        # cleanly for one leg; multi-leg baskets (per-leg vs per-basket exit,
        # ratio adds) are a genuine can of worms deferred to a later phase.
        if len(self.position.legs) != 1 or self.position.structure not in (
            Structure.LONG_CALL,
            Structure.LONG_PUT,
        ):
            raise ValueError(
                "scale_in is supported only on single-leg long_call / long_put "
                "structures in this build — multi-leg ladders are not yet supported"
            )
        # rung add_contracts are ABSOLUTE counts, so percent-of-capital sizing
        # is incoherent with a ladder (the non-obvious constraint spelled out).
        if self.sizing.method is not SizingMethod.FIXED_CONTRACTS:
            raise ValueError(
                "scale-in rungs specify absolute contract counts, so sizing.method "
                "must be fixed_contracts"
            )
        return self

    @model_validator(mode="after")
    def _intraday_vocabulary_needs_5min_clock(self) -> StrategySpec:
        """5-minute indicators, time-of-day entries and the session
        force-flat only exist at the 5-minute clock — a daily engine has no
        bars to evaluate them on."""
        if self.backtest.clock is Clock.FIVE_MIN:
            return self
        needs: list[str] = []
        all_conditions = list(self.entry.conditions) + list(self.exit.conditions or [])
        if self.entry.scale_in is not None:
            all_conditions += [*self.entry.scale_in.rungs, self.entry.scale_in.rearm]
        if any(c.timeframe is Timeframe.FIVE_MIN for c in all_conditions):
            needs.append('conditions with timeframe "5min"')
        if self.entry.schedule.time_of_day is not None:
            needs.append("schedule.time_of_day")
        if self.exit.close_at_time is not None:
            needs.append("exit.close_at_time")
        if needs:
            raise ValueError(
                f"{' and '.join(needs)} require backtest.clock \"5min\" — "
                "the daily clock has no intraday bars"
            )
        return self

    @model_validator(mode="after")
    def _theta_harvest_needs_defined_max_profit(self) -> StrategySpec:
        # owner amendment 1: profit-% of max is undefined on unlimited-upside
        # structures; the parser must ASK, and validation must refuse.
        if (
            self.exit.theta_harvest is not None
            and self.position.structure not in DEFINED_MAX_PROFIT_STRUCTURES
        ):
            raise ValueError(
                f"exit.theta_harvest is only valid on structures with a defined max "
                f"profit ({', '.join(sorted(s.value for s in DEFINED_MAX_PROFIT_STRUCTURES))}); "
                f"{self.position.structure.value} has no defined max profit"
            )
        return self
