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


# Indicators (and fields, checked separately) that require spec_version 2 —
# a v1 spec using v2 vocabulary is a versioning error, never silent.
V2_INDICATORS = {Indicator.IVX_RANK_1Y, Indicator.IVX_LEVEL_30D, Indicator.HV_IV_SPREAD_30D}


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
    model_config = ConfigDict(extra="forbid")

    target_dte: int = Field(ge=1, le=90)
    min_dte: int = Field(ge=1)
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


class Schedule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    frequency: Frequency
    day_of_week: DayOfWeek | None = None
    day_of_month: int | None = Field(default=None, ge=1, le=28)


class Entry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schedule: Schedule
    conditions: list[Condition]
    max_concurrent_positions: int = Field(ge=1, le=10)


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


class BacktestWindow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: date | None = Field(default=None, description="null = earliest available coverage")
    end: date | None = Field(default=None, description="null = latest available")
    initial_capital: float = Field(default=10_000, ge=1000)
    seed: int = 42


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
        if self.spec_version not in (1, 2):
            raise ValueError("spec_version must be 1 or 2")
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
        all_conditions = list(self.entry.conditions) + list(self.exit.conditions or [])
        v2_used = {c.indicator.value for c in all_conditions if c.indicator in V2_INDICATORS}
        used += sorted(f"indicator {name}" for name in v2_used)
        if used:
            raise ValueError(
                f"spec_version 1 cannot use v2 vocabulary: {', '.join(used)} — set spec_version 2"
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
