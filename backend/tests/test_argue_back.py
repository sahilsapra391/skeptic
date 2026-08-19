"""V-14's sensitivity lookup, under V-231's exact-cell rule.

At the confirm step, before a variant is submitted, if the edit the user just made
is one the PARENT'S SWEEP ALREADY RAN, say so with the parent's own stored number.
No engine call, no new work, no estimate.

WHY THIS MATCHES A SCENARIO AND NOT A NUMBER. The obvious implementation compares
the variant's new value against the sweep's `values` list. That is not enough, and
the sweep setters are why: `set_delta` moves EVERY delta leg to the same value, and
`set_dte` moves `target_dte` and re-derives `min_dte`/`max_dte` with it. So a
variant that nudges one leg's delta, or that sets target_dte without the derived
band, shares a number with a swept cell while being a different run. Quoting the
cell's Sharpe at it would be a claim about a backtest nobody executed.

Instead the real setter is REPLAYED: apply it to the parent spec at each swept
value and ask whether the result IS the variant's spec. A match means the parent
literally ran this. Anything else gets nothing.

That is V-231 as written — stored cells, exact match, absence over approximation —
and it is deliberately narrow. A variant that changes a swept parameter AND
something else gets no argue-back, because the sweep held everything else fixed and
so is not evidence about that run.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from app.api.argue_back import lookup

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _parent() -> tuple[dict, dict]:
    """A real stored spec plus a sweep dump shaped exactly as stats_json holds it."""
    spec = json.loads((FIXTURES / "overfit_strategy.json").read_text())
    spec = spec.get("spec", spec)
    stats = {
        "honesty_report": {
            "sensitivity": {
                "params": [
                    {
                        "name": "profit_target",
                        "values": [40.0, 45.0, 50.0, 55.0, 60.0],
                        "sharpes": [0.81, 0.75, 0.72, 0.43, 0.44],
                        "base_index": 2,
                        "classification": "plateau",
                    }
                ]
            }
        }
    }
    return spec, stats


def _with(spec: dict, **edits) -> dict:
    out = copy.deepcopy(spec)
    for path, value in edits.items():
        node = out
        parts = path.split(".")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value
    return out


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("SKEPTIC_ACCESS_TOKEN", "")
    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app)


@pytest.fixture()
def seeded_parent():
    """A stored parent carrying a real sweep dump, so the endpoint reads it from
    the database rather than from a hand-passed dict."""
    import os
    import uuid

    os.environ.setdefault("DATABASE_URL", "sqlite:///backend/runs.db")
    from app import db

    db.init_db()
    spec, stats = _parent()
    spec = _with(spec, **{"exit.profit_target_pct": 50.0})
    run_id = f"ab{uuid.uuid4().hex[:10]}"
    with db.session() as s:
        s.add(db.Run(id=run_id, status="done", stage=6, seed=42, origin="user",
                     spec_json=json.dumps(spec), stats_json=json.dumps(stats)))
        s.commit()
    yield run_id, spec
    with db.session() as s:
        row = s.get(db.Run, run_id)
        if row is not None:
            s.delete(row)
            s.commit()


class TestItFiresOnAnExactCell:
    def test_the_reference_case(self) -> None:
        """The parent swept profit target and the user just picked a swept value."""
        spec, stats = _parent()
        spec = _with(spec, **{"exit.profit_target_pct": 50.0})
        variant = _with(spec, **{"exit.profit_target_pct": 55.0})

        hit = lookup(spec, stats, variant)
        assert hit is not None
        assert hit["field"] == "exit.profit_target_pct"
        assert hit["label"] == "profit target"          # V-229: from the V-208 table
        assert hit["tested_value"] == 55.0
        assert hit["tested_sharpe"] == 0.43             # the STORED cell, verbatim
        assert hit["base_value"] == 50.0
        assert hit["base_sharpe"] == 0.72
        assert hit["classification"] == "plateau"

    def test_the_numbers_are_the_stored_ones_not_recomputed(self) -> None:
        spec, stats = _parent()
        spec = _with(spec, **{"exit.profit_target_pct": 50.0})
        variant = _with(spec, **{"exit.profit_target_pct": 40.0})
        hit = lookup(spec, stats, variant)
        assert hit["tested_sharpe"] == 0.81, "must be the stored value at that index"


class TestAbsenceOverApproximation:
    def test_a_value_between_swept_cells_gets_nothing(self) -> None:
        """No interpolation. 52 sits between 50 and 55 and was never run."""
        spec, stats = _parent()
        spec = _with(spec, **{"exit.profit_target_pct": 50.0})
        assert lookup(spec, stats, _with(spec, **{"exit.profit_target_pct": 52.0})) is None

    def test_a_value_past_the_sweep_range_gets_nothing(self) -> None:
        """No extension. 80 is outside [40, 60] and gets no nearest-neighbour."""
        spec, stats = _parent()
        spec = _with(spec, **{"exit.profit_target_pct": 50.0})
        assert lookup(spec, stats, _with(spec, **{"exit.profit_target_pct": 80.0})) is None

    def test_no_edit_at_all_gets_nothing(self) -> None:
        spec, stats = _parent()
        spec = _with(spec, **{"exit.profit_target_pct": 50.0})
        assert lookup(spec, stats, copy.deepcopy(spec)) is None

    def test_an_unswept_field_gets_nothing(self) -> None:
        """The parent swept profit target only. A window edit has no cell."""
        spec, stats = _parent()
        spec = _with(spec, **{"exit.profit_target_pct": 50.0})
        variant = _with(spec, **{"backtest.start": "2021-01-04"})
        assert lookup(spec, stats, variant) is None

    def test_a_parent_with_no_sweep_gets_nothing(self) -> None:
        spec, _ = _parent()
        spec = _with(spec, **{"exit.profit_target_pct": 50.0})
        variant = _with(spec, **{"exit.profit_target_pct": 55.0})
        assert lookup(spec, {}, variant) is None
        assert lookup(spec, {"honesty_report": {}}, variant) is None

    def test_a_null_sharpe_cell_gets_nothing(self) -> None:
        """A cell the sweep could not score is not evidence."""
        spec, stats = _parent()
        spec = _with(spec, **{"exit.profit_target_pct": 50.0})
        stats["honesty_report"]["sensitivity"]["params"][0]["sharpes"] = [
            0.81, 0.75, 0.72, None, 0.44
        ]
        assert lookup(spec, stats, _with(spec, **{"exit.profit_target_pct": 55.0})) is None


class TestScenarioNotNumber:
    def test_a_second_simultaneous_edit_suppresses_it(self) -> None:
        """The sweep held everything else fixed, so it is not evidence about a run
        that moved two things. Sharing a number with a swept cell is not enough."""
        spec, stats = _parent()
        spec = _with(spec, **{"exit.profit_target_pct": 50.0})
        variant = _with(
            spec, **{"exit.profit_target_pct": 55.0, "backtest.start": "2021-01-04"}
        )
        assert lookup(spec, stats, variant) is None

    def test_the_replay_is_the_authority_not_the_values_list(self) -> None:
        """A doctored sweep dump claiming a value the setter cannot produce must
        not fire: the match comes from replaying the setter, not from the list."""
        spec, stats = _parent()
        spec = _with(spec, **{"exit.profit_target_pct": 50.0})
        stats["honesty_report"]["sensitivity"]["params"][0]["values"] = [999.0] * 5
        assert lookup(spec, stats, _with(spec, **{"exit.profit_target_pct": 55.0})) is None


class TestItNeverRaises:
    @pytest.mark.parametrize(
        "stats",
        [None, {}, {"honesty_report": None}, {"honesty_report": {"sensitivity": {"params": None}}}],
    )
    def test_malformed_stats_return_none(self, stats) -> None:
        spec, _ = _parent()
        assert lookup(spec, stats, copy.deepcopy(spec)) is None

    def test_an_unparseable_spec_returns_none(self) -> None:
        _, stats = _parent()
        assert lookup({"nonsense": True}, stats, {"nonsense": True}) is None


class TestOneMutationSpanningSeveralFields:
    """`set_delta` moves EVERY delta leg at once, so on a spread the diff carries
    two rows and neither "leg 1 strike" nor "leg 2 strike" is what was tested.

    Measured: without naming the sweep itself, 8 of 979 replayable production
    cells returned nothing despite the parent having run exactly that
    configuration. That is silence from being unable to NAME the edit, not from
    missing evidence, and V-231's absence-over-approximation is not for that.
    """

    def _spread(self) -> tuple[dict, dict]:
        """An IRON CONDOR, which is the structure that actually produces this case.

        Found by asking production rather than by inventing one: of every stored
        spec, only `iron_condor` carries two delta legs (legs 0 and 2, the shorts;
        the longs are `width_from_leg` off them). A put credit spread does NOT —
        its long leg is width-derived, so `set_delta` moves one field and the
        single-field path already covers it. The first version of this test hand-
        added a second leg to a `short_put`, which fails structure validation, so
        `lookup` correctly returned None and the test failed for the wrong reason.
        """
        spec = json.loads((FIXTURES / "overfit_strategy.json").read_text())
        spec = spec.get("spec", spec)
        spec["position"]["structure"] = "iron_condor"
        spec["position"]["legs"] = [
            {"right": "put", "side": "short",
             "strike_selection": {"method": "delta", "value": 0.30, "reference_leg": None},
             "ratio": 1},
            {"right": "put", "side": "long",
             "strike_selection": {"method": "width_from_leg", "value": 5.0, "reference_leg": 0},
             "ratio": 1},
            {"right": "call", "side": "short",
             "strike_selection": {"method": "delta", "value": 0.30, "reference_leg": None},
             "ratio": 1},
            {"right": "call", "side": "long",
             "strike_selection": {"method": "width_from_leg", "value": 5.0, "reference_leg": 2},
             "ratio": 1},
        ]
        stats = {
            "honesty_report": {
                "sensitivity": {
                    "params": [{
                        "name": "delta",
                        "values": [0.24, 0.27, 0.3, 0.33, 0.36],
                        "sharpes": [0.81, 0.75, 0.72, 0.43, 0.44],
                        "base_index": 2,
                        "classification": "cliff",
                    }]
                }
            }
        }
        return spec, stats

    def test_the_sweep_is_the_subject_when_no_single_field_is(self) -> None:
        spec, stats = self._spread()
        variant = copy.deepcopy(spec)
        for leg in variant["position"]["legs"]:
            if leg.get("strike_selection", {}).get("method") == "delta":
                leg["strike_selection"]["value"] = 0.36

        hit = lookup(spec, stats, variant)
        assert hit is not None, "the parent ran exactly this; naming must not block it"
        assert hit["label"] == "strike delta"
        assert hit["field"] is None, "no single field is the subject"
        assert len(hit["fields"]) >= 2, "every field the mutation touched is reported"
        assert hit["tested_sharpe"] == 0.44
        assert hit["classification"] == "cliff"

    def test_a_collapsed_grid_cell_stays_silent(self) -> None:
        """0 diff rows means the setter produced an identical spec. Nothing
        changed, so there is nothing to argue about. Measured at 12 of 979."""
        spec, stats = self._spread()
        assert lookup(spec, stats, copy.deepcopy(spec)) is None


class TestTheTwoSilencesStayDistinct:
    """V-240. "Unnameable" and "absent" are different outcomes and must not collapse.

    A multi-leg cell the parent genuinely ran, whose edit cannot be attributed to a
    single dial, renders the SWEEP as subject. A cell whose replay produced an
    identical spec renders NOTHING, because there is no edit.

    Both are pinned here because a refactor that folded the first into the second
    would silently discard real evidence, and it would look like a simplification.
    """

    def test_the_multi_leg_class_carries_a_sweep_subject_and_says_what_moved(self) -> None:
        spec, stats = TestOneMutationSpanningSeveralFields()._spread()
        variant = copy.deepcopy(spec)
        for leg in variant["position"]["legs"]:
            if leg.get("strike_selection", {}).get("method") == "delta":
                leg["strike_selection"]["value"] = 0.36

        hit = lookup(spec, stats, variant)
        assert hit is not None
        assert hit["subject"] == "sweep"
        assert hit["field"] is None
        assert hit["label"] == "strike delta"
        assert hit["moved"] == "moved every delta-selected leg together", (
            "the copy must state that both legs moved, or it implies a single-leg "
            "test the sweep never ran"
        )

    def test_the_single_field_class_carries_a_field_subject_and_no_moved_clause(self) -> None:
        spec, stats = _parent()
        spec = _with(spec, **{"exit.profit_target_pct": 50.0})
        hit = lookup(spec, stats, _with(spec, **{"exit.profit_target_pct": 55.0}))
        assert hit is not None
        assert hit["subject"] == "field"
        assert hit["field"] == "exit.profit_target_pct"
        assert hit["moved"] is None, "a single dial needs no explanation of what moved"

    def test_the_identical_spec_class_is_absent_not_unnameable(self) -> None:
        """The distinction the test above protects: this one returns None, and it
        must keep returning None rather than becoming a sweep-subject render."""
        spec, stats = TestOneMutationSpanningSeveralFields()._spread()
        assert lookup(spec, stats, copy.deepcopy(spec)) is None


class TestTheEndpoint:
    """POST /api/runs/{id}/argue-back — read-only, free, and silent by default."""

    def test_a_hit_is_returned_for_a_swept_edit(self, client, seeded_parent) -> None:
        parent_id, parent_spec = seeded_parent
        variant = _with(parent_spec, **{"exit.profit_target_pct": 55.0})
        r = client.post(f"/api/runs/{parent_id}/argue-back", json={"spec": variant})
        assert r.status_code == 200
        hit = r.json()["hit"]
        assert hit is not None
        assert hit["tested_sharpe"] == 0.43
        assert hit["subject"] == "field"

    def test_an_unswept_edit_returns_a_null_hit_not_an_error(self, client, seeded_parent) -> None:
        parent_id, parent_spec = seeded_parent
        variant = _with(parent_spec, **{"exit.profit_target_pct": 52.0})
        r = client.post(f"/api/runs/{parent_id}/argue-back", json={"spec": variant})
        assert r.status_code == 200
        assert r.json()["hit"] is None, "silence is the ordinary answer, not a failure"

    def test_it_spends_nothing_and_creates_nothing(self, client, seeded_parent) -> None:
        """V-09's posture applied to the argue-back: looking costs nothing."""
        from app import db

        parent_id, parent_spec = seeded_parent
        with db.session() as s:
            runs_before = s.query(db.Run).count()
            ledger_before = s.query(db.CreditLedger).count()
        client.post(
            f"/api/runs/{parent_id}/argue-back",
            json={"spec": _with(parent_spec, **{"exit.profit_target_pct": 55.0})},
        )
        with db.session() as s:
            assert s.query(db.Run).count() == runs_before
            assert s.query(db.CreditLedger).count() == ledger_before

    def test_a_missing_run_is_404(self, client) -> None:
        r = client.post("/api/runs/nope/argue-back", json={"spec": {}})
        assert r.status_code == 404
