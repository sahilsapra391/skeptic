"""drawdown_from_high_pct: period bounds the reference high (rolling
N-session peak); no period keeps the since-inception behavior."""

from datetime import date, timedelta

from app.engine.conditions import all_conditions_pass
from app.engine.market import MarketStore, MarketView
from app.models.spec import Condition


def _store(closes: list[float]) -> MarketStore:
    d0 = date(2026, 1, 5)
    sessions = [d0 + timedelta(days=i) for i in range(len(closes))]
    return MarketStore(
        ticker="SPY",
        sessions=sessions,
        underlying_open={d: c for d, c in zip(sessions, closes, strict=True)},
        underlying_close={d: c for d, c in zip(sessions, closes, strict=True)},
        chains={},
        chain_dates=[],
    )


def _dd_cond(value: float, period: int | None = None) -> Condition:
    return Condition.model_validate(
        {"indicator": "drawdown_from_high_pct", "operator": ">=", "value": value,
         **({"period": period} if period else {})}
    )


def test_inf_high_is_unevaluable_not_a_100pct_drawdown() -> None:
    # a poisoned inf close would launder into a FINITE drawdown of
    # exactly 100% and fire every threshold, forever — refuse instead
    closes = [100.0, float("inf")] + [100.0] * 10 + [98.0]
    view = MarketView(_store(closes), _store(closes).sessions[-1])
    assert not all_conditions_pass(view, [_dd_cond(20.0)])
    # a bounded period whose window excludes the poison stays evaluable
    assert all_conditions_pass(view, [_dd_cond(2.0, period=5)])


def test_period_bounds_the_reference_high() -> None:
    # spike to 200 long ago, then a flat 100 stretch ending at 98:
    # vs the all-time 200 high that's a 51% drawdown; vs the 5-session
    # high (100) it's the honest 2%
    closes = [100.0, 200.0] + [100.0] * 10 + [98.0]
    view = MarketView(_store(closes), _store(closes).sessions[-1])
    assert all_conditions_pass(view, [_dd_cond(50.0)])  # since-inception sees 51%
    assert all_conditions_pass(view, [_dd_cond(2.0, period=5)])
    assert not all_conditions_pass(view, [_dd_cond(5.0, period=5)])  # only 2% off 5d high
