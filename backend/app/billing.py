"""Stripe billing (launch L3): $10 one-time = 50 backtest credits.

Checkout is Stripe-HOSTED (the buy button redirects to Stripe). Credits are
granted ONLY by the signature-verified webhook — never the success redirect,
which a user can forge. Every grant is idempotent per Stripe EVENT id
(db.grant_purchase), because Stripe redelivers webhooks.

Dev / pre-config: with no STRIPE_SECRET_KEY / STRIPE_PRICE_ID, checkout refuses
cleanly and the webhook is unavailable — the app runs without Stripe keys, the
same way it runs without the mailer or Turnstile.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, cast

import stripe

log = logging.getLogger("billing")

# stamped on every Checkout session we create and REQUIRED by the webhook
# before it grants — so only OUR credit purchases mint credits, never some
# other future Stripe product or integration that happens to set a
# client_reference_id on the same account (defense-in-depth)
CHECKOUT_PURPOSE = "backtest_credits"

# Refund / dispute events that claw back a purchase's credits (launch L3
# money-exposure fix): charge.refunded fires on a Stripe refund; the dispute
# event fires when a cardholder files a chargeback. Both carry a payment_intent
# that links back to the granting purchase row, so the webhook can reverse it.
# We reverse the moment a dispute is OPENED (charge.dispute.created) rather than
# waiting for funds_withdrawn — protecting the credits the instant the buyer
# contests the charge. Idempotency lives per-payment in db.reverse_purchase, so
# a refund and a dispute on the same charge still reverse only once.
REVERSAL_EVENTS = frozenset({"charge.refunded", "charge.dispute.created"})


def _secret_key() -> str:
    return os.environ.get("STRIPE_SECRET_KEY", "")


def _webhook_secret() -> str:
    return os.environ.get("STRIPE_WEBHOOK_SECRET", "")


def _price_id() -> str:
    return os.environ.get("STRIPE_PRICE_ID", "")


def purchase_credits() -> int:
    """Credits granted per purchase (owner: $10 = 50). Env-overridable so a
    promo or a price change never needs a redeploy."""
    try:
        return max(1, int(os.environ.get("STRIPE_PURCHASE_CREDITS", "50")))
    except ValueError:
        return 50


def checkout_configured() -> bool:
    """Can we start a Checkout? Needs the secret key AND a price to sell."""
    return bool(_secret_key() and _price_id())


def webhook_configured() -> bool:
    return bool(_webhook_secret())


def create_checkout_session(user_id: str, success_url: str, cancel_url: str) -> str:
    """Create a Stripe Checkout session for this account; return its hosted
    URL. client_reference_id carries the user id so the webhook knows exactly
    which account to credit — the browser never asserts its own identity."""
    stripe.api_key = _secret_key()
    session = stripe.checkout.Session.create(
        mode="payment",
        line_items=[{"price": _price_id(), "quantity": 1}],
        client_reference_id=user_id,
        metadata={"purpose": CHECKOUT_PURPOSE},
        success_url=success_url,
        cancel_url=cancel_url,
    )
    url = session.url
    if not url:  # defensive — a session with no hosted URL is unusable
        raise RuntimeError("stripe checkout session has no url")
    return url


def verify_webhook_event(payload: bytes, sig_header: str) -> dict[str, Any]:
    """Verify a Stripe webhook and return its event as a plain dict.

    verify_header does the HMAC-SHA256 signature check AND the timestamp/replay
    window — raising SignatureVerificationError on a forged or stale payload.
    This is the ONLY trust boundary for granting credits.

    We deliberately do NOT use stripe.Webhook.construct_event: it additionally
    builds a typed StripeObject Event, and (a) that object's API is version-
    variant — stripe-python 15.x dropped dict.get(), so `session.get(...)` on it
    raised AttributeError and 500'd the live webhook — and (b) constructing it
    can itself raise on unusual-but-valid payloads. The bytes we just verified
    are authentic, so json.loads over the same payload is safe and version-proof.
    """
    # verify_header signs `f"{ts}.{payload}"`; handed bytes it interpolates the
    # b'...' repr and every signature mismatches (a 400) — decode to the exact
    # UTF-8 body Stripe signed first. (This is what construct_event does inside.)
    body = payload.decode("utf-8")
    # the stripe SDK ships no type stubs — verify_header is untyped
    stripe.WebhookSignature.verify_header(  # type: ignore[no-untyped-call]
        body, sig_header, _webhook_secret(), stripe.Webhook.DEFAULT_TOLERANCE
    )
    return cast("dict[str, Any]", json.loads(body))
