"""D3c: verdict receipts — a daily verdict faces its 5-minute replay.
The receipt attaches to the ORIGINAL run at read time; the stored verdict
is never rewritten (owner amendment 4). Replays are origin=receipt runs
and never bump the family trial counter."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app import db
from app.api.replay import (
    RECEIPT_WORSE_DELTA,
    build_receipt,
    build_replay_spec,
    replay_eligible_spec,
)
from app.engine.market import build_fixture_slice, build_fixture_store
from app.main import app
from tests.fixtures.engine import fx_short_put_assigned as fx
from tests.fixtures.engine.common import make_spec, put
from tests.test_five_min_clock import FixtureIntraday, _put

# Short-tenor fixture: the ONLY specs that can face the 5-minute record
# like-for-like are those whose whole tenor band fits the intraday slice
# (max_dte ≤ 2). Entry Monday, expiry Tuesday, put finishes OTM.
R_ENTRY = "2025-01-06"
R_EXPIRY = "2025-01-07"
R_CHAINS = {R_ENTRY: [put(100.0, 2.00, 2.20, -0.30, R_EXPIRY)]}
R_UNDERLYING = {R_ENTRY: (100.0, 100.0), R_EXPIRY: (101.0, 102.0)}
R_SPEC = make_spec(
    position={
        "structure": "short_put",
        "legs": [
            {"right": "put", "side": "short", "ratio": 1,
             "strike_selection": {"method": "delta", "value": 0.30}}
        ],
        "expiration_selection": {"target_dte": 1, "min_dte": 1, "max_dte": 2},
    },
    entry={"schedule": {"frequency": "weekly", "day_of_week": "monday"},
           "conditions": [], "max_concurrent_positions": 1},
    exit={"time_exit_dte": 0},
    backtest={"start": R_ENTRY, "end": R_EXPIRY,
              "initial_capital": 10_000, "seed": 42},
)


class TestReplaySpecRules:
    def test_eligibility(self) -> None:
        assert replay_eligible_spec(R_SPEC)  # daily, band 1–2 fits the slice
        # a WIDE band (min 1, target 11, max 30) is NOT eligible: its daily
        # run trades 11–16 DTE while a replay would trade ≤2 — apples to
        # oranges dressed as a like-for-like receipt
        assert not replay_eligible_spec(fx.SPEC)
        long_tenor = json.loads(json.dumps(R_SPEC))
        long_tenor["position"]["expiration_selection"] = {
            "target_dte": 45, "min_dte": 30, "max_dte": 60}
        assert not replay_eligible_spec(long_tenor)
        already_5min = json.loads(json.dumps(R_SPEC))
        already_5min["backtest"]["clock"] = "5min"
        assert not replay_eligible_spec(already_5min)

    def test_mutation_is_minimal(self) -> None:
        replay = build_replay_spec(R_SPEC)
        assert replay["backtest"]["clock"] == "5min"
        assert replay["spec_version"] == 2
        assert replay["position"] == R_SPEC["position"]  # strategy untouched
        assert R_SPEC["backtest"].get("clock", "daily") == "daily"  # no aliasing

    def test_worse_definition(self) -> None:
        def receipt(daily: float | None, five: float | None) -> dict:
            return build_receipt(
                "r1",
                {"metrics": {"sharpe": daily, "total_return": 0.1}},
                {"metrics": {"sharpe": five, "total_return": 0.05}},
                {"fillSources": {"ivol_5min": 10}},
                "2026-07-05T00:00:00Z",
            )

        assert not receipt(1.0, 0.9)["worse"]  # within tolerance
        assert receipt(1.0, 1.0 - RECEIPT_WORSE_DELTA - 0.01)["worse"]
        assert receipt(0.3, -0.1)["worse"]  # sign flip
        assert not receipt(None, 1.0)["worse"]  # unknowns never accuse


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    # Empty, not deleted: scripts/nightly_improve.py runs load_local_env()
    # at import (its db engine binds at import, so env must load first),
    # and setdefault would RESURRECT a deleted key mid-test — turning the
    # template-verdict path into real 100-second LLM calls. An empty var
    # survives setdefault and still reads as "not configured".
    monkeypatch.setenv("SKEPTIC_ACCESS_TOKEN", "")
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    import app.data.chains as chains_module
    import app.data.intraday as intraday_module

    monkeypatch.setattr(
        chains_module,
        "load_market_store",
        lambda ticker, **kw: build_fixture_store("SPY", R_CHAINS, R_UNDERLYING),
    )
    slc = build_fixture_slice(
        R_ENTRY,
        quotes={"09:30": [_put(2.00, 2.20, -0.50, R_EXPIRY)]},
        underlying={"09:30": 100.0},
    )
    monkeypatch.setattr(
        intraday_module,
        "load_intraday_store",
        lambda ticker: FixtureIntraday({R_ENTRY: slc}, max_dte=2),
    )
    return TestClient(app)


class TestReplayEndpoint:
    def test_full_receipt_loop(self, client: TestClient) -> None:
        # 1) the daily original
        parent_id = client.post("/api/backtest", json={"spec": R_SPEC}).json()["run_id"]
        parent_payload = client.get(f"/api/runs/{parent_id}").json()
        assert parent_payload["replayEligible"] is True
        assert "receipts" not in parent_payload

        with db.session() as s:
            row = s.get(db.TrialCounter, "SPY:short_put")
            trials_before = row.trials if row else 0

        # 2) on-demand replay (owner amendment 1's button path)
        r = client.post(f"/api/runs/{parent_id}/replay")
        assert r.status_code == 200
        replay_id = r.json()["run_id"]
        replay_payload = client.get(f"/api/runs/{replay_id}").json()
        assert replay_payload["status"] == "done"
        assert replay_payload["clock"] == "5min"

        # 3) the receipt attached to the ORIGINAL, verdict untouched
        parent_after = client.get(f"/api/runs/{parent_id}").json()
        receipts = parent_after.get("receipts")
        assert receipts and receipts[0]["replay_run_id"] == replay_id
        assert "five_min_sharpe" in receipts[0]
        assert "fill_sources" in receipts[0]
        assert parent_after["verdict"] == parent_payload["verdict"]  # NEVER rewritten

        # 4) replays don't bump the family trials (mechanical, not a try)
        with db.session() as s:
            row = s.get(db.TrialCounter, "SPY:short_put")
            assert (row.trials if row else 0) == trials_before
            child = s.get(db.Run, replay_id)
            assert child is not None and child.origin == "receipt"
            assert json.loads(child.summary_json)["autoNote"] == "5-min replay (verdict receipt)"

    def test_ineligible_spec_409(self, client: TestClient) -> None:
        long_spec = json.loads(json.dumps(R_SPEC))
        long_spec["position"]["expiration_selection"] = {
            "target_dte": 45, "min_dte": 30, "max_dte": 60}
        parent_id = client.post("/api/backtest", json={"spec": long_spec}).json()["run_id"]
        client.get(f"/api/runs/{parent_id}")  # ensure done
        r = client.post(f"/api/runs/{parent_id}/replay")
        assert r.status_code == 409
        assert "not replayable" in r.json()["detail"]


class TestEligibleForReceipt:
    def test_scans_only_bare_user_daily_runs(self, client: TestClient) -> None:
        from scripts import nightly_improve as ni

        parent_id = client.post("/api/backtest", json={"spec": R_SPEC}).json()["run_id"]
        client.get(f"/api/runs/{parent_id}")
        assert parent_id in ni.eligible_for_receipt()

        # once receipted, it drops out (incremental after night one)
        replay_id = client.post(f"/api/runs/{parent_id}/replay").json()["run_id"]
        client.get(f"/api/runs/{replay_id}")
        eligible = ni.eligible_for_receipt()
        assert parent_id not in eligible
        assert replay_id not in eligible  # receipt runs never receipt themselves
