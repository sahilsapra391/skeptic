"""API surface tests: health is live, data routes refuse honestly without R2,
run routes are explicit 501s (nothing pretends to be an engine), and the
bearer middleware guards everything but health."""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.test_spec_roundtrip import CANONICAL


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    for var in (
        "SKEPTIC_ACCESS_TOKEN",
        "OPENROUTER_API_KEY",  # hermetic: never live LLM calls from tests
        "R2_ACCOUNT_ID",
        "R2_ACCESS_KEY_ID",
        "R2_SECRET_ACCESS_KEY",
        "R2_BUCKET",
    ):
        monkeypatch.delenv(var, raising=False)
    return TestClient(app)


def test_health(client: TestClient) -> None:
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["r2_configured"] is False


def test_coverage_refuses_without_r2(client: TestClient) -> None:
    r = client.get("/api/data/coverage")
    assert r.status_code == 503
    assert "never" in r.json()["detail"]  # coverage is never faked


def test_underlying_refuses_without_r2(client: TestClient) -> None:
    assert client.get("/api/data/underlying/SPY").status_code == 503
    assert client.get("/api/data/underlying/TSLA").status_code == 404


def test_unbuilt_routes_are_explicit_501(client: TestClient) -> None:
    # parser is M4; standalone sweeps await the compare UI. Grounded ask is
    # LIVE now — an unknown run is a plain 404, never an invented answer.
    assert client.post("/api/parse", json={"text": "sell a put"}).status_code == 501
    assert client.post("/api/runs/abc/ask", json={"question": "?"}).status_code == 404
    assert client.post("/api/sweep", json={}).status_code == 501


def test_unknown_run_is_404(client: TestClient) -> None:
    assert client.get("/api/runs/nope").status_code == 404


def test_backtest_validates_spec(client: TestClient) -> None:
    bad = {**CANONICAL, "underlying": {"ticker": "TSLA"}}
    r = client.post("/api/backtest", json={"spec": bad})
    assert r.status_code == 422


def test_bearer_token_enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SKEPTIC_ACCESS_TOKEN", "sekrit")
    client = TestClient(app)
    assert client.get("/api/health").status_code == 200  # health stays open
    assert client.get("/api/runs").status_code == 401
    assert (
        client.get("/api/runs", headers={"Authorization": "Bearer sekrit"}).status_code == 200
    )
