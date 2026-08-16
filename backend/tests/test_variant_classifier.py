"""V-20 / V-127: the three-tier representability classifier.

Tier (c) is built and tested to the same standard as (a) and (b) even though
production has none of it (V-127). Zero in 99 is a measurement of today's
stored runs, not a permanent property, and the safety property must not depend
on a screen existing.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from app.api.variant import STRUCTURE_LEGS, classify, locked_field_paths
from app.models.spec import StrategySpec

from .test_spec_roundtrip import CANONICAL


def _spec(**patch: Any) -> dict[str, Any]:
    spec = copy.deepcopy(CANONICAL)
    for path, value in patch.items():
        node = spec
        parts = path.split("__")
        for p in parts[:-1]:
            node = node[p]
        node[parts[-1]] = value
    return spec


def _validated(spec: dict[str, Any]) -> dict[str, Any]:
    return StrategySpec.model_validate(spec).model_dump(mode="json", exclude_none=True)


# --- tier (a): the overwhelming majority ------------------------------------


def test_canonical_spec_is_fully_representable() -> None:
    rep = classify(_validated(CANONICAL))
    assert rep.tier == "a"
    assert rep.reasons == {}
    assert not rep.blocks


def test_ticker_and_structure_are_locked_on_every_variant() -> None:
    """V-06: locked regardless of tier. Changing either is a New Analysis."""
    rep = classify(_validated(CANONICAL))
    assert set(rep.locked) == {"ticker", "structure"}
    assert set(locked_field_paths(rep)) == {"underlying.ticker", "position.structure"}


def test_a_non_standard_tenor_band_is_tier_a() -> None:
    """V-121. The DTE dial owns target_dte AND the derived band (V-77), so a
    band that moves when the dial moves is documented ownership, not a rule the
    dials cannot express. This reclassification is what took tier (b) from 17
    production runs to 1."""
    spec = _validated(
        _spec(position__expiration_selection={"target_dte": 45, "min_dte": 30, "max_dte": 90})
    )
    rep = classify(spec)
    assert rep.tier == "a"
    assert "dte" not in rep.locked


def test_a_custom_spread_width_is_tier_a_since_v17() -> None:
    """Before V-17 this was tier (b): the rebuild reset every wing to $5. Now
    `positionLegs` carries width_from_leg through whether or not the strike
    dial moves, so the width is PRESERVED rather than merely untouched.
    `test_custom_width_survives_a_strike_edit` in the V-18 guard proves the
    preservation; this pins the classification that follows from it."""
    spec = _validated(
        _spec(
            position__structure="put_credit_spread",
            position__legs=[
                {"right": "put", "side": "short", "ratio": 1,
                 "strike_selection": {"method": "delta", "value": 0.30}},
                {"right": "put", "side": "long", "ratio": 1,
                 "strike_selection": {"method": "width_from_leg", "value": 10,
                                      "reference_leg": 0}},
            ],
        )
    )
    rep = classify(spec)
    assert rep.tier == "a", "a $10 wing survives a dial edit, so nothing is inexpressible"
    assert "strike" not in rep.locked


# --- tier (b): one dial locked, the run still goes ---------------------------


def test_non_delta_lead_strike_locks_only_that_dial() -> None:
    """V-21: do NOT block tier (b). Lock the one dial, name the real rule, and
    leave everything else editable."""
    spec = _validated(
        _spec(
            position__legs=[
                {"right": "put", "side": "short", "ratio": 1,
                 "strike_selection": {"method": "offset_pct", "value": -0.02}}
            ]
        )
    )
    rep = classify(spec)
    assert rep.tier == "b"
    assert not rep.blocks, "tier (b) must never block"
    assert "strike" in rep.locked
    assert rep.reasons["strike"] == "2% below spot by offset"
    # every other dial stays editable
    assert "dte" not in rep.locked and "exit" not in rep.locked


def test_the_production_tier_b_case_reads_in_plain_words() -> None:
    """The one tier (b) run in production, a7dac59b3f96, is a long_call whose
    lead strike is `atm`. The dial can only say ".30Δ", which would invite an
    edit the user reads as small and the engine reads as a change of category."""
    spec = _spec(
        position__structure="long_call",
        position__legs=[
            {"right": "call", "side": "long", "ratio": 1,
             "strike_selection": {"method": "atm"}}
        ],
    )
    # `atm` normalizes to delta 0.5 in the model, so classify the RAW stored
    # shape — which is what the endpoint reads out of spec_json.
    rep = classify(spec)
    assert rep.tier == "b"
    assert rep.reasons["strike"] == "at the money"
    assert set(locked_field_paths(rep)) == {
        "underlying.ticker", "position.structure", "position.legs",
    }


# --- tier (c): the only tier that blocks -------------------------------------


def test_structure_outside_the_seven_blocks() -> None:
    rep = classify(_spec(position__structure="calendar_spread"))
    assert rep.tier == "c"
    assert rep.blocks
    assert "calendar_spread" in rep.reasons["structure"]


def test_leg_count_disagreeing_with_the_structure_blocks() -> None:
    """A short_put carrying two legs cannot be rebuilt from the dials, whatever
    the dials are set to."""
    spec = _spec(
        position__legs=[
            {"right": "put", "side": "short", "ratio": 1,
             "strike_selection": {"method": "delta", "value": 0.30}},
            {"right": "put", "side": "long", "ratio": 1,
             "strike_selection": {"method": "delta", "value": 0.10}},
        ]
    )
    rep = classify(spec)
    assert rep.tier == "c"
    assert "implies 1 legs" in rep.reasons["structure"]


def test_non_unit_leg_ratio_blocks() -> None:
    """A ratio spread is exactly the 'exotic leg ratio' V-20 names."""
    spec = _spec(
        position__legs=[
            {"right": "put", "side": "short", "ratio": 2,
             "strike_selection": {"method": "delta", "value": 0.30}}
        ]
    )
    rep = classify(spec)
    assert rep.tier == "c"
    assert "ratio other than 1" in rep.reasons["structure"]


@pytest.mark.parametrize("structure", sorted(STRUCTURE_LEGS))
def test_every_supported_structure_classifies_without_blocking(structure: str) -> None:
    """The seven are representable by construction. If one ever is not, that is
    a dial regression rather than an exotic spec."""
    legs = [
        {"right": "put", "side": "short", "ratio": 1,
         "strike_selection": {"method": "delta", "value": 0.30}}
        for _ in range(STRUCTURE_LEGS[structure])
    ]
    rep = classify(_spec(position__structure=structure, position__legs=legs))
    assert not rep.blocks, f"{structure} should not block"
