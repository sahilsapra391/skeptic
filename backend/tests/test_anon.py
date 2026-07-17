"""Launch L4 anonymous-trial armor (the public-launch blocker).

An anonymous visitor gets exactly ONE real backtest, defended in layers so a
doctored client can't turn the engine into free compute: Cloudflare Turnstile,
one run per SIGNED anon token, one run per IP window, a global daily budget,
and a daily-clock / ≤3-year constraint. Signed-in accounts and the service
principal skip ALL of it. Signup re-parents the device's anon run (the
conversion moment). No raw token or IP is ever stored — only HMAC'd hashes.

Every anon POST here stubs the background engine (`_execute_run`): the armor
is entirely PRE-run, so the gauntlet is irrelevant and a real 5-minute run
would block the test on the intraday lake. The AnonTrial row and the run row
are both written synchronously in the endpoint, before the task is queued.

Each test sources its anon runs from a UNIQUE IP (`fresh_ip`) so the
module-level per-IP window and the global daily budget — both counted over a
session-shared SQLite file — never cross-contaminate between tests.
"""

from __future__ import annotations

import copy
import itertools
import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import anon, db
from app.main import app
from app.models.spec import Clock, StrategySpec
from tests.fixtures.engine import fx_short_put_assigned as fx

# this whole module drives the REAL armor — opt out of the conftest
# neutralizer that disables it for every other (pre-armor) test
pytestmark = pytest.mark.real_anon_armor

PASSWORD = "correct-horse-battery"
SERVICE_TOKEN = "svc-anon-test-token"

# a distinct second octet from test_auth (10.77.x) / test_runs_api (10.66.x)
# so IPs never collide across modules in the shared session DB
_ip_counter = itertools.count(1)


def fresh_ip() -> dict[str, str]:
    n = next(_ip_counter)
    return {"x-forwarded-for": f"10.88.{n // 250}.{n % 250}"}


def unique_email() -> str:
    return f"anon-{uuid.uuid4().hex[:10]}@example.com"


def new_device() -> TestClient:
    """A different browser: a brand-new cookie jar on the same app."""
    return TestClient(app, base_url="https://testserver")


def anon_trial_count() -> int:
    with db.session() as s:
        return s.query(db.AnonTrial).count()


def run_count() -> int:
    with db.session() as s:
        return s.query(db.Run).count()


def trials_for(run_id: str) -> int:
    with db.session() as s:
        return s.query(db.AnonTrial).filter(db.AnonTrial.run_id == run_id).count()


def owner_of(run_id: str) -> str | None:
    with db.session() as s:
        return s.get(db.Run, run_id).user_id


def uid_of(email: str) -> str:
    with db.session() as s:
        return s.query(db.User).filter(db.User.email == email.lower()).one().id


def session_of(resp) -> str:
    token = resp.cookies.get("skeptic_session")
    assert token, "no session cookie on the response"
    return token


def signup(client: TestClient, email: str | None = None, claim: list[str] | None = None):
    body: dict[str, object] = {"email": email or unique_email(), "password": PASSWORD}
    if claim is not None:
        body["claim_run_ids"] = claim
    return client.post("/api/auth/signup", json=body, headers=fresh_ip())


# --------------------------------------------------------------- fixtures


@pytest.fixture()
def _stub_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    """The armor is all pre-run — stub the background gauntlet so anon POSTs
    are fast and never touch the engine or the intraday lake."""
    import app.api.runs as runs_mod

    monkeypatch.setattr(runs_mod, "_execute_run", lambda *a, **k: None)


def _clear_armor_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        "OPENROUTER_API_KEY",  # hermetic: never a live LLM call
        "TURNSTILE_SECRET",  # dev default: the human check is skipped
        "SKEPTIC_ANON_SECRET",
        "SKEPTIC_ANON_DAILY_BUDGET",
        "SKEPTIC_ANON_IP_WINDOW_HOURS",
    ):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture()
def anon_client(monkeypatch: pytest.MonkeyPatch, _stub_engine: None) -> TestClient:
    """Dev shape: no service token (the gate is open), no Turnstile. https
    base_url so the secure `skeptic_anon` cookie survives in the jar."""
    _clear_armor_env(monkeypatch)
    monkeypatch.delenv("SKEPTIC_ACCESS_TOKEN", raising=False)
    return TestClient(app, base_url="https://testserver")


@pytest.fixture()
def deployed_client(monkeypatch: pytest.MonkeyPatch, _stub_engine: None) -> TestClient:
    """Deployed shape: the service token is set, so `is_service` and the
    proxy gate key are live. Anonymous traffic reaches the route through the
    Next proxy's `x-skeptic-gate`."""
    _clear_armor_env(monkeypatch)
    monkeypatch.setenv("SKEPTIC_ACCESS_TOKEN", SERVICE_TOKEN)
    return TestClient(app, base_url="https://testserver")


# --------------------------------------------------------- the free run


def test_first_anon_backtest_arms_exactly_one_trial(anon_client: TestClient) -> None:
    r = anon_client.post("/api/backtest", json={"spec": fx.SPEC}, headers=fresh_ip())
    assert r.status_code == 200
    body = r.json()
    assert body["demo"] is False
    # the anon wait UX: an honest queue depth and the constraint it ran under
    assert isinstance(body["queuePosition"], int)
    assert isinstance(body["trialConstraint"], str) and body["trialConstraint"]
    run_id = body["run_id"]

    # the device gets a signed, httpOnly, secure cookie — its one-run identity
    set_cookie = r.headers["set-cookie"]
    assert "skeptic_anon=" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "Secure" in set_cookie

    # the trial is recorded AFTER the run row exists, tied to the run id
    with db.session() as s:
        trials = s.query(db.AnonTrial).filter(db.AnonTrial.run_id == run_id).all()
        assert len(trials) == 1
        assert trials[0].run_id == run_id
        run = s.get(db.Run, run_id)
        assert run is not None
        assert run.user_id is None  # anonymous → unowned, claimable at signup
        assert run.origin == "user"


def test_second_run_same_device_is_refused(anon_client: TestClient) -> None:
    ip = fresh_ip()
    first = anon_client.post("/api/backtest", json={"spec": fx.SPEC}, headers=ip)
    assert first.status_code == 200

    before_trials, before_runs = anon_trial_count(), run_count()
    # the cookie rides along on the same client → the per-token rule fires
    second = anon_client.post("/api/backtest", json={"spec": fx.SPEC}, headers=ip)
    assert second.status_code == 402
    assert "used this device's free backtest" in second.json()["detail"]
    assert "run_id" not in second.json()
    # nothing was burned: no second trial, no second run
    assert anon_trial_count() == before_trials
    assert run_count() == before_runs


def test_new_device_same_ip_blocked_then_allowed_past_the_window(
    anon_client: TestClient,
) -> None:
    ip = fresh_ip()
    first = anon_client.post("/api/backtest", json={"spec": fx.SPEC}, headers=ip)
    assert first.status_code == 200
    run_id = first.json()["run_id"]

    # a fresh browser (new cookie jar) from the same IP is inside the window —
    # the cookie-clearer defense
    blocked = new_device().post("/api/backtest", json={"spec": fx.SPEC}, headers=ip)
    assert blocked.status_code == 402
    assert "used this device's free backtest" in blocked.json()["detail"]

    # prove the window is real: age the trial past SKEPTIC_ANON_IP_WINDOW_HOURS
    # and a fresh device from the same IP is allowed again
    with db.session() as s:
        row = s.query(db.AnonTrial).filter(db.AnonTrial.run_id == run_id).one()
        row.created_at = datetime.now(UTC) - anon._ip_window() - timedelta(hours=1)
        s.commit()
    allowed = new_device().post("/api/backtest", json={"spec": fx.SPEC}, headers=ip)
    assert allowed.status_code == 200


def test_global_daily_budget_has_a_distinct_refusal(
    anon_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # one success guarantees the 24h count is ≥ 1 before we drop the ceiling
    assert (
        anon_client.post("/api/backtest", json={"spec": fx.SPEC}, headers=fresh_ip()).status_code
        == 200
    )
    monkeypatch.setenv("SKEPTIC_ANON_DAILY_BUDGET", "1")
    # a brand-new device on a brand-new IP now hits the CEILING, not the
    # device rule — the budget check runs first and its message is distinct
    r = new_device().post("/api/backtest", json={"spec": fx.SPEC}, headers=fresh_ip())
    assert r.status_code == 402
    detail = r.json()["detail"]
    assert "busy" in detail
    assert "used this device" not in detail


# ------------------------------------------------------- constraint set


def test_enforce_constraints_rejects_intraday_and_long_windows() -> None:
    # a doctored client asking for more than the fast path is REJECTED (422),
    # never silently clamped — tested at the unit that guards it, since a full
    # 5-minute StrategySpec is awkward to build end to end
    daily = StrategySpec.model_validate(fx.SPEC)
    anon.enforce_constraints(daily)  # daily clock, ~15-day window → passes

    intraday = StrategySpec.model_validate(fx.SPEC)
    intraday.backtest.clock = Clock.FIVE_MIN
    with pytest.raises(HTTPException) as ei:
        anon.enforce_constraints(intraday)
    assert ei.value.status_code == 422
    assert "daily clock" in ei.value.detail

    long_window = StrategySpec.model_validate(fx.SPEC)
    long_window.backtest.start = date(2015, 1, 5)
    long_window.backtest.end = date(2025, 1, 21)  # ~10 years
    with pytest.raises(HTTPException) as el:
        anon.enforce_constraints(long_window)
    assert el.value.status_code == 422
    assert "3-year window" in el.value.detail


def test_signed_in_user_bypasses_the_anon_constraints(anon_client: TestClient) -> None:
    """The constraint is anon-ONLY: a signed-in caller may run intraday and
    the full history. The engine is stubbed, so this asserts the endpoint
    ACCEPTS the spec (no 422), which is the constraint decision."""
    token = session_of(signup(anon_client))
    auth = {"x-skeptic-session": token}

    five = copy.deepcopy(fx.SPEC)
    five["spec_version"] = 2  # clock is v2 vocabulary
    five["backtest"]["clock"] = "5min"
    r = anon_client.post(
        "/api/backtest", json={"spec": five}, headers={**auth, **fresh_ip()}
    )
    assert r.status_code == 200  # 5-minute accepted — not the anon 422

    long_window = copy.deepcopy(fx.SPEC)
    long_window["backtest"]["start"] = "2015-01-05"
    long_window["backtest"]["end"] = "2025-01-21"
    r2 = anon_client.post(
        "/api/backtest", json={"spec": long_window}, headers={**auth, **fresh_ip()}
    )
    assert r2.status_code == 200  # 10-year window accepted


# ----------------------------------------------------------- turnstile


def test_turnstile_failure_blocks_the_anon_run(
    deployed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TURNSTILE_SECRET", "ts-secret")
    monkeypatch.setattr(anon, "verify_turnstile", lambda token, ip: False)
    before = anon_trial_count()
    # anonymous traffic reaches the route through the proxy gate (no session,
    # no service bearer) — the human check runs before anything is recorded
    r = deployed_client.post(
        "/api/backtest",
        json={"spec": fx.SPEC, "turnstile_token": "bad-token"},
        headers={"x-skeptic-gate": SERVICE_TOKEN, **fresh_ip()},
    )
    assert r.status_code == 403
    assert "human check" in r.json()["detail"]
    assert anon_trial_count() == before  # a failed human check burns nothing


def test_verify_turnstile_unit(monkeypatch: pytest.MonkeyPatch) -> None:
    class Resp:
        status_code = 200

        def __init__(self, ok: bool) -> None:
            self._ok = ok

        def json(self) -> dict[str, bool]:
            return {"success": self._ok}

    monkeypatch.setenv("TURNSTILE_SECRET", "ts-secret")
    monkeypatch.setattr(anon.requests, "post", lambda *a, **k: Resp(False))
    assert anon.verify_turnstile("tok", "1.2.3.4") is False
    monkeypatch.setattr(anon.requests, "post", lambda *a, **k: Resp(True))
    assert anon.verify_turnstile("tok", "1.2.3.4") is True
    # a configured secret with no token fails closed
    assert anon.verify_turnstile(None, "1.2.3.4") is False

    def boom(*a: object, **k: object) -> Resp:
        raise anon.requests.RequestException("cloudflare down")

    monkeypatch.setattr(anon.requests, "post", boom)
    assert anon.verify_turnstile("tok", "1.2.3.4") is False  # network error → closed

    # dev / pre-launch: no secret configured → the check is skipped
    monkeypatch.delenv("TURNSTILE_SECRET", raising=False)
    assert anon.verify_turnstile(None, "1.2.3.4") is True


# ------------------------------------------------------ token signature


def test_anon_token_roundtrips_and_forgeries_are_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SKEPTIC_ANON_SECRET", "a-real-signing-secret")
    token, stored = anon.new_token()
    assert anon.verified_hash(token) == stored  # a genuine token round-trips

    assert anon.verified_hash(None) is None  # missing
    assert anon.verified_hash("no-dot-here") is None  # malformed (no signature)
    assert anon.verified_hash("somerand.deadbeef") is None  # wrong signature
    rand, _, sig = token.rpartition(".")
    assert anon.verified_hash(rand + ".") is None  # empty signature
    assert anon.verified_hash(rand + "." + "0" * len(sig)) is None  # tampered sig

    # rotate the secret: a token signed under the old key no longer verifies —
    # the signature is what lets us reject a forged token before the DB
    monkeypatch.setenv("SKEPTIC_ANON_SECRET", "rotated-secret")
    assert anon.verified_hash(token) is None


# --------------------------------------------------- signed-in / service


def test_signed_in_run_skips_the_armor_entirely(anon_client: TestClient) -> None:
    token = session_of(signup(anon_client))
    before = anon_trial_count()
    r = anon_client.post(
        "/api/backtest",
        json={"spec": fx.SPEC},
        headers={"x-skeptic-session": token, **fresh_ip()},
    )
    assert r.status_code == 200
    # no Turnstile, no limits, no anon cookie, no trial row
    assert "skeptic_anon" not in r.headers.get("set-cookie", "")
    assert trials_for(r.json()["run_id"]) == 0
    assert anon_trial_count() == before


def test_service_principal_skips_the_armor_entirely(
    deployed_client: TestClient,
) -> None:
    bearer = {"authorization": f"Bearer {SERVICE_TOKEN}"}
    before = anon_trial_count()
    r = deployed_client.post(
        "/api/backtest", json={"spec": fx.SPEC}, headers={**bearer, **fresh_ip()}
    )
    assert r.status_code == 200
    assert "skeptic_anon" not in r.headers.get("set-cookie", "")
    assert trials_for(r.json()["run_id"]) == 0
    assert anon_trial_count() == before

    # …and the service principal is not bound by the anon constraints either
    five = copy.deepcopy(fx.SPEC)
    five["spec_version"] = 2
    five["backtest"]["clock"] = "5min"
    assert (
        deployed_client.post(
            "/api/backtest", json={"spec": five}, headers={**bearer, **fresh_ip()}
        ).status_code
        == 200
    )


# -------------------------------------------------------------- claim flow


def test_signup_claims_the_anon_run_without_double_counting(
    anon_client: TestClient,
) -> None:
    run_id = (
        anon_client.post("/api/backtest", json={"spec": fx.SPEC}, headers=fresh_ip())
        .json()["run_id"]
    )
    assert trials_for(run_id) == 1
    assert owner_of(run_id) is None

    # sign up on the SAME device (the skeptic_anon cookie rides the jar) AND
    # also name the run in the localStorage breadcrumb. The anon token and the
    # breadcrumb are unioned — the run must claim exactly once, not twice.
    email = unique_email()
    r = signup(anon_client, email, claim=[run_id])
    assert r.status_code == 200
    assert r.json()["claimedRuns"] == 1
    assert owner_of(run_id) == uid_of(email)

    # a second account can't re-claim it — it's owned now
    again = signup(anon_client, claim=[run_id])
    assert again.json()["claimedRuns"] == 0
    assert owner_of(run_id) == uid_of(email)


# ---------------------------------------------------------- sqlite fallback


def test_armor_proceeds_on_the_sqlite_fallback(
    anon_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A session-less anon caller never touches the accounts DB, so the armor
    does NOT 503 like the account surfaces — it PROCEEDS, stamping a NULL
    owner (still claimable), and the trial is recorded best-effort."""
    monkeypatch.setattr(db, "FALLBACK_REASON", "neon unreachable (test)")
    r = anon_client.post("/api/backtest", json={"spec": fx.SPEC}, headers=fresh_ip())
    assert r.status_code == 200
    run_id = r.json()["run_id"]
    assert owner_of(run_id) is None
    assert trials_for(run_id) == 1
