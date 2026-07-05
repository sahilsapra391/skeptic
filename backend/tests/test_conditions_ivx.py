"""IVX / HV market-condition filters (D1c) — hand-computed, point-in-time.

Rank fixture: IVX series 0.100, 0.101, …, +0.001/day (strictly rising) on
consecutive weekdays. On day N the current value is the maximum of its own
trailing window → rank exactly 100. Owner amendment 3: below 126 trailing
observations the rank is UNEVALUABLE that day → False, whatever the
operator asks.

Levels are decimals in the lake (0.229 = 22.9%); conditions compare in
percentage points, like vix_level.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.engine.conditions import evaluate_condition
from app.engine.market import MarketView, build_fixture_store
from app.models.spec import Condition, Indicator, Operator


def _weekdays(start: date, n: int) -> list[date]:
    out: list[date] = []
    d = start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _store(n_obs: int, hv: float | None = None, ivx_override: float | None = None):
    days = _weekdays(date(2024, 1, 1), n_obs)
    ivx = {d.isoformat(): 0.100 + 0.001 * i for i, d in enumerate(days)}
    if ivx_override is not None:
        ivx[days[-1].isoformat()] = ivx_override
    underlying = {d.isoformat(): (100.0, 100.0) for d in days}
    hv_map = {days[-1].isoformat(): hv} if hv is not None else None
    return build_fixture_store("SPY", {}, underlying, ivx_30d=ivx, hv_30d=hv_map), days


def _cond(indicator: Indicator, op: Operator, value: float) -> Condition:
    return Condition(indicator=indicator, operator=op, value=value)


class TestIvxRank1y:
    def test_rising_series_ranks_100(self) -> None:
        store, days = _store(126)
        view = MarketView(store, days[-1])
        assert evaluate_condition(view, _cond(Indicator.IVX_RANK_1Y, Operator.GT, 90))
        assert not evaluate_condition(view, _cond(Indicator.IVX_RANK_1Y, Operator.LT, 90))

    def test_below_126_observations_is_unevaluable(self) -> None:
        # owner amendment 3 — the boundary, both sides
        store, days = _store(125)
        view = MarketView(store, days[-1])
        # even a trivially-true comparison is refused on a thin window
        assert not evaluate_condition(view, _cond(Indicator.IVX_RANK_1Y, Operator.GT, 0))

        store, days = _store(126)
        view = MarketView(store, days[-1])
        assert evaluate_condition(view, _cond(Indicator.IVX_RANK_1Y, Operator.GT, 0))

    def test_rank_is_point_in_time(self) -> None:
        # a view 30 sessions before the end sees only its own history:
        # 126-obs series viewed at obs 100 → unevaluable there, evaluable
        # at the end — the same store, bounded by as_of
        store, days = _store(126)
        early = MarketView(store, days[99])
        assert not evaluate_condition(early, _cond(Indicator.IVX_RANK_1Y, Operator.GT, 0))
        late = MarketView(store, days[-1])
        assert evaluate_condition(late, _cond(Indicator.IVX_RANK_1Y, Operator.GT, 0))

    def test_history_accessor_excludes_the_future(self) -> None:
        # canary-style boundedness for the new accessors (guardrail #2)
        store, days = _store(130)
        view = MarketView(store, days[99])
        history = view.ivx_30d_history()
        assert len(history) == 100
        future_value = 0.100 + 0.001 * 129
        assert future_value not in history
        assert view.ivx_30d() == pytest.approx(0.100 + 0.001 * 99)


class TestIvxLevel30d:
    def test_percent_point_comparison(self) -> None:
        store, days = _store(10, ivx_override=0.229)  # 22.9%
        view = MarketView(store, days[-1])
        assert evaluate_condition(view, _cond(Indicator.IVX_LEVEL_30D, Operator.GT, 20))
        assert not evaluate_condition(view, _cond(Indicator.IVX_LEVEL_30D, Operator.GT, 25))

    def test_no_series_is_unevaluable(self) -> None:
        days = _weekdays(date(2024, 1, 1), 5)
        store = build_fixture_store(
            "SPY", {}, {d.isoformat(): (100.0, 100.0) for d in days}
        )
        view = MarketView(store, days[-1])
        assert not evaluate_condition(view, _cond(Indicator.IVX_LEVEL_30D, Operator.GT, 0))


class TestHvIvSpread30d:
    def test_spread_in_percentage_points(self) -> None:
        # ivx 25%, hv 20% → spread 5.0 points
        store, days = _store(10, hv=0.20, ivx_override=0.25)
        view = MarketView(store, days[-1])
        assert evaluate_condition(view, _cond(Indicator.HV_IV_SPREAD_30D, Operator.GTE, 4))
        assert not evaluate_condition(view, _cond(Indicator.HV_IV_SPREAD_30D, Operator.GT, 6))

    def test_missing_either_leg_is_unevaluable(self) -> None:
        store, days = _store(10)  # ivx present, hv absent
        view = MarketView(store, days[-1])
        assert not evaluate_condition(view, _cond(Indicator.HV_IV_SPREAD_30D, Operator.GT, 0))
