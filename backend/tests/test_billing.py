"""Launch L3: Stripe top-ups. $10 one-time = 50 credits.

Credits are granted ONLY by the signature-verified webhook (never the browser
success redirect, which is forgeable), and exactly once per Stripe event id
(Stripe redelivers events). Checkout is Stripe-hosted; we just hand back a URL.
Stripe itself is stubbed here — the SDK's own crypto is not under test, our
wiring + idempotency + trust boundary are.
"""

from __future__ import annotations

import itertools
import uuid

import pytest
import stripe
from fastapi.testclient import TestClient

from app import billing, db
from app.main import app

PASSWORD = "correct-horse-battery"

# distinct octet from the other suites (credits 10.55 / anon 10.88 / auth 10.77)
_ip_counter = itertools.count(1)


def _fresh_ip() -> dict[str, str]:
    n = next(_ip_counter)
    return {"x-forwarded-for": f"10.44.{n // 250}.{n % 250}"}


def _email() -> str:
    return f"billing-{uuid.uuid4().hex[:10]}@example.com"


def _client() -> TestClient:
    return TestClient(app, base_url="https://testserver")


def _signup(client: TestClient, email: str) -> None:
    r = client.post(
        "/api/auth/signup", json={"email": email, "password": PASSWORD}, headers=_fresh_ip()
    )
    assert r.status_code == 200, r.text


def _credits(client: TestClient) -> int:
    return int(client.get("/api/me").json()["credits"])


def _uid(email: str) -> str:
    with db.session() as s:
        return s.query(db.User).filter(db.User.email == email.lower()).one().id


@pytest.fixture()
def stripe_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Dev gate open (no service token) + Stripe 'configured' for checkout and
    webhook. The Stripe SDK calls themselves are stubbed per test."""
    monkeypatch.setenv("SKEPTIC_ACCESS_TOKEN", "")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_x")
    monkeypatch.setenv("STRIPE_PRICE_ID", "price_x")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_x")


def _event(uid: str, event_id: str = "evt_1", *, paid: bool = True,
           etype: str = "checkout.session.completed") -> dict:
    return {
        "id": event_id,
        "type": etype,
        "data": {"object": {
            "client_reference_id": uid,
            "payment_status": "paid" if paid else "unpaid",
        }},
    }


# ----------------------------------------------------------------- checkout


def test_checkout_returns_a_hosted_url_for_a_signed_in_user(
    stripe_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client()
    email = _email()
    _signup(client, email)
    captured: dict[str, str] = {}

    def fake_create(user_id: str, success_url: str, cancel_url: str) -> str:
        captured.update(user_id=user_id, success_url=success_url, cancel_url=cancel_url)
        return "https://checkout.stripe.com/c/pay/cs_test_abc"

    monkeypatch.setattr(billing, "create_checkout_session", fake_create)
    r = client.post("/api/checkout")
    assert r.status_code == 200
    assert r.json()["url"].startswith("https://checkout.stripe.com/")
    # the ACCOUNT rides into the session server-side — the browser never
    # asserts its own identity to Stripe
    assert captured["user_id"] == _uid(email)
    assert "purchase=success" in captured["success_url"]


def test_checkout_requires_sign_in(stripe_env: None) -> None:
    r = _client().post("/api/checkout")  # anon, no session
    assert r.status_code == 401


def test_checkout_503_when_stripe_unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SKEPTIC_ACCESS_TOKEN", "")
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    monkeypatch.delenv("STRIPE_PRICE_ID", raising=False)
    client = _client()
    _signup(client, _email())
    r = client.post("/api/checkout")
    assert r.status_code == 503
    assert "coming soon" in r.json()["detail"]


# ------------------------------------------------------------------ webhook


def test_webhook_grants_credits_on_paid_checkout(
    stripe_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client()
    email = _email()
    _signup(client, email)
    uid = _uid(email)
    assert _credits(client) == 5
    monkeypatch.setattr(billing, "verify_webhook_event", lambda payload, sig: _event(uid))
    r = client.post("/api/stripe/webhook", content=b"{}", headers={"stripe-signature": "t=1,v1=x"})
    assert r.status_code == 200 and r.json()["received"] is True
    assert _credits(client) == 5 + billing.purchase_credits()


def test_webhook_is_idempotent_per_event(
    stripe_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client()
    email = _email()
    _signup(client, email)
    uid = _uid(email)
    monkeypatch.setattr(billing, "verify_webhook_event", lambda p, s: _event(uid, "evt_dup"))
    # Stripe redelivers the same event — the grant must happen exactly once
    for _ in range(3):
        assert client.post(
            "/api/stripe/webhook", content=b"{}", headers={"stripe-signature": "x"}
        ).status_code == 200
    assert _credits(client) == 5 + billing.purchase_credits()


def test_webhook_rejects_a_forged_signature(
    stripe_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client()
    email = _email()
    _signup(client, email)

    def boom(payload: bytes, sig: str) -> dict:
        raise stripe.SignatureVerificationError("bad sig", sig)

    monkeypatch.setattr(billing, "verify_webhook_event", boom)
    r = client.post("/api/stripe/webhook", content=b"{}", headers={"stripe-signature": "forged"})
    assert r.status_code == 400
    assert _credits(client) == 5  # a forged event never touches the ledger


def test_webhook_ignores_unpaid_and_unrelated_events(
    stripe_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client()
    email = _email()
    _signup(client, email)
    uid = _uid(email)
    # a completed-but-UNPAID session (async payment methods) grants nothing
    monkeypatch.setattr(
        billing, "verify_webhook_event", lambda p, s: _event(uid, "evt_u", paid=False)
    )
    client.post("/api/stripe/webhook", content=b"{}", headers={"stripe-signature": "x"})
    assert _credits(client) == 5
    # an unrelated event type is a no-op 200
    monkeypatch.setattr(
        billing,
        "verify_webhook_event",
        lambda p, s: _event(uid, "evt_o", etype="payment_intent.created"),
    )
    assert client.post(
        "/api/stripe/webhook", content=b"{}", headers={"stripe-signature": "x"}
    ).status_code == 200
    assert _credits(client) == 5


def test_webhook_503_when_secret_unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SKEPTIC_ACCESS_TOKEN", "")
    monkeypatch.delenv("STRIPE_WEBHOOK_SECRET", raising=False)
    r = _client().post("/api/stripe/webhook", content=b"{}", headers={"stripe-signature": "x"})
    assert r.status_code == 503


# --------------------------------------------------------------- db unit


def test_grant_purchase_is_idempotent_per_event(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SKEPTIC_ACCESS_TOKEN", "")
    client = _client()
    email = _email()
    _signup(client, email)
    uid = _uid(email)
    assert db.grant_purchase(uid, 50, "evt_A") is True
    assert db.grant_purchase(uid, 50, "evt_A") is False  # same event → no double grant
    assert db.grant_purchase(uid, 50, "evt_B") is True  # a different event grants
    assert _credits(client) == 5 + 100
