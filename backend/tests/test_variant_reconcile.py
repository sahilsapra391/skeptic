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

from app.api.variant import reconcile


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


class TestStoredInProvenance:
    """The reconciliation is computed at creation and stored, never recomputed
    per page view (V-13's rule: no later reader redoes the comparison)."""

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

    def test_the_record_carries_the_labels(self) -> None:
        record = self._record(
            [q("pt", "Profit target?"), a("pt", "50")],
            [row("exit.profit_target_pct", 50, 35)],
        )
        assert record["reconciliation"]["labels"][0]["field"] == "exit.profit_target_pct"
        assert record["reconciliation"]["counts"]["superseded"] == 1

    def test_a_non_variant_run_has_no_reconciliation(self) -> None:
        """Nothing to reconcile against, so the key is absent rather than empty.
        A renderer keying off its presence must not see one on a fresh run."""
        record = self._record([q("pt"), a("pt", "50")], None)
        assert "reconciliation" not in record

    def test_the_index_refers_to_the_conversation_AS_STORED(self) -> None:
        """The regression this design exists to prevent.

        `_clean_conversation` drops events that are neither question nor answer,
        so an index taken from the client's raw list points at a different event
        once anything is filtered. Here a junk event sits at raw index 0, which
        shifts the answer from raw index 2 to stored index 1. A label saying 2
        would attach the SUPERSEDED marker to the wrong card, which is a false
        statement about the user's own history.
        """
        conversation = [
            {"kind": "note", "text": "not an exchange"},
            q("pt", "Profit target?"),
            a("pt", "50"),
        ]
        record = self._record(conversation, [row("exit.profit_target_pct", 50, 35)])

        stored = record["conversation"]
        assert len(stored) == 2, "the junk event should have been filtered"
        index = record["reconciliation"]["labels"][0]["answer_index"]
        assert index == 1
        assert stored[index]["kind"] == "answer"
        assert stored[index]["answer"] == "50"
