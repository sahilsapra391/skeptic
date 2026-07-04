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


class Exit(BaseModel):
    """At least one exit rule must be present. The parser must ASK rather
    than default when the user gave none (guardrail #3)."""

    model_config = ConfigDict(extra="forbid")

    profit_target_pct: float | None = Field(default=None, gt=0)
    stop_loss_pct: float | None = Field(default=None, gt=0)
    time_exit_dte: int | None = Field(default=None, ge=0)
    conditions: list[Condition] | None = None

    @model_validator(mode="after")
    def _at_least_one_rule(self) -> Exit:
        if (
            self.profit_target_pct is None
            and self.stop_loss_pct is None
            and self.time_exit_dte is None
            and self.conditions is None
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


class Costs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    commission_per_contract: float = Field(default=0.65, ge=0)
    # 0 (mid fills) is forbidden by the schema's exclusiveMinimum — guardrail #1
    slippage_half_spread_fraction: float = Field(default=0.5, gt=0, le=1)


class BacktestWindow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: date | None = Field(default=None, description="null = earliest available coverage")
    end: date | None = Field(default=None, description="null = latest available")
    initial_capital: float = Field(default=10_000, ge=1000)
    seed: int = 42


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
    def _version_is_one(self) -> StrategySpec:
        if self.spec_version != 1:
            raise ValueError("spec_version must be the constant 1")
        return self
