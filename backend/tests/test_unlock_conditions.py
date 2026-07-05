"""D3a: structured unlock conditions + the nightly unlock scan's decision
logic. The refusal screen's "unlocks at…" promise becomes computable facts:
what a refused verdict needs, stored from the SAME stage numbers the text
shows, compared later against what the lake actually gained."""

from __future__ import annotations

import json
from datetime import date

from app.honesty.report import (
    Concentration,
    Coverage,
    Dsr,
    HonestyReport,
    MonteCarlo,
    OosSplit,
    RegimeSample,
    Sensitivity,
    Trust,
    WalkForward,
)
from app.honesty.stages import COVERAGE_MIN_RATIO, MIN_TRADES, unlock_conditions
from app.models.spec import StrategySpec
from tests.fixtures.engine.common import make_spec


def _report(label: str, trades: int = 4, regimes: int = 1,
            ratio: float = 0.3, short: bool = True) -> HonestyReport:
    return HonestyReport(
        oos=OosSplit(split_date="2024-06-01", is_sharpe=None, oos_sharpe=None,
                     is_return=None, oos_return=None, is_trades=trades,
                     oos_trades=0, degradation=None, sign_flip=False, flagged=True),
        walk_forward=WalkForward(meaningful=False, note="short", test_sessions=21),
        monte_carlo=MonteCarlo(resamples=1000, block=5, seed=42, trades=trades,
                               terminal_p5=None, terminal_p50=None, terminal_p95=None,
                               max_drawdown_p50=None, max_drawdown_p95=None,
                               p_loss=None),
        sensitivity=Sensitivity(params=[], verdict=None),
        dsr=Dsr(trials=1, daily_sharpe=None, expected_max_sharpe=None, dsr=None),
        regime_sample=RegimeSample(trades=trades, days_low_vix=10, days_mid_vix=0,
                                   days_high_vix=0, regimes_present=regimes,
                                   capped=trades < MIN_TRADES or regimes < 2,
                                   cap_reason="thin"),
        coverage=Coverage(requested_start="2024-01-01", requested_end="2024-12-31",
                          effective_start="2024-02-01", effective_end="2024-11-30",
                          requested_sessions=250, chain_sessions=int(250 * ratio),
                          coverage_ratio=ratio, materially_short=short,
                          reason="short" if short else None),
        concentration=Concentration(meaningful=False),
        trust=Trust(level=None if label == "insufficient_evidence" else 3,
                    label=label, survived={}, survived_count=0, reasons=[]),
        metrics={},
        effective_start="2024-02-01", effective_end="2024-11-30", seed=42,
    )


def _spec() -> StrategySpec:
    return StrategySpec.model_validate(make_spec(
        position={
            "structure": "short_put",
            "legs": [{"right": "put", "side": "short", "ratio": 1,
                      "strike_selection": {"method": "delta", "value": 0.30}}],
            "expiration_selection": {"target_dte": 45, "min_dte": 30, "max_dte": 60},
        },
        exit={"profit_target_pct": 50},
    ))


class TestUnlockConditionsBuilder:
    def test_refused_run_stores_its_binding_needs(self) -> None:
        unlock = unlock_conditions(_report("insufficient_evidence"), _spec())
        assert unlock is not None
        assert unlock.ticker == "SPY" and unlock.clock == "daily"
        assert unlock.requested_start == "2024-01-01"
        # all three constraints bind in this fixture
        assert unlock.coverage is not None
        assert unlock.coverage.has == 0.3 and unlock.coverage.needs == COVERAGE_MIN_RATIO
        assert unlock.trades is not None
        assert unlock.trades.has == 4 and unlock.trades.needs == MIN_TRADES
        assert unlock.regimes is not None and unlock.regimes.needs == 2
        assert unlock.sessions_at_refusal == 75  # 250 × 0.3

    def test_only_binding_constraints_present(self) -> None:
        report = _report("insufficient_evidence", trades=40, regimes=3,
                         ratio=0.3, short=True)
        unlock = unlock_conditions(report, _spec())
        assert unlock is not None
        assert unlock.coverage is not None  # the one binding constraint
        assert unlock.trades is None
        assert unlock.regimes is None

    def test_graded_verdicts_store_nothing(self) -> None:
        assert unlock_conditions(_report("weak", short=False), _spec()) is None


class TestDbMigration:
    def test_d3_columns_exist(self) -> None:
        from sqlalchemy import inspect

        from app import db
        db.init_db()
        cols = {c["name"] for c in inspect(db._engine).get_columns("runs")}
        assert {"unlock_json", "origin", "parent_run_id"} <= cols


class TestUnlockScan:
    def test_scan_decides_from_new_sessions(self, monkeypatch) -> None:
        from app import db
        from scripts import nightly_improve as ni

        db.init_db()
        with db.session() as s:
            s.query(db.Run).filter(db.Run.id.in_(["refusedA", "refusedB", "childA"])).delete()
            s.add(db.Run(id="refusedA", status="done", spec_json="{}",
                         unlock_json=json.dumps({
                             "ticker": "SPY", "clock": "daily",
                             "requested_start": "2024-01-01",
                             "requested_end": "2024-12-31",
                             "sessions_at_refusal": 40})))
            s.add(db.Run(id="refusedB", status="done", spec_json="{}",
                         unlock_json=json.dumps({
                             "ticker": "SPY", "clock": "daily",
                             "requested_start": "2024-01-01",
                             "requested_end": "2024-12-31",
                             "sessions_at_refusal": 95})))
            s.commit()

        # simulated coverage delta (the brief's acceptance): the lake now
        # covers 100 sessions in the window
        monkeypatch.setattr(ni, "_covered_sessions_now", lambda *_a, **_k: 100)
        decisions = {d.run_id: d for d in ni.scan_unlocks(today=date(2025, 1, 2))}

        assert decisions["refusedA"].new_sessions == 60
        assert decisions["refusedA"].should_rerun  # ≥ UNLOCK_MIN_NEW_SESSIONS
        assert decisions["refusedB"].new_sessions == 5
        assert not decisions["refusedB"].should_rerun

        # a run already superseded by an auto-upgrade never re-queues
        with db.session() as s:
            s.add(db.Run(id="childA", status="done", spec_json="{}",
                         origin="auto_unlock", parent_run_id="refusedA"))
            s.commit()
        decisions2 = {d.run_id for d in ni.scan_unlocks(today=date(2025, 1, 2))}
        assert "refusedA" not in decisions2
        assert "refusedB" in decisions2

        with db.session() as s:  # cleanup for other tests
            s.query(db.Run).filter(db.Run.id.in_(["refusedA", "refusedB", "childA"])).delete()
            s.commit()
