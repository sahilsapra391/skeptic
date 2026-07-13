"""Validator refusals must reach the user as JSON, never as plaintext 500s.

Every cross-field rule in the spec model raises ValueError inside a
model_validator, and pydantic keeps the live exception object in
errors()[].ctx. The custom HTTPException handler json.dumps's the detail,
so feeding it raw errors() crashed the handler itself and every honest
422 surfaced as `500: {}` in the UI (incident 2026-07-07: an RSI-ladder
spec with the SCANNING dial flipped to every_setup — the user never saw
"intraday_scan cannot combine with scale_in"). These tests pin the whole
path: raise site, handler, and the shape the frontend renders."""

import asyncio
import json

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.main import app, http_exception_handler

# The incident spec: a valid v4 intraday long call CARRYING a scale-in
# ladder AND intraday_scan — refused by _intraday_scan_constraints, which
# is a model_validator ValueError (the ctx-poisoning class).
LADDER_PLUS_SCAN_SPEC = {
    "spec_version": 4,
    "meta": {"name": "SPY 1DTE RSI ladder", "description_raw": "incident repro"},
    "underlying": {"ticker": "SPY"},
    "position": {
        "structure": "long_call",
        "legs": [
            {
                "right": "call",
                "side": "long",
                "ratio": 1,
                "strike_selection": {"method": "delta", "value": 0.5},
            }
        ],
        "expiration_selection": {"target_dte": 1, "min_dte": 0, "max_dte": 16},
    },
    "entry": {
        "schedule": {"frequency": "signal_only", "day_of_week": None},
        "conditions": [],
        "max_concurrent_positions": 5,
        "intraday_scan": "every_setup",
        "scale_in": {
            "mode": "signal_ladder",
            "basket": True,
            "rungs": [
                {"indicator": "rsi", "operator": "<=", "value": 30, "period": 14,
                 "timeframe": "5min", "add_contracts": 2},
                {"indicator": "rsi", "operator": "<=", "value": 25, "period": 14,
                 "timeframe": "5min", "add_contracts": 3},
            ],
            "rearm": {"indicator": "rsi", "operator": ">", "value": 30, "period": 14,
                      "timeframe": "5min"},
            "max_total_contracts": 20,
        },
    },
    "exit": {"profit_target_pct": 40},
    "sizing": {"method": "fixed_contracts", "value": 1},
    "costs": {"commission_per_contract": 0.65, "slippage_half_spread_fraction": 0.5,
                                               "slippage_half_spread_fraction_sell": 0.5},
    "backtest": {"start": "2023-07-07", "end": None, "initial_capital": 25000,
                 "seed": 42, "clock": "5min"},
}


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.delenv("SKEPTIC_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    return TestClient(app, raise_server_exceptions=False)


def test_validator_refusal_is_json_422(client: TestClient) -> None:
    r = client.post("/api/backtest", json={"spec": LADDER_PLUS_SCAN_SPEC})
    assert r.status_code == 422, r.text  # was 500 text/plain before the fix
    assert r.headers["content-type"].startswith("application/json")
    detail = r.json()["detail"]
    assert isinstance(detail, list) and detail
    msgs = " ".join(str(e.get("msg")) for e in detail)
    assert "cannot combine" in msgs  # the explanation actually reaches the user
    # ctx (and the live exception inside it) must be stripped at the raise site
    assert all("ctx" not in e for e in detail)


def test_validator_refusal_round_trips_as_json(client: TestClient) -> None:
    r = client.post("/api/backtest", json={"spec": LADDER_PLUS_SCAN_SPEC})
    json.loads(r.text)  # the frontend's res.json() path — plaintext would raise


def test_handler_survives_non_serializable_detail() -> None:
    """Defense in depth: even if a future raise site leaks a live exception
    into detail, the handler must degrade to JSON, never crash to plaintext."""
    exc = HTTPException(status_code=422, detail=[{"ctx": {"error": ValueError("boom")}}])
    resp = asyncio.run(http_exception_handler(None, exc))  # type: ignore[arg-type]
    assert resp.status_code == 422
    assert resp.media_type == "application/json"
    json.loads(resp.body)  # serializable, whatever the fallback chose
