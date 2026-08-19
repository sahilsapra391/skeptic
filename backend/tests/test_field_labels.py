"""V-208's table, and the two ways a label table rots.

A table like this fails in two directions and only one of them is obvious.

The obvious one: a path occurs that has no label. Handled by design — the caller
renders the raw path and the gap is counted, so nothing breaks and the hole
reports itself.

The dangerous one: the table carries a label for a path that no longer exists,
because a field was renamed and the entry was left behind. That failure is
invisible, because the missing label only shows up when someone runs that exact
edit. `test_every_key_matches_a_real_spec_path` is the fence: the table is
checked against the paths that actually occur in stored specs.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.api.field_labels import FIELD_LABELS, label_for, label_rows, normalize

# the 67 distinct paths observed across every stored spec in the local and
# production databases, indices collapsed. Recorded here rather than queried so
# the test runs offline and in CI, and so a change to this list is a visible diff.
OBSERVED_PATHS = {
    "backtest.clock", "backtest.end", "backtest.initial_capital", "backtest.resolution",
    "backtest.seed", "backtest.start", "costs.commission_per_contract",
    "costs.liquidity_mode", "costs.max_spread_pct", "costs.min_open_interest",
    "costs.min_volume", "costs.slippage_half_spread_fraction",
    "costs.slippage_half_spread_fraction_sell", "entry.conditions[].indicator",
    "entry.conditions[].operator", "entry.conditions[].params",
    "entry.conditions[].period", "entry.conditions[].timeframe",
    "entry.conditions[].value", "entry.intraday_scan", "entry.max_concurrent_positions",
    "entry.scale_in", "entry.scale_in.basket", "entry.scale_in.max_total_contracts",
    "entry.scale_in.mode", "entry.scale_in.rearm.indicator",
    "entry.scale_in.rearm.operator", "entry.scale_in.rearm.params",
    "entry.scale_in.rearm.period", "entry.scale_in.rearm.timeframe",
    "entry.scale_in.rearm.value", "entry.scale_in.rungs[].add_contracts",
    "entry.scale_in.rungs[].indicator", "entry.scale_in.rungs[].operator",
    "entry.scale_in.rungs[].params", "entry.scale_in.rungs[].period",
    "entry.scale_in.rungs[].timeframe", "entry.scale_in.rungs[].value",
    "entry.scale_in.stop_adding_on.mode", "entry.schedule.day_of_month",
    "entry.schedule.day_of_week", "entry.schedule.frequency",
    "entry.schedule.time_of_day", "exit.close_at_time", "exit.conditions",
    "exit.delta_stop_abs", "exit.profit_target_pct", "exit.stop_loss_pct",
    "exit.theta_harvest", "exit.time_exit_dte", "meta.description_raw", "meta.name",
    "position.expiration_selection.max_dte", "position.expiration_selection.min_dte",
    "position.expiration_selection.target_dte", "position.legs[].ratio",
    "position.legs[].right", "position.legs[].side",
    "position.legs[].strike_selection.method",
    "position.legs[].strike_selection.reference_leg",
    "position.legs[].strike_selection.value", "position.max_vega_per_contract",
    "position.structure", "sizing.method", "sizing.value", "spec_version",
    "underlying.ticker",
}


class TestTheTableCannotRotSilently:
    def test_every_key_matches_a_real_spec_path(self) -> None:
        """The invisible failure. An entry for a path nothing emits is a label
        that will never appear, and nobody finds out by using the app."""
        stale = set(FIELD_LABELS) - OBSERVED_PATHS
        assert not stale, (
            f"these keys name paths no stored spec produces: {sorted(stale)}. "
            "Either the field was renamed and the entry is stale, or the key has "
            "a typo, and in both cases the label silently never renders."
        )

    def test_every_observed_path_has_a_label(self) -> None:
        """The visible failure, kept at zero on purpose. Unmapped is SAFE (the
        raw path renders and the gap is counted), so this is a completeness bar
        rather than a correctness one — if a new field appears and this fails,
        adding the label is the fix, not relaxing the test."""
        missing = OBSERVED_PATHS - set(FIELD_LABELS)
        assert not missing, f"paths with no label: {sorted(missing)}"

    def test_the_schema_still_contains_the_top_level_sections(self) -> None:
        """A cheap second anchor: the labels are grouped by spec section, and a
        section rename would strand a whole block of them at once."""
        schema = json.loads(
            (Path(__file__).resolve().parents[2] / "docs" / "strategy-spec.schema.json")
            .read_text()
        )
        sections = {key.split(".")[0].split("[")[0] for key in FIELD_LABELS}
        sections.discard("spec_version")
        assert sections <= set(schema["properties"]), (
            f"labels reference spec sections the schema does not have: "
            f"{sorted(sections - set(schema['properties']))}"
        )


class TestIndexHandling:
    @pytest.mark.parametrize(
        "path,expected",
        [
            ("position.legs[0].strike_selection.value", "leg 1 strike"),
            ("position.legs[1].strike_selection.value", "leg 2 strike"),
            ("position.legs[3].ratio", "leg 4 ratio"),
            ("entry.conditions[0].value", "entry condition 1 threshold"),
            ("entry.conditions[2].indicator", "entry condition 3 indicator"),
            ("entry.scale_in.rungs[1].add_contracts", "scale-in rung 2 contracts"),
        ],
    )
    def test_positions_render_one_based(self, path: str, expected: str) -> None:
        """A user counts legs from one. `legs[0]` is an implementation detail and
        putting it in front of them is the same mistake as showing them a path."""
        assert label_for(path) == expected

    def test_normalize_collapses_every_index(self) -> None:
        assert normalize("position.legs[2].strike_selection.value") == (
            "position.legs[].strike_selection.value"
        )
        assert normalize("a[0].b[11].c") == "a[].b[].c"

    def test_unindexed_labels_are_returned_verbatim(self) -> None:
        assert label_for("exit.profit_target_pct") == "profit target"


class TestUnmappedIsSafe:
    def test_an_unknown_path_returns_none(self) -> None:
        assert label_for("exit.something_new_nobody_labelled") is None

    def test_rows_keep_going_and_the_gap_is_counted(self) -> None:
        rows = [
            {"field": "exit.profit_target_pct", "parent": 50, "variant": 35},
            {"field": "exit.brand_new_field", "parent": 1, "variant": 2},
        ]
        labelled, unlabeled = label_rows(rows)
        assert unlabeled == 1
        assert labelled[0]["label"] == "profit target"
        assert "label" not in labelled[1], "an unmapped row carries no label at all"
        assert labelled[1]["field"] == "exit.brand_new_field"

    def test_label_rows_does_not_mutate_the_shared_rows(self) -> None:
        """These row objects are also read by the lock check, the zero-edit guard
        and the reconciler (V-162). A presentation pass must not reach into them."""
        rows = [{"field": "exit.profit_target_pct", "parent": 50, "variant": 35}]
        before = json.dumps(rows, sort_keys=True)
        label_rows(rows)
        assert json.dumps(rows, sort_keys=True) == before


class TestPresentationOnly:
    def test_labels_are_absent_from_the_matching_path(self) -> None:
        """V-208's boundary, asserted rather than asserted-in-prose: the module
        that matches values must not import the module that captions them."""
        import inspect

        from app.api import variant

        source = inspect.getsource(variant)
        assert "field_labels" not in source, (
            "variant.py matches on values and paths; labels are presentation and "
            "must not reach it"
        )
