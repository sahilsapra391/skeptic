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

import logging
import os

import stripe

log = logging.getLogger("billing")


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
        success_url=success_url,
        cancel_url=cancel_url,
    )
    url = session.url
    if not url:  # defensive — a session with no hosted URL is unusable
        raise RuntimeError("stripe checkout session has no url")
    return url


def verify_webhook_event(payload: bytes, sig_header: str) -> stripe.Event:
    """Verify + parse a Stripe webhook. construct_event does the HMAC-SHA256
    signature check AND the timestamp/replay window — raising on a forged or
    stale payload. This is the ONLY trust boundary for granting credits."""
    # the stripe SDK ships no type stubs — construct_event is untyped
    return stripe.Webhook.construct_event(  # type: ignore[no-untyped-call, no-any-return]
        payload, sig_header, _webhook_secret()
    )
