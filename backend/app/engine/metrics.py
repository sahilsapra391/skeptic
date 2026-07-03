"""Run metrics (TECH-SPEC §5). Conventions, fixed and documented:
  - returns are daily equity pct-changes; annualization factor 252
  - Sharpe/Sortino use sample std (ddof=1), risk-free 0
  - CAGR uses calendar days / 365.25
  - metrics that cannot be computed honestly are None, never 0
"""

from __future__ import annotations

import math

from app.engine.types import RunResult, TradeEvent


def _returns(equity: list[float]) -> list[float]:
    out: list[float] = []
    for prev, cur in zip(equity, equity[1:], strict=False):
        if prev > 0:
            out.append(cur / prev - 1.0)
    return out


def _std(values: list[float]) -> float | None:
    n = len(values)
    if n < 2:
        return None
    mean = sum(values) / n
    var = sum((v - mean) ** 2 for v in values) / (n - 1)
    return math.sqrt(var)


def compute_metrics(result: RunResult) -> dict[str, float | None]:
    equity = result.equity
    metrics: dict[str, float | None] = {
        "cagr": None,
        "sharpe": None,
        "sortino": None,
        "max_drawdown": None,
        "win_rate": None,
        "profit_factor": None,
        "exposure": None,
        "total_return": None,
        "trades": float(result.filled),
    }
    if len(equity) < 2 or equity[0] <= 0:
        return metrics

    start, end = equity[0], equity[-1]
    metrics["total_return"] = end / start - 1.0

    days = (result.effective_end - result.effective_start).days
    if days > 0 and end > 0:
        metrics["cagr"] = (end / start) ** (365.25 / days) - 1.0

    rets = _returns(equity)
    std = _std(rets)
    if std and std > 0:
        mean = sum(rets) / len(rets)
        metrics["sharpe"] = mean / std * math.sqrt(252)
        downside = [r for r in rets if r < 0]
        dstd = _std(downside) if len(downside) >= 2 else None
        if dstd and dstd > 0:
            metrics["sortino"] = mean / dstd * math.sqrt(252)

    if result.dates:
        metrics["exposure"] = result.days_in_market / len(result.dates)

    peak = equity[0]
    max_dd = 0.0
    for v in equity:
        peak = max(peak, v)
        if peak > 0:
            max_dd = max(max_dd, 1.0 - v / peak)
    metrics["max_drawdown"] = max_dd

    closed: list[TradeEvent] = [t for t in result.trades if t.pl is not None]
    if closed:
        wins = [t for t in closed if (t.pl or 0.0) > 0]
        metrics["win_rate"] = len(wins) / len(closed)
        gross_win = sum(t.pl or 0.0 for t in closed if (t.pl or 0.0) > 0)
        gross_loss = -sum(t.pl or 0.0 for t in closed if (t.pl or 0.0) < 0)
        if gross_loss > 0:
            metrics["profit_factor"] = gross_win / gross_loss
        elif gross_win > 0:
            metrics["profit_factor"] = None  # undefined without losses — not ∞ theater

    return metrics
