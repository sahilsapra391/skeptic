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
