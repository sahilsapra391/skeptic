"""Run orchestration: spec → store → engine → RunResult with metrics.
Deterministic by construction in M2 (no stochastic stages yet); the seed
is recorded with the run so M3's Monte Carlo inherits the contract."""

from __future__ import annotations

from app.engine.engine import run_engine
from app.engine.market import IntradayProvider, MarketStore
from app.engine.metrics import compute_metrics
from app.engine.types import RunResult
from app.models.spec import StrategySpec


def run_backtest(
    spec: StrategySpec, store: MarketStore, intraday: IntradayProvider | None = None
) -> RunResult:
    result = run_engine(spec, store, intraday)
    result.metrics = compute_metrics(result)
    return result
