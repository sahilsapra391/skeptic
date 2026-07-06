"""Spec v2 validation rules (D1c + owner amendments 1 & 4).

The version is load-bearing: v1 specs stay valid forever, v2 vocabulary on
a v1 spec is a loud error, and theta_harvest refuses structures whose max
profit is undefined — validation is where "never silent" lives.
"""

from __future__ import annotations

import copy

import pytest
from pydantic import ValidationError

from app.models.spec import StrategySpec
from tests.fixtures.engine.common import make_spec


def _v1_short_put(**overrides: object) -> dict:
    return make_spec(
        position={
            "structure": "short_put",
            "legs": [{"right": "put", "side": "short", "ratio": 1,
                      "strike_selection": {"method": "delta", "value": 0.30}}],
            "expiration_selection": {"target_dte": 45, "min_dte": 30, "max_dte": 60},
        },
        exit={"profit_target_pct": 50},
        **overrides,
    )


def test_v1_specs_stay_valid() -> None:
    spec = StrategySpec.model_validate(_v1_short_put())
    assert spec.spec_version == 1


def test_v2_without_new_vocabulary_is_fine() -> None:
    spec = StrategySpec.model_validate(_v1_short_put(spec_version=2))
    assert spec.spec_version == 2


def test_unsupported_version_rejected() -> None:
    # v3 is valid as of D5a; 4 is still out of range
    with pytest.raises(ValidationError, match="spec_version must be 1, 2, or 3"):
        StrategySpec.model_validate(_v1_short_put(spec_version=4))


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (lambda s: s["exit"].update({"delta_stop_abs": 0.6}), "exit.delta_stop_abs"),
        (
            lambda s: s["exit"].update(
                {"theta_harvest": {"dte_from": 21, "dte_to": 7, "profit_pct": 50}}
            ),
            "exit.theta_harvest",
        ),
        (
            lambda s: s["position"].update({"max_vega_per_contract": 30}),
            "position.max_vega_per_contract",
        ),
        (
            lambda s: s["entry"].update(
                {"conditions": [{"indicator": "ivx_rank_1y", "operator": ">", "value": 50}]}
            ),
            "ivx_rank_1y",
        ),
    ],
)
def test_v2_vocabulary_on_v1_is_a_versioning_error(mutate, expected) -> None:
    raw = _v1_short_put()
    mutate(raw)
    with pytest.raises(ValidationError, match="spec_version 2") as exc:
        StrategySpec.model_validate(raw)
    assert expected in str(exc.value)
    # the same spec at version 2 validates
    raw2 = copy.deepcopy(raw)
    raw2["spec_version"] = 2
    StrategySpec.model_validate(raw2)


def test_delta_stop_whole_number_normalizes() -> None:
    raw = _v1_short_put(spec_version=2)
    raw["exit"]["delta_stop_abs"] = 60
    spec = StrategySpec.model_validate(raw)
    assert spec.exit.delta_stop_abs == pytest.approx(0.60)


def test_delta_stop_alone_is_a_complete_exit() -> None:
    raw = _v1_short_put(spec_version=2)
    raw["exit"] = {"delta_stop_abs": 0.6}
    spec = StrategySpec.model_validate(raw)
    assert spec.exit.delta_stop_abs == pytest.approx(0.60)


def test_theta_harvest_window_must_run_downward() -> None:
    # owner amendment 4: dte_from > dte_to, always
    raw = _v1_short_put(spec_version=2)
    raw["exit"]["theta_harvest"] = {"dte_from": 7, "dte_to": 21, "profit_pct": 50}
    with pytest.raises(ValidationError, match="dte_from must be greater"):
        StrategySpec.model_validate(raw)
    raw["exit"]["theta_harvest"] = {"dte_from": 21, "dte_to": 21, "profit_pct": 50}
    with pytest.raises(ValidationError, match="dte_from must be greater"):
        StrategySpec.model_validate(raw)


@pytest.mark.parametrize("structure", ["long_call", "long_put"])
def test_theta_harvest_refused_on_undefined_max_profit(structure: str) -> None:
    # owner amendment 1: no defined max profit → no "profit % of max"
    raw = _v1_short_put(spec_version=2)
    raw["position"]["structure"] = structure
    raw["position"]["legs"] = [
        {"right": "call" if structure == "long_call" else "put", "side": "long", "ratio": 1,
         "strike_selection": {"method": "delta", "value": 0.50}},
    ]
    raw["exit"]["theta_harvest"] = {"dte_from": 21, "dte_to": 7, "profit_pct": 50}
    with pytest.raises(ValidationError, match="defined max profit"):
        StrategySpec.model_validate(raw)


@pytest.mark.parametrize(
    "structure",
    ["short_put", "put_credit_spread", "call_credit_spread", "iron_condor", "covered_call"],
)
def test_theta_harvest_allowed_on_defined_max_profit(structure: str) -> None:
    raw = _v1_short_put(spec_version=2)
    raw["position"]["structure"] = structure
    # legs stay the short put's — leg/structure consistency is the engine's
    # concern; this test isolates the theta_harvest × structure rule
    raw["exit"]["theta_harvest"] = {"dte_from": 21, "dte_to": 7, "profit_pct": 50}
    spec = StrategySpec.model_validate(raw)
    assert spec.exit.theta_harvest is not None
