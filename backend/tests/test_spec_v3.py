"""Spec v3 validation (D5a): the scale-in ladder + exit.close_at_time.

Same posture as v2 — v3 vocabulary on a v1/v2 spec is a loud error, and the
scale-in scope rules (single-leg only, fixed_contracts only, 5-min clock)
refuse rather than silently do the wrong thing (guardrail #3).
"""

from __future__ import annotations

import copy

import pytest
from pydantic import ValidationError

from app.models.spec import StrategySpec


def _ladder(**over: object) -> dict:
    spec: dict = {
        "spec_version": 3,
        "meta": {"name": "ladder", "description_raw": "rsi scale-in"},
        "underlying": {"ticker": "SPY"},
        "position": {
            "structure": "long_call",
            "legs": [{"right": "call", "side": "long", "ratio": 1,
                      "strike_selection": {"method": "atm", "value": 0}}],
            "expiration_selection": {"target_dte": 1, "min_dte": 0, "max_dte": 2},
        },
        "entry": {
            "schedule": {"frequency": "signal_only"}, "conditions": [],
            "max_concurrent_positions": 1,
            "scale_in": {
                "mode": "signal_ladder", "basket": True,
                "rungs": [
                    {"indicator": "rsi", "timeframe": "5min", "period": 14,
                     "operator": "<=", "value": 30, "add_contracts": 2},
                    {"indicator": "rsi", "timeframe": "5min", "period": 14,
                     "operator": "<=", "value": 20, "add_contracts": 5},
                ],
                "rearm": {"indicator": "rsi", "timeframe": "5min", "period": 14,
                          "operator": ">", "value": 30},
                "max_total_contracts": 20,
            },
        },
        "exit": {"profit_target_pct": 40, "stop_loss_pct": 75, "close_at_time": "15:45"},
        "sizing": {"method": "fixed_contracts", "value": 1},
        "costs": {"commission_per_contract": 0.65, "slippage_half_spread_fraction": 0.5},
        "backtest": {"start": None, "end": None, "initial_capital": 100_000,
                     "seed": 42, "clock": "5min"},
    }
    for k, v in over.items():
        spec[k] = v
    return spec


def test_full_ladder_validates() -> None:
    spec = StrategySpec.model_validate(_ladder())
    assert spec.spec_version == 3
    assert spec.entry.scale_in is not None
    assert len(spec.entry.scale_in.rungs) == 2
    assert spec.entry.scale_in.rungs[0].add_contracts == 2
    assert spec.entry.scale_in.max_total_contracts == 20
    # default stop_adding_on is the only implemented mode this phase
    assert spec.entry.scale_in.stop_adding_on.mode.value == "next_rung_not_reached"
    assert spec.exit.close_at_time == "15:45"


def test_scale_in_requires_v3() -> None:
    # on a v2 spec the only v3 flag is scale_in → the v3 message points at it
    raw = _ladder(spec_version=2)
    raw["exit"] = {"profit_target_pct": 40}  # drop close_at_time (also v3)
    with pytest.raises(ValidationError, match="entry.scale_in — set spec_version 3"):
        StrategySpec.model_validate(raw)


def test_scale_in_on_v1_is_rejected() -> None:
    # a v1 ladder is rejected regardless — its 5-min clock trips the v2 gate
    # first — but it never silently validates (guardrail #3)
    raw = _ladder(spec_version=1)
    raw["exit"] = {"profit_target_pct": 40}
    with pytest.raises(ValidationError):
        StrategySpec.model_validate(raw)


def test_close_at_time_requires_v3() -> None:
    raw = _ladder(spec_version=2)
    del raw["entry"]["scale_in"]
    with pytest.raises(ValidationError, match="exit.close_at_time — set spec_version 3"):
        StrategySpec.model_validate(raw)


def test_scale_in_rejects_multi_leg() -> None:
    raw = _ladder()
    raw["position"]["structure"] = "put_credit_spread"
    raw["position"]["legs"] = [
        {"right": "put", "side": "short", "ratio": 1,
         "strike_selection": {"method": "delta", "value": 0.30}},
        {"right": "put", "side": "long", "ratio": 1,
         "strike_selection": {"method": "width_from_leg", "value": 5, "reference_leg": 0}},
    ]
    with pytest.raises(ValidationError, match="multi-leg ladders are not yet supported"):
        StrategySpec.model_validate(raw)


def test_scale_in_requires_fixed_contracts() -> None:
    raw = _ladder()
    raw["sizing"] = {"method": "risk_pct_of_equity", "value": 2}
    with pytest.raises(ValidationError, match="must be fixed_contracts") as exc:
        StrategySpec.model_validate(raw)
    # the error explains WHY — the non-obvious constraint spelled out
    assert "absolute contract counts" in str(exc.value)


def test_cap_must_cover_largest_rung() -> None:
    raw = _ladder()
    raw["entry"]["scale_in"]["max_total_contracts"] = 3  # < the +5 rung
    with pytest.raises(ValidationError, match="largest rung add_contracts"):
        StrategySpec.model_validate(raw)


def test_close_at_time_needs_five_min_clock() -> None:
    raw = _ladder()
    # a daily-timeframe ladder could run daily, but close_at_time can't
    raw["backtest"]["clock"] = "daily"
    for rung in raw["entry"]["scale_in"]["rungs"]:
        rung["timeframe"] = "daily"
    raw["entry"]["scale_in"]["rearm"]["timeframe"] = "daily"
    with pytest.raises(ValidationError, match="require backtest.clock"):
        StrategySpec.model_validate(raw)


def test_basket_must_be_true() -> None:
    raw = _ladder()
    raw["entry"]["scale_in"]["basket"] = False
    with pytest.raises(ValidationError, match="basket must be true"):
        StrategySpec.model_validate(raw)


def test_v1_and_v2_specs_unchanged() -> None:
    # a plain v1 spec still validates and carries no scale_in
    raw = _ladder(spec_version=1)
    del raw["entry"]["scale_in"]
    raw["exit"] = {"profit_target_pct": 40}
    raw["backtest"]["clock"] = "daily"
    for section in (raw["position"]["expiration_selection"],):
        section.update({"target_dte": 30, "min_dte": 20, "max_dte": 45})
    spec = StrategySpec.model_validate(raw)
    assert spec.entry.scale_in is None
    assert spec.exit.close_at_time is None


def test_spec_v3_matches_json_schema() -> None:
    raw = copy.deepcopy(_ladder())
    # the schema and the pydantic models must agree the full ladder is valid
    import json
    from pathlib import Path

    schema_path = Path(__file__).resolve().parents[2] / "docs" / "strategy-spec.schema.json"
    schema = json.loads(schema_path.read_text())
    try:
        import jsonschema
    except ImportError:
        pytest.skip("jsonschema not installed")
    jsonschema.validate(raw, schema)
