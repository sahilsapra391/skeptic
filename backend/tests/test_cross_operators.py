"""crosses_above/crosses_below need series context (yesterday's value).

Regression for the crosses-on-scalar 500: {indicator: vix_level, operator:
crosses_above, value: 20} — the natural "when VIX crosses above 20" — used
to VALIDATE and then crash the run at its first evaluated session (the
engine's _compare raises on crosses without series context). The Condition
model now refuses the pair, so every ingress — the parser retry loop,
POST /api/backtest, stored-spec re-validation, ladder rungs and the rearm —
gets an actionable 422 instead of a mid-run 500. Guardrail #3: the parser
asks whether the level form is meant; it never substitutes it.
"""

from datetime import date, timedelta

import pytest
from pydantic import ValidationError

from app.engine.conditions import evaluate_condition
from app.engine.market import MarketView, build_fixture_store
from app.models.spec import (
    CROSS_CAPABLE_INDICATORS,
    Condition,
    Indicator,
    Operator,
    Rung,
    StrategySpec,
    Timeframe,
)

CROSS_OPS = (Operator.CROSSES_ABOVE, Operator.CROSSES_BELOW)
SCALAR_READS = sorted(i for i in Indicator if i not in CROSS_CAPABLE_INDICATORS)


def _tf(indicator: Indicator) -> Timeframe:
    # price_vs_vwap_pct is intraday-only; every other scalar read is daily
    return (
        Timeframe.FIVE_MIN
        if indicator is Indicator.PRICE_VS_VWAP_PCT
        else Timeframe.DAILY
    )


class TestValidation:
    @pytest.mark.parametrize("op", CROSS_OPS)
    @pytest.mark.parametrize("indicator", SCALAR_READS)
    def test_scalar_reads_refuse_crosses(
        self, indicator: Indicator, op: Operator
    ) -> None:
        with pytest.raises(ValidationError, match="point-in-time"):
            Condition(indicator=indicator, operator=op, value=20,
                      timeframe=_tf(indicator))

    @pytest.mark.parametrize("indicator", sorted(CROSS_CAPABLE_INDICATORS))
    def test_series_indicators_keep_crosses(self, indicator: Indicator) -> None:
        cond = Condition(indicator=indicator, operator=Operator.CROSSES_ABOVE,
                         value=30)
        assert cond.operator is Operator.CROSSES_ABOVE

    def test_ladder_rung_refuses_crosses_on_scalar_read(self) -> None:
        # Rung subclasses Condition — the ladder inherits the refusal
        with pytest.raises(ValidationError, match="point-in-time"):
            Rung(indicator=Indicator.VIX_LEVEL, operator=Operator.CROSSES_ABOVE,
                 value=20, add_contracts=1)

    def test_parsed_spec_shape_refuses_at_validation(self) -> None:
        """The exact shape the parser used to emit for 'when VIX crosses
        above 20' — must 422 at validation, never reach the engine."""
        raw = {
            "spec_version": 1,
            "meta": {"name": "VIX cross regression",
                     "description_raw": "when VIX crosses above 20"},
            "underlying": {"ticker": "SPY"},
            "position": {"structure": "short_put",
                "legs": [{"right": "put", "side": "short", "ratio": 1,
                          "strike_selection": {"method": "delta", "value": 0.30}}],
                "expiration_selection": {"target_dte": 45, "min_dte": 35,
                                         "max_dte": 60}},
            "entry": {"schedule": {"frequency": "signal_only"},
                      "conditions": [{"indicator": "vix_level",
                                      "operator": "crosses_above", "value": 20}],
                      "max_concurrent_positions": 1},
            "exit": {"profit_target_pct": 50},
            "sizing": {"method": "fixed_contracts", "value": 1},
            "costs": {"commission_per_contract": 0.65,
                      "slippage_half_spread_fraction": 0.85,
                      "slippage_half_spread_fraction_sell": 0.90},
            "backtest": {"start": None, "end": None, "initial_capital": 25_000,
                         "seed": 42},
        }
        with pytest.raises(ValidationError, match="point-in-time"):
            StrategySpec.model_validate(raw)


def _view(with_vix: bool = False) -> MarketView:
    d0 = date(2026, 1, 5)
    n = 60
    days = [(d0 + timedelta(days=i)).isoformat() for i in range(n)]
    closes = [100.0 + (i % 7) - 3.0 for i in range(n)]  # wiggles → crosses exist
    store = build_fixture_store(
        "SPY",
        chains={},
        underlying={d: (c, c) for d, c in zip(days, closes, strict=True)},
        vix={d: 21.0 for d in days} if with_vix else None,
    )
    return MarketView(store, store.sessions[-1])


class TestEngineLockstep:
    def test_cross_capable_set_matches_engine(self) -> None:
        """Every CROSS_CAPABLE indicator must actually evaluate a crosses
        condition (a bool, no raise) — the validation set and the engine's
        _series_pair routing stay in lockstep as vocabulary grows."""
        view = _view()
        for indicator in sorted(CROSS_CAPABLE_INDICATORS):
            cond = Condition(indicator=indicator,
                             operator=Operator.CROSSES_ABOVE, value=1.0)
            assert isinstance(evaluate_condition(view, cond), bool)

    def test_engine_backstop_still_refuses_unvalidated_crosses(self) -> None:
        """Defense in depth: a crosses-on-scalar condition built AROUND
        validation is still refused loudly by the engine — never a
        fabricated signal."""
        view = _view(with_vix=True)
        cond = Condition.model_construct(
            indicator=Indicator.VIX_LEVEL, operator=Operator.CROSSES_ABOVE,
            value=20.0, period=None, params=None, timeframe=Timeframe.DAILY)
        with pytest.raises(ValueError, match="series context"):
            evaluate_condition(view, cond)
