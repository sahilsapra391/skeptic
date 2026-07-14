"""IVX / HV market-condition filters (D1c) — hand-computed, point-in-time.

Rank fixture: IVX series 0.100, 0.101, …, +0.001/day (strictly rising) on
consecutive weekdays. On day N the current value is the maximum of its own
trailing window → rank exactly 100. Owner amendment 3: below 126 trailing
observations the rank is UNEVALUABLE that day → False, whatever the
operator asks.

Levels are decimals in the lake (0.229 = 22.9%); conditions compare in
percentage points, like vix_level.

Non-finite observations (poisoned vendor rows) are UNEVALUABLE, never a
number: window-wide for ranks (an unguarded NaN current obs reads as rank
0.0 — every "rank < X" fabricated), per-read for levels (±inf would
fabricate a threshold cross; NaN silently compares False).
"""

from __future__ import annotations

import copy
from datetime import date, timedelta

import pytest
from pydantic import ValidationError

from app.engine.conditions import _trailing_rank, _trailing_zscore, evaluate_condition
from app.engine.market import MarketView, build_fixture_store
from app.models.spec import Condition, Indicator, Operator, StrategySpec
from tests.test_spec_roundtrip import CANONICAL


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

    def test_poisoned_current_observation_refuses(self) -> None:
        # the fabricated-signal shape the kernel guard exists for: an
        # unguarded NaN current observation counts nothing and reads as
        # rank 0.0 — every "rank < X" passed on the poisoned day
        store, days = _store(126, ivx_override=float("nan"))
        view = MarketView(store, days[-1])
        assert not evaluate_condition(view, _cond(Indicator.IVX_RANK_1Y, Operator.LT, 5))
        assert not evaluate_condition(view, _cond(Indicator.IVX_RANK_1Y, Operator.GT, 0))


class TestTrailingRankKernel:
    """_trailing_rank shared by every *_rank/percentile indicator — the
    gex/dex/nope/net_premium/market_tide ranks and iv_percentile inherit
    each refusal proven here."""

    def test_midpoint_rank_hand_computed(self) -> None:
        # 125 ramp values 0.100..0.224 plus a current of 0.1625: the 63
        # ramp values ≤ 0.1625 plus the current itself → 64/126 → 50.79365
        history = [0.100 + 0.001 * i for i in range(125)] + [0.1625]
        assert _trailing_rank(history, min_obs=126) == pytest.approx(64 / 126 * 100.0)

    def test_non_finite_observation_is_none(self) -> None:
        # one bad vendor row must refuse, not read as rank 0.0: "v <= nan"
        # is False for every v, so a NaN CURRENT observation counted
        # nothing and any "rank < X" fabricated a signal
        history = [0.100 + 0.001 * i for i in range(126)]
        history[-1] = float("nan")
        assert _trailing_rank(history, min_obs=126) is None
        history[-1] = float("inf")
        assert _trailing_rank(history, min_obs=126) is None

    def test_mid_window_nan_is_none(self) -> None:
        # a mid-window NaN is subtler: out of the ≤-count but still in the
        # denominator, silently deflating every rank — refuse instead
        history = [0.100 + 0.001 * i for i in range(126)]
        history[50] = float("nan")
        assert _trailing_rank(history, min_obs=126) is None

    def test_non_finite_outside_window_is_ignored(self) -> None:
        # only the trailing 252 observations are evaluated; ancient poison
        # must not refuse today's rank
        history = [float("nan")] + [0.100 + 0.001 * i for i in range(252)]
        assert _trailing_rank(history, min_obs=126) == 100.0


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


# ─────────────────────────── ivx_zscore_1y (v8) ────────────────────────────
# The σ-unit sibling of ivx_rank_1y: same 30d IVX series, same 126-obs
# floor, standardized instead of ranked. Closed forms used below:
#   * linear ramp of n obs, any step:  z = √(3(n−1)/(n+1))
#     (last − mean = s(n−1)/2, population σ = s·√((n²−1)/12))
#   * n−1 equal values + one dip:      z = −√(n−1)
#     (the outlier owns the variance: σ = |d|·√(n−1)/n, x − mean = d(n−1)/n)


class TestTrailingZscoreKernel:
    def test_linear_ramp_closed_form(self) -> None:
        history = [0.100 + 0.001 * i for i in range(126)]
        # √(3·125/127) = 1.7183585…
        assert _trailing_zscore(history, min_obs=126) == pytest.approx(
            1.7183585, abs=1e-6)

    def test_window_caps_at_252_observations(self) -> None:
        history = [0.100 + 0.001 * i for i in range(300)]
        # last 252 only: √(3·251/253) = 1.7251912 — NOT the 300-obs value
        # √(3·299/301) = 1.7262368
        z = _trailing_zscore(history, min_obs=126)
        assert z == pytest.approx(1.7251912, abs=1e-6)

    def test_below_min_obs_is_none(self) -> None:
        history = [0.100 + 0.001 * i for i in range(125)]
        assert _trailing_zscore(history, min_obs=126) is None

    @pytest.mark.parametrize(("value", "n"), [
        (0.2, 126),      # variance lands on exactly 0.0
        (0.229, 144),    # float residue: var > 0, z would be exactly +1.0
        (0.1834, 175),   # +1.0
        (0.1567, 211),   # −1.0
        (1 / 3, 200),    # +1.0
    ])
    def test_flat_window_is_none(self, value: float, n: int) -> None:
        # a flat series has no σ to standardize by — and the guard must be
        # on the VALUES, not on var <= 0: sum(window)/n leaves a residual
        # for most flat decimals and the residual z is exactly ±1.0
        # (review finding, reproduced) — a dead forward-filling feed must
        # never manufacture a signal
        assert _trailing_zscore([value] * n, min_obs=126) is None

    def test_non_finite_observation_is_none(self) -> None:
        # one bad vendor row must refuse, not silently poison the window
        # into a NaN that every comparison reads as False
        history = [0.100 + 0.001 * i for i in range(126)]
        history[50] = float("nan")
        assert _trailing_zscore(history, min_obs=126) is None
        history[50] = float("inf")
        assert _trailing_zscore(history, min_obs=126) is None

    def test_single_dip_is_negative_sqrt_n_minus_1(self) -> None:
        history = [0.200] * 125 + [0.190]
        # −√125 = −11.1803399
        assert _trailing_zscore(history, min_obs=126) == pytest.approx(
            -11.1803399, abs=1e-6)


class TestIvxZscore1y:
    def test_ramp_z_and_operator_directions(self) -> None:
        # the rank fixture's rising series, standardized: z = 1.7183585
        store, days = _store(126)
        view = MarketView(store, days[-1])
        assert evaluate_condition(view, _cond(Indicator.IVX_ZSCORE_1Y, Operator.GT, 1.7))
        assert not evaluate_condition(view, _cond(Indicator.IVX_ZSCORE_1Y, Operator.GT, 1.75))
        assert evaluate_condition(view, _cond(Indicator.IVX_ZSCORE_1Y, Operator.LT, 1.75))

    def test_below_126_observations_is_unevaluable(self) -> None:
        # the ivx_rank floor, inherited — the boundary, both sides
        store, days = _store(125)
        view = MarketView(store, days[-1])
        # even a trivially-true comparison is refused on a thin window
        assert not evaluate_condition(
            view, _cond(Indicator.IVX_ZSCORE_1Y, Operator.GT, -100))

        store, days = _store(126)
        view = MarketView(store, days[-1])
        assert evaluate_condition(
            view, _cond(Indicator.IVX_ZSCORE_1Y, Operator.GT, -100))

    def test_zscore_is_point_in_time(self) -> None:
        # same store, bounded by as_of: unevaluable at obs 100, evaluable
        # at the end — no future observation leaks into the window
        store, days = _store(126)
        early = MarketView(store, days[99])
        assert not evaluate_condition(
            early, _cond(Indicator.IVX_ZSCORE_1Y, Operator.GT, -100))
        late = MarketView(store, days[-1])
        assert evaluate_condition(
            late, _cond(Indicator.IVX_ZSCORE_1Y, Operator.GT, -100))

    def test_flat_series_is_unevaluable(self) -> None:
        # 0.229 × 144 is a residual-variance shape: var <= 0 would MISS it
        # and fabricate z = +1.0 (kernel test above) — prove the refusal
        # holds through the full evaluate_condition path too
        days = _weekdays(date(2024, 1, 1), 144)
        ivx = {d.isoformat(): 0.229 for d in days}
        underlying = {d.isoformat(): (100.0, 100.0) for d in days}
        store = build_fixture_store("SPY", {}, underlying, ivx_30d=ivx)
        view = MarketView(store, days[-1])
        assert not evaluate_condition(
            view, _cond(Indicator.IVX_ZSCORE_1Y, Operator.GT, -100))
        assert not evaluate_condition(
            view, _cond(Indicator.IVX_ZSCORE_1Y, Operator.LT, 100))


def test_ivx_family_registries_stay_in_sync() -> None:
    # the three IVX-family indicators ride one series, and the engine's
    # dead-feed (staleness) and splice-disclosure registries read them via
    # .get(indicator, ()) — a missing member is a SILENT empty default, so
    # membership is pinned here (review finding: no enumeration test
    # guarded these registries)
    from app.engine.engine import _PROVENANCE_SERIES, _STALENESS_ONLY_SERIES

    family = {Indicator.IVX_LEVEL_30D, Indicator.IVX_RANK_1Y,
              Indicator.IVX_ZSCORE_1Y}
    assert family <= set(_STALENESS_ONLY_SERIES)
    assert family <= set(_PROVENANCE_SERIES)
    for ind in family:
        assert ("30d IVX", "ivx_dates") in _STALENESS_ONLY_SERIES[ind]
        assert "ivx_30d" in _PROVENANCE_SERIES[ind]


class TestSpecV8Gating:
    def test_v8_vocabulary_on_v7_is_loud(self) -> None:
        doc = copy.deepcopy(CANONICAL)
        doc["spec_version"] = 7
        doc["entry"]["conditions"] = [
            {"indicator": "ivx_zscore_1y", "operator": ">", "value": 1.5}]
        with pytest.raises(ValidationError, match="cannot use v8 vocabulary"):
            StrategySpec.model_validate(doc)

    def test_ladder_rung_cannot_smuggle_v8(self) -> None:
        # a Rung IS a Condition — the gate must see the ladder's vocabulary
        doc = copy.deepcopy(CANONICAL)
        doc["spec_version"] = 7
        doc["entry"]["scale_in"] = {
            "mode": "signal_ladder",
            "basket": True,
            "rungs": [{"indicator": "ivx_zscore_1y", "operator": "<", "value": -1,
                       "add_contracts": 1}],
            "rearm": {"indicator": "rsi", "operator": ">", "value": 50},
            "max_total_contracts": 2,
        }
        with pytest.raises(ValidationError, match="cannot use v8 vocabulary"):
            StrategySpec.model_validate(doc)

    def test_v8_spec_validates_and_matches_schema(self) -> None:
        import json
        from pathlib import Path

        import jsonschema

        doc = copy.deepcopy(CANONICAL)
        doc["spec_version"] = 8
        doc["entry"]["conditions"] = [
            {"indicator": "ivx_zscore_1y", "operator": ">", "value": 2}]
        spec = StrategySpec.model_validate(doc)
        assert spec.spec_version == 8
        schema = json.loads((Path(__file__).resolve().parents[2]
                             / "docs" / "strategy-spec.schema.json").read_text())
        jsonschema.validate(spec.model_dump(mode="json", exclude_none=True), schema)


class TestNonFiniteLevelReads:
    """The _finite gate on scalar vendor reads, proven through the ivx
    branches — every *_level/spread/ratio branch reads through the same
    gate."""

    def test_nan_level_is_unevaluable_both_directions(self) -> None:
        store, days = _store(10, ivx_override=float("nan"))
        view = MarketView(store, days[-1])
        assert not evaluate_condition(view, _cond(Indicator.IVX_LEVEL_30D, Operator.GT, 0))
        assert not evaluate_condition(view, _cond(Indicator.IVX_LEVEL_30D, Operator.LT, 100))

    def test_inf_level_must_not_fabricate_a_cross(self) -> None:
        # +inf compares greater than any threshold: unguarded, a poisoned
        # row read "IVX above 25" as True — a fabricated level signal
        store, days = _store(10, ivx_override=float("inf"))
        view = MarketView(store, days[-1])
        assert not evaluate_condition(view, _cond(Indicator.IVX_LEVEL_30D, Operator.GT, 25))

    def test_nan_spread_leg_is_unevaluable(self) -> None:
        store, days = _store(10, hv=float("nan"), ivx_override=0.25)
        view = MarketView(store, days[-1])
        assert not evaluate_condition(view, _cond(Indicator.HV_IV_SPREAD_30D, Operator.GT, 0))
        assert not evaluate_condition(view, _cond(Indicator.HV_IV_SPREAD_30D, Operator.LT, 99))
