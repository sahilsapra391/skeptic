"""HonestyReport — the single output of the gauntlet (TECH-SPEC §6) and
the ONLY input the verdict writer ever sees (guardrail #4)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class OosSplit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    split_date: str
    is_sharpe: float | None
    oos_sharpe: float | None
    is_return: float | None
    oos_return: float | None
    is_trades: int
    oos_trades: int
    degradation: float | None  # oos_sharpe / is_sharpe when both computable
    sign_flip: bool
    flagged: bool  # oos < 50% of is, or sign flip (TECH-SPEC §6.1)


class WalkForwardFold(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: str
    end: str
    ret: float
    trades: int


class WalkForward(BaseModel):
    model_config = ConfigDict(extra="forbid")

    meaningful: bool
    note: str | None = None
    test_sessions: int = 0
    folds: list[WalkForwardFold] = []
    consistency: float | None = None  # fraction of profitable folds


class MonteCarlo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resamples: int
    block: int
    seed: int
    trades: int
    terminal_p5: float | None
    terminal_p50: float | None
    terminal_p95: float | None
    max_drawdown_p50: float | None
    max_drawdown_p95: float | None
    p_loss: float | None  # fraction of paths ending below initial capital
    # percentile equity paths for the fan chart (downsampled)
    fan_p5: list[float] = []
    fan_p50: list[float] = []
    fan_p95: list[float] = []


class ParamSweep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    values: list[float]
    sharpes: list[float | None]
    base_index: int
    classification: str | None  # "plateau" | "cliff" | None (not classifiable)


class Sensitivity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    params: list[ParamSweep] = []
    verdict: str | None = None  # plateau | cliff | None


class Dsr(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trials: int
    daily_sharpe: float | None
    expected_max_sharpe: float | None
    dsr: float | None  # P(true SR > 0 | N trials); < 0.5 = likely mined


class RegimeSample(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trades: int
    days_low_vix: int  # VIX < 15
    days_mid_vix: int  # 15–20
    days_high_vix: int  # > 20
    regimes_present: int  # buckets holding ≥ 10% of days
    capped: bool
    cap_reason: str | None


class Trust(BaseModel):
    model_config = ConfigDict(extra="forbid")

    level: int | None  # 1..5; None when capped
    label: str  # noise | weak | suggestive | robust | proven | insufficient_evidence
    survived: dict[str, bool]  # oos / walk_forward / monte_carlo / sensitivity / sample
    survived_count: int
    reasons: list[str]


class HonestyReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    oos: OosSplit
    walk_forward: WalkForward
    monte_carlo: MonteCarlo
    sensitivity: Sensitivity
    dsr: Dsr
    regime_sample: RegimeSample
    trust: Trust
    metrics: dict[str, float | None]
    effective_start: str
    effective_end: str
    seed: int
