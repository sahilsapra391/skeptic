"""Gauntlet orchestration: RunResult → HonestyReport, stage by stage.

`on_stage(index, label)` fires as each stage begins so the API can write
run_events and the UI can show the strategy being attacked live.
Stage indices match the frontend's gauntlet screen:
  0 backtest (already done) · 1 IS/OOS · 2 walk-forward · 3 Monte Carlo ·
  4 sensitivity · 5 verdict
"""

from __future__ import annotations

from collections.abc import Callable

from app.engine.market import MarketStore
from app.engine.types import RunResult
from app.honesty import stages
from app.honesty.report import HonestyReport
from app.honesty.trust import compute_trust
from app.models.spec import StrategySpec

StageHook = Callable[[int, str], None]


def _noop(_i: int, _label: str) -> None:  # pragma: no cover
    return None


def run_gauntlet(
    spec: StrategySpec,
    store: MarketStore,
    result: RunResult,
    trials: int,
    on_stage: StageHook = _noop,
) -> HonestyReport:
    on_stage(1, "in-sample / out-of-sample split")
    oos = stages.oos_split(result)

    on_stage(2, "walk-forward windows")
    wf = stages.walk_forward(result)

    on_stage(3, "Monte Carlo resampling")
    mc = stages.monte_carlo(result, spec.backtest.initial_capital)

    on_stage(4, "parameter sensitivity sweep")
    sens = stages.sensitivity(spec, store)

    on_stage(5, "deflated Sharpe + regime guardrail + verdict")
    dsr = stages.deflated_sharpe(result, trials)
    sample = stages.regime_sample(result, store)
    trust = compute_trust(oos, wf, mc, sens, sample, dsr)

    return HonestyReport(
        oos=oos,
        walk_forward=wf,
        monte_carlo=mc,
        sensitivity=sens,
        dsr=dsr,
        regime_sample=sample,
        trust=trust,
        metrics=result.metrics,
        effective_start=result.effective_start.isoformat(),
        effective_end=result.effective_end.isoformat(),
        seed=result.seed,
    )
