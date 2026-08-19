"""V-14: the parent already ran this. PR-B's argue-back lookup.

At the confirm step, before a variant is submitted, if the edit the user just made
is one the PARENT'S SENSITIVITY SWEEP ALREADY EXECUTED, this returns that cell's
stored result so the screen can say so. No engine call, no new work, no estimate.

V-231 IS THE WHOLE DESIGN. The argue-back is a claim about stored parent output, so
it renders only what the sweep actually contains for the exact cell: no
interpolation between swept values, no extension past the sweep's range, and no
argue-back at all rather than a nearest-neighbour guess. The marker in A2 died for
rendering a weak mapping; this lives only while its mapping is exact.

WHY IT MATCHES A SCENARIO, NOT A NUMBER. The obvious implementation compares the
variant's new value against the sweep's `values` list. That is not enough, and the
setters in `app/honesty/stages.py` are why:

    set_delta   moves EVERY leg whose strike method is delta to the same value
    set_dte     moves target_dte AND re-derives min_dte / max_dte around it
    set_pt/sl   move a single field

So a variant that nudges one leg of a spread, or that sets target_dte without the
derived band, shares a number with a swept cell while being a materially different
run. Quoting that cell's Sharpe at it would be a claim about a backtest nobody
executed — the exact failure A2 spent a phase removing.

Instead the REAL SETTER IS REPLAYED. `_mutations` is imported from the honesty
layer (imported and called, never edited — the tree is frozen) to obtain the same
setter objects the sweep used, applied to a copy of the parent spec at each swept
value. If the result equals the variant's spec, the parent literally ran this
configuration and its number is quotable. If not, nothing is returned.

Replaying rather than mapping also dissolves a problem worth noting: sweep names
are only PARTLY a fixed vocabulary. `delta`, `dte`, `profit_target`, `stop_loss`
and `entry_time` are constants, but entry-condition sweeps are named `cond_<indicator>`
and, when two conditions share an indicator, `cond_<indicator>_<operator>`
(production carries `cond_rsi` and `cond_rsi_<=`). Any name-to-field-path table
would have to resolve those back to a condition index. Comparing whole
canonicalized specs needs no such table and cannot be wrong about identity.

DELIBERATELY NARROW. A variant that changes a swept parameter and ALSO changes
something else gets no argue-back, because the sweep held everything else fixed and
is therefore not evidence about that run. That is a real reduction in how often
this fires, and it is the correct direction: V-231 prefers absence.
"""

from __future__ import annotations

import json
from typing import Any

_SWEPT_FIELD_HINT: dict[str, str] = {
    # For the RENDERED SENTENCE only — which field to name when the diff carries
    # more than one row for a single swept mutation (dte moves target_dte plus the
    # derived band). Never used to decide a match; the replay does that.
    "dte": "position.expiration_selection.target_dte",
}

# V-240: what the sweep MOVED, in words, for the sweep-as-subject sentence. The
# label alone ("strike delta") does not tell a reader that both short legs moved
# together, and that is the fact which makes the cell evidence about their edit.
_SWEEP_MOVED: dict[str, str] = {
    "delta": "moved every delta-selected leg together",
    "dte": "moved the tenor and its derived window together",
}

_SWEEP_LABEL: dict[str, str] = {
    # When ONE swept mutation legitimately spans several fields and no single one
    # of them is the subject, the sweep itself is the subject. `set_delta` moves
    # every delta leg at once, so on a two-leg spread the diff carries two rows
    # and neither "leg 1 strike" nor "leg 2 strike" is what the sweep tested.
    #
    # Measured: without this, 8 of 979 replayable production cells returned
    # nothing despite the parent having run exactly that configuration — silence
    # caused by an inability to NAME the edit rather than by missing evidence,
    # which is not what V-231's absence-over-approximation is for.
    #
    # Only the fixed sweep names appear here. A `cond_<indicator>` sweep spanning
    # multiple fields still returns nothing, because its name is constructed and
    # would have to be resolved back to a condition to be spoken aloud.
    "delta": "strike delta",
    "dte": "days to expiration",
    "profit_target": "profit target",
    "stop_loss": "stop loss",
    "entry_time": "entry time",
}


def _sweeps(parent_stats: dict[str, Any] | None) -> list[dict[str, Any]]:
    """The stored sweep dump, or nothing. Never raises on a malformed payload:
    an absent sweep and an unreadable one both mean "no evidence"."""
    if not isinstance(parent_stats, dict):
        return []
    report = parent_stats.get("honesty_report")
    if not isinstance(report, dict):
        return []
    sensitivity = report.get("sensitivity")
    if not isinstance(sensitivity, dict):
        return []
    params = sensitivity.get("params")
    if not isinstance(params, list):
        return []
    return [p for p in params if isinstance(p, dict)]


def lookup(
    parent_spec: dict[str, Any],
    parent_stats: dict[str, Any] | None,
    variant_spec: dict[str, Any],
) -> dict[str, Any] | None:
    """The parent's stored result for this exact edit, or None.

    None is the overwhelmingly common answer and is not a failure. It means the
    parent's sweep did not run this configuration, so there is nothing to say.
    """
    from app.api.field_labels import label_for
    from app.api.variant import canonical_json, diff_specs

    sweeps = _sweeps(parent_stats)
    if not sweeps:
        return None

    try:
        variant_target = canonical_json(variant_spec)
    except Exception:
        # an unparseable spec is not an argue-back opportunity
        return None
    if canonical_json(parent_spec) == variant_target:
        return None  # nothing changed; the zero-edit guard owns that case

    try:
        from app.honesty.stages import _mutations
        from app.models.spec import StrategySpec

        typed_parent = StrategySpec.model_validate(parent_spec)
        setters = {name: setter for name, _values, _base, setter in _mutations(typed_parent)[0]}
    except Exception:
        return None

    for sweep in sweeps:
        name = sweep.get("name")
        setter = setters.get(name) if isinstance(name, str) else None
        if setter is None:
            continue
        values = sweep.get("values")
        sharpes = sweep.get("sharpes")
        base_index = sweep.get("base_index")
        if not isinstance(values, list) or not isinstance(sharpes, list):
            continue
        if len(values) != len(sharpes) or not isinstance(base_index, int):
            continue
        if not 0 <= base_index < len(sharpes):
            continue

        for index, value in enumerate(values):
            if sharpes[index] is None:
                # a cell the sweep could not score is not evidence
                continue
            try:
                candidate = StrategySpec.model_validate(json.loads(typed_parent.model_dump_json()))
                setter(candidate, float(value))
                replayed = canonical_json(json.loads(candidate.model_dump_json()))
            except Exception:
                continue
            if replayed != variant_target:
                continue

            # the parent ran exactly this. Name the field from the diff, through
            # the V-208 table (V-229), and quote the stored numbers verbatim.
            rows = diff_specs(parent_spec, variant_spec)
            if not rows:
                # the setter produced an identical spec (a collapsed grid cell —
                # dte rounding two steps onto the same day). Nothing changed, so
                # there is no edit to argue about. Measured at 12 of 979 cells.
                return None
            hint = _SWEPT_FIELD_HINT.get(str(name))
            if hint and any(r["field"] == hint for r in rows):
                field, label = hint, label_for(hint)
            elif len(rows) == 1:
                field, label = rows[0]["field"], label_for(rows[0]["field"])
            else:
                # one mutation, several fields, none of them the subject
                sweep_label = _SWEEP_LABEL.get(str(name))
                if sweep_label is None:
                    return None
                field, label = None, sweep_label
            base_sharpe = sharpes[base_index]
            return {
                "sweep": name,
                "field": field,          # None when the sweep itself is the subject
                "fields": [r["field"] for r in rows],
                "label": label,
                "tested_value": float(value),
                "tested_sharpe": sharpes[index],
                "base_value": float(values[base_index]),
                "base_sharpe": base_sharpe,
                "classification": sweep.get("classification") or None,
                "cells": len(values),
                # V-240: the two silences are DIFFERENT and must stay different.
                # `subject` says which sentence to render. "field" means one dial
                # moved and the copy names it. "sweep" means one mutation moved
                # several fields together and the copy must say so, because "leg 1
                # strike" would be a claim the sweep did not test. An
                # identical-spec cell returns None instead and renders nothing,
                # since there is no edit to argue about. A refactor that collapsed
                # unnameable into absent would silently drop real evidence, so a
                # test pins each class to its own rendering.
                "subject": "field" if field is not None else "sweep",
                "moved": None if field is not None else _SWEEP_MOVED.get(str(name)),
            }
    return None
