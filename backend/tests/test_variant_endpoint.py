"""GET /api/runs/{id}/variant — the projection that reopens a stored run.

Covers V-03 (refused runs included), V-09 (costs nothing), V-28 (pre-provenance
runs project from spec_json alone), V-34/V-35 (costs and seed inherit from the
parent), V-50/V-133 (the three window states) and V-128 (tier c refuses with a
real reason).
"""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app import db
from app.main import app

from .test_spec_roundtrip import CANONICAL

EFFECTIVE = {"effective_start": "2023-04-03", "effective_end": "2026-07-17"}


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """HARNESS FACT 1: the /api gate in app/main.py opens only when
    SKEPTIC_ACCESS_TOKEN is ABSENT (auth.gate_allows returns True immediately
    on an unset token). A stray token in the environment — from a .env, or
    exported in the shell — turns every request in this file into a bare 401
    with no hint about why. Deleting it is what test_runs_api does too.

    The runs stored below are unowned (user_id NULL), which
    _enforce_run_access deliberately leaves reachable by id: that is how an
    anonymous device revisits its own run."""
    monkeypatch.delenv("SKEPTIC_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    return TestClient(app)


def _store(
    run_id: str,
    spec: dict[str, Any],
    provenance: dict[str, Any] | None = None,
    stats: dict[str, Any] | None = None,
) -> str:
    with db.session() as s:
        s.query(db.Run).filter(db.Run.id == run_id).delete()
        s.add(
            db.Run(
                id=run_id,
                status="done",
                spec_json=json.dumps(spec),
                provenance_json=json.dumps(provenance) if provenance else None,
                stats_json=json.dumps(stats) if stats else None,
            )
        )
        # HARNESS FACT 2, and the one that will cost you an hour: db.session()
        # returns a RAW SQLAlchemy Session. Its __exit__ closes and rolls back;
        # it does not commit. A fixture that forgets this stores nothing, and
        # the endpoint then answers "run <id> not found" — a 404 that reads as
        # an endpoint bug and sends you debugging the wrong file entirely.
        s.commit()
    return run_id


def _spec_with_window(start: str | None) -> dict[str, Any]:
    spec = copy.deepcopy(CANONICAL)
    spec["backtest"] = {**spec["backtest"], "start": start, "end": None}
    return spec


def _prov(window: dict[str, Any] | None, conversation: list[Any] | None = None) -> dict[str, Any]:
    return {
        "v": 1,
        "prompt": {"text": "sell a 30 delta put on SPY"},
        "conversation": conversation or [],
        "confirmed": {"draft": {"window": window}} if window is not None else {},
    }


def test_projects_a_pre_provenance_run_from_spec_json_alone(client: TestClient) -> None:
    """V-28: no stored draft is the MAJORITY case in production (73 of 99), so
    this path is primary, not a fallback."""
    rid = _store("varsrc1", _spec_with_window(None), provenance=None,
                 stats={"honesty_report": EFFECTIVE})
    r = client.get(f"/api/runs/{rid}/variant")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["tier"] == "a"
    assert body["draft"]["ticker"] == "SPY"
    # V-28: the prompt DERIVES from description_raw, and the conversation that
    # was never stored is not invented
    assert body["conversation"] == []
    assert body["draft"]["quote"] == CANONICAL["meta"]["description_raw"]


def test_costs_and_seed_inherit_from_the_parent(client: TestClient) -> None:
    """V-34 / V-35: never from the copier's current Settings."""
    spec = _spec_with_window("2024-01-01")
    spec["costs"] = {
        "commission_per_contract": 1.11,
        "slippage_half_spread_fraction": 0.42,
        "slippage_half_spread_fraction_sell": 0.44,
    }
    spec["backtest"]["seed"] = 777
    rid = _store("varsrc2", spec)
    draft = client.get(f"/api/runs/{rid}/variant").json()["draft"]
    assert draft["costs"] == spec["costs"]
    assert draft["seed"] == 777


def test_window_carried_when_the_spec_names_dates(client: TestClient) -> None:
    rid = _store("varsrc3", _spec_with_window("2024-01-01"),
                 stats={"honesty_report": EFFECTIVE})
    r = client.get(f"/api/runs/{rid}/variant")
    assert r.status_code == 200, r.text
    draft = r.json()["draft"]
    assert draft["variantWindow"]["state"] == "carried"
    assert draft["window"] == {"kind": "custom", "start": "2024-01-01", "end": None}
    assert draft["variantWindow"]["parentEffective"] == {
        "start": "2023-04-03", "end": "2026-07-17"
    }


def test_window_carried_all_when_the_stored_draft_chose_all(client: TestClient) -> None:
    """V-51: an inherited "all" resolves against CURRENT coverage, so it may
    legitimately test more history than the parent. Distinguishable ONLY via the
    stored draft — spec.backtest.start is NULL for "all" and for a
    pre-directive run alike."""
    rid = _store("varsrc4", _spec_with_window(None), provenance=_prov({"kind": "all"}),
                 stats={"honesty_report": EFFECTIVE})
    draft = client.get(f"/api/runs/{rid}/variant").json()["draft"]
    assert draft["variantWindow"]["state"] == "carried_all"
    assert draft["window"] == {"kind": "all"}


def test_window_unset_when_the_parent_recorded_none(client: TestClient) -> None:
    """V-50: do not synthesize a requested window from the effective one. Leave
    it unset with the parent's effective window as context, run locked until the
    user picks. V-132: routine, not degraded."""
    rid = _store("varsrc5", _spec_with_window(None), stats={"honesty_report": EFFECTIVE})
    draft = client.get(f"/api/runs/{rid}/variant").json()["draft"]
    assert draft["variantWindow"]["state"] == "unset"
    assert draft["window"] is None, "the RUN button stays locked until a pick"
    assert draft["variantWindow"]["parentEffective"]["start"] == "2023-04-03"


def test_the_carried_conversation_is_the_parents_verbatim(client: TestClient) -> None:
    """V-05: carried and NOT re-asked."""
    qa = [
        {"kind": "question", "id": "exit-rule", "question": "When do you close?"},
        {"kind": "answer", "id": "exit-rule", "answer": "50% profit"},
    ]
    rid = _store("varsrc6", _spec_with_window("2024-01-01"),
                 provenance=_prov({"kind": "3y"}, conversation=qa))
    body = client.get(f"/api/runs/{rid}/variant").json()
    assert body["conversation"] == qa
    assert body["prompt"]["text"] == "sell a 30 delta put on SPY"


def test_tier_c_refuses_with_a_real_reason_and_no_draft(client: TestClient) -> None:
    """V-128: never a generic error. V-21: only tier (c) blocks."""
    spec = copy.deepcopy(CANONICAL)
    spec["position"]["structure"] = "short_put"
    spec["position"]["legs"] = [
        {"right": "put", "side": "short", "ratio": 1,
         "strike_selection": {"method": "delta", "value": 0.3}},
        {"right": "put", "side": "long", "ratio": 1,
         "strike_selection": {"method": "delta", "value": 0.1}},
    ]
    rid = _store("varsrc7", spec)
    body = client.get(f"/api/runs/{rid}/variant").json()
    assert body["tier"] == "c"
    assert body["draft"] is None
    assert "implies 1 legs" in body["reasons"]["structure"]


def test_tier_b_locks_one_dial_and_still_returns_a_draft(client: TestClient) -> None:
    """V-21: locking one dial and naming why is the honest version; blocking the
    whole copy is the tool refusing work it can do."""
    spec = copy.deepcopy(CANONICAL)
    spec["position"]["legs"] = [
        {"right": "put", "side": "short", "ratio": 1,
         "strike_selection": {"method": "offset_pct", "value": -0.02}}
    ]
    rid = _store("varsrc8", spec)
    body = client.get(f"/api/runs/{rid}/variant").json()
    assert body["tier"] == "b"
    assert body["draft"] is not None, "tier (b) still runs"
    assert "strike" in body["locked"]
    assert body["reasons"]["strike"] == "2% below spot by offset"
    assert "position.legs" in body["lockedPaths"]


def test_a_refused_run_is_still_variant_able(client: TestClient) -> None:
    """V-03: refused runs are the HIGHEST-value copy source — the usual fix is
    widening the window, which is what this button is for."""
    rid = _store("varsrc9", _spec_with_window(None),
                 stats={"honesty_report": EFFECTIVE, "verdict": {"refusal": True}})
    r = client.get(f"/api/runs/{rid}/variant")
    assert r.status_code == 200
    assert r.json()["draft"] is not None


def test_unknown_run_is_404(client: TestClient) -> None:
    assert client.get("/api/runs/nope-not-real/variant").status_code == 404


def test_reading_the_variant_draft_creates_nothing(client: TestClient) -> None:
    """V-09: clicking costs nothing and commits to nothing. No run row, no
    ledger entry — the credit is debited at SUBMIT."""
    rid = _store("varsrc10", _spec_with_window("2024-01-01"))
    with db.session() as s:
        before = s.query(db.Run).count()
    client.get(f"/api/runs/{rid}/variant")
    with db.session() as s:
        assert s.query(db.Run).count() == before, "reading a variant draft created a run"


@pytest.mark.parametrize("field", ["ticker", "structure"])
def test_identity_fields_are_locked_on_every_variant(client: TestClient, field: str) -> None:
    """V-06: locked regardless of tier."""
    rid = _store("varsrc11", _spec_with_window("2024-01-01"))
    assert field in client.get(f"/api/runs/{rid}/variant").json()["locked"]
