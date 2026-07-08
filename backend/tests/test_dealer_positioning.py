"""UW dealer-positioning signals (ENGINE-V4 F1) — hand-computed.

Spec v6 gating, loader coercion/dedupe, PIT boundedness, the BarView
previous-session rule, sign/rank condition semantics with the ≥126-obs
rank floor (owner amendment — inherits the D1 ivx_rank convention), and
the PRE-RUN signal-coverage refusal (owner decision 2026-07-07: a window
starting before the signal's first covered session is refused with the
covered window offered — corrupted long-window stats are prevented, not
disclosed).

Values are VENDOR UNITS: only the sign (threshold 0) and the trailing
percentile rank are honest vocabulary; the parser refuses raw-unit
thresholds. The engine compares numbers either way — the unit discipline
lives in the vocabulary layer, pinned by the eval set.
"""

from __future__ import annotations

import copy
from datetime import date, timedelta

import pandas as pd
import pytest
from pydantic import ValidationError

from app.data.gex_signals import load_dealer_exposure
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


def _store(gex: dict[str, float] | None = None,
           dex: dict[str, float] | None = None,
           days: list[date] | None = None):
    days = days or _weekdays(date(2024, 1, 1), 5)
    underlying = {d.isoformat(): (100.0, 100.0) for d in days}
    return build_fixture_store("SPY", {}, underlying,
                               net_gex=gex, net_dex=dex), days


def _cond(indicator: Indicator, op: Operator, value: float) -> Condition:
    return Condition(indicator=indicator, operator=op, value=value)


class TestLoader:
    def _frame(self, **overrides) -> pd.DataFrame:
        base = {
            "date": ["2026-07-01", "2026-07-02"],
            "call_gamma": ["4.0", "5.0"],  # string-typed on purpose
            "put_gamma": ["-3.0", "-6.0"],
            "call_delta": [10.0, 11.0],
            "put_delta": [-4.0, None],
            "ticker": ["SPY", "SPY"],
        }
        base.update(overrides)
        return pd.DataFrame(base)

    def test_coerces_sums_and_skips_per_signal(self, monkeypatch) -> None:
        from app.data import r2

        monkeypatch.setattr(r2, "get_parquet", lambda s3, key: self._frame())
        gex, dex = load_dealer_exposure(object(), "SPY")
        # net gex = call + put (vendor signs embedded): 1.0 then -1.0
        assert gex == {date(2026, 7, 1): 1.0, date(2026, 7, 2): -1.0}
        # 07-02 put_delta is missing → dex honestly absent THAT day only
        assert dex == {date(2026, 7, 1): 6.0}

    def test_duplicate_sessions_last_write_wins(self, monkeypatch) -> None:
        from app.data import r2

        df = self._frame(date=["2026-07-01", "2026-07-01"])
        monkeypatch.setattr(r2, "get_parquet", lambda s3, key: df)
        gex, _ = load_dealer_exposure(object(), "SPY")
        assert gex == {date(2026, 7, 1): -1.0}

    def test_missing_columns_or_artifact_is_empty(self, monkeypatch) -> None:
        from app.data import r2

        monkeypatch.setattr(r2, "get_parquet",
                            lambda s3, key: pd.DataFrame({"date": ["2026-07-01"]}))
        assert load_dealer_exposure(object(), "SPY") == ({}, {})
        monkeypatch.setattr(r2, "get_parquet", lambda s3, key: None)
        assert load_dealer_exposure(object(), "SPY") == ({}, {})


class TestPointInTime:
    def test_bounded_at_as_of(self) -> None:
        days = _weekdays(date(2024, 1, 1), 5)
        gex = {d.isoformat(): float(i) for i, d in enumerate(days)}
        store, _ = _store(gex=gex, days=days)
        assert MarketView(store, days[2]).gex_level() == 2.0
        # history is bounded — never the later values
        assert MarketView(store, days[2]).gex_history() == [0.0, 1.0, 2.0]

    def test_before_first_observation_is_none(self) -> None:
        days = _weekdays(date(2024, 1, 1), 5)
        store, _ = _store(dex={days[3].isoformat(): 7.0}, days=days)
        assert MarketView(store, days[1]).dex_level() is None
        assert MarketView(store, days[1]).dex_history() == []
        assert MarketView(store, days[4]).dex_level() == 7.0


class _StubIView:
    """BarView's dealer-positioning passthroughs touch only the prev view."""


class TestBarViewPreviousSessionRule:
    def test_intraday_bar_reads_yesterdays_observation(self) -> None:
        days = _weekdays(date(2024, 1, 1), 3)
        gex = {days[1].isoformat(): 2.0, days[2].isoformat(): -9.0}
        store, _ = _store(gex=gex, days=days)
        prev = MarketView(store, days[1])
        bview = BarView(_StubIView(), prev)  # type: ignore[arg-type]
        assert bview.gex_level() == 2.0
        assert bview.gex_history() == [2.0]


class TestConditionSemantics:
    def test_gamma_regime_is_the_sign(self) -> None:
        days = _weekdays(date(2024, 1, 1), 3)
        store, _ = _store(gex={days[-1].isoformat(): 282_792.0}, days=days)
        view = MarketView(store, days[-1])
        assert evaluate_condition(view, _cond(Indicator.GEX_LEVEL, Operator.GT, 0))
        assert not evaluate_condition(view, _cond(Indicator.GEX_LEVEL, Operator.LT, 0))
        store2, _ = _store(gex={days[-1].isoformat(): -124_483.0}, days=days)
        view2 = MarketView(store2, days[-1])
        assert evaluate_condition(view2, _cond(Indicator.GEX_LEVEL, Operator.LT, 0))

    def test_dex_sign(self) -> None:
        days = _weekdays(date(2024, 1, 1), 3)
        store, _ = _store(dex={days[-1].isoformat(): -5.0}, days=days)
        view = MarketView(store, days[-1])
        assert evaluate_condition(view, _cond(Indicator.DEX_LEVEL, Operator.LT, 0))

    @pytest.mark.parametrize("indicator", [Indicator.GEX_LEVEL,
                                           Indicator.GEX_RANK_1Y,
                                           Indicator.DEX_LEVEL,
                                           Indicator.DEX_RANK_1Y])
    def test_unavailable_is_false_never_a_guess(self, indicator: Indicator) -> None:
        store, days = _store()
        view = MarketView(store, days[-1])
        assert not evaluate_condition(view, _cond(indicator, Operator.GT, -999))
        assert not evaluate_condition(view, _cond(indicator, Operator.LT, 999))


class TestRankFloor:
    """Owner amendment: gex/dex ranks inherit the D1 ivx_rank floor —
    below 126 trailing observations the rank is unevaluable that day.
    UW's window crossed the floor ~124 sessions in; the rank 'unlocks as
    data accrues' with no code change."""

    def _rising(self, n: int):
        days = _weekdays(date(2024, 1, 1), n)
        gex = {d.isoformat(): 100.0 + i for i, d in enumerate(days)}
        return _store(gex=gex, days=days)

    def test_boundary_both_sides(self) -> None:
        store, days = self._rising(125)
        view = MarketView(store, days[-1])
        # even a trivially-true comparison is refused on a thin window
        assert not evaluate_condition(view, _cond(Indicator.GEX_RANK_1Y,
                                                  Operator.GT, 0))
        store, days = self._rising(126)
        view = MarketView(store, days[-1])
        assert evaluate_condition(view, _cond(Indicator.GEX_RANK_1Y,
                                              Operator.GT, 0))

    def test_rising_series_ranks_100(self) -> None:
        # strictly rising → the current value is the max of its trailing
        # window → rank exactly 100 (the ivx_rank hand fixture, ported)
        store, days = self._rising(200)
        view = MarketView(store, days[-1])
        assert evaluate_condition(view, _cond(Indicator.GEX_RANK_1Y,
                                              Operator.GTE, 100))
        assert not evaluate_condition(view, _cond(Indicator.GEX_RANK_1Y,
                                                  Operator.LT, 100))


def _v6_spec(indicator: str, spec_version: int = 6) -> dict:
    doc = copy.deepcopy(CANONICAL)
    doc["spec_version"] = spec_version
    doc["entry"]["conditions"] = [
        {"indicator": indicator, "operator": ">", "value": 0}
    ]
    return doc


class TestSpecV6Gating:
    @pytest.mark.parametrize("indicator", ["gex_level", "gex_rank_1y",
                                           "dex_level", "dex_rank_1y"])
    def test_v6_vocabulary_on_v5_is_loud(self, indicator: str) -> None:
        with pytest.raises(ValidationError, match="cannot use v6 vocabulary"):
            StrategySpec.model_validate(_v6_spec(indicator, spec_version=5))

    def test_v6_rung_on_v3_is_loud(self) -> None:
        doc = copy.deepcopy(CANONICAL)
        doc["spec_version"] = 3
        doc["position"] = {
            "structure": "long_call",
            "legs": [{"right": "call", "side": "long", "ratio": 1,
                      "strike_selection": {"method": "delta", "value": 0.50}}],
            "expiration_selection": {"target_dte": 45, "min_dte": 35,
                                     "max_dte": 60},
        }
        doc["entry"] = {
            "schedule": {"frequency": "signal_only"}, "conditions": [],
            "max_concurrent_positions": 1,
            "scale_in": {
                "mode": "signal_ladder", "basket": True,
                "rungs": [{"indicator": "gex_level", "operator": "<",
                           "value": 0, "add_contracts": 1}],
                "rearm": {"indicator": "rsi", "operator": ">", "value": 50},
                "max_total_contracts": 3,
            },
        }
        with pytest.raises(ValidationError, match="cannot use v6 vocabulary"):
            StrategySpec.model_validate(doc)

    @pytest.mark.parametrize("indicator", ["gex_level", "dex_rank_1y"])
    def test_v6_spec_validates(self, indicator: str) -> None:
        assert StrategySpec.model_validate(_v6_spec(indicator)).spec_version == 6

    def test_v6_spec_matches_json_schema(self) -> None:
        import json
        from pathlib import Path

        import jsonschema

        schema = json.loads((Path(__file__).resolve().parents[2]
                             / "docs" / "strategy-spec.schema.json").read_text())
        raw = StrategySpec.model_validate(
            _v6_spec("gex_level")).model_dump(mode="json", exclude_none=True)
        jsonschema.validate(raw, schema)


# ------------------------------------------- pre-run coverage refusal

def _run_spec(days: list[date], start: date | None,
              conditions: list[dict]) -> StrategySpec:
    return StrategySpec.model_validate({
        "spec_version": 6,
        "meta": {"name": "gex-gated short put", "description_raw": "f1 e2e"},
        "underlying": {"ticker": "SPY"},
        "position": {
            "structure": "short_put",
            "legs": [{"right": "put", "side": "short", "ratio": 1,
                      "strike_selection": {"method": "delta", "value": 0.50}}],
            "expiration_selection": {"target_dte": 10, "min_dte": 5, "max_dte": 15},
        },
        "entry": {"schedule": {"frequency": "signal_only"},
                  "conditions": conditions, "max_concurrent_positions": 1},
        "exit": {"time_exit_dte": 0},
        "sizing": {"method": "fixed_contracts", "value": 1},
        "costs": {"commission_per_contract": 0.65,
                  "slippage_half_spread_fraction": 0.5},
        "backtest": {"start": start.isoformat() if start else None, "end": None,
                     "initial_capital": 25000, "seed": 42},
    })


def _chained_store(days: list[date], gex: dict[str, float]):
    expiry = days[-1] + timedelta(days=7)
    chains = {}
    underlying = {}
    for d in days:
        underlying[d.isoformat()] = (100.0, 100.0)
        chains[d.isoformat()] = [{
            "expiration": expiry.isoformat(), "right": "put", "strike": 100.0,
            "bid": 1.00, "ask": 1.10, "delta": -0.50, "iv": 0.2,
        }]
    return build_fixture_store("SPY", chains, underlying, net_gex=gex)


_GEX_COND = [{"indicator": "gex_level", "operator": ">", "value": 0}]


class TestSignalCoverageRefusal:
    def test_window_before_signal_is_refused_with_the_covered_window(self) -> None:
        days = _weekdays(date(2024, 1, 1), 6)
        # signal starts on day 3 of a 6-session lake
        gex = {days[3].isoformat(): 5.0, days[4].isoformat(): 5.0,
               days[5].isoformat(): 5.0}
        store = _chained_store(days, gex)
        spec = _run_spec(days, start=days[0], conditions=_GEX_COND)
        with pytest.raises(SliceCoverageError,
                           match=rf"starts {days[3].isoformat()}"):
            run_backtest(spec, store)

    def test_default_full_window_is_refused_too(self) -> None:
        # start=None means "everything" — which includes the uncovered
        # years, so it refuses the same way (no diluted verdicts exist)
        days = _weekdays(date(2024, 1, 1), 6)
        gex = {days[3].isoformat(): 5.0}
        store = _chained_store(days, gex)
        spec = _run_spec(days, start=None, conditions=_GEX_COND)
        with pytest.raises(SliceCoverageError, match="flat cash"):
            run_backtest(spec, store)

    def test_covered_window_runs(self) -> None:
        days = _weekdays(date(2024, 1, 1), 6)
        gex = {days[3].isoformat(): 5.0, days[4].isoformat(): -5.0,
               days[5].isoformat(): -5.0}
        store = _chained_store(days, gex)
        spec = _run_spec(days, start=days[3], conditions=_GEX_COND)
        result = run_backtest(spec, store)
        # and the condition gates honestly inside the covered window:
        # only day 3 has positive net gamma
        opens = [t for t in result.trades if t.action == "OPEN"]
        assert [t.day for t in opens] == [days[3]]

    def test_unconditioned_spec_never_refuses(self) -> None:
        # the refusal is scoped to specs that USE the signal — a plain
        # strategy over the same window is untouched (additive guarantee)
        days = _weekdays(date(2024, 1, 1), 6)
        store = _chained_store(days, gex={})
        spec = _run_spec(days, start=days[0], conditions=[])
        assert run_backtest(spec, store).trades  # runs, fills

    def test_no_signal_data_at_all_is_refused_plainly(self) -> None:
        days = _weekdays(date(2024, 1, 1), 6)
        store = _chained_store(days, gex={})
        spec = _run_spec(days, start=days[0], conditions=_GEX_COND)
        with pytest.raises(SliceCoverageError, match="not banked"):
            run_backtest(spec, store)

    def test_entirely_before_window_offers_the_real_covered_window(self) -> None:
        # review finding F1 #1: start AND end before the signal — the old
        # message offered "signal_first → win_end", an INVERTED window
        days = _weekdays(date(2024, 1, 1), 6)
        gex = {days[4].isoformat(): 5.0, days[5].isoformat(): 5.0}
        store = _chained_store(days, gex)
        spec = StrategySpec.model_validate({
            **_run_spec(days, start=days[0], conditions=_GEX_COND).model_dump(
                mode="json", exclude_none=True),
            "backtest": {"start": days[0].isoformat(),
                         "end": days[2].isoformat(),
                         "initial_capital": 25000, "seed": 42},
        })
        with pytest.raises(SliceCoverageError) as exc:
            run_backtest(spec, store)
        msg = str(exc.value)
        assert "entirely before" in msg
        # the offered window is the real covered one, never inverted
        assert f"Run {days[4].isoformat()} → {days[-1].isoformat()}" in msg

    def test_stale_tail_past_grace_is_refused(self) -> None:
        # the feed died on day 5 of a 12-session window: 7 uncovered tail
        # sessions (> STALE_TAIL_GRACE_SESSIONS) would silently re-read one
        # stale observation — refused, covered window offered
        days = _weekdays(date(2024, 1, 1), 12)
        gex = {days[i].isoformat(): 5.0 for i in range(5)}
        store = _chained_store(days, gex)
        spec = _run_spec(days, start=days[0], conditions=_GEX_COND)
        with pytest.raises(SliceCoverageError) as exc:
            run_backtest(spec, store)
        msg = str(exc.value)
        assert "last observed" in msg
        assert f"Run {days[0].isoformat()} → {days[4].isoformat()}" in msg

    def test_stale_tail_within_grace_runs(self) -> None:
        # exactly STALE_TAIL_GRACE_SESSIONS uncovered tail sessions =
        # vendor publishing lag, not a dead feed — the run proceeds
        days = _weekdays(date(2024, 1, 1), 10)
        gex = {days[i].isoformat(): 5.0 for i in range(5)}
        store = _chained_store(days, gex)
        spec = _run_spec(days, start=days[0], conditions=_GEX_COND)
        result = run_backtest(spec, store)
        opens = [t for t in result.trades if t.action == "OPEN"]
        assert opens  # positive gex on the covered head → entries fired

    def test_rank_condition_refusal_names_the_unlock_date(self) -> None:
        # review finding F1 #2: the offered window must not hide six
        # structurally unevaluable months — the rank floor date is named
        days = _weekdays(date(2024, 1, 1), 260)
        gex = {d.isoformat(): float(i) for i, d in enumerate(days[4:])}
        store = _chained_store(days, gex)
        spec = _run_spec(days, start=days[0], conditions=[
            {"indicator": "gex_rank_1y", "operator": ">", "value": 75}])
        with pytest.raises(SliceCoverageError) as exc:
            run_backtest(spec, store)
        # dates[125] of the signal series = days[4 + 125]
        assert days[129].isoformat() in str(exc.value)
        assert "126 trailing observations" in str(exc.value)

    def test_refusal_fires_at_the_five_minute_clock_too(self) -> None:
        # review finding F1 #6: "at BOTH clocks" was untested at 5min
        from app.engine.market import build_fixture_slice
        from tests.test_five_min_clock import FixtureIntraday, _put

        days = _weekdays(date(2025, 1, 6), 3)
        slices = {}
        underlying = {}
        for d in days:
            expiry = (d + timedelta(days=1)).isoformat()
            slices[d.isoformat()] = build_fixture_slice(
                d.isoformat(),
                quotes={"09:30": [_put(2.00, 2.10, -0.50, expiry)]},
                underlying={"09:30": 100.0},
            )
            underlying[d.isoformat()] = (100.0, 100.0)
        store = build_fixture_store(
            "SPY", {}, underlying,
            net_gex={days[2].isoformat(): 5.0})  # signal starts on day 3
        intraday = FixtureIntraday(slices)
        spec = StrategySpec.model_validate({
            "spec_version": 6,
            "meta": {"name": "5min gex refusal", "description_raw": "f1"},
            "underlying": {"ticker": "SPY"},
            "position": {
                "structure": "short_put",
                "legs": [{"right": "put", "side": "short", "ratio": 1,
                          "strike_selection": {"method": "delta", "value": 0.50}}],
                "expiration_selection": {"target_dte": 1, "min_dte": 0,
                                         "max_dte": 2},
            },
            "entry": {"schedule": {"frequency": "daily"},
                      "conditions": _GEX_COND, "max_concurrent_positions": 1},
            "exit": {"profit_target_pct": 50},
            "sizing": {"method": "fixed_contracts", "value": 1},
            "costs": {"commission_per_contract": 0.65,
                      "slippage_half_spread_fraction": 0.5},
            "backtest": {"start": days[0].isoformat(), "end": None,
                         "initial_capital": 25000, "seed": 42,
                         "clock": "5min"},
        })
        with pytest.raises(SliceCoverageError,
                           match=rf"starts {days[2].isoformat()}"):
            run_backtest(spec, store, intraday)
