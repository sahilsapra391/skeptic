"""5-minute timeframe conditions + time-of-day entries (D2c) — hand-computed.

VWAP hand math (equal volume 100/bar):
  lasts 100, 99, 98 → VWAP after 3 bars = 99.0
  pct at bar 3 = (98/99 − 1)×100 = −1.0101…%
  → "price_vs_vwap_pct < −1" fires exactly at bar 3, not bar 2
    (at bar 2: VWAP 99.5, pct = (99/99.5−1)×100 = −0.5025% — no trigger).

RSI warmup at 5-min (Wilder, period 3, monotonic-down lasts → RSI exactly 0
once seeded): rsi values start at index 3; crossing checks need TWO valid
values (_series_pair) → earliest possible trigger is the 5th bar. The
entry-bar stamp in the trade log proves the bar.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.engine.conditions import evaluate_condition
from app.engine.market import build_fixture_slice, build_fixture_store
from app.engine.runner import run_backtest
from app.models.spec import Condition, Indicator, Operator, StrategySpec, Timeframe
from tests.test_five_min_clock import FixtureIntraday, _put


class _FakeBar:
    """Minimal MarketViewLike for unit-testing the intraday branch."""

    def __init__(self, lasts: list[float], vwap: float | None) -> None:
        self._lasts = lasts
        self._vwap = vwap

    def intraday_closes_upto(self) -> list[float]:
        return self._lasts

    def intraday_vwap(self) -> float | None:
        return self._vwap


class TestVwapUnit:
    def test_hand_computed_pct(self) -> None:
        cond = Condition(indicator=Indicator.PRICE_VS_VWAP_PCT, operator=Operator.LT,
                         value=-1, timeframe=Timeframe.FIVE_MIN)
        # bar 3: vwap 99.0, last 98 → −1.0101% < −1 ✓
        assert evaluate_condition(_FakeBar([100.0, 99.0, 98.0], 99.0), cond)
        # bar 2: vwap 99.5, last 99 → −0.5025% — no trigger
        assert not evaluate_condition(_FakeBar([100.0, 99.0], 99.5), cond)

    def test_no_vwap_is_unevaluable(self) -> None:
        cond = Condition(indicator=Indicator.PRICE_VS_VWAP_PCT, operator=Operator.LT,
                         value=-1, timeframe=Timeframe.FIVE_MIN)
        assert not evaluate_condition(_FakeBar([100.0, 99.0, 98.0], None), cond)


class TestValidation:
    BASE = {
        "spec_version": 2,
        "meta": {"name": "v", "description_raw": "v"},
        "underlying": {"ticker": "SPY"},
        "position": {"structure": "short_put",
            "legs": [{"right": "put", "side": "short", "ratio": 1,
                      "strike_selection": {"method": "delta", "value": 0.50}}],
            "expiration_selection": {"target_dte": 1, "min_dte": 0, "max_dte": 2}},
        "entry": {"schedule": {"frequency": "daily"}, "conditions": [],
                  "max_concurrent_positions": 1},
        "exit": {"profit_target_pct": 50},
        "sizing": {"method": "fixed_contracts", "value": 1},
        "costs": {"commission_per_contract": 0.65,
                  "slippage_half_spread_fraction": 0.5},
        "backtest": {"start": None, "end": None, "initial_capital": 10_000,
                     "seed": 42, "clock": "5min"},
    }

    def test_daily_only_indicator_refuses_5min_timeframe(self) -> None:
        with pytest.raises(ValidationError, match="daily series"):
            Condition(indicator=Indicator.VIX_LEVEL, operator=Operator.GT,
                      value=20, timeframe=Timeframe.FIVE_MIN)

    def test_vwap_refuses_daily_timeframe(self) -> None:
        with pytest.raises(ValidationError, match="intraday-only"):
            Condition(indicator=Indicator.PRICE_VS_VWAP_PCT, operator=Operator.LT,
                      value=-1)

    def test_intraday_vocabulary_needs_5min_clock(self) -> None:
        import copy
        raw = copy.deepcopy(self.BASE)
        raw["backtest"]["clock"] = "daily"
        raw["position"]["expiration_selection"] = {
            "target_dte": 30, "min_dte": 20, "max_dte": 45}
        raw["entry"]["conditions"] = [
            {"indicator": "rsi", "operator": "<", "value": 30, "timeframe": "5min"}]
        with pytest.raises(ValidationError, match='clock "5min"'):
            StrategySpec.model_validate(raw)

        raw["entry"]["conditions"] = []
        raw["entry"]["schedule"]["time_of_day"] = "10:00"
        with pytest.raises(ValidationError, match='clock "5min"'):
            StrategySpec.model_validate(raw)

    def test_v1_cannot_use_intraday_vocabulary(self) -> None:
        import copy
        raw = copy.deepcopy(self.BASE)
        raw["spec_version"] = 1
        with pytest.raises(ValidationError, match="spec_version 2"):
            StrategySpec.model_validate(raw)  # clock 5min alone is v2

    def test_time_of_day_format(self) -> None:
        import copy
        raw = copy.deepcopy(self.BASE)
        raw["entry"]["schedule"]["time_of_day"] = "9:30"  # must be zero-padded
        with pytest.raises(ValidationError):
            StrategySpec.model_validate(raw)
        raw["entry"]["schedule"]["time_of_day"] = "09:30"
        StrategySpec.model_validate(raw)


def _run(slices: dict, underlying: dict, conditions: list[dict],
         time_of_day: str | None = None, frequency: str = "signal_only"):
    schedule: dict = {"frequency": frequency}
    if time_of_day:
        schedule["time_of_day"] = time_of_day
    spec = StrategySpec.model_validate({
        "spec_version": 2,
        "meta": {"name": "intraday signals", "description_raw": "fixture"},
        "underlying": {"ticker": "SPY"},
        "position": {"structure": "short_put",
            "legs": [{"right": "put", "side": "short", "ratio": 1,
                      "strike_selection": {"method": "delta", "value": 0.50}}],
            "expiration_selection": {"target_dte": 1, "min_dte": 0, "max_dte": 2}},
        "entry": {"schedule": schedule, "conditions": conditions,
                  "max_concurrent_positions": 1},
        "exit": {"profit_target_pct": 90},
        "sizing": {"method": "fixed_contracts", "value": 1},
        "costs": {"commission_per_contract": 0.65,
                  "slippage_half_spread_fraction": 0.5},
        "backtest": {"start": None, "end": None, "initial_capital": 10_000,
                     "seed": 42, "clock": "5min"},
    })
    store = build_fixture_store("SPY", {}, underlying)
    return run_backtest(spec, store, FixtureIntraday(slices))


class TestEngineIntegration:
    def _slice(self, lasts: list[float], volumes: list[float] | None = None):
        bars = [f"{9 + (30 + 5 * i) // 60:02d}:{(30 + 5 * i) % 60:02d}"
                for i in range(len(lasts))]
        return build_fixture_slice(
            "2025-01-06",
            quotes={b: [_put(2.00, 2.10, -0.50, "2025-01-07")] for b in bars},
            underlying={b: v for b, v in zip(bars, lasts, strict=True)},
            volumes={b: v for b, v in zip(bars, volumes, strict=True)}
            if volumes else None,
        )

    def test_five_min_rsi_gates_the_entry_bar(self) -> None:
        # monotonic-down lasts → RSI(3) = 0 from index 3; the crossing check
        # needs two valid values → earliest trigger is bar index 4 = 09:50
        lasts = [100.0, 99.5, 99.0, 98.5, 98.0, 97.5, 97.0]
        result = _run({"2025-01-06": self._slice(lasts)},
                      {"2025-01-06": (100.0, 97.0), "2025-01-07": (97.0, 97.0)},
                      [{"indicator": "rsi", "period": 3, "operator": "<",
                        "value": 30, "timeframe": "5min"}])
        assert result.filled == 1
        opens = [t for t in result.trades if t.action == "OPEN"]
        assert "09:50" in opens[0].detail  # warmup held it off until bar 5

    def test_vwap_condition_fires_at_the_hand_computed_bar(self) -> None:
        lasts = [100.0, 99.0, 98.0, 98.0]
        vols = [100.0, 100.0, 100.0, 100.0]
        result = _run({"2025-01-06": self._slice(lasts, vols)},
                      {"2025-01-06": (100.0, 98.0), "2025-01-07": (98.0, 98.0)},
                      [{"indicator": "price_vs_vwap_pct", "operator": "<",
                        "value": -1, "timeframe": "5min"}])
        assert result.filled == 1
        opens = [t for t in result.trades if t.action == "OPEN"]
        assert "09:40" in opens[0].detail  # bar 3 — module-docstring math

    def test_time_of_day_holds_entries(self) -> None:
        lasts = [100.0] * 8  # bars 09:30 … 10:05
        result = _run({"2025-01-06": self._slice(lasts)},
                      {"2025-01-06": (100.0, 100.0), "2025-01-07": (100.0, 100.0)},
                      [], time_of_day="10:00", frequency="daily")
        assert result.filled == 1
        opens = [t for t in result.trades if t.action == "OPEN"]
        assert "10:00" in opens[0].detail  # not 09:30
