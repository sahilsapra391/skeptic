"""M2 happy path: POST /api/backtest → engine runs → GET /api/runs/{id}
returns the verdict-withheld payload with REAL numbers. The lake is
replaced by the assigned-short-put fixture store, so the whole API path
runs hermetically and the payload numbers are hand-verifiable."""

import pytest
from fastapi.testclient import TestClient

from app.engine.market import build_fixture_store
from app.main import app
from tests.fixtures.engine import fx_short_put_assigned as fx


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.delenv("SKEPTIC_ACCESS_TOKEN", raising=False)
    import app.data.chains as chains_module

    monkeypatch.setattr(
        chains_module,
        "load_market_store",
        lambda ticker: build_fixture_store("SPY", fx.CHAINS, fx.UNDERLYING),
    )
    # TestClient executes BackgroundTasks synchronously after the response
    return TestClient(app)


def test_backtest_happy_path(client: TestClient) -> None:
    r = client.post("/api/backtest", json={"spec": fx.SPEC})
    assert r.status_code == 200
    body = r.json()
    assert body["demo"] is False
    run_id = body["run_id"]

    payload = client.get(f"/api/runs/{run_id}").json()
    assert payload["status"] == "done"
    assert payload["demo"] is False
    # the run is real but UNBLESSED until M3 — refusal state by design
    assert payload["verdict"]["refusal"] is True
    assert "M3" in payload["verdict"]["headline"]
    # real numbers from the fixture math (final equity 9,654.35 on 10,000)
    assert payload["equitySeries"][-1]["v"] == pytest.approx(9654.35, abs=0.005)
    assert "1 filled" in payload["tradeHeader"]
    actions = [t["a"] for t in payload["trades"]]
    assert "ASSIGN" in actions and "OPEN" in actions

    listing = client.get("/api/runs").json()
    assert listing["demo"] is False
    assert any(item["id"] == run_id for item in listing["runs"])


def test_run_error_is_surfaced(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    import app.data.chains as chains_module

    def boom(ticker: str):
        raise RuntimeError("lake unreachable")

    monkeypatch.setattr(chains_module, "load_market_store", boom)
    r = client.post("/api/backtest", json={"spec": fx.SPEC})
    run_id = r.json()["run_id"]
    payload = client.get(f"/api/runs/{run_id}").json()
    assert payload["status"] == "error"
    assert "lake unreachable" in payload["error"]


def test_seed_override_recorded(client: TestClient) -> None:
    r = client.post("/api/backtest", json={"spec": fx.SPEC, "seed": 7})
    run_id = r.json()["run_id"]
    payload = client.get(f"/api/runs/{run_id}").json()
    assert "seed 7" in payload["meta"]
