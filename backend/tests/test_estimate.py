"""Pre-run time estimates (2026-07-06): session counts are real coverage,
time estimates are MEDIANS of measured runs (runs.perf_json) — an
unmeasured clock answers null and says the first run calibrates. Never an
invented number."""

from __future__ import annotations

import json
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app import db
from app.engine.market import build_fixture_store
from app.main import app
from tests.fixtures.engine import fx_short_put_assigned as fx


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("SKEPTIC_ACCESS_TOKEN", "")
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    import app.data.chains as chains_module

    # a store whose chain dates straddle the 1y cutoff: 300 recent
    # sessions + 300 older-than-a-year sessions
    today = date.today()
    chains = {}
    underlying = {}
    for i in range(300):
        d = (today - timedelta(days=i)).isoformat()
        chains[d] = list(fx.CHAINS[fx.ENTRY])
        underlying[d] = (100.0, 100.0)
    for i in range(300):
        d = (today - timedelta(days=400 + i)).isoformat()
        chains[d] = list(fx.CHAINS[fx.ENTRY])
        underlying[d] = (100.0, 100.0)
    monkeypatch.setattr(
        chains_module,
        "load_market_store",
        lambda ticker, **kw: build_fixture_store("SPY", chains, underlying),
    )
    return TestClient(app)


def _seed_perf(n_runs: int, clock: str, s_per_session: float, sessions: int) -> list[str]:
    ids = []
    with db.session() as s:
        for i in range(n_runs):
            rid = f"perf-seed-{clock}-{i}"
            s.add(db.Run(
                id=rid, status="done", stage=6, seed=42,
                spec_json=json.dumps(fx.SPEC),
                perf_json=json.dumps({
                    "clock": clock,
                    "sessions": sessions,
                    "engine_s": s_per_session * sessions / 2,
                    "gauntlet_s": s_per_session * sessions / 2,
                    "conditions": False,
                }),
            ))
            ids.append(rid)
        s.commit()
    return ids


def _cleanup(ids: list[str]) -> None:
    with db.session() as s:
        for rid in ids:
            row = s.get(db.Run, rid)
            if row is not None:
                s.delete(row)
        s.commit()


class TestEstimate:
    def test_unmeasured_clock_is_honest(self, client: TestClient) -> None:
        # other test files may have completed 5min fixture runs in this
        # shared DB — clear their measurements so "unmeasured" is true
        # regardless of suite ordering
        with db.session() as s:
            for row in s.query(db.Run).filter(db.Run.perf_json.isnot(None)).all():
                row.perf_json = None
            s.commit()
        r = client.get("/api/data/estimate?ticker=SPY&clock=5min")
        # no 5min runs have perf rows in this test DB — estimates are null
        # and the basis says so; 5min needs the intraday store though, so
        # accept a 503 when the fixture env has no R2 (honest refusal)
        if r.status_code == 200:
            body = r.json()
            assert body["basis"]["measured_runs"] == 0
            assert "first run calibrates" in body["basis"]["note"]
            assert all(o["est_seconds"] is None for o in body["options"])
        else:
            assert r.status_code == 503

    def test_sessions_counted_per_window_and_estimates_from_medians(
        self, client: TestClient
    ) -> None:
        ids = _seed_perf(4, "daily", s_per_session=0.5, sessions=200)
        try:
            r = client.get("/api/data/estimate?ticker=SPY&clock=daily")
            assert r.status_code == 200
            body = r.json()
            opts = {o["key"]: o for o in body["options"]}
            # 300 sessions inside the last year; all 600 in "all"
            assert opts["1y"]["sessions"] == 300
            assert opts["all"]["sessions"] == 600
            # 0.5 s/session medians → 150s for 1y, 300s for all
            assert opts["1y"]["est_seconds"] == pytest.approx(150, abs=2)
            assert opts["all"]["est_seconds"] == pytest.approx(300, abs=2)
            assert body["basis"]["measured_runs"] == 4
        finally:
            _cleanup(ids)

    def test_rates_do_not_cross_clocks(self, client: TestClient) -> None:
        # a wildly slow 5min history must not pollute daily estimates
        ids = _seed_perf(3, "5min", s_per_session=10.0, sessions=100)
        ids += _seed_perf(3, "daily", s_per_session=0.1, sessions=100)
        try:
            r = client.get("/api/data/estimate?ticker=SPY&clock=daily")
            body = r.json()
            opts = {o["key"]: o for o in body["options"]}
            assert opts["1y"]["est_seconds"] == pytest.approx(30, abs=2)  # 300 × 0.1
        finally:
            _cleanup(ids)

    def test_unknown_ticker_404(self, client: TestClient) -> None:
        assert client.get("/api/data/estimate?ticker=TSLA").status_code == 404


class TestPerfRecorded:
    def test_completed_run_stores_measured_cost(self, client: TestClient) -> None:
        import app.data.chains as chains_module  # noqa: F401 — patched in fixture

        run_id = client.post("/api/backtest", json={"spec": fx.SPEC}).json()["run_id"]
        client.get(f"/api/runs/{run_id}")
        with db.session() as s:
            row = s.get(db.Run, run_id)
            assert row is not None and row.perf_json
            perf = json.loads(row.perf_json)
            assert perf["clock"] == "daily"
            assert perf["sessions"] > 0
            assert perf["engine_s"] >= 0 and perf["gauntlet_s"] >= 0
