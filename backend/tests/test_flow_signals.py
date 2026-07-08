"""UW flow/sentiment/pin signals (ENGINE-V4 F2/F3) — hand-computed.

Reduction conventions pinned against the 2026-07-07 probes:
net_prem_ticks rows are per-minute BUCKETS → session totals are SUMS;
market_tide is CUMULATIVE → the session value is the LAST row; NOPE is
the last stamp's vendor value; max pain reads the FRONT expiry (owner
decision: pin dynamics are a front-expiry phenomenon — the convention
is the concept). Dollar-valued signals are sign/rank vocabulary;
put_call_flow_ratio and max_pain_distance_pct are unit-free (raw legal).
Deferred with disclosure (owner decision): oi_change_signal (top-50
movers list = vendor curation, not market dynamics), oi_concentration /
pin_risk (no defensible market-standard formula).
"""

from __future__ import annotations

import copy
from datetime import date, timedelta

import pandas as pd
import pytest
from pydantic import ValidationError

from app.data.flow_signals import derive_flow_row, derive_tide_row
from app.engine.conditions import evaluate_condition
from app.engine.engine import BarView, SliceCoverageError
from app.engine.market import MarketView, build_fixture_store
from app.engine.runner import run_backtest
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


class TestDeriveFlowRow:
    def _net_prem(self) -> pd.DataFrame:
        # two buckets: net premium = (100 + 50) − (30 + 40) = 80;
        # put/call ratio = (200 + 100) / (400 + 100) = 0.6
        return pd.DataFrame({
            "net_call_premium": ["100", "50"],  # string-typed on purpose
            "net_put_premium": [30.0, 40.0],
            "call_volume": [400, 100],
            "put_volume": [200, 100],
        })

    def _nope(self) -> pd.DataFrame:
        # LAST stamp wins even when rows arrive out of order
        return pd.DataFrame({
            "timestamp": ["2026-07-02T19:59:00Z", "2026-07-02T14:31:00Z"],
            "nope": [0.6777, 0.3119],
        })

    def _max_pain(self) -> pd.DataFrame:
        # session 2026-07-02: expired 06-30 ignored; the SAME-DAY 07-02
        # expiry is SETTLING (not front — owner decision 2026-07-08: the
        # value must reference the pin the trade actually faces, and the
        # forward reference is by CALENDAR, not by data — PIT-clean);
        # front = 07-06 (max pain 743 vs close 744.78 → −0.2390%)
        return pd.DataFrame({
            "expiry": ["2026-06-30", "2026-07-06", "2026-07-02"],
            "max_pain": [700, 743, 745],
            "close": [744.78, 744.78, 744.78],
        })

    def test_hand_computed_reductions(self) -> None:
        row = derive_flow_row(self._net_prem(), self._nope(),
                              self._max_pain(), "2026-07-02")
        assert row["net_premium"] == 80.0
        assert row["put_call_ratio"] == 0.6
        assert row["nope_eod"] == 0.6777  # the later stamp, not the larger row
        assert row["max_pain_dist_pct"] == pytest.approx(
            (743 - 744.78) / 744.78 * 100, abs=1e-4)

    def test_missing_families_yield_none_per_signal(self) -> None:
        row = derive_flow_row(self._net_prem(), None, None, "2026-07-02")
        assert row["net_premium"] == 80.0
        assert row["nope_eod"] is None
        assert row["max_pain_dist_pct"] is None
        assert all(v is None for v in derive_flow_row(
            None, None, None, "2026-07-02").values())

    def test_zero_call_volume_never_divides(self) -> None:
        df = self._net_prem()
        df["call_volume"] = [0, 0]
        row = derive_flow_row(df, None, None, "2026-07-02")
        assert row["put_call_ratio"] is None
        assert row["net_premium"] == 80.0  # premium unaffected

    def test_no_expiry_strictly_after_is_none(self) -> None:
        # expired AND same-day rows both fail the strictly-after rule
        mp = pd.DataFrame({"expiry": ["2026-06-30", "2026-07-02"],
                           "max_pain": [700, 745],
                           "close": [744.78, 744.78]})
        row = derive_flow_row(None, None, mp, "2026-07-02")
        assert row["max_pain_dist_pct"] is None

    def test_all_nan_columns_fabricate_nothing(self) -> None:
        # review finding F2/F3 #1: an all-NaN column must yield None —
        # "put/call ratio below 0.8" must never be True on missing data
        df = pd.DataFrame({
            "net_call_premium": [None, None],
            "net_put_premium": [None, None],
            "call_volume": [400.0, 100.0],
            "put_volume": [None, None],
        })
        row = derive_flow_row(df, None, None, "2026-07-02")
        assert row["net_premium"] is None
        assert row["put_call_ratio"] is None


class TestDeriveTideRow:
    def test_cumulative_last_row_wins(self) -> None:
        # cumulative series: the LAST row is the session total — never sum
        tide = pd.DataFrame({
            "timestamp": ["2026-07-02T13:31:00Z", "2026-07-02T20:10:00Z",
                          "2026-07-02T16:00:00Z"],
            "net_call_premium": [10_000_000, -544_169_083, 200_000_000],
            "net_put_premium": [0, 1_000_000, 0],
        })
        row = derive_tide_row(tide)
        assert row["market_tide"] == -544_169_083 - 1_000_000

    def test_empty_or_missing_columns_is_none(self) -> None:
        assert derive_tide_row(None)["market_tide"] is None
        assert derive_tide_row(pd.DataFrame({"x": [1]}))["market_tide"] is None


def _store(days: list[date] | None = None, **series):
    days = days or _weekdays(date(2024, 1, 1), 5)
    underlying = {d.isoformat(): (100.0, 100.0) for d in days}
    return build_fixture_store("SPY", {}, underlying, **series), days


def _cond(indicator: Indicator, op: Operator, value: float) -> Condition:
    return Condition(indicator=indicator, operator=op, value=value)


class TestConditionSemantics:
    def test_signs_and_raw_thresholds(self) -> None:
        days = _weekdays(date(2024, 1, 1), 3)
        iso = days[-1].isoformat()
        store, _ = _store(days=days, net_premium={iso: 80.0},
                          market_tide={iso: -5e8}, nope_eod={iso: 0.68},
                          put_call_ratio={iso: 0.6},
                          max_pain_dist={iso: 0.03})
        view = MarketView(store, days[-1])
        assert evaluate_condition(view, _cond(Indicator.NET_PREMIUM_LEVEL,
                                              Operator.GT, 0))
        assert evaluate_condition(view, _cond(Indicator.MARKET_TIDE_LEVEL,
                                              Operator.LT, 0))
        assert evaluate_condition(view, _cond(Indicator.NOPE_LEVEL,
                                              Operator.GT, 0))
        # unit-free raw thresholds: ratio below 0.8; |distance| < 1
        assert evaluate_condition(view, _cond(Indicator.PUT_CALL_FLOW_RATIO,
                                              Operator.LT, 0.8))
        assert evaluate_condition(view, _cond(Indicator.MAX_PAIN_DISTANCE_PCT,
                                              Operator.LT, 1))
        assert evaluate_condition(view, _cond(Indicator.MAX_PAIN_DISTANCE_PCT,
                                              Operator.GT, -1))

    @pytest.mark.parametrize("indicator", [
        Indicator.NET_PREMIUM_LEVEL, Indicator.NET_PREMIUM_RANK_1Y,
        Indicator.MARKET_TIDE_LEVEL, Indicator.MARKET_TIDE_RANK_1Y,
        Indicator.NOPE_LEVEL, Indicator.NOPE_RANK_1Y,
        Indicator.PUT_CALL_FLOW_RATIO, Indicator.MAX_PAIN_DISTANCE_PCT,
    ])
    def test_unavailable_is_false_never_a_guess(self, indicator) -> None:
        store, days = _store()
        view = MarketView(store, days[-1])
        assert not evaluate_condition(view, _cond(indicator, Operator.GT, -999))
        assert not evaluate_condition(view, _cond(indicator, Operator.LT, 999))

    def test_rank_floor_and_pit(self) -> None:
        days = _weekdays(date(2024, 1, 1), 126)
        nope = {d.isoformat(): 0.1 + 0.001 * i for i, d in enumerate(days)}
        store, _ = _store(days=days, nope_eod=nope)
        # 125 trailing obs → unevaluable; 126 → rank 100 on a rising series
        assert not evaluate_condition(MarketView(store, days[-2]),
                                      _cond(Indicator.NOPE_RANK_1Y, Operator.GT, 0))
        assert evaluate_condition(MarketView(store, days[-1]),
                                  _cond(Indicator.NOPE_RANK_1Y, Operator.GTE, 100))

    def test_barview_reads_previous_session(self) -> None:
        days = _weekdays(date(2024, 1, 1), 3)
        store, _ = _store(days=days, market_tide={
            days[1].isoformat(): 1.0, days[2].isoformat(): -9.0})

        class _Stub:  # BarView's passthroughs touch only the prev view
            pass

        bview = BarView(_Stub(), MarketView(store, days[1]))  # type: ignore[arg-type]
        assert bview.market_tide_level() == 1.0
        assert bview.put_call_ratio() is None


class TestSpecV7Gating:
    @pytest.mark.parametrize("indicator", [
        "net_premium_level", "market_tide_rank_1y", "nope_level",
        "put_call_flow_ratio", "max_pain_distance_pct",
    ])
    def test_v7_vocabulary_on_v6_is_loud(self, indicator: str) -> None:
        doc = copy.deepcopy(CANONICAL)
        doc["spec_version"] = 6
        doc["entry"]["conditions"] = [
            {"indicator": indicator, "operator": ">", "value": 0}]
        with pytest.raises(ValidationError, match="cannot use v7 vocabulary"):
            StrategySpec.model_validate(doc)

    def test_v7_spec_validates_and_matches_schema(self) -> None:
        import json
        from pathlib import Path

        import jsonschema

        doc = copy.deepcopy(CANONICAL)
        doc["spec_version"] = 7
        doc["entry"]["conditions"] = [
            {"indicator": "max_pain_distance_pct", "operator": "<", "value": 1},
            {"indicator": "max_pain_distance_pct", "operator": ">", "value": -1},
        ]
        spec = StrategySpec.model_validate(doc)
        assert spec.spec_version == 7
        schema = json.loads((Path(__file__).resolve().parents[2]
                             / "docs" / "strategy-spec.schema.json").read_text())
        jsonschema.validate(spec.model_dump(mode="json", exclude_none=True), schema)


class TestSignalCoverageRefusal:
    def test_flow_conditioned_window_refuses_before_signal(self) -> None:
        days = _weekdays(date(2024, 1, 1), 6)
        expiry = days[-1] + timedelta(days=7)
        chains = {d.isoformat(): [{
            "expiration": expiry.isoformat(), "right": "put", "strike": 100.0,
            "bid": 1.00, "ask": 1.10, "delta": -0.50, "iv": 0.2,
        }] for d in days}
        underlying = {d.isoformat(): (100.0, 100.0) for d in days}
        store = build_fixture_store(
            "SPY", chains, underlying,
            market_tide={days[3].isoformat(): 5.0, days[4].isoformat(): -5.0,
                         days[5].isoformat(): 5.0})
        spec = StrategySpec.model_validate({
            "spec_version": 7,
            "meta": {"name": "tide-gated short put", "description_raw": "f2 e2e"},
            "underlying": {"ticker": "SPY"},
            "position": {
                "structure": "short_put",
                "legs": [{"right": "put", "side": "short", "ratio": 1,
                          "strike_selection": {"method": "delta", "value": 0.50}}],
                "expiration_selection": {"target_dte": 10, "min_dte": 5,
                                         "max_dte": 15},
            },
            "entry": {"schedule": {"frequency": "signal_only"}, "conditions": [
                {"indicator": "market_tide_level", "operator": ">", "value": 0}],
                "max_concurrent_positions": 2},
            "exit": {"time_exit_dte": 0},
            "sizing": {"method": "fixed_contracts", "value": 1},
            "costs": {"commission_per_contract": 0.65,
                      "slippage_half_spread_fraction": 0.5},
            "backtest": {"start": days[0].isoformat(), "end": None,
                         "initial_capital": 25000, "seed": 42},
        })
        with pytest.raises(SliceCoverageError,
                           match=rf"starts {days[3].isoformat()}"):
            run_backtest(spec, store)
        # covered window runs AND gates: tide positive on days 3 and 5 only
        covered = spec.model_dump(mode="json", exclude_none=True)
        covered["backtest"]["start"] = days[3].isoformat()
        result = run_backtest(StrategySpec.model_validate(covered), store)
        opens = [t for t in result.trades if t.action == "OPEN"]
        assert [t.day for t in opens] == [days[3], days[5]]
