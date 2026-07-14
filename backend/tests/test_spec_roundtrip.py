"""Spec IR contract tests (M0 acceptance): a valid spec round-trips, invalid
specs are rejected — including the guardrail-encoding rejections (mid fills,
empty exit)."""

import copy

import pytest
from pydantic import ValidationError

from app.models.spec import StrategySpec

# the canonical strategy: "Sell a 30 delta put on SPY every Monday,
# close at 50% profit or 21 DTE"
CANONICAL = {
    "spec_version": 1,
    "meta": {
        "name": "SPY 30-delta weekly short put",
        "description_raw": "Sell a 30 delta put on SPY every Monday, close at 50% profit or 21 DTE",
    },
    "underlying": {"ticker": "SPY"},
    "position": {
        "structure": "short_put",
        "legs": [
            {
                "right": "put",
                "side": "short",
                "ratio": 1,
                "strike_selection": {"method": "delta", "value": 0.30},
            }
        ],
        "expiration_selection": {"target_dte": 45, "min_dte": 35, "max_dte": 60},
    },
    "entry": {
        "schedule": {"frequency": "weekly", "day_of_week": "monday"},
        "conditions": [],
        "max_concurrent_positions": 5,
    },
    "exit": {"profit_target_pct": 50, "time_exit_dte": 21},
    "sizing": {"method": "fixed_contracts", "value": 1},
    "costs": {"commission_per_contract": 0.65, "slippage_half_spread_fraction": 0.5,
                                               "slippage_half_spread_fraction_sell": 0.5},
    "backtest": {"start": None, "end": None, "initial_capital": 25000, "seed": 42},
}


def test_valid_spec_round_trips() -> None:
    spec = StrategySpec.model_validate(CANONICAL)
    dumped = spec.model_dump(mode="json", exclude_none=True)
    # every field we put in comes back out unchanged
    assert dumped["meta"] == CANONICAL["meta"]
    assert dumped["underlying"]["ticker"] == "SPY"
    assert dumped["position"]["legs"][0]["strike_selection"]["value"] == 0.30
    assert dumped["exit"] == {"profit_target_pct": 50.0, "time_exit_dte": 21}
    assert dumped["backtest"]["initial_capital"] == 25000.0
    # and re-validates to an identical model
    assert StrategySpec.model_validate(dumped) == spec


def _mutated(path: list, value) -> dict:
    doc = copy.deepcopy(CANONICAL)
    node = doc
    for key in path[:-1]:
        node = node[key]
    if value is _DELETE:
        del node[path[-1]]
    else:
        node[path[-1]] = value
    return doc


_DELETE = object()


@pytest.mark.parametrize(
    ("path", "value", "why"),
    [
        (["costs", "slippage_half_spread_fraction"], 0, "mid fills are banned (guardrail #1)"),
        (["exit"], {}, "empty exit must ask, never default (guardrail #3)"),
        (["underlying", "ticker"], "TSLA", "v1 universe is SPY/QQQ/IWM only"),
        (["spec_version"], 9, "spec_version must be 1-8 (v8 added by parity Tier 3)"),
        (["position", "structure"], "wheel", "unknown structure"),
        (["position", "expiration_selection", "target_dte"], 0, "target_dte >= 1"),
        (["entry", "max_concurrent_positions"], 11, "max 10 concurrent"),
        (["sizing", "value"], 0, "sizing value must be > 0"),
        (["backtest", "initial_capital"], 500, "minimum capital 1000"),
        (["meta", "name"], "x" * 81, "name maxLength 80"),
        (["unexpected_root_key"], True, "additionalProperties: false at root"),
    ],
)
def test_invalid_specs_rejected(path: list, value, why: str) -> None:
    with pytest.raises(ValidationError):
        StrategySpec.model_validate(_mutated(path, value))


def test_width_from_leg_requires_reference() -> None:
    doc = copy.deepcopy(CANONICAL)
    doc["position"]["legs"].append(
        {
            "right": "put",
            "side": "long",
            "ratio": 1,
            "strike_selection": {"method": "width_from_leg", "value": 5},
        }
    )
    with pytest.raises(ValidationError, match="reference_leg"):
        StrategySpec.model_validate(doc)
    doc["position"]["legs"][1]["strike_selection"]["reference_leg"] = 0
    assert StrategySpec.model_validate(doc)


def test_too_many_legs_rejected() -> None:
    doc = copy.deepcopy(CANONICAL)
    doc["position"]["legs"] = doc["position"]["legs"] * 5
    with pytest.raises(ValidationError):
        StrategySpec.model_validate(doc)


def test_atm_normalizes_to_50_delta_at_the_model() -> None:
    """ATM IS the 50-delta strike. The rewrite lives on StrikeSelection so
    EVERY ingress — parser, POST /api/backtest, stored specs re-validated
    for a run — lands on the same editable .50Δ."""
    spec = copy.deepcopy(CANONICAL)
    spec["position"]["legs"][0]["strike_selection"] = {"method": "atm", "value": 0}
    model = StrategySpec.model_validate(spec)
    sel = model.position.legs[0].strike_selection
    assert sel.method.value == "delta"
    assert sel.value == 0.5


def test_width_from_leg_requires_positive_width() -> None:
    """Zero/negative widths are structural nonsense — 422 at the boundary,
    never reinterpreted per-entry inside the engine."""
    for bad in (0, -5):
        spec = copy.deepcopy(CANONICAL)
        spec["position"]["structure"] = "put_credit_spread"
        spec["position"]["legs"].append(
            {"right": "put", "side": "long", "ratio": 1,
             "strike_selection": {"method": "width_from_leg", "value": bad,
                                  "reference_leg": 0}}
        )
        with pytest.raises(ValidationError):
            StrategySpec.model_validate(spec)
