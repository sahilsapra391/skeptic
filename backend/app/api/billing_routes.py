"""Billing endpoints (launch L3 — Stripe top-ups).

Two routes, two trust models:
  POST /api/checkout        signed-in user → a Stripe-hosted Checkout URL
  POST /api/stripe/webhook  Stripe → us; the ONLY place credits are granted

The webhook is the trust boundary: credits are granted only after Stripe's
signature verifies, never on the browser's success redirect (which is
forgeable). Grants are idempotent per Stripe event id (db.grant_purchase),
because Stripe redelivers events.
"""

from __future__ import annotations

import logging
import os

import stripe
from fastapi import APIRouter, HTTPException, Request

from app import billing, db
from app.auth import require_user

log = logging.getLogger("billing.routes")
router = APIRouter()


def _app_url() -> str:
    return os.environ.get("SKEPTIC_APP_URL", "https://skeptic.fyi").rstrip("/")


@router.post("/checkout")
def checkout(request: Request) -> dict[str, str]:
    """Start a purchase: create a Stripe Checkout session for the signed-in
    account and hand back its hosted URL for the browser to redirect to."""
    user = require_user(request)  # 401 / 503 if not a resolved account
    if not billing.checkout_configured():
        raise HTTPException(
            status_code=503,
            detail="purchases aren't available yet — credit top-ups are coming soon",
        )
    app = _app_url()
    try:
        url = billing.create_checkout_session(
            user_id=user.id,
            success_url=f"{app}/new?purchase=success",
            cancel_url=f"{app}/new?purchase=cancelled",
        )
    except Exception:
        log.exception("stripe checkout session create failed for %s", user.id)
        raise HTTPException(
            status_code=502,
            detail="couldn't reach the payment processor — try again in a moment",
        ) from None
    return {"url": url}


@router.post("/stripe/webhook")
async def stripe_webhook(request: Request) -> dict[str, bool]:
    """Stripe calls this DIRECTLY (no proxy, no session) — the signature is the
    only auth. On a completed Checkout, grant the account its credits, exactly
    once per event."""
    if not billing.webhook_configured():
        raise HTTPException(status_code=503, detail="stripe webhook not configured")
    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")
    try:
        event = billing.verify_webhook_event(payload, sig)
    except (stripe.SignatureVerificationError, ValueError):
        # forged / stale / malformed — refuse without touching the ledger
        log.warning("stripe webhook signature verification failed")
        raise HTTPException(status_code=400, detail="invalid signature") from None

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        user_id = session.get("client_reference_id")
        purpose = (session.get("metadata") or {}).get("purpose")
        # grant only for a fully-PAID session THAT WE created for credits
        # (Checkout can complete unpaid for async methods; the purpose marker
        # means a different future Stripe product can't mint credits here);
        # client_reference_id is the account we set at checkout, not the browser
        if (
            user_id
            and session.get("payment_status") == "paid"
            and purpose == billing.CHECKOUT_PURPOSE
        ):
            granted = db.grant_purchase(user_id, billing.purchase_credits(), event["id"])
            if granted:
                log.info(
                    "purchase: +%d credits to %s (event %s)",
                    billing.purchase_credits(), user_id, event["id"],
                )
            else:
                log.info("purchase event %s already processed — idempotent skip", event["id"])
    return {"received": True}
