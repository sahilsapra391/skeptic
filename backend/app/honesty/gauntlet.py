"""Gauntlet orchestration: RunResult → HonestyReport, stage by stage.

`on_stage(index, label, preview)` fires as each stage begins so the API
can write run_events and the UI can show the strategy being attacked
live. `preview` is a REAL one-line stat from the stage that just
finished (never a fabrication) — the progress screen's teaser feed.
Stage indices match the frontend's gauntlet screen:
  0 backtest (already done) · 1 IS/OOS · 2 walk-forward · 3 Monte Carlo ·
  4 sensitivity · 5 verdict
"""

from __future__ import annotations

from collections.abc import Callable

from app.engine.market import MarketStore
from app.engine.types import RunResult
from app.honesty import stages
from app.honesty.report import HonestyReport, MonteCarlo, OosSplit, WalkForward
from app.honesty.trust import compute_trust
from app.models.spec import StrategySpec

StageHook = Callable[[int, str, str | None], None]


def _noop(_i: int, _label: str, _preview: str | None = None) -> None:  # pragma: no cover
    return None


def _backtest_preview(result: RunResult, initial: float) -> str:
    final = result.equity[-1] if result.equity else initial
    return (
        f"backtest done — {result.filled} fills · "
        f"${initial:,.0f} → ${final:,.0f} net of costs"
    )


def _oos_preview(oos: OosSplit) -> str:
    if oos.is_sharpe is None or oos.oos_sharpe is None:
        return "unseen-data check: not enough history to split honestly"
    verdict = "fading ⚠" if oos.flagged else "holding ✓"
    return (
        f"unseen data: Sharpe {oos.oos_sharpe:.2f} vs {oos.is_sharpe:.2f} "
        f"in training — {verdict}"
    )


def _wf_preview(wf: WalkForward) -> str:
    if not wf.meaningful or wf.consistency is None:
        return "walk-forward: history too short to slice"
    positive = sum(1 for f in wf.folds if f.ret > 0)
    return f"walk-forward: {positive} of {len(wf.folds)} time windows profitable"


def _mc_preview(mc: MonteCarlo) -> str:
    if mc.p_loss is None:
        return "Monte Carlo: too few trades to reshuffle"
    return f"1,000 reshuffles: {mc.p_loss:.0%} of orderings lose money"


def run_gauntlet(
    spec: StrategySpec,
    store: MarketStore,
    result: RunResult,
    trials: int,
    on_stage: StageHook = _noop,
) -> HonestyReport:
    on_stage(
        1,
        "in-sample / out-of-sample split",
        _backtest_preview(result, spec.backtest.initial_capital),
    )
    oos = stages.oos_split(result)

    on_stage(2, "walk-forward windows", _oos_preview(oos))
    wf = stages.walk_forward(result)

    on_stage(3, "Monte Carlo resampling", _wf_preview(wf))
    mc = stages.monte_carlo(result, spec.backtest.initial_capital)

    on_stage(4, "parameter sensitivity sweep", _mc_preview(mc))
    sens = stages.sensitivity(spec, store)

    sens_preview = (
        f"±20% nudges: the optimum is a {sens.verdict}"
        if sens.verdict
        else "±20% nudges: not classifiable"
    )
    on_stage(5, "deflated Sharpe + regime guardrail + verdict", sens_preview)
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
