"""UX Chunk A: run provenance — the setup story snapshotted at creation,
mechanics appended at completion, and READ-TIME derivation for rows that
predate the column. Derive-don't-fabricate (owner amendment 2026-07-14):
the clarifying conversation was never stored for old runs and must never
be invented; everything else is recovered from stored fields and marked
derived."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app import db
from app.api.provenance import (
    MAX_RECORD_BYTES,
    creation_record,
    derived_record,
)
from app.engine.market import build_fixture_store
from app.main import app
from tests.fixtures.engine import fx_short_put_assigned as fx


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("SKEPTIC_ACCESS_TOKEN", "")
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    import app.data.chains as chains_module

    monkeypatch.setattr(
        chains_module,
        "load_market_store",
        lambda ticker, **kw: build_fixture_store("SPY", fx.CHAINS, fx.UNDERLYING),
    )
    return TestClient(app)


CLIENT_PROVENANCE = {
    "v": 1,
    "source": "text",
    "prompt": {"text": "sell a put on SPY, exit at expiration"},
    "conversation": [
        {"kind": "question", "id": "exit", "question": "How should the position exit?",
         "options": ["50% profit", "hold to expiry"], "asked_at": "2026-07-14T14:00:00+00:00"},
        {"kind": "answer", "id": "exit", "answer": "hold to expiry",
         "answered_at": "2026-07-14T14:00:05+00:00"},
    ],
    "confirmed": {
        "draft": {"ticker": "SPY", "structure": "short_put", "dte": 11},
        "costs": {"commission_per_contract": 0.65},
        "untouched": True,
    },
}


class TestFreshRuns:
    def test_all_four_sections_stored_and_served(self, client: TestClient) -> None:
        r = client.post("/api/backtest",
                        json={"spec": fx.SPEC, "provenance": CLIENT_PROVENANCE})
        run_id = r.json()["run_id"]
        payload = client.get(f"/api/runs/{run_id}").json()
        assert payload["status"] == "done"
        prov = payload["provenance"]

        # section 1 — the prompt, verbatim
        assert prov["origin"] == "user"
        assert prov["source"] == "text"
        assert prov["prompt"]["text"] == "sell a put on SPY, exit at expiration"
        # section 2 — the conversation, in order, with timestamps
        assert [e["kind"] for e in prov["conversation"]] == ["question", "answer"]
        assert prov["conversation"][0]["options"] == ["50% profit", "hold to expiry"]
        assert prov["conversation"][0]["asked_at"]
        assert prov["conversation"][1]["answered_at"]
        # section 3 — the confirmed draft, not a re-parse
        assert prov["confirmed"]["draft"]["structure"] == "short_put"
        assert prov["confirmed"]["untouched"] is True
        # section 4 — measured mechanics, appended at completion
        mech = prov["mechanics"]
        assert mech["engine_s"] >= 0 and mech["gauntlet_s"] >= 0
        assert mech["effective_start"] and mech["effective_end"]
        assert mech["build"]["fill_model"] == "liquidity-v1"
        assert mech["build"]["spec_version"] == 1
        # a fresh record is the stored truth, never a derivation
        assert "derived" not in prov
        assert prov["recorded_at"]

    def test_capture_less_user_run_still_marks_recording(self, client: TestClient) -> None:
        # a submitter that captured nothing (curl / old client): the record
        # exists (so "missing" cleanly means "predates the column") but has
        # NO conversation key — none was captured, none is invented
        r = client.post("/api/backtest", json={"spec": fx.SPEC})
        payload = client.get(f"/api/runs/{r.json()['run_id']}").json()
        prov = payload["provenance"]
        assert prov["origin"] == "user"
        assert "conversation" not in prov
        assert "derived" not in prov
        assert prov["mechanics"]["engine_s"] >= 0


class TestAutomaticRuns:
    def test_auto_unlock_gets_origin_record_and_ignores_client_blob(
        self, client: TestClient
    ) -> None:
        parent = client.post("/api/backtest", json={"spec": fx.SPEC}).json()["run_id"]
        r = client.post("/api/backtest", json={
            "spec": fx.SPEC,
            "origin": "auto_unlock",
            "parent_run_id": parent,
            "auto_note": "62 new sessions",
            # an automatic run has no conversation — a smuggled one is ignored
            "provenance": CLIENT_PROVENANCE,
        })
        payload = client.get(f"/api/runs/{r.json()['run_id']}").json()
        prov = payload["provenance"]
        assert prov["origin"] == "auto_unlock"
        assert prov["parent_run_id"] == parent
        assert "62 new sessions" in prov["note"]
        assert "conversation" not in prov and "prompt" not in prov
        assert prov["mechanics"]["engine_s"] >= 0

    def test_receipt_record(self) -> None:
        record = json.loads(creation_record(None, "receipt", "abc123"))
        assert record["origin"] == "receipt"
        assert record["parent_run_id"] == "abc123"
        assert "replay" in record["note"]


class TestCapsAndCorruption:
    def test_oversize_conversation_truncates_head_first_never_refuses(self) -> None:
        long_answer = "x" * 1_900
        conversation = [
            {"kind": "answer", "id": f"q{i}", "answer": long_answer,
             "answered_at": "2026-07-14T14:00:00+00:00"}
            for i in range(120)
        ]
        record = json.loads(creation_record(
            {**CLIENT_PROVENANCE, "conversation": conversation}, "user"))
        assert len(json.dumps(record).encode()) <= MAX_RECORD_BYTES
        kept = record["conversation"]
        assert kept, "truncation must keep the chronological head"
        assert kept[0]["id"] == "q0"
        assert record["truncated"]["dropped_events"] == 120 - len(kept)

    def test_the_cap_holds_on_a_VARIANT_record_too(self) -> None:
        """V-225: the fixture the test above was missing.

        The assertion above is correct and was under-powered: it calls
        creation_record with no `what_changed`, so it exercises the one path where
        a variant's extra keys cannot appear. The overflow it was meant to catch
        lived entirely on the variant path — labelled diff rows, the labeling
        tally and the reconcile telemetry were added AFTER the byte budget had
        been measured — and the suite stayed green while asserting the opposite.
        That is the same false-green shape as a suite that never clicks a card:
        the claim was right, the coverage did not reach the defect.

        So this drives the same cap through the variant path, at the size that
        overflowed: a conversation big enough to consume the whole budget, plus
        enough labelled rows to matter. Verified to FAIL against the pre-fix
        ordering (keys added after the envelope) and pass with it.
        """
        # 200 events of 240 characters, which is where the packing is TIGHTEST:
        # it fills the budget to within ~130 bytes, so a key written after the
        # budget was measured pushes the record over. The first version of this
        # fixture used 1,900-character answers and passed, because their leftover
        # slack was wider than the overflow — an under-powered test replacing an
        # under-powered test. Measured against the pre-fix code this configuration
        # exceeds the cap by 102 bytes; the size was found by sweeping, not chosen.
        conversation = [
            {"kind": "answer", "id": f"q{i}", "answer": "y" * 240,
             "answered_at": "2026-07-14T14:00:00+00:00"}
            for i in range(200)
        ]
        # every one of these gets a label from the V-208 table, so each row grows
        what_changed = [
            {"field": "exit.profit_target_pct", "parent": 50, "variant": 35},
            {"field": "exit.stop_loss_pct", "parent": 200, "variant": 150},
            {"field": "backtest.start", "parent": None, "variant": "2022-01-03"},
            {"field": "backtest.initial_capital", "parent": 25_000, "variant": 50_000},
            {"field": "position.expiration_selection.target_dte", "parent": 45, "variant": 30},
            {"field": "position.legs[0].strike_selection.value", "parent": 0.3, "variant": 0.2},
            {"field": "sizing.value", "parent": 1, "variant": 3},
            {"field": "costs.commission_per_contract", "parent": 0.65, "variant": 0.5},
        ]
        record = json.loads(creation_record(
            {**CLIENT_PROVENANCE, "conversation": conversation},
            "user", "parent123", None, what_changed=what_changed))

        size = len(json.dumps(record).encode())
        assert size <= MAX_RECORD_BYTES, (
            f"variant record is {size} bytes, over the {MAX_RECORD_BYTES} cap"
        )
        # and the variant keys the old test never saw are actually present, so
        # this cannot pass by accidentally exercising the non-variant path again
        assert record["labeling"]["rows"] == len(what_changed)
        assert record["reconcile_telemetry"]["counts"]["carried"] > 0
        assert all("label" in r for r in record["what_changed"])
        # and the packing really was tight, so a future edit that loosens the
        # fixture cannot quietly remove this test's power
        assert MAX_RECORD_BYTES - size < 400, (
            f"only {MAX_RECORD_BYTES - size} bytes of slack; this fixture must pack "
            "the budget tightly or it cannot catch a key added after the budget"
        )

    def test_null_optional_fields_never_become_the_string_none(self) -> None:
        # dict.get defaults don't fire on present-but-null keys — str(None)
        # would store the literal text "None" as a bar time or ticker
        record = json.loads(creation_record({
            "prompt": {"text": "x", "chart": {
                "ticker": None, "pins": [{"entry": None, "exit": None}]}},
            "conversation": [{"kind": "answer", "id": None, "answer": "ok"}],
        }, "user"))
        assert record["prompt"]["chart"]["ticker"] == ""
        assert record["prompt"]["chart"]["pins"][0] == {"entry": "", "exit": None}
        assert record["conversation"][0]["id"] == ""
        assert '"None"' not in json.dumps(record)

    def test_event_cap_counts_dropped(self) -> None:
        conversation = [
            {"kind": "answer", "id": f"q{i}", "answer": "ok"} for i in range(250)
        ]
        record = json.loads(creation_record(
            {"conversation": conversation}, "user"))
        assert len(record["conversation"]) == 200
        assert record["truncated"]["dropped_events"] == 50

    def test_corrupt_stored_record_never_500s_the_run_screen(
        self, client: TestClient
    ) -> None:
        run_id = client.post("/api/backtest", json={"spec": fx.SPEC}).json()["run_id"]
        with db.session() as s:
            run = s.get(db.Run, run_id)
            assert run is not None
            run.provenance_json = "{not json"
            s.commit()
        resp = client.get(f"/api/runs/{run_id}")
        assert resp.status_code == 200
        assert "provenance" not in resp.json()


class TestDerivedOldRuns:
    def test_pre_column_row_gets_read_time_derivation(self, client: TestClient) -> None:
        run_id = client.post("/api/backtest", json={"spec": fx.SPEC}).json()["run_id"]
        with db.session() as s:  # simulate a run stored before the column
            run = s.get(db.Run, run_id)
            assert run is not None
            run.provenance_json = None
            s.commit()
        prov = client.get(f"/api/runs/{run_id}").json()["provenance"]

        assert prov["derived"] is True
        assert "not captured" in prov["note"]
        # prompt recovered from the stored spec's verbatim text
        assert prov["prompt"]["text"] == "fixture"
        # the decision grid comes from spec_json and says so
        boxes = prov["confirmed"]["boxes"]
        assert prov["confirmed"]["derived"] is True
        assert boxes["ticker"] == "SPY" and boxes["structure"] == "short_put"
        assert boxes["exit"] == {"time_exit_dte": 0}
        assert boxes["costs"]["commission_per_contract"] == 0.65
        # mechanics recovered from perf_json + the stored honesty report
        mech = prov["mechanics"]
        assert mech["engine_s"] >= 0
        assert mech["effective_start"] and mech["effective_end"]
        # the conversation was never stored — it must never appear
        assert "conversation" not in prov

    def test_nothing_fabricated_when_sources_are_missing(self) -> None:
        prov = derived_record(
            {"underlying": {"ticker": "QQQ"}, "meta": {"description_raw": "   "}},
            None, None, None, None)
        assert prov["derived"] is True
        assert "prompt" not in prov  # blank description_raw is not a prompt
        assert "mechanics" not in prov  # no perf/stats → no invented numbers
        assert "conversation" not in prov
        assert prov["confirmed"]["boxes"]["ticker"] == "QQQ"

    def test_origin_and_parent_survive_derivation(self) -> None:
        prov = derived_record({}, None, None, "auto_unlock", "parent99")
        assert prov["origin"] == "auto_unlock"
        assert prov["parent_run_id"] == "parent99"
