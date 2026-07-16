"""Auth interface (launch L1). Provider-agnostic by construction: routes
and middleware see only AuthContext / require_user, so swapping Clerk for
a self-rolled provider later touches app/auth/* alone (owner decision D1).

Two independent facts are resolved per request:

- SERVICE — the Authorization bearer equals SKEPTIC_ACCESS_TOKEN. The
  automation principal (nightly-improve, workflows, the pre-launch proxy).
  It authenticates the SYSTEM and can never act as a person: it resolves
  to no users row and /api/me refuses it.
- USER — a Clerk session JWT proves a person. It rides the Authorization
  bearer when that slot is free, or x-skeptic-session when the bearer
  already carries the service token (the pre-launch proxy sends both).

The gate keeps today's semantics exactly: with SKEPTIC_ACCESS_TOKEN set,
the service bearer passes everything; a verified user session additionally
passes USER_PATH_PREFIXES (grown chunk by chunk — L2 opens the run
routes). With no token configured (local dev), everything stays open.
"""

from __future__ import annotations

import hmac
import os
from dataclasses import dataclass

from fastapi import HTTPException, Request

from app import db
from app.auth import clerk
from app.auth.accounts import AccountsUnavailableError, user_from_claims

__all__ = [
    "AccountsUnavailableError",
    "AuthContext",
    "USER_PATH_PREFIXES",
    "authenticate",
    "gate_allows",
    "require_user",
]

# the surface a signed-in user may reach without the service token —
# deliberately small; each launch chunk widens it explicitly
USER_PATH_PREFIXES: tuple[str, ...] = ("/api/me",)


@dataclass(frozen=True)
class AuthContext:
    service: bool = False
    user: db.User | None = None


def _bearer(request: Request) -> str | None:
    supplied = request.headers.get("authorization", "")
    if supplied.startswith("Bearer "):
        return supplied[len("Bearer ") :]
    return None


def authenticate(request: Request) -> AuthContext:
    """Resolve both principals. Raises AccountsUnavailableError when a user
    session is presented but the accounts DB is on the SQLite fallback."""
    token = _bearer(request)
    service_token = os.environ.get("SKEPTIC_ACCESS_TOKEN", "")
    service = bool(
        service_token
        and token
        and hmac.compare_digest(token.encode(), service_token.encode())
    )

    user: db.User | None = None
    if clerk.configured():
        session_jwt = request.headers.get("x-skeptic-session")
        if not session_jwt and token and not service and token.count(".") == 2:
            session_jwt = token
        if session_jwt:
            claims = clerk.verify(session_jwt)
            if claims is not None:
                user = user_from_claims(claims)
    return AuthContext(service=service, user=user)


def gate_allows(path: str, ctx: AuthContext) -> bool:
    if not os.environ.get("SKEPTIC_ACCESS_TOKEN"):
        return True  # local dev — unchanged behavior
    if ctx.service:
        return True
    return ctx.user is not None and path.startswith(USER_PATH_PREFIXES)


def require_user(request: Request) -> db.User:
    """FastAPI dependency: the person behind the request, or 401."""
    ctx: AuthContext | None = getattr(request.state, "auth", None)
    if ctx is None or ctx.user is None:
        detail = (
            "the service token is not a user account — sign in for account surfaces"
            if ctx is not None and ctx.service
            else "sign in required"
        )
        raise HTTPException(status_code=401, detail=detail)
    return ctx.user
