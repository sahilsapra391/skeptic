"""Variant representability: can this stored spec be reopened on the dials?

The classifier is SERVER-SIDE and authoritative (V-22). A doctored client that
claims a locked field is unchanged is rejected against this, not against
whatever the browser decided.

THE THREE TIERS (V-20)
    (a) fully representable   every dial editable, nothing special
    (b) partially             one rule the dials cannot express, so THAT dial
                              renders read-only with the real rule in words;
                              every other dial stays editable and the run goes
    (c) unrepresentable       pass-through plus an editable remainder cannot
                              produce a coherent run. The ONLY tier that blocks

Only tier (c) blocks (V-21). Locking one dial and naming why is the honest
version; refusing the whole copy is the tool declining work it can do, on
exactly the specs a serious user cares about most.

WHAT IS *NOT* TIER (b)
    A non-standard tenor band (V-121). V-77's ownership table governs: the DTE
    dial owns `target_dte` AND the derived band, so a band that moves when the
    dial moves is documented ownership rather than a silent rewrite.

    A custom spread width. Since V-17, `positionLegs` carries `width_from_leg`
    through from the base whether or not the strike dial moves, so a $10 wing
    survives an edit. It is preserved, not merely untouched, and
    `test_custom_width_survives_a_strike_edit` proves it rather than asserting
    it. Before V-17 this WOULD have been tier (b).

Measured against production 2026-08-16 (99 stored runs): tier (a) 98,
tier (b) 1, tier (c) 0. The one tier (b) run is a7dac59b3f96, whose lead
strike is `atm`.
"""

from __future__ import annotations

from typing import Any

# the seven structures the dials can build, and the leg count each implies.
# Mirrors `legs()` in frontend/lib/spec.ts — a spec whose shape disagrees
# cannot be rebuilt from the dials at all.
STRUCTURE_LEGS: dict[str, int] = {
    "short_put": 1,
    "put_credit_spread": 2,
    "call_credit_spread": 2,
    "iron_condor": 4,
    "covered_call": 1,
    "long_call": 1,
    "long_put": 1,
}

# dial ids, matching the tiles on the spec screen. `ticker` and `structure` are
# locked on EVERY variant (V-06); the rest are locked only by tier (b).
LOCK_ALWAYS: tuple[str, ...] = ("ticker", "structure")


class Representability:
    """The classifier's verdict: a tier, the dials to lock, and why.

    `reasons` is keyed by dial so the UI can state the real rule beside the
    control it disabled, rather than in a banner the reader has to match back
    to a field themselves.
    """

    __slots__ = ("tier", "locked", "reasons")

    def __init__(
        self, tier: str, locked: list[str], reasons: dict[str, str]
    ) -> None:
        self.tier = tier
        self.locked = locked
        self.reasons = reasons

    def as_dict(self) -> dict[str, Any]:
        return {"tier": self.tier, "locked": self.locked, "reasons": self.reasons}

    @property
    def blocks(self) -> bool:
        return self.tier == "c"


def _strike_rule_in_words(sel: dict[str, Any]) -> str:
    """The real rule, in the register the read-only dial will print it."""
    method = sel.get("method")
    value = sel.get("value")
    if method == "offset_pct" and isinstance(value, int | float):
        pct = value * 100
        side = "below" if pct < 0 else "above"
        return f"{abs(pct):g}% {side} spot by offset"
    if method == "atm":
        return "at the money"
    return f"{method}" if method else "an unrecognised rule"


def classify(spec: dict[str, Any]) -> Representability:
    """Tier a stored spec. Pure, so both the read endpoint and the submit-time
    check reason from ONE implementation (V-22)."""
    position = spec.get("position") or {}
    structure = position.get("structure")
    legs = position.get("legs") or []

    # --- tier (c): the dials cannot build this shape at all -----------------
    if structure not in STRUCTURE_LEGS:
        return Representability(
            "c",
            [],
            {
                "structure": f"{structure!r} is not one of the seven structures "
                "the dials can build"
            },
        )
    expected = STRUCTURE_LEGS[structure]
    if len(legs) != expected:
        return Representability(
            "c",
            [],
            {
                "structure": f"{structure} implies {expected} legs, this spec "
                f"has {len(legs)}"
            },
        )
    odd = [i for i, leg in enumerate(legs) if (leg.get("ratio") or 1) != 1]
    if odd:
        return Representability(
            "c",
            [],
            {"structure": f"legs {odd} carry a ratio other than 1, which the "
             "dials cannot express"},
        )

    # --- tier (b): buildable, but one dial would lie about a rule -----------
    locked = list(LOCK_ALWAYS)
    reasons: dict[str, str] = {}
    lead_sel = (legs[0].get("strike_selection") or {}) if legs else {}
    if lead_sel.get("method") != "delta":
        # the dial can only say ".30Δ". Showing that for an ATM or offset spec
        # invites an edit the user reads as small and the engine reads as a
        # change of category.
        locked.append("strike")
        reasons["strike"] = _strike_rule_in_words(lead_sel)

    return Representability("b" if reasons else "a", locked, reasons)


def locked_field_paths(rep: Representability) -> list[str]:
    """Spec paths the submitted variant must match its parent on (V-22).

    Dial ids are a UI concept; the submit check compares spec JSON, so the
    lock is expressed in the same terms the comparison uses.
    """
    paths: list[str] = []
    for dial in rep.locked:
        if dial == "ticker":
            paths.append("underlying.ticker")
        elif dial == "structure":
            paths.append("position.structure")
        elif dial == "strike":
            paths.append("position.legs")
    return paths
