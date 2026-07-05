"""Greek-based exits and the net-vega entry cap (D1c) — hand-computed.

Delta-stop unevaluable-day sequence (single short put, threshold 0.60):
  day1 entry: bid 2.00/2.20 → SELL 2.05 → cash +205.00 − 0.65 = +204.35
  day2: delta None → rule UNEVALUABLE, waits (no guess); marks still work:
        btc mark = 3.10 + 0.5×0.10 = 3.15 → equity 10,204.35 − 315 = 9,889.35
  day3: delta −0.70 ≥ 0.60 → close: btc = 4.15 + 0.5×0.15 = 4.225
        cash −422.50 − 0.65 = −423.15 → final 10,204.35 − 423.15 = 9,781.20
        P/L = 204.35 − 423.15 = −218.80

Net-vega cap (owner amendment 2 — |NET| vega of the contract-set):
  spread: short 100-put vega 0.30, long 95-put vega 0.22
          net = (+0.22) + (−0.30) = −0.08 → |net|×100 = $8
  naked:  short 100-put alone → $30
  cap $10: the spread fits ($8), the naked short put does not ($30) —
  netting is the whole point.
"""

from __future__ import annotations

import copy

import pytest

from app.engine.market import build_fixture_store
from app.engine.runner import run_backtest
from app.models.spec import StrategySpec
from tests.fixtures.engine.common import make_spec, put

EXPIRY = "2025-01-31"


def _spec(exit_rules: dict, legs: list[dict], structure: str = "short_put",
          max_vega: float | None = None, end: str = "2025-01-08") -> StrategySpec:
    position: dict = {
        "structure": structure,
        "legs": legs,
        "expiration_selection": {"target_dte": 25, "min_dte": 1, "max_dte": 40},
    }
    if max_vega is not None:
        position["max_vega_per_contract"] = max_vega
    return StrategySpec.model_validate(make_spec(
        spec_version=2,
        position=position,
        entry={"schedule": {"frequency": "weekly", "day_of_week": "monday"},
               "conditions": [], "max_concurrent_positions": 1},
        exit=exit_rules,
        backtest={"start": "2025-01-06", "end": end, "initial_capital": 10_000, "seed": 42},
    ))


SHORT_PUT_LEG = [{"right": "put", "side": "short", "ratio": 1,
                  "strike_selection": {"method": "delta", "value": 0.30}}]


class TestDeltaStopUnevaluableDays:
    CHAINS = {
        "2025-01-06": [put(100.0, 2.00, 2.20, -0.30, EXPIRY)],
        "2025-01-07": [put(100.0, 3.00, 3.20, None, EXPIRY)],  # no delta today
        "2025-01-08": [put(100.0, 4.00, 4.30, -0.70, EXPIRY)],
    }
    UNDERLYING = {
        "2025-01-06": (100.0, 100.0),
        "2025-01-07": (98.0, 97.0),
        "2025-01-08": (96.0, 95.0),
    }

    def test_waits_on_missing_delta_then_fires(self) -> None:
        spec = _spec({"delta_stop_abs": 0.60}, copy.deepcopy(SHORT_PUT_LEG))
        store = build_fixture_store("SPY", self.CHAINS, self.UNDERLYING)
        result = run_backtest(spec, store)

        closes = [t for t in result.trades if t.action == "CLOSE"]
        assert len(closes) == 1
        assert closes[0].reason == "delta_stop"
        assert closes[0].day.isoformat() == "2025-01-08"  # NOT the None-delta day
        # marks kept working on the unevaluable day (math in docstring)
        by_date = dict(zip([d.isoformat() for d in result.dates], result.equity, strict=True))
        assert by_date["2025-01-07"] == pytest.approx(9_889.35, abs=0.005)
        assert result.equity[-1] == pytest.approx(9_781.20, abs=0.005)
        assert closes[0].pl == pytest.approx(-218.80, abs=0.005)


class TestNetVegaCap:
    SPREAD_LEGS = [
        {"right": "put", "side": "short", "ratio": 1,
         "strike_selection": {"method": "delta", "value": 0.30}},
        {"right": "put", "side": "long", "ratio": 1,
         "strike_selection": {"method": "width_from_leg", "value": 5, "reference_leg": 0}},
    ]
    CHAINS = {
        "2025-01-06": [
            put(100.0, 2.00, 2.20, -0.30, EXPIRY, vega=0.30),
            put(95.0, 1.00, 1.10, -0.18, EXPIRY, vega=0.22),
        ],
    }
    UNDERLYING = {"2025-01-06": (100.0, 100.0)}

    def _run(self, legs: list[dict], structure: str, cap: float) -> tuple[int, list[str]]:
        spec = _spec({"profit_target_pct": 50}, copy.deepcopy(legs), structure=structure,
                     max_vega=cap, end="2025-01-06")
        store = build_fixture_store("SPY", self.CHAINS, self.UNDERLYING)
        result = run_backtest(spec, store)
        skips = [t.reason or "" for t in result.trades if t.action == "SKIP"]
        return result.filled, skips

    def test_spread_nets_under_the_cap(self) -> None:
        filled, skips = self._run(self.SPREAD_LEGS, "put_credit_spread", cap=10.0)
        assert filled == 1  # |net| $8 ≤ $10
        assert "vega_cap_exceeded" not in skips

    def test_spread_over_a_tighter_cap_skips(self) -> None:
        filled, skips = self._run(self.SPREAD_LEGS, "put_credit_spread", cap=5.0)
        assert filled == 0
        assert "vega_cap_exceeded" in skips

    def test_naked_leg_shows_netting_matters(self) -> None:
        # the SAME short leg alone carries $30 of vega — the $10 cap that
        # admitted the spread refuses the naked put
        filled, skips = self._run(copy.deepcopy(SHORT_PUT_LEG), "short_put", cap=10.0)
        assert filled == 0
        assert "vega_cap_exceeded" in skips

    def test_missing_vega_never_silently_ignores_the_rule(self) -> None:
        chains = {
            "2025-01-06": [
                put(100.0, 2.00, 2.20, -0.30, EXPIRY, vega=0.30),
                put(95.0, 1.00, 1.10, -0.18, EXPIRY),  # vega unknown
            ],
        }
        spec = _spec({"profit_target_pct": 50}, copy.deepcopy(self.SPREAD_LEGS),
                     structure="put_credit_spread", max_vega=10.0, end="2025-01-06")
        store = build_fixture_store("SPY", chains, self.UNDERLYING)
        result = run_backtest(spec, store)
        skips = [t.reason for t in result.trades if t.action == "SKIP"]
        assert result.filled == 0
        assert "vega_unavailable" in skips
