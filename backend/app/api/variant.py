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

from collections import Counter
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


# Explicit, closed vocabulary. Not fuzzy matching: every entry is an exact
# string that maps to exactly one canonical token. Parser options for boolean
# spec fields are worded this way ("Yes"/"No"), and without this an answer of
# "no" could never match a stored `false`.
_BOOLEAN_WORDS = {
    "yes": "true", "y": "true", "true": "true", "on": "true", "enabled": "true",
    "no": "false", "n": "false", "false": "false", "off": "false",
    "disabled": "false", "none": "false",
}

# Units that qualify a number without rescaling it. `%` is here deliberately:
# the schema stores whole percents (`exit.profit_target_pct: 50` means 50%),
# so "50%" and "50" are the same value and dividing by 100 would invent one.
_TRAILING_UNITS = ("%", "dte", "dtes", "days", "day", "d", "contracts", "contract", "x")


def canonical_token(value: object) -> str | None:
    """THE normalization for value matching (V-202), applied to BOTH sides.

    One function, not two, for the reason V-163 gives: a recorded answer and a
    stored spec value are compared, so they must be normalized by the same code
    or the comparison is between two different spaces. The answer string
    "50%" and the spec value `50.0` both land on "50" here, and equality after
    that is exact. There is no fuzzy step, no nearest match, no threshold.

    Returns None when the input cannot be canonicalized, which the reconciler
    treats as a safe miss and counts (V-204). None never equals None: callers
    must not compare two Nones and call it a match.
    """
    if value is None or isinstance(value, (list, dict)):
        return None
    if isinstance(value, bool):  # before the numeric branch; bool is an int
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return _number_token(float(value))

    text = " ".join(str(value).split()).strip().casefold()
    if not text:
        return None
    if text in _BOOLEAN_WORDS:
        return _BOOLEAN_WORDS[text]

    number = _extract_number(text)
    if number is not None:
        return _number_token(number)
    return text


def _extract_number(text: str) -> float | None:
    """A number, optionally wearing a currency symbol, thousands separators, or
    one trailing unit. Anything else is not a number, deliberately: "between 30
    and 45" must not silently become 30."""
    cleaned = text.replace(",", "").replace("$", "").strip()
    for unit in _TRAILING_UNITS:
        if cleaned.endswith(unit):
            cleaned = cleaned[: -len(unit)].strip()
            break
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _pair_conversation(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Mirrors `pairConversation` in frontend/components/results/how-built.tsx
    exactly: an answer attaches to the first OPEN question sharing its id, and
    otherwise stands alone.

    Mirrored rather than reinvented, and the labels this module emits are
    anchored on the ANSWER EVENT'S INDEX rather than on a position in this
    list, so the two implementations never have to agree for a label to land on
    the right card. If they ever diverge, the pairing changes and the anchor
    does not.
    """
    out: list[dict[str, Any]] = []
    for index, event in enumerate(events):
        if not isinstance(event, dict):
            continue
        if event.get("kind") == "question":
            out.append({"question": event})
            continue
        if event.get("kind") != "answer":
            continue
        open_pair = next(
            (
                x
                for x in out
                if x.get("question", {}).get("id") == event.get("id") and "answer" not in x
            ),
            None,
        )
        if open_pair is not None:
            open_pair["answer"] = event
            open_pair["answer_index"] = index
        else:
            out.append({"answer": event, "answer_index": index})
    return out


def reconcile(
    conversation: list[dict[str, Any]] | None,
    diff_rows: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """V-200: which carried exchanges did this variant's edits supersede.

    An exchange is SUPERSEDED when its recorded ANSWER, canonicalized, equals
    the `parent` value of a row the user actually changed. Both sides go through
    `canonical_token` (V-202), equality is exact after that, and there is no
    fuzzy or nearest matching. Substring matching in particular is forbidden and
    fenced by a test: measured against production, 14 unanchored answers
    produced 80+ substring hits between them, because a prose answer containing
    any digit matches every numeric field sharing that digit.

    Diff-anchored, so only CHANGED rows are candidates. An answer equal to a
    value that stayed put is history, not a supersession.

    V-201, unique in BOTH directions. An answer matching two changed rows cannot
    name which one it settled; two answers matching one row cannot say which
    exchange the edit displaced. Either way nothing is marked and the
    suppression is counted. Never guess, applied to values.

    Emits ONLY superseded entries. Absence of an entry means STILL HOLDS, which
    makes the safe state structural: a dropped field, an unhandled shape or a
    serialization bug degrades to the brief's own fallback instead of to a false
    claim on the provenance screen. NOT APPLICABLE is deliberately absent
    (V-203): a locked field has no diff row, so finding one would mean scanning
    unchanged values, which breaks diff-anchoring for a state measured at 1 run
    in 99 whose real disclosure is the locked dial's own copy.

    Counts, per V-204, are exchange-side and reported with the total they came
    from, because a bare miss count is unreadable as a rate:

        carried      exchanges in the conversation
        superseded   uniquely matched, one row each
        unmatched    an answer that canonicalized and matched no changed row
        suppressed   exchanges dropped by the uniqueness rule
        unparseable  an answer that could not be canonicalized at all

    Changed fields with NO matching exchange are deliberately not counted: most
    fields were never asked about, so that is the normal case, not a gap.
    """
    events = [e for e in (conversation or []) if isinstance(e, dict)]
    rows = [r for r in (diff_rows or []) if isinstance(r, dict) and "field" in r]
    pairs = _pair_conversation(events)

    counts = {
        "carried": len(pairs),
        "superseded": 0,
        "unmatched": 0,
        "suppressed": 0,
        "unparseable": 0,
    }
    if not pairs:
        return {"labels": [], "counts": counts}

    # index the changed rows by their parent value's canonical token
    rows_by_token: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        token = canonical_token(r.get("parent"))
        if token is not None:
            rows_by_token.setdefault(token, []).append(r)

    # candidate answers, and which rows each one could be talking about
    candidates: list[tuple[int, str, list[dict[str, Any]]]] = []
    for pair in pairs:
        answer_event = pair.get("answer")
        if answer_event is None:
            continue
        token = canonical_token(answer_event.get("answer"))
        if token is None:
            counts["unparseable"] += 1
            continue
        matched = rows_by_token.get(token, [])
        if not matched:
            counts["unmatched"] += 1
            continue
        candidates.append((pair["answer_index"], token, matched))

    # V-201, both directions. An answer wanting more than one row is ambiguous;
    # a row wanted by more than one answer is ambiguous for every answer in it.
    contested: set[str] = {
        token for token, n in Counter(t for _, t, _ in candidates).items() if n > 1
    }

    labels: list[dict[str, Any]] = []
    for answer_index, token, matched in candidates:
        if len(matched) > 1 or token in contested:
            counts["suppressed"] += 1
            continue
        row = matched[0]
        labels.append(
            {
                "answer_index": answer_index,
                "state": "superseded",
                "field": row["field"],
                "parent": row.get("parent"),
                "variant": row.get("variant"),
            }
        )
        counts["superseded"] += 1

    labels.sort(key=lambda label: label["answer_index"])
    return {"labels": labels, "counts": counts}


def _number_token(number: float) -> str:
    """`50`, `50.0` and `"50%"` must all render identically, and a float that
    is integral must not render as `50.0` while the int renders as `50`."""
    if number == int(number):
        return str(int(number))
    return repr(round(number, 10))


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
