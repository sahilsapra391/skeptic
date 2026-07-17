"""Auth interface (launch L1b — self-rolled, owner decision reversing D1).
Routes and middleware see only gate_allows / require_user / resolve_user;
the provider behind them changed from Clerk JWTs to in-house sessions
without touching a single route — the swappable-interface promise kept.

Principals per request:

- SERVICE — the Authorization bearer equals SKEPTIC_ACCESS_TOKEN. The
  automation principal (nightly-improve, workflows, the pre-launch proxy).
  It authenticates the SYSTEM and can never act as a person.
- USER — an opaque session token (httpOnly cookie `skeptic_session`, or
  x-skeptic-session when a caller prefers the header) resolved against
  the auth_sessions table. DB truth: revocation works instantly.

Identity resolution stays LAZY (L1 review finding): only paths that need
a person pay the DB lookup, and an accounts-DB outage 503s only account
surfaces — charts and the automation lane stay up.

The gate: service passes everything (unchanged since the single-user
era); a signed-in user passes the app surface (USER_PATH_PREFIXES); the
auth endpoints and health stay open to everyone (you can't sign in from
behind a sign-in gate). With SKEPTIC_ACCESS_TOKEN unset (local dev),
everything is open. Anonymous armored run access arrives with the
anon-trial chunk — until the public flip, the proxy's service bearer
keeps today's behavior byte-identical.
"""

from __future__ import annotations

import hmac
import os
from typing import cast

from fastapi import HTTPException, Request

from app import db
from app.auth.accounts import AccountsUnavailableError

__all__ = [
    "AccountsUnavailableError",
    "OPEN_PATH_PREFIXES",
    "USER_PATH_PREFIXES",
    "gate_allows",
    "is_service",
    "require_user",
    "require_verified_user",
    "resolve_user",
]

# reachable without ANY principal: probes + the doorway itself
OPEN_PATH_PREFIXES: tuple[str, ...] = ("/api/health", "/api/auth")

# the surface a signed-in user may reach — the app (launch L1b widened
# this from just /api/me: the product is account-gated now)
USER_PATH_PREFIXES: tuple[str, ...] = (
    "/api/me",
    "/api/parse",
    "/api/backtest",
    "/api/runs",
    "/api/sweep",
    "/api/data",
)

_UNRESOLVED = object()


def _bearer(request: Request) -> str | None:
    supplied = request.headers.get("authorization", "")
    if supplied.startswith("Bearer "):
        return supplied[len("Bearer ") :]
    return None


def is_service(request: Request) -> bool:
    token = _bearer(request)
    service_token = os.environ.get("SKEPTIC_ACCESS_TOKEN", "")
    return bool(
        service_token
        and token
        and hmac.compare_digest(token.encode(), service_token.encode())
    )


def _session_token(request: Request) -> str | None:
    return (
        request.headers.get("x-skeptic-session")
        or request.cookies.get("skeptic_session")
        or None
    )


def session_presented(request: Request) -> bool:
    return _session_token(request) is not None


def resolve_user(request: Request) -> db.User | None:
    """The person behind the request — memoized per request, resolved on
    first need. Raises AccountsUnavailableError when a session is presented
    while the accounts DB sits on the throwaway SQLite fallback."""
    cached = getattr(request.state, "auth_user", _UNRESOLVED)
    if cached is not _UNRESOLVED:
        return cast("db.User | None", cached)
    user: db.User | None = None
    token = _session_token(request)
    if token:
        if db.FALLBACK_REASON is not None:
            raise AccountsUnavailableError(db.FALLBACK_REASON)
        from app.auth import password as pw

        user = pw.resolve_session(token)
    request.state.auth_user = user
    return user


def _on(path: str, prefixes: tuple[str, ...]) -> bool:
    # segment-aware: "/api/me" matches "/api/me/…", never "/api/messages"
    return any(path == p or path.startswith(p + "/") for p in prefixes)


def gate_allows(request: Request) -> bool:
    """Pass/block for the middleware. Only resolves identity when the path
    actually requires a person. May raise AccountsUnavailableError."""
    if not os.environ.get("SKEPTIC_ACCESS_TOKEN"):
        return True  # local dev — unchanged behavior
    path = request.url.path
    if _on(path, OPEN_PATH_PREFIXES):
        return True
    if is_service(request):
        return True
    if _on(path, USER_PATH_PREFIXES):
        return resolve_user(request) is not None
    return False


def require_user(request: Request) -> db.User:
    """FastAPI dependency: the person behind the request, or an honest
    refusal that names the actual problem."""
    try:
        user = resolve_user(request)
    except AccountsUnavailableError as exc:
        raise HTTPException(
            status_code=503,
            detail="accounts are unavailable — the accounts database is "
            "unreachable right now; charts and existing runs stay up",
        ) from exc
    if user is not None:
        return user
    if session_presented(request):
        raise HTTPException(
            status_code=401,
            detail="session expired or signed out — sign in again",
        )
    if is_service(request):
        raise HTTPException(
            status_code=401,
            detail="the service token is not a user account — sign in for "
            "account surfaces",
        )
    raise HTTPException(status_code=401, detail="sign in required")


def require_verified_user(request: Request) -> db.User:
    """require_user + the email-verification bar, enforced only once a
    sender exists (env SKEPTIC_REQUIRE_VERIFIED=1 — owner flips it after
    configuring Resend/SMTP; before that, blocking would strand everyone)."""
    user = require_user(request)
    if os.environ.get("SKEPTIC_REQUIRE_VERIFIED") == "1" and user.verified_at is None:
        raise HTTPException(
            status_code=403,
            detail="verify your email to run backtests — check your inbox "
            "or resend the link from your account",
        )
    return user
