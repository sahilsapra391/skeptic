"""D3b: the auto-unlock queue — the brief's acceptance ("an
insufficient_evidence run demonstrably auto-upgrades after a simulated
coverage delta") plus the owner's scoring-policy line: auto re-runs NEVER
bump the family trial counter (same spec + more data ≠ a new try)."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app import db
from app.engine.market import build_fixture_store
from app.main import app
from tests.fixtures.engine import fx_short_put_assigned as fx


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    # Empty, not deleted — the in-test import of scripts/nightly_improve
    # runs load_local_env(), whose setdefault would resurrect a DELETED
    # key mid-test (real LLM calls); an empty var survives setdefault.
    monkeypatch.setenv("SKEPTIC_ACCESS_TOKEN", "")
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    import app.data.chains as chains_module

    monkeypatch.setattr(
        chains_module,
        "load_market_store",
        lambda ticker, **kw: build_fixture_store("SPY", fx.CHAINS, fx.UNDERLYING),
    )
    return TestClient(app)


def _family_trials() -> int:
    with db.session() as s:
        row = s.get(db.TrialCounter, "SPY:short_put")
        return row.trials if row else 0


class TestAutoUnlockRun:
    def test_auto_rerun_inherits_trials_and_marks_lineage(
        self, client: TestClient
    ) -> None:
        # 1) a REFUSED parent run (the fixture store's single trade always
        #    refuses) — stores unlock_json + its trial count
        r = client.post("/api/backtest", json={"spec": fx.SPEC})
        parent_id = r.json()["run_id"]
        parent_payload = client.get(f"/api/runs/{parent_id}").json()
        assert parent_payload["verdict"]["refusal"] is True
        with db.session() as s:
            parent = s.get(db.Run, parent_id)
            assert parent is not None and parent.unlock_json is not None
            parent_trials = json.loads(parent.stats_json)["honesty_report"]["dsr"]["trials"]
        trials_before = _family_trials()

        # 2) the simulated coverage delta → the nightly queue re-runs it
        r2 = client.post("/api/backtest", json={
            "spec": fx.SPEC,
            "origin": "auto_unlock",
            "parent_run_id": parent_id,
            "auto_note": "62 new sessions",
        })
        child_id = r2.json()["run_id"]
        child_payload = client.get(f"/api/runs/{child_id}").json()
        assert child_payload["status"] == "done"

        # scoring policy: NO trial bump, parent's count inherited
        assert _family_trials() == trials_before
        assert f"trials {parent_trials}" in child_payload["meta"]
        assert "auto-upgraded (62 new sessions)" in child_payload["meta"]

        # lineage markers, both directions
        with db.session() as s:
            child = s.get(db.Run, child_id)
            assert child is not None
            assert child.origin == "auto_unlock"
            assert child.parent_run_id == parent_id
            child_summary = json.loads(child.summary_json)
            assert child_summary["upgradeOf"] == parent_id
            assert "62 new sessions" in child_summary["autoNote"]
            parent = s.get(db.Run, parent_id)
            assert parent is not None
            assert json.loads(parent.summary_json)["supersededBy"] == child_id

    def test_human_runs_still_bump(self, client: TestClient) -> None:
        before = _family_trials()
        client.post("/api/backtest", json={"spec": fx.SPEC})
        assert _family_trials() == before + 1

    def test_unknown_origin_rejected(self, client: TestClient) -> None:
        r = client.post("/api/backtest", json={"spec": fx.SPEC, "origin": "hacker"})
        assert r.status_code == 422


class TestExecuteUnlocks:
    def test_cap_and_readiness_respected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import requests

        from scripts import nightly_improve as ni

        db.init_db()
        with db.session() as s:  # spec rows the executor fetches
            for i in range(5):
                s.merge(db.Run(id=f"exec{i}", status="done",
                               spec_json=json.dumps(fx.SPEC)))
            s.commit()

        decisions = [
            ni.UnlockDecision(run_id=f"exec{i}", ticker="SPY", clock="daily",
                              new_sessions=50, reason="test", should_rerun=(i < 4))
            for i in range(5)
        ]
        posted: list[dict] = []

        def fake_post(url, json=None, headers=None, timeout=None):  # noqa: A002
            posted.append({"url": url, "body": json})

            class R:
                status_code = 200

                @staticmethod
                def json() -> dict:
                    return {"run_id": "new"}

            return R()

        monkeypatch.setenv("SKEPTIC_API_URL", "http://api.test")
        monkeypatch.setenv("SKEPTIC_ACCESS_TOKEN", "tok")
        monkeypatch.setattr(requests, "post", fake_post)

        submitted = ni.execute_unlocks(decisions)
        # 4 ready, but the nightly cap is 3 — and the not-ready one never posts
        assert submitted == ni.AUTO_RERUNS_PER_NIGHT == 3
        assert len(posted) == 3
        assert all(p["body"]["origin"] == "auto_unlock" for p in posted)
        assert all(p["body"]["auto_note"] == "50 new sessions" for p in posted)
        assert {p["body"]["parent_run_id"] for p in posted} == {"exec0", "exec1", "exec2"}

        with db.session() as s:  # cleanup
            s.query(db.Run).filter(db.Run.id.like("exec%")).delete(
                synchronize_session=False)
            s.commit()
