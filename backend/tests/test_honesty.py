"""Honesty-layer unit tests with hand-computed fixtures (repo convention)."""

from datetime import date, timedelta

import pytest

from app.engine.types import RunResult, TradeEvent
from app.honesty import stages
from app.honesty.report import (
    Dsr,
    MonteCarlo,
    OosSplit,
    RegimeSample,
    Sensitivity,
    WalkForward,
)
from app.honesty.trust import compute_trust
from app.honesty.verdict import validate_numbers


def _result(equity: list[float], pls: list[float] | None = None, seed: int = 42) -> RunResult:
    start = date(2024, 1, 2)
    dates = [start + timedelta(days=i) for i in range(len(equity))]
    r = RunResult(
        ticker="SPY",
        effective_start=dates[0],
        effective_end=dates[-1],
        seed=seed,
        dates=dates,
        equity=equity,
    )
    if pls:
        # spread closed trades across the window
        step = max(1, len(dates) // len(pls))
        r.trades = [
            TradeEvent(day=dates[min(i * step, len(dates) - 1)], action="CLOSE", detail="", pl=pl)
            for i, pl in enumerate(pls)
        ]
    return r


# ------------------------------------------------------------------- OOS
def test_oos_split_flags_degradation() -> None:
    # hand-built: strong steady rise for 70%, flat-noise for the last 30%
    rise = [10_000 * (1.005**i) for i in range(70)]
    flat = [rise[-1] + ((-1) ** i) * 8 for i in range(1, 31)]
    split = stages.oos_split(_result(rise + flat))
    assert split.is_sharpe is not None and split.is_sharpe > 3
    assert split.oos_sharpe is not None and split.oos_sharpe < split.is_sharpe * 0.5
    assert split.flagged


def test_oos_split_passes_consistent_curve() -> None:
    steady = [10_000 * (1.002**i) + ((-1) ** i) * 5 for i in range(200)]
    split = stages.oos_split(_result(steady))
    assert not split.flagged
    assert split.degradation is not None and split.degradation >= 0.5


# ---------------------------------------------------------- walk-forward
def test_walk_forward_refuses_short_history() -> None:
    wf = stages.walk_forward(_result([10_000 + i for i in range(40)]))
    assert not wf.meaningful
    assert "not meaningful" in (wf.note or "")


def test_walk_forward_consistency_counts_positive_folds() -> None:
    # 126 sessions → 3 folds of 42: up, down, up → consistency 2/3
    up1 = [10_000 + 10 * i + ((-1) ** i) * 3 for i in range(42)]
    down = [up1[-1] - 12 * i + ((-1) ** i) * 3 for i in range(1, 43)]
    up2 = [down[-1] + 11 * i + ((-1) ** i) * 3 for i in range(1, 43)]
    wf = stages.walk_forward(_result(up1 + down + up2))
    assert wf.meaningful and len(wf.folds) == 3
    assert wf.consistency == pytest.approx(2 / 3)


# ------------------------------------------------------------ Monte Carlo
def test_monte_carlo_all_winning_trades_never_lose() -> None:
    r = _result([10_000, 10_500, 11_000], pls=[100.0] * 40)
    mc = stages.monte_carlo(r, 10_000)
    assert mc.p_loss == 0.0
    # every resample is 40 trades of +100 → terminal is exactly +4,000
    assert mc.terminal_p50 == pytest.approx(14_000)
    assert mc.terminal_p5 == pytest.approx(14_000)


def test_monte_carlo_seeded_determinism() -> None:
    # aperiodic P/L (a 5-periodic list with block=5 makes every block sum
    # identical, hiding seed differences)
    pls = [float(((i * 37) % 23) * 20 - 180) for i in range(50)]
    a = stages.monte_carlo(_result([10_000, 10_100], pls, seed=7), 10_000)
    b = stages.monte_carlo(_result([10_000, 10_100], pls, seed=7), 10_000)
    c = stages.monte_carlo(_result([10_000, 10_100], pls, seed=8), 10_000)
    assert a.terminal_p50 == b.terminal_p50 and a.fan_p50 == b.fan_p50
    assert a.terminal_p50 != c.terminal_p50  # different seed, different draw


def test_monte_carlo_refuses_thin_samples() -> None:
    mc = stages.monte_carlo(_result([10_000, 10_100], pls=[50.0, 60.0]), 10_000)
    assert mc.p_loss is None and mc.terminal_p50 is None


# -------------------------------------------------------------------- DSR
def test_dsr_shrinks_as_trials_grow() -> None:
    # modest edge with real noise so DSR sits inside (0, 1)
    equity = [
        10_000 + 6.0 * i + (((i * 37) % 23) - 11) * 55.0
        for i in range(300)
    ]
    few = stages.deflated_sharpe(_result(equity), trials=1)
    many = stages.deflated_sharpe(_result(equity), trials=200)
    assert few.dsr is not None and many.dsr is not None
    assert many.dsr < few.dsr


# ------------------------------------------------------------- trust rules
def _oos(flagged: bool = False, sign_flip: bool = False) -> OosSplit:
    return OosSplit(
        split_date="2024-06-01", is_sharpe=1.2, oos_sharpe=0.3 if flagged else 1.1,
        is_return=0.1, oos_return=0.02, is_trades=40, oos_trades=18,
        degradation=0.25 if flagged else 0.92, sign_flip=sign_flip, flagged=flagged,
    )


def _wf(consistency: float = 0.8) -> WalkForward:
    return WalkForward(meaningful=True, test_sessions=42, folds=[], consistency=consistency)


def _mc(p_loss: float = 0.05) -> MonteCarlo:
    return MonteCarlo(
        resamples=1000, block=5, seed=42, trades=60,
        terminal_p5=9_000, terminal_p50=11_000, terminal_p95=13_000,
        max_drawdown_p50=0.08, max_drawdown_p95=0.2, p_loss=p_loss,
    )


def _sample(capped: bool = False) -> RegimeSample:
    return RegimeSample(
        trades=60, days_low_vix=100, days_mid_vix=100, days_high_vix=100,
        regimes_present=3, capped=capped,
        cap_reason="only 10 closed trades — minimum is 15" if capped else None,
    )


def _dsr(v: float | None = 0.9) -> Dsr:
    return Dsr(trials=3, daily_sharpe=0.06, expected_max_sharpe=0.02, dsr=v)


def test_trust_all_survived_is_proven() -> None:
    t = compute_trust(_oos(), _wf(), _mc(), Sensitivity(verdict="plateau"), _sample(), _dsr())
    assert t.level == 5 and t.label == "proven"


def test_trust_oos_failure_costs_a_level() -> None:
    t = compute_trust(_oos(flagged=True), _wf(), _mc(), Sensitivity(verdict="plateau"),
                      _sample(), _dsr())
    assert t.level == 4


def test_trust_dsr_gate_caps_at_two() -> None:
    t = compute_trust(_oos(), _wf(), _mc(), Sensitivity(verdict="plateau"),
                      _sample(), _dsr(0.2))
    assert t.level == 2
    assert any("mined" in r for r in t.reasons)


def test_trust_insufficient_evidence_overrides_everything() -> None:
    t = compute_trust(_oos(), _wf(), _mc(), Sensitivity(verdict="plateau"),
                      _sample(capped=True), _dsr())
    assert t.level is None and t.label == "insufficient_evidence"


# --------------------------------------------------------------- validator
def test_validator_rejects_hallucinated_numbers() -> None:
    allowed = {1.2, 0.3, 42.0, 0.25, 25.0}
    ok = validate_numbers("Sharpe 1.20 fell to 0.30, a 25% survival", allowed)
    assert ok == []
    bad = validate_numbers("Sharpe 1.20 fell to 0.30 across 777 windows", allowed)
    assert bad == ["777"]
