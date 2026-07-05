"""Liquidity-aware fill model (D1b) — hand-computed, like every engine test.

effective_slip, base 0.5, min_oi 10 → knee at 10×floor = 100:
  OI None → 0.5 (unknown is disclosed, never penalized)
  OI 100  → p = 1 − 100/100 = 0   → 0.5
  OI  50  → p = 1 − 50/100  = 0.5 → 0.5 + 0.5×0.5  = 0.75
  OI  10  → p = 1 − 10/100  = 0.9 → 0.5 + 0.5×0.9  = 0.95
  min_oi 0 disables scaling entirely.

Gates (Moderate defaults: spread ≤ 25% of mid, OI ≥ 10, volume floor off):
  bid 0.02 / ask 0.30 → mid 0.16, spread 0.28/0.16 = 175%  → illiquid_spread
  OI 5 (< 10)                                              → illiquid_oi
  min_volume 10 with volume 3                              → illiquid_volume

Stress-mode entry math (fx_skip_illiquid chains, mode=stress):
  SELL put at slip 1.0 → fill = bid = 0.02
  cash  = +0.02×100 − 0.65 = +1.35                → 10,001.35
  mark  = buy-to-close at base slip (OI unknown):
          mid 0.16 + 0.5×(0.30 − 0.16) = 0.23     → −23.00
  equity = 10,001.35 − 23.00 = 9,978.35

OI-penalized entry math:
  put K=100 bid 2.00 / ask 2.20, OI 50 → eff slip 0.75
  SELL fill = 2.10 − 0.75×0.10 = 2.025
  cash  = +202.50 − 0.65 = +201.85                → 10,201.85
  mark  = 2.10 + 0.75×0.10 = 2.175                → −217.50
  equity = 10,201.85 − 217.50 = 9,984.35
  spread% at fill = 0.20/2.10 = 0.095238
"""

from __future__ import annotations

import copy

import pytest

from app.engine.fills import effective_slip, liquidity_gate, spread_pct
from app.engine.market import build_fixture_store
from app.engine.runner import run_backtest
from app.engine.types import Quote
from app.honesty.stages import liquidity_profile
from app.models.spec import Costs, StrategySpec
from tests.fixtures.engine import fx_skip_illiquid
from tests.fixtures.engine.common import make_spec, put

DEFAULTS = Costs()  # Moderate: spread 25%, OI ≥ 10, volume off, mode=skip


class TestFillHelpers:
    def test_spread_pct_hand_checked(self) -> None:
        assert spread_pct(Quote(bid=2.00, ask=2.20, delta=None)) == pytest.approx(0.2 / 2.1)
        assert spread_pct(Quote(bid=0.02, ask=0.30, delta=None)) == pytest.approx(1.75)
        assert spread_pct(Quote(bid=None, ask=1.0, delta=None)) is None
        assert spread_pct(Quote(bid=0.0, ask=0.0, delta=None)) is None  # mid 0: not assessable

    def test_effective_slip_oi_scaling(self) -> None:
        def q(oi: int | None) -> Quote:
            return Quote(bid=1.0, ask=1.1, delta=None, open_interest=oi)

        assert effective_slip(0.5, q(None), 10) == 0.5  # unknown never penalized
        assert effective_slip(0.5, q(100), 10) == pytest.approx(0.5)
        assert effective_slip(0.5, q(50), 10) == pytest.approx(0.75)
        assert effective_slip(0.5, q(10), 10) == pytest.approx(0.95)
        assert effective_slip(0.5, q(5), 10) == pytest.approx(0.975)  # floor gates it anyway
        assert effective_slip(0.5, q(5), 0) == 0.5  # scaling disabled

    def test_liquidity_gate_reasons(self) -> None:
        wide = Quote(bid=0.02, ask=0.30, delta=None, open_interest=500, volume=100)
        assert liquidity_gate(wide, DEFAULTS) == "illiquid_spread"

        thin_oi = Quote(bid=1.00, ask=1.10, delta=None, open_interest=5, volume=100)
        assert liquidity_gate(thin_oi, DEFAULTS) == "illiquid_oi"

        vol_floor = DEFAULTS.model_copy(update={"min_volume": 10})
        thin_vol = Quote(bid=1.00, ask=1.10, delta=None, open_interest=500, volume=3)
        assert liquidity_gate(thin_vol, vol_floor) == "illiquid_volume"

        unknown = Quote(bid=1.00, ask=1.10, delta=None)  # OI/volume unknown → no gate
        assert liquidity_gate(unknown, vol_floor) is None

        deep = Quote(bid=1.00, ask=1.10, delta=None, open_interest=5000, volume=900)
        assert liquidity_gate(deep, DEFAULTS) is None


def _stress_spec() -> StrategySpec:
    spec = copy.deepcopy(fx_skip_illiquid.SPEC)
    spec["costs"]["liquidity_mode"] = "stress"
    return StrategySpec.model_validate(spec)


class TestStressMode:
    def test_gated_contract_fills_at_full_adverse(self) -> None:
        store = build_fixture_store(
            "SPY", fx_skip_illiquid.CHAINS, fx_skip_illiquid.UNDERLYING
        )
        result = run_backtest(_stress_spec(), store)

        assert result.filled == 1
        assert result.fills_stressed == 1
        assert result.option_leg_fills == 1
        # cash 10,001.35 − mark 23.00 (math in module docstring)
        assert result.equity[-1] == pytest.approx(9_978.35, abs=0.005)
        skips = [t.reason for t in result.trades if t.action == "SKIP"]
        assert "illiquid_spread" not in skips  # stress mode fills instead


class TestOiPenalizedFill:
    CHAINS = {
        "2025-01-06": [put(100.0, 2.00, 2.20, -0.30, "2025-01-17",
                           volume=200, open_interest=50)],
    }
    UNDERLYING = {"2025-01-06": (100.0, 100.0)}

    def test_thin_known_oi_scales_the_slip(self) -> None:
        spec = StrategySpec.model_validate(make_spec(
            position={
                "structure": "short_put",
                "legs": [
                    {"right": "put", "side": "short", "ratio": 1,
                     "strike_selection": {"method": "delta", "value": 0.30}},
                ],
                "expiration_selection": {"target_dte": 11, "min_dte": 1, "max_dte": 30},
            },
            exit={"time_exit_dte": 0},
            backtest={"start": "2025-01-06", "end": "2025-01-06",
                      "initial_capital": 10_000, "seed": 42},
        ))
        store = build_fixture_store("SPY", self.CHAINS, self.UNDERLYING)
        result = run_backtest(spec, store)

        assert result.filled == 1
        assert result.fills_penalized == 1
        assert result.fills_stressed == 0
        assert result.fills_unknown_liquidity == 0
        assert result.fill_spread_pcts[0] == pytest.approx(0.2 / 2.1)
        # equity math in module docstring: 10,201.85 − 217.50
        assert result.equity[-1] == pytest.approx(9_984.35, abs=0.005)


class TestLiquidityProfile:
    def test_skip_mode_counts_refused_entries(self) -> None:
        # 5 sessions of the fantasy quote → 5 gate skips → material
        days = [f"2025-01-{d:02d}" for d in (6, 7, 8, 9, 10)]
        chains = {d: fx_skip_illiquid.CHAINS[fx_skip_illiquid.DAY1] for d in days}
        underlying = {d: (100.0, 100.0) for d in days}
        spec_json = copy.deepcopy(fx_skip_illiquid.SPEC)
        spec_json["backtest"]["end"] = days[-1]
        spec = StrategySpec.model_validate(spec_json)
        result = run_backtest(spec, build_fixture_store("SPY", chains, underlying))

        profile = liquidity_profile(result, spec)
        assert profile.skipped_illiquid == 5
        assert profile.option_leg_fills == 0
        assert profile.median_spread_pct is None
        assert profile.material
        assert profile.note is not None and "refused" in profile.note

    def test_stress_share_and_median_spread(self) -> None:
        store = build_fixture_store(
            "SPY", fx_skip_illiquid.CHAINS, fx_skip_illiquid.UNDERLYING
        )
        spec = _stress_spec()
        result = run_backtest(spec, store)
        profile = liquidity_profile(result, spec)

        assert profile.mode == "stress"
        assert profile.stressed_share == pytest.approx(1.0)
        assert profile.unknown_liquidity_share == pytest.approx(1.0)  # fixture has no OI
        assert profile.median_spread_pct == pytest.approx(1.75)
        assert profile.material
        assert profile.note is not None and "full adverse" in profile.note

    def test_clean_liquid_run_is_not_material(self) -> None:
        chains = {"2025-01-06": [put(100.0, 2.00, 2.20, -0.30, "2025-01-17",
                                     volume=500, open_interest=4000)]}
        underlying = {"2025-01-06": (100.0, 100.0)}
        spec = StrategySpec.model_validate(make_spec(
            position={
                "structure": "short_put",
                "legs": [
                    {"right": "put", "side": "short", "ratio": 1,
                     "strike_selection": {"method": "delta", "value": 0.30}},
                ],
                "expiration_selection": {"target_dte": 11, "min_dte": 1, "max_dte": 30},
            },
            exit={"time_exit_dte": 0},
            backtest={"start": "2025-01-06", "end": "2025-01-06",
                      "initial_capital": 10_000, "seed": 42},
        ))
        result = run_backtest(spec, build_fixture_store("SPY", chains, underlying))
        profile = liquidity_profile(result, spec)

        assert not profile.material
        assert profile.note is None
        assert profile.penalized_share == pytest.approx(0.0)
        assert profile.unknown_liquidity_share == pytest.approx(0.0)


class TestVerdictCaveat:
    def test_material_liquidity_reaches_the_verdict_grounded(self) -> None:
        from app.honesty.gauntlet import run_gauntlet
        from app.honesty.verdict import allowed_numbers, template_verdict, validate_numbers

        days = [f"2025-01-{d:02d}" for d in (6, 7, 8, 9, 10)]
        chains = {d: fx_skip_illiquid.CHAINS[fx_skip_illiquid.DAY1] for d in days}
        underlying = {d: (100.0, 100.0) for d in days}
        spec_json = copy.deepcopy(fx_skip_illiquid.SPEC)
        spec_json["backtest"]["end"] = days[-1]
        spec = StrategySpec.model_validate(spec_json)
        store = build_fixture_store("SPY", chains, underlying)
        result = run_backtest(spec, store)

        report = run_gauntlet(spec, store, result, trials=1)
        assert report.liquidity is not None and report.liquidity.material

        verdict = template_verdict(report)
        assert any("Liquidity:" in c for c in verdict.caveats)
        joined = " ".join([verdict.headline, *verdict.evidence,
                           *verdict.breaks_where, *verdict.caveats])
        assert validate_numbers(joined, allowed_numbers(report)) == []
