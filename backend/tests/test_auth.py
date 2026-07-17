"""Launch L1b auth (self-rolled, D1 reversed): signup creates the user +
argon2id hash + the exactly-once grant and sets the session cookie; login
failures are uniform and per-account rate-limited; sessions are opaque DB
rows (httpOnly cookie or x-skeptic-session header) with instant revocation;
email verification is single-use hashed tokens and the mailer reports
honestly when nothing was sent; runs gain owners, signup claiming, and
404-to-strangers privacy; the gate keeps every pre-launch behavior; and
accounts refuse loudly on the throwaway SQLite fallback."""

from __future__ import annotations

import hashlib
import itertools
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from app import db
from app.engine.market import build_fixture_store
from app.ratelimit import SlidingWindowLimiter
from tests.fixtures.engine import fx_short_put_assigned as fx

SERVICE_TOKEN = "svc-test-token"
PASSWORD = "correct-horse-battery"

# the module-level limiters persist across tests — every test owns its
# whole per-IP window by sourcing requests from an address nobody reuses
_ip_counter = itertools.count(1)


def fresh_ip() -> dict[str, str]:
    n = next(_ip_counter)
    return {"x-forwarded-for": f"10.77.{n // 250}.{n % 250}"}


def unique_email() -> str:
    return f"who-{uuid.uuid4().hex[:10]}@example.com"


def as_user(token: str) -> dict[str, str]:
    return {"x-skeptic-session": token}


def as_service() -> dict[str, str]:
    return {"authorization": f"Bearer {SERVICE_TOKEN}"}


def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        "SKEPTIC_ACCESS_TOKEN",
        "OPENROUTER_API_KEY",  # hermetic: never live LLM calls from tests
        "R2_ACCOUNT_ID",
        "R2_ACCESS_KEY_ID",
        "R2_SECRET_ACCESS_KEY",
        "R2_BUCKET",
        "SIGNUP_GRANT_CREDITS",
        "SKEPTIC_REQUIRE_VERIFIED",
        "RESEND_API_KEY",  # no sender configured — the mailer only logs
        "SKEPTIC_SMTP_HOST",
    ):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Deployed shape: service token set; the session cookie is secure=True,
    so the client speaks https for the cookie jar to keep it."""
    _clear_env(monkeypatch)
    monkeypatch.setenv("SKEPTIC_ACCESS_TOKEN", SERVICE_TOKEN)
    from app.main import app

    return TestClient(app, base_url="https://testserver")


@pytest.fixture()
def dev_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Local-dev shape (no service token) on the hermetic fixture lake —
    how anonymous devices run backtests pre-flip, and what the claim flow
    re-parents at signup."""
    _clear_env(monkeypatch)
    import app.data.chains as chains_module

    monkeypatch.setattr(
        chains_module,
        "load_market_store",
        lambda ticker, **kw: build_fixture_store("SPY", fx.CHAINS, fx.UNDERLYING),
    )
    from app.main import app

    return TestClient(app, base_url="https://testserver")


def signup(
    client: TestClient,
    email: str | None = None,
    password: str = PASSWORD,
    claim: list[str] | None = None,
):
    body: dict[str, object] = {"email": email or unique_email(), "password": password}
    if claim is not None:
        body["claim_run_ids"] = claim
    return client.post("/api/auth/signup", json=body, headers=fresh_ip())


def session_of(response) -> str:
    token = response.cookies.get("skeptic_session")
    assert token, "no session cookie on the response"
    return token


def user_id_of(email: str) -> str:
    with db.session() as s:
        return s.query(db.User).filter(db.User.email == email.lower()).one().id


# ----------------------------------------------------------------- signup


def test_signup_creates_account_grant_and_session(client: TestClient) -> None:
    email = unique_email()
    r = signup(client, email)
    assert r.status_code == 200
    body = r.json()
    assert body["email"] == email
    assert body["credits"] == 5
    assert body["verified"] is False
    assert body["claimedRuns"] == 0
    # no sender configured → nothing was handed to a mail provider, and the
    # response says so instead of pretending mail is on its way
    assert body["verificationSent"] is False

    set_cookie = r.headers["set-cookie"]
    assert "skeptic_session=" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "Secure" in set_cookie

    with db.session() as s:
        user = s.query(db.User).filter(db.User.email == email).one()
        assert user.password_hash is not None
        assert user.password_hash.startswith("$argon2id$")
        # the plaintext never persists — not in ANY column of the row
        for column in db.User.__table__.columns:
            assert PASSWORD not in str(getattr(user, column.name))
        grants = (
            s.query(db.CreditLedger)
            .filter(
                db.CreditLedger.user_id == user.id,
                db.CreditLedger.reason == "signup_grant",
            )
            .all()
        )
        assert len(grants) == 1
        assert grants[0].delta == 5

    # the cookie IS the session: /api/me authenticates off the jar alone
    me = client.get("/api/me")
    assert me.status_code == 200
    assert me.json()["email"] == email


def test_duplicate_email_is_409(client: TestClient) -> None:
    email = unique_email()
    assert signup(client, email).status_code == 200
    r = signup(client, email)
    assert r.status_code == 409
    assert "sign in" in r.json()["detail"]
    # normalization applies: the same address in different case is the same account
    assert signup(client, email.upper()).status_code == 409


def test_weak_password_is_422(client: TestClient) -> None:
    r = signup(client, password="short")
    assert r.status_code == 422
    assert "10" in r.json()["detail"]


def test_bad_email_is_422(client: TestClient) -> None:
    assert signup(client, email="not-an-address").status_code == 422
    assert signup(client, email="a@b").status_code == 422


def test_email_normalized_to_lowercase(client: TestClient) -> None:
    email = f"MiXeD-{uuid.uuid4().hex[:8]}@Example.COM"
    r = signup(client, email)
    assert r.status_code == 200
    assert r.json()["email"] == email.lower()
    # login accepts the original casing against the normalized account
    good = client.post(
        "/api/auth/login",
        json={"email": email, "password": PASSWORD},
        headers=fresh_ip(),
    )
    assert good.status_code == 200


def test_signup_grant_env_override(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SIGNUP_GRANT_CREDITS", "7")
    assert signup(client).json()["credits"] == 7


def test_double_grant_impossible_at_the_database(client: TestClient) -> None:
    # belt and suspenders: even a buggy future code path cannot double-grant
    email = unique_email()
    signup(client, email)
    with db.session() as s:
        s.add(db.CreditLedger(user_id=user_id_of(email), delta=5, reason="signup_grant"))
        with pytest.raises(IntegrityError):
            s.commit()
        s.rollback()


def test_other_reasons_repeat_freely(client: TestClient) -> None:
    # the uniqueness backstop is scoped to the signup grant only
    email = unique_email()
    signup(client, email)
    uid = user_id_of(email)
    with db.session() as s:
        s.add(db.CreditLedger(user_id=uid, delta=-1, reason="run_debit"))
        s.add(db.CreditLedger(user_id=uid, delta=-1, reason="run_debit"))
        s.commit()
    assert db.credit_balance(uid) == 3


# ------------------------------------------------------------------ login


def test_login_roundtrip(client: TestClient) -> None:
    email = unique_email()
    signup(client, email)
    r = client.post(
        "/api/auth/login",
        json={"email": email, "password": PASSWORD},
        headers=fresh_ip(),
    )
    assert r.status_code == 200
    assert r.json() == {"email": email, "credits": 5, "verified": False}
    assert "skeptic_session=" in r.headers["set-cookie"]
    # the fresh session works on its own
    token = session_of(r)
    assert client.get("/api/me", headers=as_user(token)).status_code == 200


def test_login_failures_are_uniform(client: TestClient) -> None:
    # wrong password and unknown account must be indistinguishable — no
    # account-existence oracle on the login form
    email = unique_email()
    signup(client, email)
    wrong = client.post(
        "/api/auth/login",
        json={"email": email, "password": "wrong-password-x"},
        headers=fresh_ip(),
    )
    unknown = client.post(
        "/api/auth/login",
        json={"email": unique_email(), "password": "wrong-password-x"},
        headers=fresh_ip(),
    )
    assert wrong.status_code == 401
    assert unknown.status_code == 401
    assert wrong.json()["detail"] == unknown.json()["detail"] == "wrong email or password"


def test_login_limited_per_account_after_10_attempts(client: TestClient) -> None:
    # 10 attempts per account per 15 minutes, wherever they come from
    email = unique_email()
    ip = fresh_ip()
    for _ in range(10):
        r = client.post(
            "/api/auth/login", json={"email": email, "password": "nope-nope-nope"}, headers=ip
        )
        assert r.status_code == 401
    blocked = client.post(
        "/api/auth/login", json={"email": email, "password": "nope-nope-nope"}, headers=ip
    )
    assert blocked.status_code == 429
    assert "account" in blocked.json()["detail"]
    assert int(blocked.headers["retry-after"]) >= 1
    # keyed per ACCOUNT: a different account from the same address still gets through
    other = client.post(
        "/api/auth/login", json={"email": unique_email(), "password": "nope-nope-nope"}, headers=ip
    )
    assert other.status_code == 401


# --------------------------------------------------------------- sessions


def test_me_via_cookie_and_via_header(client: TestClient) -> None:
    email = unique_email()
    token = session_of(signup(client, email))
    # via the jar cookie
    assert client.get("/api/me").json()["email"] == email
    # via the x-skeptic-session header alone (no cookie in play)
    client.cookies.clear()
    r = client.get("/api/me", headers=as_user(token))
    assert r.status_code == 200
    assert r.json()["email"] == email


def test_logout_revokes_the_session(client: TestClient) -> None:
    token = session_of(signup(client))
    assert client.get("/api/me").status_code == 200
    assert client.post("/api/auth/logout").json() == {"ok": True}
    # revoked server-side — the raw token is dead, not just the cookie cleared
    assert client.get("/api/me", headers=as_user(token)).status_code == 401
    assert client.get("/api/me").status_code == 401


def test_expired_session_rejected(client: TestClient) -> None:
    email = unique_email()
    signup(client, email)
    token = "expired-" + uuid.uuid4().hex
    with db.session() as s:
        s.add(
            db.AuthSession(
                token_hash=hashlib.sha256(token.encode()).hexdigest(),
                user_id=user_id_of(email),
                expires_at=datetime.now(UTC) - timedelta(hours=1),
            )
        )
        s.commit()
    client.cookies.clear()
    assert client.get("/api/me", headers=as_user(token)).status_code == 401


def test_garbage_session_rejected(client: TestClient) -> None:
    assert client.get("/api/me", headers=as_user("not-a-real-token")).status_code == 401


def test_session_rides_secondary_header_beside_service_bearer(client: TestClient) -> None:
    # the pre-launch proxy shape: owner token in Authorization, the user's
    # session in x-skeptic-session — identity must still resolve
    email = unique_email()
    token = session_of(signup(client, email))
    client.cookies.clear()
    r = client.get("/api/me", headers={**as_service(), **as_user(token)})
    assert r.status_code == 200
    assert r.json()["email"] == email


# ------------------------------------------------------ email verification


def _capture_mailer(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The DB stores only token digests — the raw token is observable
    exactly where production hands it off: the mailer call."""
    sent: list[str] = []

    def capture(email: str, token: str) -> bool:
        sent.append(token)
        return False  # "nothing was actually delivered", like the no-sender path

    monkeypatch.setattr("app.auth.mailer.send_verification", capture)
    return sent


def test_verify_token_is_single_use(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent = _capture_mailer(monkeypatch)
    r = signup(client)
    assert r.json()["verificationSent"] is False
    assert len(sent) == 1

    ok = client.post("/api/auth/verify", json={"token": sent[0]}, headers=fresh_ip())
    assert ok.status_code == 200
    assert ok.json()["verified"] is True
    assert client.get("/api/me").json()["verified"] is True

    again = client.post("/api/auth/verify", json={"token": sent[0]}, headers=fresh_ip())
    assert again.status_code == 400
    assert "invalid, used, or expired" in again.json()["detail"]


def test_expired_verify_token_rejected(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent = _capture_mailer(monkeypatch)
    signup(client)
    with db.session() as s:
        row = s.get(db.EmailToken, hashlib.sha256(sent[0].encode()).hexdigest())
        assert row is not None
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        s.commit()
    r = client.post("/api/auth/verify", json={"token": sent[0]}, headers=fresh_ip())
    assert r.status_code == 400
    assert client.get("/api/me").json()["verified"] is False


def test_unknown_verify_token_rejected(client: TestClient) -> None:
    r = client.post("/api/auth/verify", json={"token": "made-up"}, headers=fresh_ip())
    assert r.status_code == 400


def test_resend_verification_needs_session_and_is_limited(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent = _capture_mailer(monkeypatch)
    # no session → 401 (probed before any signup so the jar is empty)
    assert client.post("/api/auth/resend-verification", headers=fresh_ip()).status_code == 401

    token = session_of(signup(client))
    ip = fresh_ip()
    for _ in range(3):
        r = client.post(
            "/api/auth/resend-verification", headers={**as_user(token), **ip}
        )
        assert r.status_code == 200
        assert r.json() == {"ok": True, "verified": False, "verificationSent": False}
    blocked = client.post(
        "/api/auth/resend-verification", headers={**as_user(token), **ip}
    )
    assert blocked.status_code == 429

    # already verified → nothing to send, and nothing is sent
    assert client.post(
        "/api/auth/verify", json={"token": sent[-1]}, headers=fresh_ip()
    ).status_code == 200
    done = client.post(
        "/api/auth/resend-verification", headers={**as_user(token), **fresh_ip()}
    )
    assert done.json() == {"ok": True, "verified": True}
    assert len(sent) == 4  # 1 signup + 3 resends — the verified resend sent nothing


# ----------------------------------------------------------- verified bar


def test_verified_bar_gates_backtest(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent = _capture_mailer(monkeypatch)
    token = session_of(signup(client))
    client.cookies.clear()  # identity rides the explicit header below
    monkeypatch.setenv("SKEPTIC_REQUIRE_VERIFIED", "1")
    # the bar fires BEFORE spec validation — an empty spec would 422
    r = client.post("/api/backtest", json={"spec": {}}, headers=as_user(token))
    assert r.status_code == 403
    assert "verify your email" in r.json()["detail"]
    # the bar applies to signed-in people only — the service principal
    # resolves no user and proceeds straight to spec validation
    assert client.post("/api/backtest", json={"spec": {}}, headers=as_service()).status_code == 422

    assert client.post(
        "/api/auth/verify", json={"token": sent[0]}, headers=fresh_ip()
    ).status_code == 200
    after = client.post("/api/backtest", json={"spec": {}}, headers=as_user(token))
    assert after.status_code == 422  # past the bar, into spec validation


# ------------------------------------------------------------- claim flow


def test_signup_claims_anonymous_runs_once(dev_client: TestClient) -> None:
    first = dev_client.post("/api/backtest", json={"spec": fx.SPEC}).json()["run_id"]
    second = dev_client.post("/api/backtest", json={"spec": fx.SPEC}).json()["run_id"]
    with db.session() as s:
        assert s.get(db.Run, first).user_id is None  # anonymous → unowned

    email = unique_email()
    r = signup(dev_client, email, claim=[first, second])
    assert r.status_code == 200
    assert r.json()["claimedRuns"] == 2
    uid = user_id_of(email)
    with db.session() as s:
        assert s.get(db.Run, first).user_id == uid
        assert s.get(db.Run, second).user_id == uid

    # a second account claiming the same ids gets nothing — they're owned now
    again = signup(dev_client, claim=[first, second])
    assert again.json()["claimedRuns"] == 0
    with db.session() as s:
        assert s.get(db.Run, first).user_id == uid


def test_system_runs_are_not_claimable(client: TestClient) -> None:
    run_id = uuid.uuid4().hex[:12]
    with db.session() as s:
        s.add(db.Run(id=run_id, status="done", spec_json="{}", origin="auto_unlock"))
        s.commit()
    r = signup(client, claim=[run_id])
    assert r.json()["claimedRuns"] == 0
    with db.session() as s:
        assert s.get(db.Run, run_id).user_id is None


# -------------------------------------------------------------- ownership


def test_owned_runs_are_private(
    dev_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    email_a = unique_email()
    token_a = session_of(signup(dev_client, email_a))
    token_b = session_of(signup(dev_client))
    run_a = dev_client.post(
        "/api/backtest", json={"spec": fx.SPEC}, headers=as_user(token_a)
    ).json()["run_id"]
    with db.session() as s:
        assert s.get(db.Run, run_a).user_id == user_id_of(email_a)  # stamped from session
    unowned = uuid.uuid4().hex[:12]
    with db.session() as s:
        s.add(db.Run(id=unowned, status="queued", spec_json='{"meta": {"name": "u"}}'))
        s.commit()

    # 404, not 403 — existence is nobody else's business
    assert dev_client.get(f"/api/runs/{run_a}", headers=as_user(token_b)).status_code == 404
    dev_client.cookies.clear()
    assert dev_client.get(f"/api/runs/{run_a}").status_code == 404
    # the owner and the unowned (pre-account, revisit-by-id) path stay open
    assert dev_client.get(f"/api/runs/{run_a}", headers=as_user(token_a)).status_code == 200
    assert dev_client.get(f"/api/runs/{unowned}").status_code == 200

    # a pinned example stays public even while owned
    monkeypatch.setenv("SKEPTIC_EXAMPLE_RUN_IDS", run_a)
    r = dev_client.get(f"/api/runs/{run_a}")
    assert r.status_code == 200
    assert r.json()["example"] is True
    monkeypatch.delenv("SKEPTIC_EXAMPLE_RUN_IDS")

    # the service principal reads everything (nightly automation)
    monkeypatch.setenv("SKEPTIC_ACCESS_TOKEN", SERVICE_TOKEN)
    assert dev_client.get(f"/api/runs/{run_a}", headers=as_service()).status_code == 200


def test_library_scoped_to_viewer(
    dev_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    token_a = session_of(signup(dev_client))
    token_b = session_of(signup(dev_client))
    run_a = dev_client.post(
        "/api/backtest", json={"spec": fx.SPEC}, headers=as_user(token_a)
    ).json()["run_id"]
    example = uuid.uuid4().hex[:12]
    with db.session() as s:
        s.add(db.Run(id=example, status="done", spec_json="{}",
                     summary_json=f'{{"id": "{example}", "name": "ex"}}'))
        s.commit()
    monkeypatch.setenv("SKEPTIC_EXAMPLE_RUN_IDS", example)

    # signed-in: own runs + the badged examples, no include= breadcrumb needed
    mine = dev_client.get("/api/runs", headers=as_user(token_a)).json()["runs"]
    assert {r["id"] for r in mine} == {run_a, example}
    assert next(r for r in mine if r["id"] == example)["example"] is True
    assert "example" not in next(r for r in mine if r["id"] == run_a)

    # another account never sees a stranger's runs
    theirs = dev_client.get("/api/runs", headers=as_user(token_b)).json()["runs"]
    assert {r["id"] for r in theirs} == {example}


# ---------------------------------------------------------------- the gate


def test_me_requires_identity(client: TestClient) -> None:
    assert client.get("/api/me").status_code == 401


def test_service_token_is_not_a_user(client: TestClient) -> None:
    r = client.get("/api/me", headers=as_service())
    assert r.status_code == 401
    assert "not a user account" in r.json()["detail"]


def test_gate_matrix(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    # anonymous: blocked off the app surface, open on the doorway itself
    assert client.get("/api/runs").status_code == 401
    assert client.get("/api/runs", headers=as_service()).status_code == 200
    token = session_of(signup(client))  # no Authorization — /api/auth is an open prefix
    # a signed-in user passes the app surface (the L1b widening)…
    assert client.get("/api/runs", headers=as_user(token)).status_code == 200
    # …and ONLY the listed prefixes: "/api/messages" must not ride "/api/me"
    assert client.get("/api/messages", headers=as_user(token)).status_code == 401

    # local dev (token unset) stays open; account surfaces still need a person
    monkeypatch.delenv("SKEPTIC_ACCESS_TOKEN")
    client.cookies.clear()
    assert client.get("/api/runs").status_code == 200
    assert client.get("/api/me").status_code == 401


def test_accounts_refuse_on_sqlite_fallback(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    email = unique_email()
    token = session_of(signup(client, email))
    monkeypatch.setattr(db, "FALLBACK_REASON", "neon unreachable (test)")
    # a ledger written to a container-local file evaporates on redeploy —
    # every account surface refuses rather than fake it
    assert signup(client).status_code == 503
    login = client.post(
        "/api/auth/login", json={"email": email, "password": PASSWORD}, headers=fresh_ip()
    )
    assert login.status_code == 503
    client.cookies.clear()
    r = client.get("/api/me", headers=as_user(token))
    assert r.status_code == 503
    assert "accounts" in r.json()["detail"]
    # the automation principal is unaffected — the runs-DB fallback is
    # deliberate for system work
    assert client.get("/api/runs", headers=as_service()).status_code == 200


# ---------------------------------------------------------- rate limiting


def test_limiter_allows_then_blocks() -> None:
    lim = SlidingWindowLimiter(limit=3, window_s=60)
    assert all(lim.check("k", now=float(i))[0] for i in range(3))
    allowed, retry = lim.check("k", now=3.0)
    assert not allowed
    assert 0 < retry <= 60


def test_limiter_window_slides() -> None:
    lim = SlidingWindowLimiter(limit=2, window_s=10)
    assert lim.check("k", now=0.0)[0]
    assert lim.check("k", now=1.0)[0]
    assert not lim.check("k", now=5.0)[0]
    assert lim.check("k", now=10.5)[0]  # first hit aged out


def test_limiter_keys_are_bounded() -> None:
    lim = SlidingWindowLimiter(limit=1, window_s=60, max_keys=2)
    lim.check("a", now=0.0)
    lim.check("b", now=1.0)
    lim.check("c", now=2.0)  # evicts "a"
    assert len(lim._hits) == 2
    assert lim.check("a", now=3.0)[0]  # "a" starts fresh — budget reset, not leaked


def test_me_rate_limited_per_account(client: TestClient) -> None:
    # the route's real budget (120/min per account) — a unique user gets a
    # fresh key, so this test owns its whole window
    token = session_of(signup(client))
    client.cookies.clear()
    statuses = [
        client.get("/api/me", headers=as_user(token)).status_code for _ in range(125)
    ]
    assert statuses[:120] == [200] * 120
    assert statuses[120:] == [429] * 5
    blocked = client.get("/api/me", headers=as_user(token))
    assert blocked.status_code == 429
    assert int(blocked.headers["retry-after"]) >= 1
