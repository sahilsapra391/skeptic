"""Launch L2: credits + gating.

A signed-in caller spends 1 credit per backtest. The credit law (owner
override): you only pay for a GRADED verdict — a refusal or an our-fault
failure refunds the credit. Anonymous runs are defended by the armor and cost
no credits; the service principal is never charged. The debit and the run row
are written in ONE transaction (crash between = neither), and the refund is
idempotent + DB-enforced once per run.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app import db
from app.engine.market import build_fixture_store
from app.main import app
from tests.fixtures.engine import fx_short_put_assigned as fx

PASSWORD = "correct-horse-battery"
SERVICE_TOKEN = "svc-credit-test-token"


def _refusing_store(ticker: str, **kw: object) -> object:
    # one volatility regime → the single-trade fixture REFUSES (insufficient
    # evidence), whatever the bar
    return build_fixture_store("SPY", fx.CHAINS, fx.UNDERLYING)


def _grading_store(ticker: str, **kw: object) -> object:
    # two VIX regimes → at bar=1 the single-trade fixture GRADES (only the
    # trades bar caps it), so the credit stays spent (no refund)
    days = sorted(fx.UNDERLYING)
    vix = {d: (12.0 if i < len(days) // 2 else 25.0) for i, d in enumerate(days)}
    return build_fixture_store("SPY", fx.CHAINS, fx.UNDERLYING, vix=vix)


def _client(monkeypatch: pytest.MonkeyPatch, store, *, service: bool = False) -> TestClient:
    monkeypatch.setenv("SKEPTIC_ACCESS_TOKEN", SERVICE_TOKEN if service else "")
    monkeypatch.setenv("OPENROUTER_API_KEY", "")  # hermetic: no live narration
    import app.data.chains as chains_module

    monkeypatch.setattr(chains_module, "load_market_store", store)
    # https base_url so the secure skeptic_session cookie survives the jar
    return TestClient(app, base_url="https://testserver")


@pytest.fixture()
def refusing(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    return _client(monkeypatch, _refusing_store)


@pytest.fixture()
def grading(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    return _client(monkeypatch, _grading_store)


# --------------------------------------------------------------- helpers


def _email() -> str:
    return f"credit-{uuid.uuid4().hex[:10]}@example.com"


def _signup(client: TestClient, email: str) -> dict:
    r = client.post("/api/auth/signup", json={"email": email, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return r.json()


def _credits(client: TestClient) -> int:
    return int(client.get("/api/me").json()["credits"])


def _uid(email: str) -> str:
    with db.session() as s:
        return s.query(db.User).filter(db.User.email == email.lower()).one().id


def _ledger(user_id: str) -> list[tuple[str, int, str | None]]:
    with db.session() as s:
        return [
            (r.reason, r.delta, r.run_id)
            for r in s.query(db.CreditLedger).filter(db.CreditLedger.user_id == user_id).all()
        ]


def _run_count() -> int:
    with db.session() as s:
        return s.query(db.Run).count()


def _verdict(client: TestClient, run_id: str) -> dict:
    return client.get(f"/api/runs/{run_id}").json()["verdict"]


# ------------------------------------------------------------ debit / grant


def test_signup_grants_five_and_a_graded_run_spends_one(grading: TestClient) -> None:
    email = _email()
    assert _signup(grading, email)["credits"] == 5

    r = grading.post("/api/backtest", json={"spec": fx.SPEC, "min_trades": 1})
    assert r.status_code == 200
    run_id = r.json()["run_id"]
    # the fixture grades at bar=1 (a real verdict), so the credit is SPENT
    assert not _verdict(grading, run_id).get("refusal")
    assert _credits(grading) == 4

    rows = _ledger(_uid(email))
    assert ("signup_grant", 5, None) in rows
    assert ("run_debit", -1, run_id) in rows
    assert not any(reason == "engine_refund" for reason, _, _ in rows)  # graded → no refund


# ------------------------------------------------------------ refund law


def test_a_refusal_debits_then_refunds(refusing: TestClient) -> None:
    email = _email()
    _signup(refusing, email)

    r = refusing.post("/api/backtest", json={"spec": fx.SPEC})
    assert r.status_code == 200
    run_id = r.json()["run_id"]
    assert _verdict(refusing, run_id)["refusal"] is True  # insufficient evidence

    # you only pay for a graded verdict → charged, then refunded → net zero
    assert _credits(refusing) == 5
    rows = _ledger(_uid(email))
    assert ("run_debit", -1, run_id) in rows
    assert ("engine_refund", 1, run_id) in rows


def test_refund_run_is_idempotent_and_self_scoped() -> None:
    # a run that was charged: refund once, never twice
    uid = "u-refund-test"
    run_id = "run-refund-1"
    with db.session() as s:
        s.add(db.CreditLedger(user_id=uid, delta=-1, reason="run_debit", run_id=run_id))
        s.commit()
    assert db.refund_run(run_id) is True
    assert db.credit_balance(uid) == 0  # -1 debit + 1 refund
    assert db.refund_run(run_id) is False  # idempotent — no second refund
    assert db.credit_balance(uid) == 0

    # a run that was NEVER charged (anon armor / service) is a no-op
    assert db.refund_run("run-never-charged") is False
    with db.session() as s:
        assert (
            s.query(db.CreditLedger).filter(db.CreditLedger.run_id == "run-never-charged").count()
            == 0
        )


# ----------------------------------------------------------- the hard gate


def test_out_of_credits_blocks_and_creates_nothing(grading: TestClient) -> None:
    email = _email()
    _signup(grading, email)
    uid = _uid(email)
    # drain to zero via an admin adjustment (the owner grant tool's mechanism)
    with db.session() as s:
        s.add(db.CreditLedger(user_id=uid, delta=-5, reason="admin_adjust", run_id=None))
        s.commit()
    assert db.credit_balance(uid) == 0

    before_runs = _run_count()
    before_rows = len(_ledger(uid))
    r = grading.post("/api/backtest", json={"spec": fx.SPEC, "min_trades": 1})
    assert r.status_code == 402
    assert "credit" in r.json()["detail"].lower()
    # crash-atomic gate: no run row, no debit row — neither, not one
    assert _run_count() == before_runs
    assert len(_ledger(uid)) == before_rows


# --------------------------------------------------- who is NOT charged


def test_anonymous_run_is_not_charged(refusing: TestClient) -> None:
    # no session → the armor path (one free run), never the ledger
    r = refusing.post("/api/backtest", json={"spec": fx.SPEC})
    assert r.status_code == 200
    run_id = r.json()["run_id"]
    with db.session() as s:
        run = s.get(db.Run, run_id)
        assert run is not None and run.user_id is None
        assert s.query(db.CreditLedger).filter(db.CreditLedger.run_id == run_id).count() == 0


def test_service_principal_is_not_charged(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch, _refusing_store, service=True)
    r = client.post(
        "/api/backtest",
        json={"spec": fx.SPEC},
        headers={"authorization": f"Bearer {SERVICE_TOKEN}"},
    )
    assert r.status_code == 200
    run_id = r.json()["run_id"]
    with db.session() as s:
        assert s.query(db.CreditLedger).filter(db.CreditLedger.run_id == run_id).count() == 0


# ------------------------------------------------- forced origin (no dodge)


def _boom(*a: object, **k: object) -> bool:
    raise RuntimeError("refund exploded")


# ---------------------------------------------------- the paywall-bypass seal


def test_refunded_refusal_cannot_unlock_at_a_lower_bar(grading: TestClient) -> None:
    """The critical exploit: submit at an absurd bar to FORCE a refusal (→
    refund → net 0 credits), then view at ?min_trades=1 to unlock the graded
    verdict for free. The seal keeps a REFUNDED refusal refused at any bar —
    you get the credit back OR the verdict, never both. (This fixture GRADES
    at bar 1, so without the seal the second view would bless it.)"""
    email = _email()
    _signup(grading, email)

    r = grading.post("/api/backtest", json={"spec": fx.SPEC, "min_trades": 10000})
    assert r.status_code == 200
    run_id = r.json()["run_id"]
    assert _verdict(grading, run_id)["refusal"] is True  # refused at bar 10000
    assert _credits(grading) == 5  # debited then refunded → net zero

    # the exploit's second move — view at bar 1 to unlock the verdict
    regraded = grading.get(f"/api/runs/{run_id}?min_trades=1").json()
    assert regraded["verdict"]["refusal"] is True  # SEALED — still refused
    assert _credits(grading) == 5  # and never re-charged


def test_a_charged_graded_run_still_regrades_freely(grading: TestClient) -> None:
    """The seal is scoped to REFUNDED runs: a graded (charged) run still
    re-grades both ways at view time (the shipped evidence-bar feature)."""
    email = _email()
    _signup(grading, email)
    run_id = grading.post(
        "/api/backtest", json={"spec": fx.SPEC, "min_trades": 1}
    ).json()["run_id"]
    assert not _verdict(grading, run_id).get("refusal")  # graded, charged
    assert _credits(grading) == 4
    # raise the bar at view time → it re-caps to a refusal (display only, no
    # refund) — proof the seal didn't freeze non-refunded runs
    strict = grading.get(f"/api/runs/{run_id}?min_trades=10000").json()
    assert strict["verdict"]["refusal"] is True
    assert _credits(grading) == 4  # still charged — view-time re-grade never refunds


# ------------------------------------------------ durability: interrupted runs


def test_boot_sweep_refunds_interrupted_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    """A run debits at creation; if the process dies mid-run (OOM / redeploy)
    the completion-path refund never fires. The boot sweep marks it error AND
    refunds — an interrupted run must not permanently charge the user."""
    client = _client(monkeypatch, _grading_store)
    import app.api.runs as runs_mod

    monkeypatch.setattr(runs_mod, "_execute_run", lambda *a, **k: None)  # never completes
    from app.main import _sweep_orphaned_runs

    email = _email()
    _signup(client, email)
    run_id = client.post(
        "/api/backtest", json={"spec": fx.SPEC, "min_trades": 1}
    ).json()["run_id"]
    assert _credits(client) == 4  # debited; run stuck queued (engine stubbed)

    _sweep_orphaned_runs()
    with db.session() as s:
        assert s.get(db.Run, run_id).status == "error"
    assert _credits(client) == 5  # the interrupted run's credit was refunded


def test_refund_failure_does_not_corrupt_a_completed_run(
    refusing: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The refusal refund is isolated: a transient failure in refund_run must
    NOT flip an already-committed 'done' run to 'error' via the outer except."""
    email = _email()
    _signup(refusing, email)
    monkeypatch.setattr(db, "refund_run", _boom)
    r = refusing.post("/api/backtest", json={"spec": fx.SPEC})
    assert r.status_code == 200
    run_id = r.json()["run_id"]
    with db.session() as s:
        assert s.get(db.Run, run_id).status == "done"  # not corrupted to error


def test_session_caller_origin_is_forced_to_user(grading: TestClient) -> None:
    """A signed-in caller can't declare an automation origin to dodge the
    debit — the run is stamped origin=user and charged like any other."""
    email = _email()
    _signup(grading, email)
    r = grading.post(
        "/api/backtest", json={"spec": fx.SPEC, "min_trades": 1, "origin": "auto_unlock"}
    )
    assert r.status_code == 200
    run_id = r.json()["run_id"]
    with db.session() as s:
        assert s.get(db.Run, run_id).origin == "user"  # forced, not auto_unlock
    assert ("run_debit", -1, run_id) in _ledger(_uid(email))  # and it WAS charged
