"""The field-level diff: ONE comparison, consumed three ways today and four
after A2 (V-162).

Consumers: the V-22 lock check, the V-10/V-19 zero-edit guard, provenance
section 5, and later A2's Q&A reconciler. None of them recomputes the
comparison; all of them read this output shape:

    [{"field": <spec-schema path>, "parent": <value>, "variant": <value>}, ...]

V-164: the paths are part of the CONTRACT, read by the lock check, the zero-edit
guard and provenance section 5. A2 does NOT key on them: V-53's path-keyed
reconciler was superseded by V-200 (value matching) and V-213 removed the
rendering altogether. Renaming a path is still breaking, for the readers that
remain.

V-165: the three inherited-window cases are decided HERE, before the engine
existed, because they are where "did the user change a value" and "did the
value change" come apart.
"""

from __future__ import annotations

import copy
from typing import Any

from app.api.variant import canonical_json, canonical_spec, diff_specs

from .test_spec_roundtrip import CANONICAL


def _spec(**overrides: Any) -> dict[str, Any]:
    spec = copy.deepcopy(CANONICAL)
    for dotted, value in overrides.items():
        node = spec
        parts = dotted.split("__")
        for key in parts[:-1]:
            node = node[key]
        node[parts[-1]] = value
    return spec


def _paths(diff: list[dict[str, Any]]) -> set[str]:
    return {row["field"] for row in diff}


# --- V-162: the output shape -------------------------------------------------


def test_identical_specs_diff_to_the_empty_list() -> None:
    """The zero-edit guard's whole input: empty diff = same run = block
    before the debit."""
    assert diff_specs(CANONICAL, copy.deepcopy(CANONICAL)) == []


def test_rows_carry_field_parent_and_variant() -> None:
    diff = diff_specs(CANONICAL, _spec(backtest__seed=777))
    assert diff == [{"field": "backtest.seed", "parent": 42, "variant": 777}]


# --- V-165: the three inherited-window cases ---------------------------------


def test_an_untouched_carried_window_produces_no_diff_row() -> None:
    """V-40: the parent recorded start=2024-01-01, the variant submits the
    same dates untouched. Nothing changed, nothing appears."""
    parent = _spec(backtest__start="2024-01-01")
    variant = _spec(backtest__start="2024-01-01")
    assert _paths(diff_specs(parent, variant)) == set()


def test_carried_all_resolving_wider_coverage_produces_no_diff_row() -> None:
    """V-51: the parent chose "all" (backtest.start null); the variant submits
    "all" (null again). At RUN time the variant may test more history than the
    parent did, because coverage grew — but the SPEC did not change, coverage
    did. The diff reads specs, never effective windows, so no row. This also
    covers null-vs-absent: an older parent row may lack the key entirely and
    must compare equal to an explicit null."""
    parent = _spec(backtest__start=None, backtest__end=None)
    variant = _spec(backtest__start=None, backtest__end=None)
    assert _paths(diff_specs(parent, variant)) == set()
    # absent on the parent side, null on the variant side: still no row
    parent_missing = copy.deepcopy(parent)
    del parent_missing["backtest"]["start"]
    assert _paths(diff_specs(parent_missing, variant)) == set()


def test_an_edited_window_diffs_against_the_parents_recorded_value() -> None:
    """The old side is what the parent RECORDED (its requested window), never
    its effective window — the diff does not even see stats. One row."""
    parent = _spec(backtest__start="2024-01-01")
    variant = _spec(backtest__start="2023-01-01")
    diff = diff_specs(parent, variant)
    assert diff == [
        {"field": "backtest.start", "parent": "2024-01-01", "variant": "2023-01-01"}
    ]


# --- V-164: the path vocabulary is pinned -------------------------------------


def test_the_path_vocabulary_is_pinned() -> None:
    """One edit per dial, and the exact path each produces.

    Renaming one breaks the lock check's prefix matching, the stored what_changed
    record, and the V-208 label table's keys, so a rename must fail here first.
    It does NOT break reconciliation: that matches values, not paths."""
    cases: list[tuple[dict[str, Any], str]] = [
        (_spec(backtest__start="2023-01-01"), "backtest.start"),
        (_spec(backtest__seed=7), "backtest.seed"),
        (_spec(backtest__initial_capital=50_000), "backtest.initial_capital"),
        (_spec(exit__profit_target_pct=35), "exit.profit_target_pct"),
        (_spec(sizing__value=3), "sizing.value"),
        (
            _spec(costs__commission_per_contract=0.5),
            "costs.commission_per_contract",
        ),
        (
            _spec(entry__max_concurrent_positions=2),
            "entry.max_concurrent_positions",
        ),
    ]
    for variant, expected in cases:
        assert _paths(diff_specs(CANONICAL, variant)) == {expected}, expected

    # cadence: the dial owns frequency AND day_of_week (V-77), so
    # weekly→monthly is honestly TWO rows — the day it ran on is also gone —
    # while weekly·mon→weekly·fri is one
    variant = _spec(entry__schedule={"frequency": "monthly", "day_of_week": None})
    assert _paths(diff_specs(CANONICAL, variant)) == {
        "entry.schedule.frequency",
        "entry.schedule.day_of_week",
    }
    variant = _spec(entry__schedule={"frequency": "weekly", "day_of_week": "friday"})
    assert _paths(diff_specs(CANONICAL, variant)) == {"entry.schedule.day_of_week"}

    # a leg edit names the leg by index — the lock check prefix-matches
    # "position.legs" against exactly this form
    variant = copy.deepcopy(CANONICAL)
    variant["position"]["legs"][0]["strike_selection"]["value"] = 0.20
    assert _paths(diff_specs(CANONICAL, variant)) == {
        "position.legs[0].strike_selection.value"
    }

    # tenor: target and band are SEPARATE rows (V-124 — a reader of the diff
    # sees the band move explicitly, without ownership rules in front of them)
    variant = _spec(
        position__expiration_selection={"target_dte": 30, "min_dte": 20, "max_dte": 45}
    )
    assert _paths(diff_specs(CANONICAL, variant)) == {
        "position.expiration_selection.target_dte",
        "position.expiration_selection.min_dte",
        "position.expiration_selection.max_dte",
    }


# --- V-163: one canonicalizer ------------------------------------------------


def test_canonicalizer_normalizes_null_absent_and_key_order() -> None:
    """The same function the V-18 guard uses (it imports THIS one). Null and
    absent collapse together, key order is irrelevant, and the canonical JSON
    of two equal specs is byte-identical."""
    a = _spec(backtest__start=None)
    b = copy.deepcopy(a)
    del b["backtest"]["start"]
    assert canonical_spec(a) == canonical_spec(b)
    assert canonical_json(a) == canonical_json(b)

    # key order never matters: rebuild one side with reversed insertion order
    shuffled = {k: CANONICAL[k] for k in reversed(list(CANONICAL))}
    assert canonical_json(CANONICAL) == canonical_json(shuffled)
