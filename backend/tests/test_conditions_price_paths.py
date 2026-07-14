"""Non-finite closes must never fabricate price-path signals.

pandas' NaN-only filters (dropna/notna) keep ±inf, and inf launders
through arithmetic into finite-looking lies: an inf close makes the NEXT
return exactly -1.0 (a fabricated -100% move inside the vol window) and
an inf running high makes drawdown exactly 100%. The daily price branches
gate the contributing closes, and _tail_values drops non-finite (not just
NaN) indicator values.
"""

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


def _cond(indicator: str, op: str, value: float, period: int | None = None) -> Condition:
    return Condition.model_validate(
        {"indicator": indicator, "operator": op, "value": value,
         **({"period": period} if period else {})}
    )


def test_inf_close_never_fabricates_a_realized_vol_spike() -> None:
    # flat closes → vol 0. An inf at closes[-21] puts its own inf return
    # OUTSIDE the 20-return tail but the laundered -1.0 return INSIDE it:
    # std([-1.0, 0×19], ddof=1) = √(0.95/19) ≈ 0.2236 → vol ≈ 355% —
    # a fabricated spike from one poisoned row, refused by the closes gate
    closes = [100.0] * 30
    closes[-21] = float("inf")
    view = MarketView(_store(closes), _store(closes).sessions[-1])
    assert not all_conditions_pass(view, [_cond("realized_vol_20d", ">", 30)])
    # the clean series stays evaluable (vol exactly 0 → "< 30" passes)
    clean = [100.0] * 30
    view = MarketView(_store(clean), _store(clean).sessions[-1])
    assert all_conditions_pass(view, [_cond("realized_vol_20d", "<", 30)])


def test_inf_close_never_fabricates_an_ema_signal() -> None:
    # EMA is recursive, so one inf close poisons every EMA value from
    # that bar FOREVER; the pre-fix NaN-only tail filter kept it and
    # _compare(inf, >, 100) fabricated True on every later session —
    # non-finite tail values now drop, leaving a one-value pair →
    # unevaluable. (SMA is incidentally safe: pandas' rolling mean
    # NaN-ifies the inf window and NaN always dropped; the shared
    # _tail_values gate covers both regardless of pandas internals.)
    closes = [100.0] * 25 + [float("inf")]
    view = MarketView(_store(closes), _store(closes).sessions[-1])
    assert not all_conditions_pass(view, [_cond("ema", ">", 100, period=14)])
