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


def _effective_window(stats: dict[str, Any] | None) -> dict[str, str] | None:
    """What the parent actually tested, shown as context in every window state.
    Lives in the honesty report, not the spec — the spec records what was
    REQUESTED, coverage decides what was reached."""
    report = ((stats or {}).get("honesty_report") or {})
    start, end = report.get("effective_start"), report.get("effective_end")
    return {"start": start, "end": end} if start and end else None


def window_state(
    parent_run_id: str,
    stored_draft: dict[str, Any] | None,
    spec: dict[str, Any],
    stats: dict[str, Any] | None,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """V-133: which of the three window cases this variant is in, and the
    window to prefill. Returns (window, variantWindow).

    A run with a stored draft recorded the KIND the user picked, including
    "all", so that choice carries forward intact. A run without one leaves
    `backtest.start` NULL for two different reasons that the record cannot
    tell apart — the user chose "all", or the run predates the window
    directive — so it goes to the unset state rather than guessing (V-50).
    """
    effective = _effective_window(stats)
    meta: dict[str, Any] = {"parentRunId": parent_run_id, "parentEffective": effective}

    stored = (stored_draft or {}).get("window") if stored_draft else None
    if isinstance(stored, dict) and stored.get("kind"):
        kind = stored["kind"]
        # V-51: an inherited "all" resolves against CURRENT coverage, so it may
        # legitimately reach further back than the parent did. Correct, not drift.
        meta["state"] = "carried_all" if kind == "all" else "carried"
        return dict(stored), meta

    # V-39: no stored draft, but the spec names explicit dates — that IS the
    # requested window, so carry it as a custom range.
    backtest = spec.get("backtest") or {}
    if backtest.get("start"):
        meta["state"] = "carried"
        return (
            {"kind": "custom", "start": backtest["start"], "end": backtest.get("end")},
            meta,
        )

    # V-50: leave it unset with the parent's effective window as context, and
    # keep the run locked until the user picks. V-132: this is a routine first
    # screen, not a degraded one — a third of stored runs land here.
    meta["state"] = "unset"
    return None, meta


def build_variant_draft(
    parent_run_id: str,
    spec: dict[str, Any],
    provenance: dict[str, Any] | None,
    stats: dict[str, Any] | None,
    parent_label: str | None = None,
) -> dict[str, Any]:
    """Project a stored spec onto the dials, enriched with everything
    `spec_to_draft` drops: costs, seed, and the window with its state.

    `spec_to_draft` is imported from the parser and NEVER edited — the parser
    tree is frozen for this phase.
    """
    from app.parser.parse import spec_to_draft

    prov = provenance or {}
    confirmed = prov.get("confirmed") or {}
    stored_draft = confirmed.get("draft") if isinstance(confirmed.get("draft"), dict) else None

    # V-28: the prompt comes from the stored record when there is one, and is
    # DERIVED from description_raw when there is not. Never invented.
    prompt_text = ((prov.get("prompt") or {}).get("text")) or (
        (spec.get("meta") or {}).get("description_raw") or ""
    )
    draft = spec_to_draft(spec, prompt_text)

    # V-34 / V-35: costs and seed inherit from the PARENT's confirmed spec,
    # never from the copier's current Settings.
    if spec.get("costs"):
        draft["costs"] = dict(spec["costs"])
    seed = (spec.get("backtest") or {}).get("seed")
    if seed is not None:
        draft["seed"] = seed

    window, variant_window = window_state(parent_run_id, stored_draft, spec, stats)
    variant_window["parentLabel"] = parent_label
    draft["window"] = window
    draft["variantWindow"] = variant_window
    # V-154: the composer's "Here's what I heard" is FALSE on this path — the
    # quoted prompt is the parent's, not something this user said. The screen
    # needs to know it is on the variant path to say so.
    draft["variantOf"] = {"runId": parent_run_id, "label": parent_label}
    return draft


# --- the ONE comparison (V-162 / V-163 / V-164) ------------------------------


def canonical_spec(spec: dict[str, Any]) -> dict[str, Any]:
    """THE canonicalizer — shared with the V-18 round-trip guard, which imports
    this function rather than keeping its own (V-163: two canonicalizers is the
    two-code-paths-one-comparison structure that produced four defects in the
    audit script's date handling).

    Normalizes through the SAME pydantic model the engine validates against:
    null and absent collapse together (exclude_none), types settle (ints vs
    floats), and vocabulary the model normalizes (an `atm` strike becoming
    delta 0.5) normalizes identically on both sides of any comparison.
    """
    from app.models.spec import StrategySpec

    return StrategySpec.model_validate(spec).model_dump(
        mode="json", exclude_none=True
    )


def canonical_json(spec: dict[str, Any]) -> str:
    """Byte-comparable form: sorted keys, compact separators."""
    import json

    return json.dumps(canonical_spec(spec), sort_keys=True, separators=(",", ":"))


def diff_specs(
    parent: dict[str, Any], variant: dict[str, Any]
) -> list[dict[str, Any]]:
    """The field-level diff between a parent's stored spec and a submitted
    variant. ONE function, one output, consumed by every reader (V-162):

        the V-22 lock check          — prefix-matches `field` against lockedPaths
        the V-10/V-19 zero-edit guard — empty list = same run, block pre-debit
        provenance section 5          — rendered as the what-changed record
        A2's Q&A reconciler           — maps question labels onto `field`

    Output rows are {"field", "parent", "variant"}, ordered by path.

    FIELD PATHS ARE A CONTRACT (V-164): dotted spec-schema paths with list
    indices in brackets — "backtest.start", "exit.profit_target_pct",
    "position.legs[0].strike_selection.value",
    "position.expiration_selection.target_dte". A2's label table maps question
    labels onto exactly these strings, so renaming one breaks reconciliation
    silently; test_the_path_vocabulary_is_pinned fails first.

    A key present on one side only diffs against None (canonicalization has
    already collapsed null-vs-absent, so a surviving absence is a real
    vocabulary difference, e.g. a ladder the variant dropped). Lists of equal
    length diff index-wise; a length change is ONE row at the list's own path
    carrying both lists whole.
    """
    rows: list[dict[str, Any]] = []
    _walk(canonical_spec(parent), canonical_spec(variant), "", rows)
    rows.sort(key=lambda r: r["field"])
    return rows


def _walk(a: Any, b: Any, path: str, rows: list[dict[str, Any]]) -> None:
    if isinstance(a, dict) and isinstance(b, dict):
        for key in sorted(set(a) | set(b)):
            child = f"{path}.{key}" if path else key
            _walk(a.get(key), b.get(key), child, rows)
        return
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            rows.append({"field": path, "parent": a, "variant": b})
            return
        for i, (x, y) in enumerate(zip(a, b, strict=True)):
            _walk(x, y, f"{path}[{i}]", rows)
        return
    if a != b:
        rows.append({"field": path, "parent": a, "variant": b})


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
