"""Clerk session-token verification (owner decision D1 = managed auth).

Verification is local: the session JWT's signature checks against the
instance JWKS (fetched once and cached by PyJWKClient), issuer must match
CLERK_ISSUER, exp/iat enforced. No network round-trip per request; a JWKS
that can't be fetched fails CLOSED (the token is treated as invalid).

The plaintext-credential rule is structural here: passwords live at Clerk,
this process only ever sees short-lived signed JWTs.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any

import jwt
import requests
from jwt import PyJWKClient

log = logging.getLogger("auth.clerk")

CLERK_API_URL = "https://api.clerk.com/v1"

_jwks_lock = threading.Lock()
_jwks_client: PyJWKClient | None = None
_jwks_issuer: str | None = None


def configured() -> bool:
    return bool(os.environ.get("CLERK_ISSUER"))


def _issuer() -> str:
    return os.environ.get("CLERK_ISSUER", "").rstrip("/")


def _signing_key(token: str) -> Any:
    """JWKS lookup, cached per issuer. Module-level seam: tests patch this
    with their own keypair so no test ever needs a live Clerk instance."""
    global _jwks_client, _jwks_issuer
    issuer = _issuer()
    with _jwks_lock:
        if _jwks_client is None or _jwks_issuer != issuer:
            _jwks_client = PyJWKClient(
                f"{issuer}/.well-known/jwks.json", cache_keys=True, timeout=10
            )
            _jwks_issuer = issuer
        client = _jwks_client
    return client.get_signing_key_from_jwt(token).key


def verify(token: str) -> dict[str, Any] | None:
    """Claims when the session token is valid, None otherwise (fail closed)."""
    try:
        key = _signing_key(token)
        claims: dict[str, Any] = jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            issuer=_issuer(),
            options={"require": ["exp", "iat", "sub"]},
            leeway=10,
        )
    except jwt.PyJWTError:
        return None
    except Exception:
        # JWKS unreachable — sessions can't be proven, so none are accepted
        log.exception("clerk JWKS verification unavailable")
        return None
    allowed = os.environ.get("CLERK_AUTHORIZED_PARTIES", "")
    if allowed:
        parties = {p.strip() for p in allowed.split(",") if p.strip()}
        if claims.get("azp") not in parties:
            return None
    return claims


def resolve_email(claims: dict[str, Any]) -> tuple[str, bool] | None:
    """(email, verified) for the session's user. Prefers an `email` claim
    (owner step: session-token template), falls back to one Clerk API call
    at account creation — never per-request."""
    email = claims.get("email")
    if email:
        return str(email).strip().lower(), bool(claims.get("email_verified", True))
    return _fetch_user_email(str(claims.get("sub", "")))


def _fetch_user_email(clerk_user_id: str) -> tuple[str, bool] | None:
    secret = os.environ.get("CLERK_SECRET_KEY")
    if not secret or not clerk_user_id:
        return None
    try:
        r = requests.get(
            f"{CLERK_API_URL}/users/{clerk_user_id}",
            headers={"Authorization": f"Bearer {secret}"},
            timeout=10,
        )
    except requests.RequestException:
        log.exception("clerk user lookup failed")
        return None
    if r.status_code != 200:
        log.error("clerk user lookup returned %s", r.status_code)
        return None
    data = r.json()
    primary_id = data.get("primary_email_address_id")
    for entry in data.get("email_addresses") or []:
        if entry.get("id") == primary_id and entry.get("email_address"):
            verified = (entry.get("verification") or {}).get("status") == "verified"
            return str(entry["email_address"]).strip().lower(), verified
    return None
