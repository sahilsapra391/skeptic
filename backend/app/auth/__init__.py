"""Auth interface (launch L1). Provider-agnostic by construction: routes
and middleware see only gate_allows / require_user, so swapping Clerk for
a self-rolled provider later touches app/auth/* alone (owner decision D1).

Two independent facts about a request:

- SERVICE — the Authorization bearer equals SKEPTIC_ACCESS_TOKEN. The
  automation principal (nightly-improve, workflows, the pre-launch proxy).
  It authenticates the SYSTEM and can never act as a person: it resolves
  to no users row and /api/me refuses it.
- USER — a Clerk session JWT proves a person. It rides the Authorization
  bearer when that slot is free, or x-skeptic-session when the bearer
  already carries the service token (the pre-launch proxy sends both).

Identity resolution is LAZY (review finding): the JWT verify + users-row
lookup (and, once, account creation) run only when something actually
needs the person — the gate on a user-surface path, or a route's
require_user. Chart/run traffic that happens to carry a session header
costs zero extra DB work, and an accounts-DB outage 503s only account
surfaces, never charts (the whole point of the SQLite-fallback refusal).

The gate keeps today's semantics exactly: with SKEPTIC_ACCESS_TOKEN set,
the service bearer passes everything; a verified user session additionally
passes USER_PATH_PREFIXES (grown chunk by chunk — L2 opens the run
routes). With no token configured (local dev), everything stays open.
"""

from __future__ import annotations

import hmac
import os
from typing import cast

from fastapi import HTTPException, Request

from app import db
from app.auth import clerk
from app.auth.accounts import AccountsUnavailableError, user_from_claims

__all__ = [
    "AccountsUnavailableError",
    "USER_PATH_PREFIXES",
    "gate_allows",
    "is_service",
    "require_user",
    "resolve_user",
]

# the surface a signed-in user may reach without the service token —
# deliberately small; each launch chunk widens it explicitly
USER_PATH_PREFIXES: tuple[str, ...] = ("/api/me",)

# request.state sentinel: "not resolved yet" must be distinguishable from
# "resolved to no user"
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


def _session_jwt(request: Request) -> str | None:
    header = request.headers.get("x-skeptic-session")
    if header:
        return header
    token = _bearer(request)
    # a JWT-shaped bearer is a session when it isn't the service token
    # (direct API callers and the L4 public mode use this slot)
    if token and token.count(".") == 2 and not is_service(request):
        return token
    return None


def session_presented(request: Request) -> bool:
    return clerk.configured() and _session_jwt(request) is not None


def resolve_user(request: Request) -> db.User | None:
    """The person behind the request — memoized per request, resolved on
    first need. Raises AccountsUnavailableError when a session is presented
    while the accounts DB sits on the throwaway SQLite fallback."""
    cached = getattr(request.state, "auth_user", _UNRESOLVED)
    if cached is not _UNRESOLVED:
        return cast("db.User | None", cached)
    user: db.User | None = None
    if clerk.configured():
        session_jwt = _session_jwt(request)
        if session_jwt:
            claims = clerk.verify(session_jwt)
            if claims is not None:
                user = user_from_claims(claims)
    request.state.auth_user = user
    return user


def _on_user_surface(path: str) -> bool:
    # segment-aware: "/api/me" and "/api/me/…" match, "/api/messages" must
    # not (review finding — bare startswith leaked sibling routes)
    return any(path == p or path.startswith(p + "/") for p in USER_PATH_PREFIXES)


def gate_allows(request: Request) -> bool:
    """Pass/block for the middleware. Only resolves identity when the path
    actually requires a person. May raise AccountsUnavailableError."""
    if not os.environ.get("SKEPTIC_ACCESS_TOKEN"):
        return True  # local dev — unchanged behavior
    if is_service(request):
        return True
    if _on_user_surface(request.url.path):
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
        # a session WAS offered but no account came of it: expired/invalid
        # token, or (operator misconfig) no email claim and no
        # CLERK_SECRET_KEY to resolve one — say so instead of a bare
        # "sign in required" to an already-signed-in person
        raise HTTPException(
            status_code=401,
            detail="session not accepted — sign in again; if this persists, "
            "the Clerk session token lacks an email claim and "
            "CLERK_SECRET_KEY is unset",
        )
    if is_service(request):
        raise HTTPException(
            status_code=401,
            detail="the service token is not a user account — sign in for "
            "account surfaces",
        )
    raise HTTPException(status_code=401, detail="sign in required")
