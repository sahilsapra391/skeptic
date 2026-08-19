"""V-200's value-matching reconciler.

V-53 specified a table mapping question labels to spec field paths. V-200
superseded it: parser question ids and question text are both authored by the
model per call, with no enumerated vocabulary and no validation, so a table
keyed on either is keyed on a model version and fails silently when the model
changes.

This keys on stored truth instead. A carried exchange is SUPERSEDED when its
recorded ANSWER, canonicalized, equals the `parent` value of a row the user
actually changed. Both sides go through the same normalizer (V-202), equality is
exact after that, and the match must be unique in both directions (V-201).

The bias throughout is toward STILL HOLDS. The reconciler emits ONLY superseded
entries; absence of an entry is STILL HOLDS. That way a serialization bug, a
dropped field, or an unhandled shape degrades to the safe state rather than to a
false claim on the provenance screen.
"""
from __future__ import annotations

import json

import pytest

from app.api.variant import canonical_token, reconcile


def q(qid: str, text: str = "?") -> dict:
    return {"kind": "question", "id": qid, "question": text, "options": []}


def a(qid: str, answer: str) -> dict:
    return {"kind": "answer", "id": qid, "answer": answer}


def row(field: str, parent, variant) -> dict:
    return {"field": field, "parent": parent, "variant": variant}


class TestSuperseded:
    def test_v33_the_reference_case(self) -> None:
        """The brief's own example: profit target 50 to 35. The exchange that
        settled it answered "50", the parent value is 50, so this exchange is
        the one the edit superseded."""
        convo = [q("pt", "Profit target?"), a("pt", "50")]
        result = reconcile(convo, [row("exit.profit_target_pct", 50, 35)])

        assert result["labels"] == [
            {
                "answer_index": 1,
                "state": "superseded",
                "field": "exit.profit_target_pct",
                "parent": 50,
                "variant": 35,
            }
        ]
        assert result["counts"]["superseded"] == 1
        assert result["counts"]["unmatched"] == 0

    def test_percent_and_unit_forms_match_the_stored_number(self) -> None:
        """"50%" is the same answer as "50"; the schema stores whole percents."""
        for answer in ("50", "50%", " 50 % ", "50.0"):
            result = reconcile(
                [q("pt"), a("pt", answer)], [row("exit.profit_target_pct", 50, 35)]
            )
            assert result["counts"]["superseded"] == 1, f"{answer!r} failed to match"

    def test_a_string_answer_matches_a_string_value(self) -> None:
        result = reconcile(
            [q("f"), a("f", "Weekly")], [row("entry.schedule.frequency", "weekly", "daily")]
        )
        assert result["counts"]["superseded"] == 1


class TestStillHolds:
    def test_v56_an_answer_matching_nothing_is_counted_not_swallowed(self) -> None:
        """The safe failure, made observable. The exchange renders normally, the
        diff row is untouched, and the counter records that the mechanism could
        not explain this exchange. That tally is V-57's trigger data."""
        convo = [q("x", "Which underlying?"), a("x", "whatever you think best")]
        rows = [row("exit.profit_target_pct", 50, 35)]
        result = reconcile(convo, rows)

        assert result["labels"] == []
        assert result["counts"]["unmatched"] == 1
        assert rows == [row("exit.profit_target_pct", 50, 35)], "diff rows must not be mutated"

    def test_an_unchanged_field_never_supersedes(self) -> None:
        """Diff-anchored: only CHANGED rows are candidates. An exchange whose
        answer equals a value that stayed put is history, not a supersession."""
        result = reconcile([q("pt"), a("pt", "50")], [])
        assert result["labels"] == []
        assert result["counts"]["superseded"] == 0

    def test_an_unparseable_answer_is_a_safe_miss(self) -> None:
        result = reconcile([q("pt"), a("pt", "   ")], [row("exit.profit_target_pct", 50, 35)])
        assert result["labels"] == []
        assert result["counts"]["unparseable"] == 1

    def test_a_question_with_no_answer_is_not_a_match(self) -> None:
        result = reconcile([q("pt")], [row("exit.profit_target_pct", 50, 35)])
        assert result["labels"] == []
        assert result["counts"]["carried"] == 1


class TestUniquenessV201:
    def test_two_exchanges_answering_alike_suppress_each_other(self) -> None:
        """V-205's collision case. Two answers of "50", one changed field: there
        is no way to know WHICH exchange the edit superseded, so neither is
        marked and the suppression is counted. Never guess, applied to values."""
        convo = [q("pt"), a("pt", "50"), q("sl"), a("sl", "50")]
        result = reconcile(convo, [row("exit.profit_target_pct", 50, 35)])

        assert result["labels"] == []
        assert result["counts"]["suppressed"] == 2
        assert result["counts"]["superseded"] == 0

    def test_one_answer_matching_two_changed_rows_is_suppressed(self) -> None:
        """The other direction. If both the profit target and the stop loss were
        50 and both changed, an answer of "50" cannot name which one it settled."""
        convo = [q("pt"), a("pt", "50")]
        rows = [row("exit.profit_target_pct", 50, 35), row("exit.stop_loss_pct", 50, 80)]
        result = reconcile(convo, rows)

        assert result["labels"] == []
        assert result["counts"]["suppressed"] == 1
        assert result["counts"]["superseded"] == 0

    def test_distinct_values_still_match_independently(self) -> None:
        """Uniqueness must not over-suppress: two exchanges, two rows, no
        ambiguity between them."""
        convo = [q("pt"), a("pt", "50"), q("sl"), a("sl", "80")]
        rows = [row("exit.profit_target_pct", 50, 35), row("exit.stop_loss_pct", 80, 60)]
        result = reconcile(convo, rows)

        assert result["counts"]["superseded"] == 2
        assert {label["field"] for label in result["labels"]} == {
            "exit.profit_target_pct",
            "exit.stop_loss_pct",
        }


class TestSubstringMatchingIsForbidden:
    """Measured against production, 14 unanchored answers produced 80+ substring
    hits between them, about six spec paths each: a prose answer containing any
    digit matches every numeric field sharing that digit. Loosening equality
    here manufactures false claims at scale. This test is the fence."""

    def test_a_prose_answer_containing_the_value_does_not_match(self) -> None:
        result = reconcile(
            [q("pt"), a("pt", "take profit at 50 percent of max credit")],
            [row("exit.profit_target_pct", 50, 35)],
        )
        assert result["labels"] == []
        assert result["counts"]["unmatched"] == 1

    def test_a_value_that_is_a_substring_of_another_does_not_match(self) -> None:
        result = reconcile([q("v"), a("v", "1")], [row("costs.min_volume", 10, 25)])
        assert result["labels"] == []


class TestShape:
    def test_orphan_answers_are_anchored_by_their_own_index(self) -> None:
        """An answer whose question was never recorded still renders as an
        exchange, so it must still be labelable. Anchoring on the ANSWER's index
        rather than on a pair ordinal keeps the label independent of how either
        side pairs the conversation."""
        convo = [a("ghost", "50")]
        result = reconcile(convo, [row("exit.profit_target_pct", 50, 35)])
        assert result["labels"][0]["answer_index"] == 0

    def test_empty_inputs_are_not_an_error(self) -> None:
        assert reconcile([], [])["labels"] == []
        assert reconcile(None, None)["labels"] == []

    def test_counts_are_complete_enough_to_read(self) -> None:
        """V-204: the tally is read as a rate, so a bare miss count is
        unreadable without the total it came from."""
        result = reconcile([q("pt"), a("pt", "50")], [row("exit.profit_target_pct", 50, 35)])
        assert set(result["counts"]) == {
            "carried",
            "superseded",
            "unmatched",
            "suppressed",
            "unparseable",
        }
        assert result["counts"]["carried"] == 1


class TestStoredAsTelemetryOnly:
    """V-213/V-214. The reconciler still runs and still counts; nothing renders.

    The stored record carries COUNTS and no labels, and that absence is the
    contract rather than an omission. Keeping the matched labels out of the
    payload is what makes the markers unwritable instead of merely unwired: a
    future reader cannot switch them back on from stored data, because the data
    does not contain them. Re-enabling means recomputing, which is exactly the
    moment someone has to go and read why it was turned off.
    """

    def _record(self, conversation, what_changed):
        import json

        from app.api.provenance import creation_record

        return json.loads(
            creation_record(
                {"source": "text", "prompt": "sell puts", "conversation": conversation},
                "user",
                "parent123",
                None,
                what_changed=what_changed,
            )
        )

    def test_the_match_is_counted_and_no_label_is_stored(self) -> None:
        record = self._record(
            [q("pt", "Profit target?"), a("pt", "50")],
            [row("exit.profit_target_pct", 50, 35)],
        )
        assert record["reconcile_telemetry"]["counts"]["superseded"] == 1
        assert "labels" not in record["reconcile_telemetry"]
        assert "reconciliation" not in record, (
            "the old render-input key must be gone, not merely unread"
        )

    def test_v215_the_absence_is_the_contract(self) -> None:
        """A variant editing a field the parser explicitly asked about renders NO
        marker, telemetry records the match, and the changed field still appears
        in WHAT CHANGED under its human label.

        Same reasoning as the no-run-row test on V-09: what must be guaranteed is
        an absence, so the absence gets a test. Without this, the next person to
        read `reconcile` and see a perfectly good match sitting unused will
        helpfully wire it to the screen.
        """
        record = self._record(
            [q("pt", "What profit target should I use?"), a("pt", "50")],
            [row("exit.profit_target_pct", 50, 35)],
        )

        # the match happened, as instrumentation
        assert record["reconcile_telemetry"]["counts"]["superseded"] == 1
        # nothing anywhere in the record can drive a per-exchange marker
        blob = json.dumps(record)
        assert "answer_index" not in blob
        assert '"state"' not in blob
        # and the edit is still visible, under its label, in the diff
        assert record["what_changed"] == [
            {
                "field": "exit.profit_target_pct",
                "parent": 50,
                "variant": 35,
                "label": "profit target",
            }
        ]

    def test_a_non_variant_run_has_no_telemetry(self) -> None:
        record = self._record([q("pt"), a("pt", "50")], None)
        assert "reconcile_telemetry" not in record
        assert "labeling" not in record

    def test_counts_are_computed_over_the_stored_events_only(self) -> None:
        """V-201's uniqueness is evaluated ONCE, over exactly the events that
        were stored. The previous version computed it twice, before and after
        truncation, and the second pass re-ran uniqueness over a smaller set: a
        deliberate suppression could flip into a confident match because the
        answer that made it ambiguous had been dropped. Counting once closes the
        window."""
        conversation = [
            {"kind": "note", "text": "not an exchange"},
            q("pt", "Profit target?"),
            a("pt", "50"),
            q("sl", "Stop loss?"),
            a("sl", "50"),
        ]
        record = self._record(conversation, [row("exit.profit_target_pct", 50, 35)])
        counts = record["reconcile_telemetry"]["counts"]
        assert counts["superseded"] == 0, "two answers of 50, one row: ambiguous"
        assert counts["suppressed"] == 2
        assert len(record["conversation"]) == 4, "the junk event is filtered"


class TestNonFiniteAnswersCannotCrashTheSubmitPath:
    """V-220. Named for the crash, not folded into a neighbouring test.

    `float()` accepts "inf", "-inf", "Infinity", "nan" and "1e400". Every one
    reached `_number_token`, which calls `int(number)`, which raises
    OverflowError or ValueError. That raise left `canonical_token`, `reconcile`
    and `creation_record` and surfaced as a 500 on POST /api/backtest.

    A user can type any of these into a clarifying answer. "1e400" is a typo, not
    an attack.

    Blast radius, confirmed rather than assumed: `variant.py` performs no writes
    at all, and `creation_record` is called at runs.py:793, BEFORE the ordinal
    retry loop, the credit debit and the run insert. So the crash cost a request
    and nothing else — no credit spent, no half-written run. That is why this is
    a 500 to fix rather than an incident to unwind.

    Introduced by A2: `canonical_token` does not exist on main.
    """

    @pytest.mark.parametrize(
        "answer", ["inf", "-inf", "Infinity", "INFINITY", "nan", "NaN", "1e400", "-1e400"]
    )
    def test_a_non_finite_answer_is_a_safe_miss(self, answer: str) -> None:
        assert canonical_token(answer) is None, (
            f"{answer!r} must canonicalize to None (a safe miss), never raise"
        )

    @pytest.mark.parametrize("answer", ["inf", "nan", "1e400"])
    def test_reconcile_survives_it(self, answer: str) -> None:
        result = reconcile(
            [q("x"), a("x", answer)], [row("exit.profit_target_pct", 50, 35)]
        )
        assert result["labels"] == []
        assert result["counts"]["unparseable"] == 1

    def test_finite_numbers_still_parse(self) -> None:
        """The fix must not reject real numbers: 1e308 is finite."""
        assert canonical_token("1e308") is not None
        assert canonical_token("50") == "50"
        assert canonical_token("-0.5") == "-0.5"
