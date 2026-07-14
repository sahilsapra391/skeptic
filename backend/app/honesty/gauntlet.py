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

from app.engine.market import IntradayProvider, MarketStore
from app.engine.types import RunResult
from app.honesty import stages
from app.honesty.report import HonestyReport, MonteCarlo, OosSplit, WalkForward
from app.honesty.trust import compute_trust
from app.models.spec import StrategySpec

# each preview carries both voices; the UI picks by the Verbiage setting
Preview = dict[str, str]

StageHook = Callable[[int, str, Preview | None], None]


def _noop(_i: int, _label: str, _preview: Preview | None = None) -> None:  # pragma: no cover
    return None


def _both(pro: str, retail: str) -> Preview:
    return {"pro": pro, "retail": retail}


def _backtest_preview(result: RunResult, initial: float) -> Preview:
    final = result.equity[-1] if result.equity else initial
    return _both(
        f"backtest done — {result.filled} fills · "
        f"${initial:,.0f} → ${final:,.0f} net of costs",
        f"backtest done — {result.filled} trades · "
        f"started ${initial:,.0f}, ended ${final:,.0f} after costs",
    )


def _oos_preview(oos: OosSplit) -> Preview:
    if oos.is_sharpe is None or oos.oos_sharpe is None:
        return _both(
            "unseen-data check: not enough history to split honestly",
            "hidden-data check: not enough history to split honestly",
        )
    verdict = "fading ⚠" if oos.flagged else "holding ✓"
    return _both(
        f"unseen data: Sharpe {oos.oos_sharpe:.2f} vs {oos.is_sharpe:.2f} "
        f"in training — {verdict}",
        f"on data it never saw: risk-adjusted score {oos.oos_sharpe:.2f} vs "
        f"{oos.is_sharpe:.2f} in training — {verdict}",
    )


def _wf_preview(wf: WalkForward) -> Preview:
    if not wf.meaningful or wf.consistency is None:
        return _both(
            "walk-forward: history too short to slice",
            "time-window test: history too short to slice",
        )
    positive = sum(1 for f in wf.folds if f.ret > 0)
    return _both(
        f"walk-forward: {positive} of {len(wf.folds)} time windows profitable",
        f"made money in {positive} of {len(wf.folds)} time periods",
    )


def _mc_preview(mc: MonteCarlo) -> Preview:
    if mc.p_loss is None:
        return _both(
            "Monte Carlo: too few trades to reshuffle",
            "luck check: too few trades to reshuffle",
        )
    return _both(
        f"1,000 reshuffles: {mc.p_loss:.0%} of orderings lose money",
        f"trade order reshuffled 1,000×: {mc.p_loss:.0%} of shuffles end losing money",
    )


def run_gauntlet(
    spec: StrategySpec,
    store: MarketStore,
    result: RunResult,
    trials: int,
    on_stage: StageHook = _noop,
    intraday: IntradayProvider | None = None,
    min_trades: int = stages.MIN_TRADES,
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
    sens = stages.sensitivity(spec, store, intraday)

    # "nudged around your values", not "±20%": small condition thresholds
    # sweep an absolute family-scale grid wider than ±20% (stages.py
    # _COND_FAMILY_FLOORS) — the preview must not misstate the probe
    if sens.verdict == "plateau":
        sens_preview = _both(
            "parameter nudges: the optimum is a plateau",
            "settings nudged around your values: stable — small changes don't wreck it",
        )
    elif sens.verdict == "cliff":
        sens_preview = _both(
            "parameter nudges: the optimum is a cliff",
            "settings nudged around your values: fragile — only works at exactly your settings",
        )
    else:
        sens_preview = _both(
            "parameter nudges: not classifiable",
            "settings nudged around your values: not enough data to judge",
        )
    on_stage(5, "deflated Sharpe + regime guardrail + verdict", sens_preview)
    dsr = stages.deflated_sharpe(result, trials)
    sample = stages.regime_sample(result, store, min_trades)
    cov = stages.coverage(result)
    liq = stages.liquidity_profile(result, spec)
    conc = stages.concentration(result)
    split = stages.session_split(result)
    res_split = stages.resolution_split(result, min_trades)
    ladder = stages.ladder_depth_attribution(result, spec)
    confidence = stages.data_confidence(result, spec)
    scale_in = stages.scale_in_honesty(result, spec, spec.backtest.initial_capital)
    trust = compute_trust(oos, wf, mc, sens, sample, dsr, cov, conc, scale_in,
                          res_split)

    return HonestyReport(
        oos=oos,
        walk_forward=wf,
        monte_carlo=mc,
        sensitivity=sens,
        dsr=dsr,
        regime_sample=sample,
        coverage=cov,
        liquidity=liq,
        concentration=conc,
        session_split=split,
        resolution_split=res_split,
        data_confidence=confidence,
        ladder_depth=ladder,
        scale_in=scale_in,
        fill_sources=dict(result.fill_sources),
        trust=trust,
        metrics=result.metrics,
        effective_start=result.effective_start.isoformat(),
        effective_end=result.effective_end.isoformat(),
        seed=result.seed,
    )
