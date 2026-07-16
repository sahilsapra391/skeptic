"""Launch L1 auth: the service token stays the automation principal and can
never act as a person; Clerk session JWTs resolve to the users row + the
one-time signup grant (atomic, DB-enforced idempotent); the gate preserves
every pre-launch behavior; new surfaces are rate-limited; and accounts
refuse loudly when the DB is on the throwaway SQLite fallback."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from app import db
from app.auth import clerk
from app.ratelimit import SlidingWindowLimiter

ISSUER = "https://test-instance.clerk.accounts.dev"
SERVICE_TOKEN = "svc-test-token"

_PRIVATE = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_OTHER_PRIVATE = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_PUBLIC = _PRIVATE.public_key()


def make_token(
    sub: str | None = None,
    email: str | None = None,
    *,
    exp_minutes: float = 30,
    issuer: str = ISSUER,
    key: Any = _PRIVATE,
    email_verified: bool | None = None,
    azp: str | None = None,
) -> str:
    now = datetime.now(UTC)
    claims: dict[str, Any] = {
        "sub": sub or f"user_{uuid.uuid4().hex[:10]}",
        "iss": issuer,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=exp_minutes)).timestamp()),
    }
    if email is not None:
        claims["email"] = email
    if email_verified is not None:
        claims["email_verified"] = email_verified
    if azp is not None:
        claims["azp"] = azp
    return jwt.encode(claims, key, algorithm="RS256")


def unique_email() -> str:
    return f"who-{uuid.uuid4().hex[:10]}@example.com"


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    for var in (
        "OPENROUTER_API_KEY",
        "R2_ACCOUNT_ID",
        "R2_ACCESS_KEY_ID",
        "R2_SECRET_ACCESS_KEY",
        "R2_BUCKET",
        "CLERK_SECRET_KEY",
        "CLERK_AUTHORIZED_PARTIES",
        "SIGNUP_GRANT_CREDITS",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("SKEPTIC_ACCESS_TOKEN", SERVICE_TOKEN)
    monkeypatch.setenv("CLERK_ISSUER", ISSUER)
    # no test ever needs a live Clerk instance: signature checks run against
    # this suite's own keypair
    monkeypatch.setattr(clerk, "_signing_key", lambda token: _PUBLIC)
    from app.main import app

    return TestClient(app)


def as_user(token: str) -> dict[str, str]:
    return {"authorization": f"Bearer {token}"}


def as_service() -> dict[str, str]:
    return {"authorization": f"Bearer {SERVICE_TOKEN}"}


# ---------------------------------------------------------------- the gate


def test_me_requires_identity(client: TestClient) -> None:
    assert client.get("/api/me").status_code == 401


def test_service_token_is_not_a_user(client: TestClient) -> None:
    r = client.get("/api/me", headers=as_service())
    assert r.status_code == 401
    assert "not a user account" in r.json()["detail"]


def test_service_routes_unaffected(client: TestClient) -> None:
    # the automation principal keeps full access — nightly-improve regression
    assert client.get("/api/runs", headers=as_service()).status_code == 200


def test_user_session_cannot_reach_service_surface(client: TestClient) -> None:
    # run routes stay closed to user sessions until L2 opens them with debits
    token = make_token(email=unique_email())
    assert client.get("/api/runs", headers=as_user(token)).status_code == 401


def test_local_dev_stays_open_without_token(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SKEPTIC_ACCESS_TOKEN", raising=False)
    assert client.get("/api/runs").status_code == 200
    # identity is still required for account surfaces even in open dev mode
    assert client.get("/api/me").status_code == 401


# ------------------------------------------------- session verification


def test_expired_session_rejected(client: TestClient) -> None:
    token = make_token(email=unique_email(), exp_minutes=-5)
    assert client.get("/api/me", headers=as_user(token)).status_code == 401


def test_wrong_issuer_rejected(client: TestClient) -> None:
    token = make_token(email=unique_email(), issuer="https://evil.example.com")
    assert client.get("/api/me", headers=as_user(token)).status_code == 401


def test_tampered_signature_rejected(client: TestClient) -> None:
    token = make_token(email=unique_email(), key=_OTHER_PRIVATE)
    assert client.get("/api/me", headers=as_user(token)).status_code == 401


def test_garbage_tokens_rejected(client: TestClient) -> None:
    assert client.get("/api/me", headers=as_user("abc.def.ghi")).status_code == 401
    assert client.get("/api/me", headers=as_user("not-a-jwt")).status_code == 401


def test_azp_allowlist_enforced(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLERK_AUTHORIZED_PARTIES", "https://skeptic.fyi")
    sub, email = f"user_{uuid.uuid4().hex[:10]}", unique_email()
    bad = make_token(sub, email, azp="https://phisher.example")
    assert client.get("/api/me", headers=as_user(bad)).status_code == 401
    good = make_token(sub, email, azp="https://skeptic.fyi")
    assert client.get("/api/me", headers=as_user(good)).status_code == 200


def test_session_rides_secondary_header_beside_service_bearer(
    client: TestClient,
) -> None:
    # the pre-launch proxy shape: owner token in Authorization, the user's
    # session in x-skeptic-session — identity must still resolve
    token = make_token(email=unique_email())
    r = client.get(
        "/api/me", headers={**as_service(), "x-skeptic-session": token}
    )
    assert r.status_code == 200


# ------------------------------------------- account creation + the grant


def test_first_login_creates_account_and_grants_exactly_once(
    client: TestClient,
) -> None:
    sub, email = f"user_{uuid.uuid4().hex[:10]}", unique_email()
    r = client.get("/api/me", headers=as_user(make_token(sub, email)))
    assert r.status_code == 200
    body = r.json()
    assert body["email"] == email
    assert body["credits"] == 5
    assert body["verified"] is True

    # retried and repeated sign-ins never re-grant
    for _ in range(3):
        again = client.get("/api/me", headers=as_user(make_token(sub, email)))
        assert again.json()["credits"] == 5

    with db.session() as s:
        users = s.query(db.User).filter(db.User.clerk_user_id == sub).all()
        assert len(users) == 1
        grants = (
            s.query(db.CreditLedger)
            .filter(
                db.CreditLedger.user_id == users[0].id,
                db.CreditLedger.reason == "signup_grant",
            )
            .all()
        )
        assert len(grants) == 1
        assert grants[0].delta == 5


def test_email_normalized_to_lowercase(client: TestClient) -> None:
    email = f"MiXeD-{uuid.uuid4().hex[:8]}@Example.COM"
    r = client.get("/api/me", headers=as_user(make_token(email=email)))
    assert r.status_code == 200
    assert r.json()["email"] == email.lower()


def test_unverified_email_claim_leaves_verified_false(client: TestClient) -> None:
    token = make_token(email=unique_email(), email_verified=False)
    r = client.get("/api/me", headers=as_user(token))
    assert r.status_code == 200
    assert r.json()["verified"] is False


def test_no_email_and_no_secret_means_no_account(client: TestClient) -> None:
    # without an email claim or CLERK_SECRET_KEY the traction record can't
    # get a real address — identity is refused, nothing is invented
    token = make_token()  # no email claim
    assert client.get("/api/me", headers=as_user(token)).status_code == 401


def test_email_resolved_via_clerk_api_fallback(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    email = unique_email()
    monkeypatch.setattr(clerk, "_fetch_user_email", lambda sub: (email, True))
    token = make_token()  # no email claim — forces the API path
    r = client.get("/api/me", headers=as_user(token))
    assert r.status_code == 200
    assert r.json()["email"] == email


def test_signup_grant_env_override(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SIGNUP_GRANT_CREDITS", "7")
    r = client.get("/api/me", headers=as_user(make_token(email=unique_email())))
    assert r.json()["credits"] == 7


def test_double_grant_impossible_at_the_database(client: TestClient) -> None:
    # belt and suspenders: even a buggy future code path cannot double-grant
    r = client.get("/api/me", headers=as_user(make_token(email=unique_email())))
    assert r.status_code == 200
    with db.session() as s:
        user = s.query(db.User).filter(db.User.email == r.json()["email"]).one()
        s.add(db.CreditLedger(user_id=user.id, delta=5, reason="signup_grant"))
        with pytest.raises(IntegrityError):
            s.commit()
        s.rollback()


def test_other_reasons_repeat_freely(client: TestClient) -> None:
    # the uniqueness backstop is scoped to the signup grant only
    r = client.get("/api/me", headers=as_user(make_token(email=unique_email())))
    with db.session() as s:
        user = s.query(db.User).filter(db.User.email == r.json()["email"]).one()
        s.add(db.CreditLedger(user_id=user.id, delta=-1, reason="run_debit"))
        s.add(db.CreditLedger(user_id=user.id, delta=-1, reason="run_debit"))
        s.commit()
    assert db.credit_balance(user.id) == 3


def test_accounts_refuse_on_sqlite_fallback(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(db, "FALLBACK_REASON", "neon unreachable (test)")
    token = make_token(email=unique_email())
    r = client.get("/api/me", headers=as_user(token))
    assert r.status_code == 503
    assert "accounts" in r.json()["detail"]
    # the automation principal is unaffected — the runs DB fallback is
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
    token = make_token(email=unique_email())
    statuses = [
        client.get("/api/me", headers=as_user(token)).status_code for _ in range(125)
    ]
    assert statuses[:120] == [200] * 120
    assert statuses[120:] == [429] * 5
    blocked = client.get("/api/me", headers=as_user(token))
    assert blocked.status_code == 429
    assert int(blocked.headers["retry-after"]) >= 1
