"""The declared 5-minute clock (D2b) — hand-computed, amendments pinned.

Entry math (every intraday case): short put K=100, bar quote 2.00/2.10,
SELL = 2.05 − 0.5×0.05 = 2.025 → cash +202.50 − 0.65 = +201.85.

Same-bar semantics (owner amendment 2): entry fills at the DECISION bar;
exits evaluate from the NEXT bar. At 09:35 the quote is 4.20/4.30:
  btc = 4.25 + 0.5×0.05 = 4.275 → profit = (2.025−4.275)/2.025 = −111.1%
  → stop_loss (100%) fires at 09:35, NOT at the 09:30 entry bar.
  exit cash = −427.50 − 0.65 = −428.15 → final 10,201.85 − 428.15 = 9,773.70
  P/L = 201.85 − 428.15 = −226.30. The dollar amounts prove WHICH bar
  filled — a same-bar exit would have closed at the 2.00/2.10 quote.

0DTE settlement: expiry == session; close 98 → short 100-put assigned:
  −10,000 (buy 100 sh @ 100), unwound next OPEN @ 98.50 → +9,850.
  final cash = 10,000 + 201.85 − 10,000 + 9,850 = 10,051.85.

Exit priority (owner amendment 3): a decision point where BOTH the delta
stop and the profit target are true must resolve delta_stop at BOTH clocks
(stop_loss → delta_stop → profit_target → theta_harvest → time → conditions).
"""

from __future__ import annotations

from datetime import date

import pytest

from app.engine.engine import SliceCoverageError
from app.engine.market import SessionSlice, build_fixture_slice, build_fixture_store
from app.engine.runner import run_backtest
from app.models.spec import StrategySpec


class FixtureIntraday:
    """Plain-dict IntradayProvider for engine tests."""

    def __init__(self, slices: dict[str, SessionSlice], max_dte: int = 2) -> None:
        self._slices = {date.fromisoformat(k): v for k, v in slices.items()}
        self._max_dte = max_dte

    @property
    def slice_max_trading_dte(self) -> int:
        return self._max_dte

    def sessions(self) -> list[date]:
        return sorted(self._slices)

    def refresh_sessions(self) -> None:
        return None  # fixtures are static — nothing to relist

    def slice_for(self, session: date) -> SessionSlice | None:
        return self._slices.get(session)


def _put(bid: float, ask: float, delta: float, expiration: str) -> dict:
    return {"expiration": expiration, "right": "put", "strike": 100.0,
            "bid": bid, "ask": ask, "delta": delta, "iv": 0.2}


def _spec(exit_rules: dict, clock: str = "5min", target_dte: int = 1,
          min_dte: int = 0, max_dte: int = 2, start: str | None = None,
          end: str | None = None) -> StrategySpec:
    return StrategySpec.model_validate({
        "spec_version": 2,
        "meta": {"name": "5min fixture", "description_raw": "fixture"},
        "underlying": {"ticker": "SPY"},
        "position": {
            "structure": "short_put",
            "legs": [{"right": "put", "side": "short", "ratio": 1,
                      "strike_selection": {"method": "delta", "value": 0.50}}],
            "expiration_selection": {"target_dte": target_dte,
                                     "min_dte": min_dte, "max_dte": max_dte},
        },
        "entry": {"schedule": {"frequency": "daily"}, "conditions": [],
                  "max_concurrent_positions": 1},
        "exit": exit_rules,
        "sizing": {"method": "fixed_contracts", "value": 1},
        "costs": {"commission_per_contract": 0.65,
                  "slippage_half_spread_fraction": 0.5, "slippage_half_spread_fraction_sell": 0.5},
        "backtest": {"start": start, "end": end, "initial_capital": 10_000,
                     "seed": 42, "clock": clock},
    })


class TestSameBarSemantics:
    """Owner amendment 2: the stop cannot fire on its own entry bar."""

    def test_entry_fills_at_decision_bar_stop_fires_next_bar(self) -> None:
        slc = build_fixture_slice(
            "2025-01-06",
            quotes={
                "09:30": [_put(2.00, 2.10, -0.50, "2025-01-07")],
                "09:35": [_put(4.20, 4.30, -0.80, "2025-01-07")],
                "09:40": [_put(4.20, 4.30, -0.80, "2025-01-07")],
            },
            underlying={"09:30": 100.0, "09:35": 96.0, "09:40": 96.0},
        )
        underlying = {"2025-01-06": (100.0, 96.0), "2025-01-07": (96.0, 96.5)}
        store = build_fixture_store("SPY", {}, underlying)
        result = run_backtest(
            _spec({"stop_loss_pct": 100}, end="2025-01-06"),
            store, FixtureIntraday({"2025-01-06": slc}),
        )

        assert result.clock == "5min"
        assert result.filled == 1
        closes = [t for t in result.trades if t.action == "CLOSE"]
        assert len(closes) == 1 and closes[0].reason == "stop_loss"
        # the dollars prove the bar: entry 2.025, exit 4.275 (09:35 quote)
        assert closes[0].pl == pytest.approx(-226.30, abs=0.005)
        assert result.equity[-1] == pytest.approx(9_773.70, abs=0.005)
        # bar times are stamped into the inspectable log
        opens = [t for t in result.trades if t.action == "OPEN"]
        assert "09:30" in opens[0].detail
        assert "09:35" in closes[0].detail
        assert result.fill_sources == {"ivol_5min": 2}

    def test_entry_once_per_session_and_skips_deduped(self) -> None:
        # no usable quote all session (zero bid) → one skip event per reason,
        # not one per bar
        slc = build_fixture_slice(
            "2025-01-06",
            quotes={f"{h:02d}:{m:02d}": [_put(0.0, 2.10, -0.50, "2025-01-07")]
                    for h, m in [(9, 30), (9, 35), (9, 40), (9, 45)]},
            underlying={"09:30": 100.0, "09:35": 100.0, "09:40": 100.0, "09:45": 100.0},
        )
        underlying = {"2025-01-06": (100.0, 100.0), "2025-01-07": (100.0, 100.0)}
        store = build_fixture_store("SPY", {}, underlying)
        result = run_backtest(
            _spec({"profit_target_pct": 50}, end="2025-01-06"),
            store, FixtureIntraday({"2025-01-06": slc}),
        )
        assert result.filled == 0
        skips = [t for t in result.trades if t.action == "SKIP"]
        assert len(skips) == 1  # zero_bid_short, once — not four times
        assert skips[0].reason == "zero_bid_short"


class TestDualClockExitPriority:
    """Owner amendment 3: one canonical order at every clock. A decision
    point where delta_stop AND profit_target both trigger resolves
    delta_stop — at daily and at 5min alike."""

    EXIT = {"delta_stop_abs": 0.70, "profit_target_pct": 10}

    def _both_trigger_quote(self) -> dict:
        # premium collapsed (profit > 10%) AND |delta| ≥ 0.70 — deliberately
        # contradictory so only the priority order decides the reason
        return _put(1.60, 1.70, -0.75, "2025-01-08")

    def test_five_min_resolves_delta_stop(self) -> None:
        slc = build_fixture_slice(
            "2025-01-06",
            quotes={"09:30": [_put(2.00, 2.10, -0.50, "2025-01-08")],
                    "09:35": [self._both_trigger_quote()]},
            underlying={"09:30": 100.0, "09:35": 99.0},
        )
        underlying = {"2025-01-06": (100.0, 99.0), "2025-01-07": (99.0, 99.0),
                      "2025-01-08": (99.0, 99.0)}
        store = build_fixture_store("SPY", {}, underlying)
        result = run_backtest(_spec(dict(self.EXIT), end="2025-01-06"),
                              store, FixtureIntraday({"2025-01-06": slc}))
        closes = [t for t in result.trades if t.action == "CLOSE"]
        assert closes and closes[0].reason == "delta_stop"

    def test_daily_resolves_delta_stop_identically(self) -> None:
        chains = {
            "2025-01-06": [_put(2.00, 2.10, -0.50, "2025-01-08")],
            "2025-01-07": [self._both_trigger_quote()],
        }
        underlying = {"2025-01-06": (100.0, 100.0), "2025-01-07": (99.0, 99.0),
                      "2025-01-08": (99.0, 99.0)}
        store = build_fixture_store("SPY", chains, underlying)
        result = run_backtest(
            _spec(dict(self.EXIT), clock="daily", target_dte=2, min_dte=0,
                  max_dte=4, end="2025-01-07"),
            store,
        )
        closes = [t for t in result.trades if t.action == "CLOSE"]
        assert closes and closes[0].reason == "delta_stop"
        assert result.fill_sources.get("eod_chain", 0) >= 1


class TestTradingDte:
    """Owner-confirmed basis: Friday '1DTE' selects Monday's expiry
    (3 calendar days — a calendar basis would refuse it)."""

    def test_friday_one_dte_selects_monday_expiry(self) -> None:
        slc = build_fixture_slice(
            "2025-01-10",  # Friday
            quotes={"09:30": [
                _put(1.00, 1.10, -0.50, "2025-01-10"),  # 0DTE (today)
                _put(2.00, 2.10, -0.50, "2025-01-13"),  # Monday: trading-DTE 1
            ]},
            underlying={"09:30": 100.0},
        )
        underlying = {"2025-01-10": (100.0, 100.0), "2025-01-13": (100.0, 100.0)}
        store = build_fixture_store("SPY", {}, underlying)
        result = run_backtest(
            _spec({"profit_target_pct": 90}, target_dte=1, min_dte=1, max_dte=1,
                  end="2025-01-10"),
            store, FixtureIntraday({"2025-01-10": slc}),
        )
        assert result.filled == 1
        opens = [t for t in result.trades if t.action == "OPEN"]
        assert "2025-01-13" in opens[0].detail  # Monday, not Friday's 0DTE


class TestZeroDteSettlement:
    def test_same_day_expiry_settles_at_the_close(self) -> None:
        slc = build_fixture_slice(
            "2025-01-06",
            quotes={"09:30": [_put(2.00, 2.10, -0.50, "2025-01-06")]},
            underlying={"09:30": 100.0},
        )
        underlying = {"2025-01-06": (100.0, 98.0), "2025-01-07": (98.5, 98.6)}
        store = build_fixture_store("SPY", {}, underlying)
        result = run_backtest(
            _spec({"time_exit_dte": 0}, target_dte=0, min_dte=0, max_dte=0,
                  end="2025-01-07"),
            store, FixtureIntraday({"2025-01-06": slc}),
        )
        actions = [t.action for t in result.trades]
        assert "ASSIGN" in actions and "STOCK_SELL" in actions
        # math in module docstring: 10,051.85 after next-open unwind
        assert result.equity[-1] == pytest.approx(10_051.85, abs=0.005)


class TestGapSessionFallback:
    """A 5-min session WITHOUT a slice falls back to the daily close path:
    exits/settlement use the REAL EOD chain (coarser timing, never
    synthetic) and no new entries are minted."""

    def test_eod_chain_closes_position_no_new_entries(self) -> None:
        slc = build_fixture_slice(
            "2025-01-06",
            quotes={"09:30": [_put(2.00, 2.10, -0.50, "2025-01-08")]},
            underlying={"09:30": 100.0},
        )
        chains = {  # EOD chain on the gap session — profit target territory
            "2025-01-07": [_put(0.80, 0.90, -0.30, "2025-01-08")],
        }
        underlying = {"2025-01-06": (100.0, 100.0), "2025-01-07": (101.0, 101.5),
                      "2025-01-08": (101.5, 101.5)}
        store = build_fixture_store("SPY", chains, underlying)
        result = run_backtest(
            _spec({"profit_target_pct": 50}, end="2025-01-07"),
            store, FixtureIntraday({"2025-01-06": slc}),
        )
        closes = [t for t in result.trades if t.action == "CLOSE"]
        assert closes and closes[0].reason == "profit_target"
        assert closes[0].day == date(2025, 1, 7)
        assert result.fill_sources == {"ivol_5min": 1, "eod_chain": 1}
        assert result.filled == 1  # nothing minted on the gap session
        # coverage honesty: only the slice session counts as covered
        assert result.sessions_with_chain == 1


class TestSliceRefusal:
    """Owner amendment 4: refuse BEFORE running, with a plain reason."""

    def test_long_tenor_refused_early(self) -> None:
        store = build_fixture_store("SPY", {}, {"2025-01-06": (100.0, 100.0)})
        with pytest.raises(SliceCoverageError, match="0–2 trading-DTE"):
            run_backtest(
                _spec({"profit_target_pct": 50}, target_dte=45, min_dte=30, max_dte=60),
                store, FixtureIntraday({}),
            )

    def test_ticker_without_intraday_refused_early(self) -> None:
        store = build_fixture_store("SPY", {}, {"2025-01-06": (100.0, 100.0)})
        with pytest.raises(SliceCoverageError, match="has not reached this ticker"):
            run_backtest(_spec({"profit_target_pct": 50}), store, FixtureIntraday({}))

    def test_five_min_without_store_refused(self) -> None:
        store = build_fixture_store("SPY", {}, {"2025-01-06": (100.0, 100.0)})
        with pytest.raises(SliceCoverageError, match="intraday store"):
            run_backtest(_spec({"profit_target_pct": 50}), store, None)
