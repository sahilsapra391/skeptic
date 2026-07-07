"""FX.1 — per-session resolution (spec v4 backtest.resolution="finest").

Hand-computed fixtures. Entry math convention (as test_five_min_clock):
short put K=100 quoted 2.00/2.10 → SELL = 2.05 − 0.5×0.05 = 2.025 →
cash +202.50 − 0.65 = +201.85.

What FX.1 must prove:
  * a run spanning a minute-available and a 5-min-only session resolves
    each correctly and RECORDS the mix (masterplan FX.1 fixture);
  * minute bars between 5-min NBBO stamps can fill NOTHING — every fill
    stays on a real quote bar (guardrail #1; clock-vs-quote split);
  * timeframe-"5min" indicators mean ONE thing at every session: the
    rolling series samples only 5-min boundaries on a minute grid (owner
    decision 4 — resolution never silently changes signal meaning);
  * exits on a minute grid resolve at the next QUOTED bar with the same
    dollars as the 5-min grid (grid-invariant when triggers live on
    quote stamps);
  * honest degrade: an empty minute map or an unbuildable minute grid
    falls back to 5-min and is RECORDED as five_min, never an error;
  * absent resolution ≡ explicit "5min" (bit-identical), and the v4
    vocabulary is refused on older specs (loud, never silent).
"""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from app.engine.market import SessionSlice, build_fixture_slice, build_fixture_store
from app.engine.runner import run_backtest
from app.models.spec import StrategySpec


class FinestFixtureIntraday:
    """IntradayProvider with a minute lane: `minute` maps session → 1-min
    slice (None simulates an unbuildable grid — bars_1m missing)."""

    def __init__(
        self,
        slices: dict[str, SessionSlice],
        minute: dict[str, SessionSlice | None] | None = None,
        max_dte: int = 2,
    ) -> None:
        self._slices = {date.fromisoformat(k): v for k, v in slices.items()}
        self._minute = {date.fromisoformat(k): v for k, v in (minute or {}).items()}
        self._max_dte = max_dte

    @property
    def slice_max_trading_dte(self) -> int:
        return self._max_dte

    def sessions(self) -> list[date]:
        return sorted(self._slices)

    def slice_for(self, session: date) -> SessionSlice | None:
        return self._slices.get(session)

    def minute_sessions(self) -> set[date]:
        return set(self._minute)

    def minute_slice_for(self, session: date) -> SessionSlice | None:
        return self._minute.get(session)


def _put(bid: float, ask: float, delta: float, expiration: str) -> dict:
    return {"expiration": expiration, "right": "put", "strike": 100.0,
            "bid": bid, "ask": ask, "delta": delta, "iv": 0.2}


def _spec(exit_rules: dict, *, resolution: str | None = "finest",
          spec_version: int = 4, target_dte: int = 1, min_dte: int = 0,
          max_dte: int = 2, end: str | None = None, clock: str = "5min",
          conditions: list | None = None, time_of_day: str | None = None,
          close_at_time: str | None = None) -> StrategySpec:
    backtest: dict = {"start": None, "end": end, "initial_capital": 10_000,
                      "seed": 42, "clock": clock}
    if resolution is not None:
        backtest["resolution"] = resolution
    exit_block = dict(exit_rules)
    if close_at_time is not None:
        exit_block["close_at_time"] = close_at_time
    return StrategySpec.model_validate({
        "spec_version": spec_version,
        "meta": {"name": "finest fixture", "description_raw": "fixture"},
        "underlying": {"ticker": "SPY"},
        "position": {
            "structure": "short_put",
            "legs": [{"right": "put", "side": "short", "ratio": 1,
                      "strike_selection": {"method": "delta", "value": 0.50}}],
            "expiration_selection": {"target_dte": target_dte,
                                     "min_dte": min_dte, "max_dte": max_dte},
        },
        "entry": {"schedule": {"frequency": "daily",
                               **({"time_of_day": time_of_day} if time_of_day else {})},
                  "conditions": conditions or [],
                  "max_concurrent_positions": 1},
        "exit": exit_block,
        "sizing": {"method": "fixed_contracts", "value": 1},
        "costs": {"commission_per_contract": 0.65,
                  "slippage_half_spread_fraction": 0.5},
        "backtest": backtest,
    })


def _five_min_slice(session: str, expiration: str) -> SessionSlice:
    return build_fixture_slice(
        session,
        quotes={"09:30": [_put(2.00, 2.10, -0.50, expiration)],
                "09:35": [_put(2.00, 2.10, -0.50, expiration)]},
        underlying={"09:30": 100.0, "09:35": 100.0},
    )


def _minute_slice(session: str, expiration: str,
                  quotes: dict | None = None,
                  underlying: dict | None = None) -> SessionSlice:
    """1-min grid: underlying every minute, quotes ONLY at 5-min stamps."""
    return build_fixture_slice(
        session,
        quotes=quotes if quotes is not None else {
            "09:30": [_put(2.00, 2.10, -0.50, expiration)],
            "09:35": [_put(2.00, 2.10, -0.50, expiration)],
        },
        underlying=underlying if underlying is not None else {
            "09:30": 100.0, "09:31": 100.0, "09:32": 100.0,
            "09:33": 100.0, "09:34": 100.0, "09:35": 100.0,
        },
        bar_resolution="1min",
    )


UNDERLYING_2D = {"2025-01-06": (100.0, 100.0), "2025-01-07": (100.0, 100.0),
                 "2025-01-08": (100.0, 100.5)}


class TestResolutionRecording:
    """The masterplan FX.1 fixture: mixed run resolves + records the mix."""

    def test_mixed_run_records_resolution_mix_and_runs(self) -> None:
        store = build_fixture_store("SPY", {}, UNDERLYING_2D)
        provider = FinestFixtureIntraday(
            slices={"2025-01-06": _five_min_slice("2025-01-06", "2025-01-08"),
                    "2025-01-07": _five_min_slice("2025-01-07", "2025-01-08")},
            minute={"2025-01-07": _minute_slice("2025-01-07", "2025-01-08")},
        )
        result = run_backtest(
            _spec({"profit_target_pct": 500}, end="2025-01-07"), store, provider)

        assert result.resolution_mode == "finest"
        assert result.resolution_mix == {"five_min": 1, "minute": 1}
        assert result.resolution_runs == [
            {"first": "2025-01-06", "last": "2025-01-06", "sessions": 1,
             "resolution": "five_min"},
            {"first": "2025-01-07", "last": "2025-01-07", "sessions": 1,
             "resolution": "minute"},
        ]

    def test_resolution_runs_compression_extends_runs(self) -> None:
        # review finding: the run-EXTENSION path (sessions += 1, last moves)
        # must be exercised — two consecutive minute sessions compress into
        # one run whose `last` is the second session
        underlying = dict(UNDERLYING_2D)
        underlying["2025-01-09"] = (100.0, 100.5)
        store = build_fixture_store("SPY", {}, underlying)
        provider = FinestFixtureIntraday(
            slices={"2025-01-06": _five_min_slice("2025-01-06", "2025-01-09"),
                    "2025-01-07": _five_min_slice("2025-01-07", "2025-01-09"),
                    "2025-01-08": _five_min_slice("2025-01-08", "2025-01-09")},
            minute={"2025-01-07": _minute_slice("2025-01-07", "2025-01-09"),
                    "2025-01-08": _minute_slice("2025-01-08", "2025-01-09")},
        )
        result = run_backtest(
            _spec({"profit_target_pct": 500}, end="2025-01-08"), store, provider)
        assert result.resolution_mix == {"five_min": 1, "minute": 2}
        assert result.resolution_runs == [
            {"first": "2025-01-06", "last": "2025-01-06", "sessions": 1,
             "resolution": "five_min"},
            {"first": "2025-01-07", "last": "2025-01-08", "sessions": 2,
             "resolution": "minute"},
        ]

    def test_default_runs_record_nothing_new(self) -> None:
        # a spec without the v4 field records mode None and all-five_min mix
        store = build_fixture_store("SPY", {}, UNDERLYING_2D)
        provider = FinestFixtureIntraday(
            slices={"2025-01-06": _five_min_slice("2025-01-06", "2025-01-08")})
        result = run_backtest(
            _spec({"profit_target_pct": 500}, resolution=None, spec_version=2,
                  end="2025-01-06"),
            store, provider)
        assert result.resolution_mode is None
        assert result.resolution_mix == {"five_min": 1}


class TestMinuteGridHonesty:
    """Guardrail #1 on the minute grid: quote-less bars fill nothing."""

    def test_entry_waits_for_a_real_quote_bar(self) -> None:
        # time_of_day 09:32 opens the entry window on quote-less minute bars:
        # 09:32–09:34 skip (no chain), the fill lands at 09:35's REAL quote.
        # 09:35 quotes 1.80/1.90 → sell = 1.85 − 0.025 = 1.825 → +181.85.
        slc = _minute_slice(
            "2025-01-07", "2025-01-08",
            quotes={"09:30": [_put(2.00, 2.10, -0.50, "2025-01-08")],
                    "09:35": [_put(1.80, 1.90, -0.50, "2025-01-08")]},
        )
        store = build_fixture_store("SPY", {}, UNDERLYING_2D)
        provider = FinestFixtureIntraday(
            slices={"2025-01-07": _five_min_slice("2025-01-07", "2025-01-08")},
            minute={"2025-01-07": slc},
        )
        result = run_backtest(
            _spec({"profit_target_pct": 500}, end="2025-01-07",
                  time_of_day="09:32"),
            store, provider)

        opens = [t for t in result.trades if t.action == "OPEN"]
        assert len(opens) == 1 and opens[0].bar_time == "09:35"
        # the quote-less window logged its honest skip (deduped per session)
        assert any(t.reason == "no_chain_data" for t in result.trades
                   if t.action == "SKIP")
        # the fill detail pins WHICH quote filled: 1.825 (09:35), not 2.025
        assert "1.82" in opens[0].detail, opens[0].detail

    def test_stop_resolves_at_next_quoted_bar_same_dollars_as_5min(self) -> None:
        # the D2b stop fixture on a MINUTE grid: entry 09:30 at 2.025; the
        # 09:31–09:34 bars carry no quotes (nothing can happen); the stop
        # fires at 09:35's 4.20/4.30 quote → pl −226.30, same as 5-min.
        slc = _minute_slice(
            "2025-01-06", "2025-01-07",
            quotes={"09:30": [_put(2.00, 2.10, -0.50, "2025-01-07")],
                    "09:35": [_put(4.20, 4.30, -0.80, "2025-01-07")]},
            underlying={"09:30": 100.0, "09:31": 99.0, "09:32": 98.0,
                        "09:33": 97.0, "09:34": 96.5, "09:35": 96.0},
        )
        underlying = {"2025-01-06": (100.0, 96.0), "2025-01-07": (96.0, 96.5)}
        store = build_fixture_store("SPY", {}, underlying)
        provider = FinestFixtureIntraday(
            slices={"2025-01-06": _five_min_slice("2025-01-06", "2025-01-07")},
            minute={"2025-01-06": slc},
        )
        result = run_backtest(
            _spec({"stop_loss_pct": 100}, end="2025-01-06"), store, provider)

        assert result.resolution_mix == {"minute": 1}
        closes = [t for t in result.trades if t.action == "CLOSE"]
        assert len(closes) == 1 and closes[0].reason == "stop_loss"
        assert closes[0].bar_time == "09:35"
        assert closes[0].pl == pytest.approx(-226.30, abs=0.005)
        assert result.equity[-1] == pytest.approx(9_773.70, abs=0.005)


class TestIndicatorIntegrity:
    """Owner decision 4: timeframe-"5min" indicators sample ONLY 5-min
    boundaries on a minute grid — intermediate minutes never pollute the
    rolling series. The polluted series would push price_vs_sma_pct deeply
    negative and block the entry; this test goes red if anyone appends
    minute bars to the series."""

    def test_five_min_indicator_series_not_polluted_by_minute_bars(self) -> None:
        exp = "2025-01-08"
        slc = build_fixture_slice(
            "2025-01-07",
            quotes={"09:30": [_put(2.00, 2.10, -0.50, exp)],
                    "09:35": [_put(2.00, 2.10, -0.50, exp)],
                    "09:40": [_put(2.00, 2.10, -0.50, exp)]},
            # minute bars spike to 200 between the 5-min points — if they
            # entered the series, SMA(2) at 09:40 would be far from price
            underlying={"09:30": 100.0, "09:31": 200.0, "09:32": 200.0,
                        "09:33": 200.0, "09:34": 200.0, "09:35": 100.5,
                        "09:36": 200.0, "09:37": 200.0, "09:38": 200.0,
                        "09:39": 200.0, "09:40": 101.0},
            bar_resolution="1min",
        )
        store = build_fixture_store("SPY", {}, UNDERLYING_2D)
        provider = FinestFixtureIntraday(
            slices={"2025-01-07": _five_min_slice("2025-01-07", exp)},
            minute={"2025-01-07": slc},
        )
        # clean series [100.0, 100.5, 101.0] → SMA(2)@09:40 = 100.75,
        # price_vs_sma = 101/100.75 − 1 = +0.248% > 0 → entry at 09:40.
        # polluted series would give 101/150.5 − 1 = −32.9% → no entry.
        result = run_backtest(
            _spec({"profit_target_pct": 500}, end="2025-01-07",
                  conditions=[{"indicator": "price_vs_sma_pct", "operator": ">",
                               "value": 0, "period": 2, "timeframe": "5min"}]),
            store, provider)

        opens = [t for t in result.trades if t.action == "OPEN"]
        assert len(opens) == 1 and opens[0].bar_time == "09:40"


class TestHonestDegrade:
    def test_empty_minute_map_runs_five_min_and_records_it(self) -> None:
        store = build_fixture_store("SPY", {}, UNDERLYING_2D)
        provider = FinestFixtureIntraday(
            slices={"2025-01-06": _five_min_slice("2025-01-06", "2025-01-08")})
        result = run_backtest(
            _spec({"profit_target_pct": 500}, end="2025-01-06"), store, provider)
        assert result.resolution_mode == "finest"
        assert result.resolution_mix == {"five_min": 1}

    def test_unbuildable_minute_grid_falls_back_and_records_five_min(self) -> None:
        # map says minute-eligible, but the grid can't be built (None) —
        # the session falls back to the 5-min slice, recorded five_min
        store = build_fixture_store("SPY", {}, UNDERLYING_2D)
        provider = FinestFixtureIntraday(
            slices={"2025-01-06": _five_min_slice("2025-01-06", "2025-01-08")},
            minute={"2025-01-06": None},
        )
        result = run_backtest(
            _spec({"profit_target_pct": 500}, end="2025-01-06"), store, provider)
        assert result.resolution_mix == {"five_min": 1}
        assert result.filled == 1  # the session still simulated normally


class TestBitIdentity:
    """Absent resolution ≡ explicit "5min" — byte-equal runs."""

    def test_absent_equals_explicit_5min(self) -> None:
        store = build_fixture_store("SPY", {}, UNDERLYING_2D)

        def _run(resolution: str | None, version: int):
            provider = FinestFixtureIntraday(
                slices={"2025-01-06": _five_min_slice("2025-01-06", "2025-01-08"),
                        "2025-01-07": _five_min_slice("2025-01-07", "2025-01-08")},
                # a poisoned minute lane proves neither mode touches it
                minute={"2025-01-07": _minute_slice("2025-01-07", "2025-01-08")},
            )
            return run_backtest(
                _spec({"profit_target_pct": 500}, resolution=resolution,
                      spec_version=version, end="2025-01-07"),
                store, provider)

        a = _run(None, 2)
        b = _run("5min", 4)
        assert a.equity == b.equity
        assert [(t.day, t.action, t.detail) for t in a.trades] == \
               [(t.day, t.action, t.detail) for t in b.trades]
        assert b.resolution_mode == "5min"
        assert a.resolution_mix == b.resolution_mix == {"five_min": 2}


class TestZeroDteSellTheWinner:
    """0DTE end-to-end at the intraday clock (masterplan FX.1 acceptance
    flavor): same-session expiry, the winner is SOLD at the force-flat bar,
    settlement never runs.

    Hand-computed: entry 09:30 short put 2.00/2.10 → credit 2.025 (+201.85).
    Force-flat 15:45 quote 0.50/0.60 → buy back 0.55 + 0.5×0.05 = 0.575
    → −57.50 − 0.65 = −58.15. pl = 201.85 − 58.15 = +143.70;
    final equity 10,143.70."""

    def test_0dte_flattens_before_the_bell_and_never_settles(self) -> None:
        session, exp = "2025-01-06", "2025-01-06"  # same-session expiry
        slc = build_fixture_slice(
            session,
            quotes={"09:30": [_put(2.00, 2.10, -0.50, exp)],
                    "15:45": [_put(0.50, 0.60, -0.30, exp)]},
            underlying={"09:30": 100.0, "15:45": 100.0},
        )
        store = build_fixture_store(
            "SPY", {}, {"2025-01-06": (100.0, 100.0), "2025-01-07": (100.0, 100.0)})
        provider = FinestFixtureIntraday(slices={session: slc})
        result = run_backtest(
            _spec({"profit_target_pct": 500}, resolution=None, spec_version=3,
                  target_dte=0, end=session, close_at_time="15:45"),
            store, provider)

        closes = [t for t in result.trades if t.action == "CLOSE"]
        assert len(closes) == 1 and closes[0].reason == "session_flat"
        assert closes[0].bar_time == "15:45"
        assert closes[0].pl == pytest.approx(143.70, abs=0.005)
        assert result.equity[-1] == pytest.approx(10_143.70, abs=0.005)
        # sold, not settled: no assignment/settlement events exist
        assert not any("settle" in (t.reason or "") or "assign" in (t.reason or "")
                       for t in result.trades)


class TestSpecV4Validation:
    def test_resolution_on_v3_is_loud(self) -> None:
        with pytest.raises(ValidationError, match="spec_version 3 cannot use v4"):
            _spec({"profit_target_pct": 50}, spec_version=3)

    def test_resolution_requires_intraday_clock(self) -> None:
        with pytest.raises(ValidationError, match='requires clock "5min"'):
            _spec({"profit_target_pct": 50}, clock="daily", target_dte=30,
                  max_dte=45)

    def test_finest_on_v4_intraday_is_valid(self) -> None:
        spec = _spec({"profit_target_pct": 50})
        assert spec.backtest.resolution is not None
        assert spec.backtest.resolution.value == "finest"

    def test_spec_v4_matches_json_schema(self) -> None:
        # the pydantic models and docs/strategy-spec.schema.json must agree
        # a full v4 finest spec is valid (the IR contract — CLAUDE.md)
        import json
        from pathlib import Path

        schema_path = (Path(__file__).resolve().parents[2]
                       / "docs" / "strategy-spec.schema.json")
        schema = json.loads(schema_path.read_text())
        try:
            import jsonschema
        except ImportError:
            pytest.skip("jsonschema not installed")
        raw = _spec({"profit_target_pct": 50}).model_dump(mode="json",
                                                          exclude_none=True)
        jsonschema.validate(raw, schema)
