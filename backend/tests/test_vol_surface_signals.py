"""IVS-derived vol-surface signals (ENGINE-V4 F4) — wiring tests.

Spec v5 gating, JSON-schema parity, point-in-time accessor boundedness,
the BarView previous-session rule, and condition dispatch semantics.

Units contract under test: the derived artifact stores VOL POINTS
(already ×100 — skew_25d 5.2 means "puts 5.2 vol points over calls").
Conditions therefore compare the stored value DIRECTLY: "skew above 5"
→ value 5. This is deliberately unlike ivx_level_30d (lake stores
decimals, condition multiplies by 100); re-multiplying here would make
every threshold wrong by ×100, which is the exact drift class the
derive-once design exists to kill.

The derivation MATH (interpolation exactness, ATM rows, honest absence
per missing tenor) is fixture-tested separately — this file proves the
plumbing from artifact to verdict-eligible condition.
"""

from __future__ import annotations

import copy
from datetime import date, timedelta

import pytest
from pydantic import ValidationError

from app.engine.conditions import evaluate_condition
from app.engine.engine import BarView
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


def _store(skew: dict[str, float] | None = None,
           term: dict[str, float] | None = None,
           days: list[date] | None = None):
    days = days or _weekdays(date(2024, 1, 1), 5)
    underlying = {d.isoformat(): (100.0, 100.0) for d in days}
    return build_fixture_store("SPY", {}, underlying,
                               skew_25d=skew, term_slope=term), days


def _cond(indicator: Indicator, op: Operator, value: float) -> Condition:
    return Condition(indicator=indicator, operator=op, value=value)


def _v5_spec(indicator: str, spec_version: int = 5) -> dict:
    doc = copy.deepcopy(CANONICAL)
    doc["spec_version"] = spec_version
    doc["entry"]["conditions"] = [
        {"indicator": indicator, "operator": ">", "value": 5}
    ]
    return doc


class TestSpecV5Gating:
    @pytest.mark.parametrize("indicator", ["skew_25d", "term_structure_slope"])
    def test_v5_vocabulary_on_v4_is_loud(self, indicator: str) -> None:
        with pytest.raises(ValidationError, match="cannot use v5 vocabulary"):
            StrategySpec.model_validate(_v5_spec(indicator, spec_version=4))

    def test_v5_vocabulary_in_exit_conditions_is_gated_too(self) -> None:
        doc = copy.deepcopy(CANONICAL)
        doc["exit"]["conditions"] = [
            {"indicator": "skew_25d", "operator": "<", "value": 2}
        ]
        with pytest.raises(ValidationError, match="cannot use v5 vocabulary"):
            StrategySpec.model_validate(doc)

    @pytest.mark.parametrize("indicator", ["skew_25d", "term_structure_slope"])
    def test_v5_spec_validates(self, indicator: str) -> None:
        spec = StrategySpec.model_validate(_v5_spec(indicator))
        assert spec.spec_version == 5

    def test_v5_spec_matches_json_schema(self) -> None:
        import json
        from pathlib import Path

        schema_path = (Path(__file__).resolve().parents[2]
                       / "docs" / "strategy-spec.schema.json")
        schema = json.loads(schema_path.read_text())
        try:
            import jsonschema
        except ImportError:
            pytest.skip("jsonschema not installed")
        raw = StrategySpec.model_validate(
            _v5_spec("skew_25d")).model_dump(mode="json", exclude_none=True)
        jsonschema.validate(raw, schema)


class TestPointInTimeAccessors:
    def test_reads_most_recent_at_or_before_as_of(self) -> None:
        days5 = _weekdays(date(2024, 1, 1), 5)
        store, days = _store(skew={
            d.isoformat(): v
            for d, v in zip(days5, [1.0, 2.0, 3.0, 4.0, 5.0], strict=True)
        }, days=days5)
        # on an observation day: that day's value
        assert MarketView(store, days[2]).skew_25d() == 3.0
        # the view is bounded — never the later values
        assert MarketView(store, days[0]).skew_25d() == 1.0

    def test_gap_day_reads_most_recent_prior(self) -> None:
        days = _weekdays(date(2024, 1, 1), 5)
        skew = {days[0].isoformat(): 1.0, days[3].isoformat(): 4.0}
        store, _ = _store(skew=skew, days=days)
        # days 1 and 2 have no derived row (honest gap) → day 0's value
        assert MarketView(store, days[2]).skew_25d() == 1.0

    def test_before_first_observation_is_none(self) -> None:
        days = _weekdays(date(2024, 1, 1), 5)
        store, _ = _store(term={days[3].isoformat(): -1.5}, days=days)
        assert MarketView(store, days[1]).term_structure_slope() is None
        assert MarketView(store, days[4]).term_structure_slope() == -1.5

    def test_no_artifact_is_none(self) -> None:
        store, days = _store()
        view = MarketView(store, days[-1])
        assert view.skew_25d() is None
        assert view.term_structure_slope() is None


class _StubIView:
    """BarView's surface-signal passthroughs touch only the prev view."""


class TestBarViewPreviousSessionRule:
    def test_intraday_bar_reads_yesterdays_fit(self) -> None:
        # today's EOD surface fit doesn't exist yet at 10:15 (guardrail #2):
        # the store holds BOTH days, but the bar reads the previous session
        days = _weekdays(date(2024, 1, 1), 3)
        skew = {days[1].isoformat(): 2.0, days[2].isoformat(): 9.9}
        term = {days[1].isoformat(): -0.5, days[2].isoformat(): 3.3}
        store, _ = _store(skew=skew, term=term, days=days)
        prev = MarketView(store, days[1])  # session before the traded day
        bview = BarView(_StubIView(), prev)  # type: ignore[arg-type]
        assert bview.skew_25d() == 2.0
        assert bview.term_structure_slope() == -0.5


class TestConditionDispatch:
    def test_skew_compares_vol_points_directly(self) -> None:
        # stored 5.2 vol points; "skew above 5" → value 5 — NO re-×100
        days = _weekdays(date(2024, 1, 1), 3)
        store, _ = _store(skew={days[-1].isoformat(): 5.2}, days=days)
        view = MarketView(store, days[-1])
        assert evaluate_condition(view, _cond(Indicator.SKEW_25D, Operator.GT, 5))
        assert not evaluate_condition(view, _cond(Indicator.SKEW_25D, Operator.GT, 6))
        # a ×100 bug would pass this absurd threshold
        assert not evaluate_condition(
            view, _cond(Indicator.SKEW_25D, Operator.GT, 100))

    def test_inverted_term_structure(self) -> None:
        days = _weekdays(date(2024, 1, 1), 3)
        store, _ = _store(term={days[-1].isoformat(): -1.5}, days=days)
        view = MarketView(store, days[-1])
        assert evaluate_condition(
            view, _cond(Indicator.TERM_STRUCTURE_SLOPE, Operator.LT, 0))
        assert not evaluate_condition(
            view, _cond(Indicator.TERM_STRUCTURE_SLOPE, Operator.GT, 0))

    @pytest.mark.parametrize("indicator", [Indicator.SKEW_25D,
                                           Indicator.TERM_STRUCTURE_SLOPE])
    def test_unavailable_is_false_never_a_guess(self, indicator: Indicator) -> None:
        # no derived data → even a trivially-true comparison is refused
        store, days = _store()
        view = MarketView(store, days[-1])
        assert not evaluate_condition(view, _cond(indicator, Operator.GT, -999))
        assert not evaluate_condition(view, _cond(indicator, Operator.LT, 999))


# ------------------------------------------------- derivation math (F4)
# Hand-computed surface fixtures for the derive-once math in
# app/data/ivs_signals.py — the SINGLE implementation the collector imports.

import pandas as pd  # noqa: E402

from app.data.ivs_signals import derive_signal_row, load_ivs_signals  # noqa: E402


def _surf(rows: list[tuple]) -> pd.DataFrame:
    return pd.DataFrame(
        rows, columns=["period", "Call/Put", "delta", "IV", "out-of-the-money %"])


# tenor 30: puts bracket 25Δ at |0.20|→0.30 and |0.30|→0.34; calls at
# 0.20→0.25 and 0.30→0.27. ATM rows (OTM% = 0) carry their own deltas —
# they sit OUTSIDE the 25Δ bracket and must not disturb it.
_BASE_ROWS = [
    (30, "P", -0.20, 0.30, -10), (30, "P", -0.30, 0.34, -5),
    (30, "C", 0.20, 0.25, 10), (30, "C", 0.30, 0.27, 5),
    (30, "C", 0.52, 0.20, 0), (30, "P", -0.48, 0.22, 0),
    (90, "C", 0.53, 0.24, 0), (90, "P", -0.47, 0.26, 0),
]


class TestDerivationMath:
    def test_hand_computed_skew_and_slope(self) -> None:
        # 25Δ put: 0.30 + 0.5·(0.34−0.30) = 0.32; call: 0.25 + 0.5·0.02 = 0.26
        # skew = (0.32 − 0.26)·100 = 6.0 vol points
        # ATM 30d = (0.20+0.22)/2 = 0.21 → 21.0; 90d = 0.25 → 25.0; slope 4.0
        row = derive_signal_row(_surf(_BASE_ROWS))
        assert row["skew_25d"] == 6.0
        assert row["atm_iv_30d"] == 21.0
        assert row["atm_iv_90d"] == 25.0
        assert row["term_slope_30_90"] == 4.0

    def test_exact_grid_node_needs_no_interpolation(self) -> None:
        rows = _BASE_ROWS + [(30, "P", -0.25, 0.315, -7)]
        # a row AT 25Δ wins exactly; call side still interpolates to 0.26
        row = derive_signal_row(_surf(rows))
        assert row["skew_25d"] == pytest.approx((0.315 - 0.26) * 100.0)

    def test_rounding_to_four_decimals(self) -> None:
        # put bracket 0.20→0.30, 0.35→0.34: w = 1/3 → 0.313333…;
        # skew = (0.313333… − 0.26)·100 → 5.3333 exactly 4dp
        rows = [
            (30, "P", -0.20, 0.30, -10), (30, "P", -0.35, 0.34, -5),
            (30, "C", 0.20, 0.25, 10), (30, "C", 0.30, 0.27, 5),
        ]
        assert derive_signal_row(_surf(rows))["skew_25d"] == 5.3333

    def test_unbracketed_delta_fails_closed(self) -> None:
        # puts all deeper than 25Δ → no bracket below → NO skew, even
        # though the call side brackets fine (never extrapolate)
        rows = [
            (30, "P", -0.30, 0.34, -5), (30, "P", -0.40, 0.38, -3),
            (30, "C", 0.20, 0.25, 10), (30, "C", 0.30, 0.27, 5),
        ]
        assert derive_signal_row(_surf(rows))["skew_25d"] is None
        # and the mirror: puts all shallower than 25Δ → no bracket above
        rows2 = [
            (30, "P", -0.10, 0.28, -15), (30, "P", -0.20, 0.30, -10),
            (30, "C", 0.20, 0.25, 10), (30, "C", 0.30, 0.27, 5),
        ]
        assert derive_signal_row(_surf(rows2))["skew_25d"] is None

    def test_never_interpolates_across_tenors(self) -> None:
        # perfect 25Δ brackets at the 60d tenor must NOT stand in for 30d
        rows = [
            (60, "P", -0.20, 0.30, -10), (60, "P", -0.30, 0.34, -5),
            (60, "C", 0.20, 0.25, 10), (60, "C", 0.30, 0.27, 5),
            (30, "C", 0.52, 0.20, 0), (30, "P", -0.48, 0.22, 0),
        ]
        row = derive_signal_row(_surf(rows))
        assert row["skew_25d"] is None
        assert row["atm_iv_30d"] == 21.0  # the 30d ATM row is still real

    def test_missing_tenor_kills_only_its_signals(self) -> None:
        # no 90d rows: slope + atm_90 are honest absences; skew unharmed
        rows = [r for r in _BASE_ROWS if r[0] != 90]
        row = derive_signal_row(_surf(rows))
        assert row["skew_25d"] == 6.0
        assert row["atm_iv_30d"] == 21.0
        assert row["atm_iv_90d"] is None
        assert row["term_slope_30_90"] is None

    def test_non_atm_rows_never_pollute_atm(self) -> None:
        rows = _BASE_ROWS + [(30, "C", 0.9, 0.99, -20), (90, "P", -0.9, 0.99, 20)]
        row = derive_signal_row(_surf(rows))
        assert row["atm_iv_30d"] == 21.0
        assert row["atm_iv_90d"] == 25.0

    def test_empty_surface_is_all_none(self) -> None:
        for surface in (None, pd.DataFrame()):
            assert all(v is None for v in derive_signal_row(surface).values())


class TestLoader:
    def test_per_signal_nan_rows_are_skipped(self, monkeypatch) -> None:
        from app.data import r2

        df = pd.DataFrame({
            "date": ["2024-01-02", "2024-01-03"],
            "skew_25d": [6.0, None],
            "term_slope_30_90": [None, 4.0],
        })
        monkeypatch.setattr(r2, "get_parquet", lambda s3, key: df)
        skew, term = load_ivs_signals(object(), "SPY")
        assert skew == {date(2024, 1, 2): 6.0}
        assert term == {date(2024, 1, 3): 4.0}

    def test_missing_artifact_is_empty_never_an_error(self, monkeypatch) -> None:
        from app.data import r2

        monkeypatch.setattr(r2, "get_parquet", lambda s3, key: None)
        assert load_ivs_signals(object(), "SPY") == ({}, {})


# --------------------------------------------- condition-gated run (F4)

from app.engine.runner import run_backtest  # noqa: E402


def _gated_run_result():
    """signal_only short put gated on skew_25d > 5; the skew series makes
    the condition true on exactly ONE session: 3.0 on day 0, 6.0 on day 1,
    4.0 on day 2. Days 3-4 have no derived row, so the accessor CARRIES
    FORWARD day 2's 4.0 (most recent at-or-before, like every daily
    analytic series) — which fails the > 5 test. Carry-forward staleness
    is unbounded by design, matching the IVX precedent; a series with NO
    prior observation at all is unevaluable (pinned above)."""
    days = _weekdays(date(2024, 1, 1), 5)
    expiry = date(2024, 1, 12)
    chains = {}
    underlying = {}
    for d in days:
        underlying[d.isoformat()] = (100.0, 100.0)
        chains[d.isoformat()] = [{
            "expiration": expiry.isoformat(), "right": "put", "strike": 100.0,
            "bid": 1.00, "ask": 1.10, "delta": -0.50, "iv": 0.2,
        }]
    skew = {days[0].isoformat(): 3.0, days[1].isoformat(): 6.0,
            days[2].isoformat(): 4.0}
    store = build_fixture_store("SPY", chains, underlying, skew_25d=skew)
    spec = StrategySpec.model_validate({
        "spec_version": 5,
        "meta": {"name": "skew-gated short put", "description_raw": "f4 e2e"},
        "underlying": {"ticker": "SPY"},
        "position": {
            "structure": "short_put",
            "legs": [{"right": "put", "side": "short", "ratio": 1,
                      "strike_selection": {"method": "delta", "value": 0.50}}],
            "expiration_selection": {"target_dte": 10, "min_dte": 5, "max_dte": 15},
        },
        "entry": {"schedule": {"frequency": "signal_only"}, "conditions": [
            {"indicator": "skew_25d", "operator": ">", "value": 5}],
            "max_concurrent_positions": 1},
        "exit": {"time_exit_dte": 0},
        "sizing": {"method": "fixed_contracts", "value": 1},
        "costs": {"commission_per_contract": 0.65,
                  "slippage_half_spread_fraction": 0.5},
        "backtest": {"start": None, "end": None, "initial_capital": 25000,
                     "seed": 42},
    })
    return run_backtest(spec, store), days


class TestConditionGatedRun:
    def test_entry_fires_only_on_the_qualifying_session(self) -> None:
        result, days = _gated_run_result()
        opens = [t for t in result.trades if t.action == "OPEN"]
        assert [t.day for t in opens] == [days[1]]


class TestLadderVocabularyGate:
    """Review finding (F4 #2): a Rung IS a Condition and the rearm is one
    too — a v3 ladder must not smuggle v5 vocabulary past the gate."""

    def _ladder_doc(self, rung_indicator: str, rearm_indicator: str) -> dict:
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
                "mode": "signal_ladder",
                "basket": True,
                "rungs": [{"indicator": rung_indicator, "operator": ">",
                           "value": 5, "add_contracts": 1}],
                "rearm": {"indicator": rearm_indicator, "operator": "<",
                          "value": 2},
                "max_total_contracts": 3,
            },
        }
        return doc

    def test_v5_rung_on_v3_is_loud(self) -> None:
        with pytest.raises(ValidationError, match="cannot use v5 vocabulary"):
            StrategySpec.model_validate(self._ladder_doc("skew_25d", "rsi"))

    def test_v5_rearm_on_v3_is_loud(self) -> None:
        with pytest.raises(ValidationError, match="cannot use v5 vocabulary"):
            StrategySpec.model_validate(
                self._ladder_doc("drawdown_from_high_pct", "skew_25d"))

    def test_v5_ladder_validates_at_v5(self) -> None:
        doc = self._ladder_doc("skew_25d", "skew_25d")
        doc["spec_version"] = 5
        assert StrategySpec.model_validate(doc).spec_version == 5


class TestDtypeCoercion:
    """Review finding (F4 #3): vendor JSON dtypes are untrusted — a
    string-typed surface must derive the same numbers, never a silent
    all-None row."""

    def test_string_typed_surface_derives_identically(self) -> None:
        stringy = _surf([(str(p), cp, str(d), str(iv), str(o))
                         for (p, cp, d, iv, o) in _BASE_ROWS])
        row = derive_signal_row(stringy)
        assert row["skew_25d"] == 6.0
        assert row["term_slope_30_90"] == 4.0

    def test_unrecognized_surface_shape_is_all_none(self) -> None:
        df = pd.DataFrame({"period": [30], "IV": [0.2]})  # missing columns
        assert all(v is None for v in derive_signal_row(df).values())
