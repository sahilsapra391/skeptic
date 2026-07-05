"""P&L concentration stage (D1d) — hand-computed.

Crafted 61-point equity curve → 60 daily P&L points:
  59 alternating ±10 moves (gross 590) + one +600 spike
  gross |P&L| = 590 + 600 = 1,190
  top 5% of 60 days → k = 3 → |600| + |10| + |10| = 620
  top_share = 620 / 1,190 = 0.521 ≥ 0.50 → FLAGGED
Gamma coincidence: gammas rise 0.00, 0.01, …, 0.59 across the 60 P&L days
→ the top-decile threshold is the 54th sorted value = 0.54. The spike day
(index 59, γ 0.59) is top-decile; the two ±10 tie-breaker days (indices
0 and 1 — Python's sort is stable on the |10| ties) are not →
coincidence exactly 1/3.

The flag is a REPORTED reason — compute_trust must carry the note without
changing the level (never a cap in D1).
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.engine.types import RunResult
from app.honesty.stages import CONC_MIN_SESSIONS, concentration


def _result(pnl: list[float], gammas: list[float | None] | None = None) -> RunResult:
    equity = [10_000.0]
    for p in pnl:
        equity.append(equity[-1] + p)
    days = [date(2024, 1, 1) + timedelta(days=i) for i in range(len(equity))]
    r = RunResult(
        ticker="SPY", effective_start=days[0], effective_end=days[-1], seed=42,
        dates=days, equity=equity,
    )
    r.portfolio_gamma = list(gammas) if gammas is not None else [None] * len(equity)
    return r


def _spiky_pnl() -> list[float]:
    pnl = [10.0 if i % 2 == 0 else -10.0 for i in range(59)]
    pnl.append(600.0)
    return pnl  # 60 points, gross 1,190


class TestConcentrationStage:
    def test_hand_computed_flag(self) -> None:
        c = concentration(_result(_spiky_pnl()))
        assert c.meaningful
        assert c.top_days == 3  # ceil(0.05 × 60)
        assert c.top_share == pytest.approx(620.0 / 1190.0, abs=1e-9)
        assert c.flagged
        assert c.note is not None and "52% of gross daily P&L" in c.note
        assert c.gamma_coincidence is None  # gammas all unknown → honest None

    def test_distributed_pnl_is_not_flagged(self) -> None:
        pnl = [10.0 if i % 2 == 0 else -10.0 for i in range(60)]
        c = concentration(_result(pnl))
        assert c.meaningful
        # every day identical: top 3 of 60 carry exactly 3/60 = 5%
        assert c.top_share == pytest.approx(0.05, abs=1e-9)
        assert not c.flagged
        assert c.note is None

    def test_gamma_coincidence_hand_computed(self) -> None:
        pnl = _spiky_pnl()
        gammas: list[float | None] = [i / 100.0 for i in range(61)]
        c = concentration(_result(pnl, gammas))
        assert c.gamma_coincidence == pytest.approx(1.0 / 3.0, abs=1e-9)

    def test_short_history_refuses(self) -> None:
        pnl = [10.0, -10.0] * ((CONC_MIN_SESSIONS - 2) // 2)
        c = concentration(_result(pnl))
        assert not c.meaningful
        assert c.note is not None and "marked sessions" in c.note

    def test_flat_equity_refuses(self) -> None:
        c = concentration(_result([0.0] * 60))
        assert not c.meaningful


class TestTrustIntegration:
    def test_flag_is_a_reason_never_a_cap(self) -> None:
        from app.honesty.report import (
            Coverage,
            Dsr,
            MonteCarlo,
            OosSplit,
            RegimeSample,
            Sensitivity,
            WalkForward,
        )
        from app.honesty.trust import compute_trust

        oos = OosSplit(split_date="2024-06-01", is_sharpe=1.0, oos_sharpe=0.9,
                       is_return=0.1, oos_return=0.08, is_trades=20, oos_trades=10,
                       degradation=0.9, sign_flip=False, flagged=False)
        wf = WalkForward(meaningful=True, test_sessions=42, folds=[], consistency=0.8)
        mc = MonteCarlo(resamples=1000, block=5, seed=42, trades=30,
                        terminal_p5=9_000.0, terminal_p50=11_000.0, terminal_p95=13_000.0,
                        max_drawdown_p50=0.1, max_drawdown_p95=0.2, p_loss=0.1)
        sens = Sensitivity(params=[], verdict="plateau")
        sample = RegimeSample(trades=30, days_low_vix=100, days_mid_vix=100,
                              days_high_vix=100, regimes_present=3,
                              capped=False, cap_reason=None)
        dsr = Dsr(trials=1, daily_sharpe=0.05, expected_max_sharpe=0.0, dsr=0.9)
        cov = Coverage(requested_start="2024-01-01", requested_end="2024-12-31",
                       effective_start="2024-01-01", effective_end="2024-12-31",
                       requested_sessions=250, chain_sessions=250, coverage_ratio=1.0,
                       materially_short=False, reason=None)

        baseline = compute_trust(oos, wf, mc, sens, sample, dsr, cov)
        conc = concentration(_result(_spiky_pnl()))
        with_flag = compute_trust(oos, wf, mc, sens, sample, dsr, cov, conc)

        assert with_flag.level == baseline.level  # NEVER a cap in D1
        assert conc.note is not None and any(conc.note in r for r in with_flag.reasons)
        assert not any(
            (conc.note or "") in r for r in baseline.reasons
        )
