"""Bars route contract: honest refusals and validation (data-path tests that
need the lake run against the live R2 in dev, not in CI)."""

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    for var in (
        "SKEPTIC_ACCESS_TOKEN",
        "R2_ACCOUNT_ID",
        "R2_ACCESS_KEY_ID",
        "R2_SECRET_ACCESS_KEY",
        "R2_BUCKET",
        "APCA_API_KEY_ID",
        "APCA_API_SECRET_KEY",
    ):
        monkeypatch.delenv(var, raising=False)
    return TestClient(app)


def test_bad_ticker_404(client: TestClient) -> None:
    assert client.get("/api/data/bars/TSLA").status_code == 404


def test_tick_interval_rejected_with_honest_reason(client: TestClient) -> None:
    r = client.get("/api/data/bars/SPY?interval=1t")
    assert r.status_code == 422
    assert "tick" in r.json()["detail"]


def test_bad_window_rejected(client: TestClient) -> None:
    assert client.get("/api/data/bars/SPY?interval=5m&window=2decades").status_code == 422


def test_refuses_without_r2(client: TestClient) -> None:
    r = client.get("/api/data/bars/SPY?interval=5m&window=1w")
    assert r.status_code == 503
