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


def _event(
    uid: str,
    event_id: str = "evt_1",
    *,
    paid: bool = True,
    etype: str = "checkout.session.completed",
    purpose: str | None = "backtest_credits",
    payment_intent: str | None = None,
) -> dict:
    obj: dict = {
        "client_reference_id": uid,
        "payment_status": "paid" if paid else "unpaid",
        # a real Checkout carries the payment_intent; default it per-event so
        # distinct grants get distinct payment ids (a later refund keys on it)
        "payment_intent": payment_intent or f"pi_{event_id}",
    }
    if purpose is not None:
        obj["metadata"] = {"purpose": purpose}
    return {"id": event_id, "type": etype, "data": {"object": obj}}


def _reversal(
    payment_intent: str, event_id: str, *, etype: str = "charge.refunded"
) -> dict:
    """A refund (charge.refunded → a Charge) or a dispute (charge.dispute.created
    → a Dispute) webhook. Both objects expose the payment_intent that links back
    to the granting purchase row."""
    return {
        "id": event_id,
        "type": etype,
        "data": {"object": {"payment_intent": payment_intent}},
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


def test_webhook_ignores_a_paid_session_without_our_marker(
    stripe_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Defense-in-depth: a paid session from some OTHER Stripe product/integration
    that happens to set a client_reference_id must NOT mint credits — only a
    session we created (carrying the purpose marker) grants."""
    client = _client()
    email = _email()
    _signup(client, email)
    uid = _uid(email)
    monkeypatch.setattr(
        billing, "verify_webhook_event", lambda p, s: _event(uid, "evt_nomark", purpose=None)
    )
    client.post("/api/stripe/webhook", content=b"{}", headers={"stripe-signature": "x"})
    assert _credits(client) == 5  # no marker → no grant


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


# ----------------------------------------------------- refund / dispute reversal


def _buy(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, uid: str, pi: str
) -> None:
    """Grant credits through a paid Checkout webhook carrying payment_intent pi."""
    monkeypatch.setattr(
        billing, "verify_webhook_event", lambda p, s: _event(uid, f"buy_{pi}", payment_intent=pi)
    )
    r = client.post("/api/stripe/webhook", content=b"{}", headers={"stripe-signature": "x"})
    assert r.status_code == 200


def _deliver(client: TestClient, monkeypatch: pytest.MonkeyPatch, event: dict) -> None:
    monkeypatch.setattr(billing, "verify_webhook_event", lambda p, s: event)
    r = client.post("/api/stripe/webhook", content=b"{}", headers={"stripe-signature": "x"})
    assert r.status_code == 200


def test_refund_reverses_the_grant_and_redelivery_is_a_noop(
    stripe_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client()
    email = _email()
    _signup(client, email)
    uid = _uid(email)
    pi = f"pi_{uuid.uuid4().hex[:12]}"
    _buy(client, monkeypatch, uid, pi)
    assert _credits(client) == 5 + billing.purchase_credits()
    # the buyer gets a refund — the credits it granted are clawed back. Stripe
    # redelivers the event; the reversal must still happen exactly once.
    for _ in range(3):
        _deliver(client, monkeypatch, _reversal(pi, "evt_refund"))
    assert _credits(client) == 5


def test_dispute_created_reverses_the_grant(
    stripe_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client()
    email = _email()
    _signup(client, email)
    uid = _uid(email)
    pi = f"pi_{uuid.uuid4().hex[:12]}"
    _buy(client, monkeypatch, uid, pi)
    _deliver(client, monkeypatch, _reversal(pi, "evt_disp", etype="charge.dispute.created"))
    assert _credits(client) == 5


def test_refund_and_dispute_on_same_charge_reverse_only_once(
    stripe_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A refund and a dispute can both land for one charge (two event ids). The
    money left our account once, so the credits are clawed back only once —
    idempotency is per PAYMENT, not per event."""
    client = _client()
    email = _email()
    _signup(client, email)
    uid = _uid(email)
    pi = f"pi_{uuid.uuid4().hex[:12]}"
    _buy(client, monkeypatch, uid, pi)
    _deliver(client, monkeypatch, _reversal(pi, "evt_refund"))
    assert _credits(client) == 5
    _deliver(client, monkeypatch, _reversal(pi, "evt_disp", etype="charge.dispute.created"))
    assert _credits(client) == 5  # not clawed back a second time


def test_unrelated_dispute_does_not_reverse(
    stripe_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client()
    email = _email()
    _signup(client, email)
    uid = _uid(email)
    pi = f"pi_{uuid.uuid4().hex[:12]}"
    _buy(client, monkeypatch, uid, pi)
    # a dispute for some OTHER payment we never granted for — must not touch this
    # account's balance
    _deliver(
        client, monkeypatch, _reversal("pi_not_ours", "evt_x", etype="charge.dispute.created")
    )
    assert _credits(client) == 5 + billing.purchase_credits()


def test_chargeback_can_drive_the_balance_negative(monkeypatch: pytest.MonkeyPatch) -> None:
    """The buyer spends the credits, THEN files a chargeback — balance goes
    negative and they can't run again until they re-buy. That's correct."""
    monkeypatch.setenv("SKEPTIC_ACCESS_TOKEN", "")
    client = _client()
    email = _email()
    _signup(client, email)
    uid = _uid(email)
    tag = uuid.uuid4().hex[:12]
    pi = f"pi_{tag}"
    assert db.grant_purchase(uid, 50, f"evt_g_{tag}", payment_intent=pi) is True
    assert _credits(client) == 55
    with db.session() as s:  # spend everything (5 signup + 50 bought)
        s.add(db.CreditLedger(user_id=uid, delta=-55, reason="run_debit", run_id=f"r_{tag}"))
        s.commit()
    assert _credits(client) == 0
    assert db.reverse_purchase(pi, f"evt_cb_{tag}") is True
    assert _credits(client) == -50


def test_reverse_purchase_is_idempotent_per_payment_and_exact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SKEPTIC_ACCESS_TOKEN", "")
    client = _client()
    email = _email()
    _signup(client, email)
    uid = _uid(email)
    tag = uuid.uuid4().hex[:12]
    pi = f"pi_{tag}"
    # a promo-sized grant (30, not the 50 constant) — the reversal must match it
    assert db.grant_purchase(uid, 30, f"evt_g_{tag}", payment_intent=pi) is True
    assert _credits(client) == 35
    assert db.reverse_purchase(pi, f"evt_cb1_{tag}") is True  # reverses exactly 30
    assert db.reverse_purchase(pi, f"evt_cb2_{tag}") is False  # same payment → no double
    assert db.reverse_purchase(f"pi_unknown_{tag}", f"evt_cb3_{tag}") is False  # no purchase
    assert _credits(client) == 5
