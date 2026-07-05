"""Portfolio greeks per marked session (D1d) — hand-computed.

Single short put, 2 contracts, quote greeks: delta −0.30, gamma 0.02,
theta −0.05 ($/day/share), vega 0.30. Short → sign −1, scale = −1×2×100:
  delta = −(−0.30)×200 = +60 share-equivalents (short put is long delta)
  gamma = −(0.02)×200  = −4
  theta = −(−0.05)×200 = +10 $/day (the short collects decay)
  vega  = −(0.30)×200  = −60 $/vol-pt
Covered-call stock adds +100Δ per 100 shares (1Δ per share).
A greek missing on any open leg → THAT aggregate is None for the day —
a partial sum would understate exposure. Flat book → exact zeros.
"""

from __future__ import annotations

import pytest

from app.engine.market import build_fixture_store
from app.engine.runner import run_backtest
from app.models.spec import StrategySpec
from tests.fixtures.engine.common import make_spec

EXPIRY = "2025-01-31"


def _row(**over: object) -> dict:
    base: dict = {
        "expiration": EXPIRY, "right": "put", "strike": 100.0,
        "bid": 2.00, "ask": 2.20, "delta": -0.30,
        "gamma": 0.02, "theta": -0.05, "vega": 0.30,
    }
    base.update(over)
    return base


def _spec(contracts: int = 2, end: str = "2025-01-07") -> StrategySpec:
    return StrategySpec.model_validate(make_spec(
        position={
            "structure": "short_put",
            "legs": [{"right": "put", "side": "short", "ratio": 1,
                      "strike_selection": {"method": "delta", "value": 0.30}}],
            "expiration_selection": {"target_dte": 25, "min_dte": 1, "max_dte": 40},
        },
        entry={"schedule": {"frequency": "weekly", "day_of_week": "monday"},
               "conditions": [], "max_concurrent_positions": 1},
        exit={"profit_target_pct": 90},
        sizing={"method": "fixed_contracts", "value": contracts},
        backtest={"start": "2025-01-06", "end": end,
                  "initial_capital": 25_000, "seed": 42},
    ))


class TestAggregation:
    def test_short_put_signs_and_scale(self) -> None:
        chains = {"2025-01-06": [_row()], "2025-01-07": [_row()]}
        underlying = {"2025-01-06": (100.0, 100.0), "2025-01-07": (100.0, 100.0)}
        result = run_backtest(_spec(), build_fixture_store("SPY", chains, underlying))

        assert result.portfolio_delta == [pytest.approx(60.0)] * 2
        assert result.portfolio_gamma == [pytest.approx(-4.0)] * 2
        assert result.portfolio_theta == [pytest.approx(10.0)] * 2
        assert result.portfolio_vega == [pytest.approx(-60.0)] * 2

    def test_missing_greek_is_an_honest_gap_not_a_partial_sum(self) -> None:
        chains = {
            "2025-01-06": [_row()],
            "2025-01-07": [_row(vega=None)],  # vega vanishes on day 2
        }
        underlying = {"2025-01-06": (100.0, 100.0), "2025-01-07": (100.0, 100.0)}
        result = run_backtest(_spec(), build_fixture_store("SPY", chains, underlying))

        assert result.portfolio_vega == [pytest.approx(-60.0), None]
        # the other greeks stay computed — per-greek independence
        assert result.portfolio_delta == [pytest.approx(60.0)] * 2

    def test_flat_book_is_exact_zero(self) -> None:
        # zero-bid quote → entry skips → the book stays flat: greeks are 0.0
        chains = {"2025-01-06": [_row(bid=0.0)]}
        underlying = {"2025-01-06": (100.0, 100.0)}
        result = run_backtest(
            _spec(end="2025-01-06"), build_fixture_store("SPY", chains, underlying)
        )
        assert result.filled == 0
        assert result.portfolio_delta == [0.0]
        assert result.portfolio_vega == [0.0]

    def test_covered_call_stock_adds_share_delta(self) -> None:
        chains = {"2025-01-06": [
            _row(right="call", strike=105.0, delta=0.20, gamma=0.02,
                 theta=-0.05, vega=0.30, bid=1.50, ask=1.60),
        ]}
        underlying = {"2025-01-06": (100.0, 100.0)}
        spec = StrategySpec.model_validate(make_spec(
            position={
                "structure": "covered_call",
                "legs": [{"right": "call", "side": "short", "ratio": 1,
                          "strike_selection": {"method": "delta", "value": 0.20}}],
                "expiration_selection": {"target_dte": 25, "min_dte": 1, "max_dte": 40},
            },
            entry={"schedule": {"frequency": "weekly", "day_of_week": "monday"},
                   "conditions": [], "max_concurrent_positions": 1},
            exit={"profit_target_pct": 90},
            backtest={"start": "2025-01-06", "end": "2025-01-06",
                      "initial_capital": 25_000, "seed": 42},
        ))
        result = run_backtest(spec, build_fixture_store("SPY", chains, underlying))
        # short call: −0.20×100 = −20Δ; 100 shares: +100Δ → net +80
        assert result.portfolio_delta == [pytest.approx(80.0)]

    def test_series_aligns_with_dates(self) -> None:
        chains = {"2025-01-06": [_row()], "2025-01-07": [_row()]}
        underlying = {"2025-01-06": (100.0, 100.0), "2025-01-07": (100.0, 100.0)}
        result = run_backtest(_spec(), build_fixture_store("SPY", chains, underlying))
        for series in (result.portfolio_delta, result.portfolio_gamma,
                       result.portfolio_theta, result.portfolio_vega):
            assert len(series) == len(result.dates) == len(result.equity)
