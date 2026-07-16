"""User rows + the signup grant (launch L1).

First verified sighting of a Clerk session creates the users row and the
one-time 5-credit grant IN ONE TRANSACTION — the account cannot exist
without its grant, and the partial unique index on the ledger makes a
double grant impossible even under races or retries.

The runs DB deliberately falls back to a throwaway local SQLite when the
configured database is unreachable (db.init_db). Accounts and credits must
NOT inherit that: a ledger written to a container-local file evaporates on
redeploy. When the fallback is active, user auth refuses loudly (503)
while charts and the parser stay up.
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.exc import IntegrityError

from app import db
from app.auth import clerk

log = logging.getLogger("auth.accounts")

GRANT_REASON = "signup_grant"


class AccountsUnavailableError(Exception):
    """The accounts database is unreachable — refuse rather than write
    grants/debits to the throwaway SQLite fallback."""


def signup_grant_credits() -> int:
    raw = os.environ.get("SIGNUP_GRANT_CREDITS", "5")
    try:
        return max(0, int(raw))
    except ValueError:
        log.error("SIGNUP_GRANT_CREDITS=%r is not an integer — using 5", raw)
        return 5


def user_from_claims(claims: dict[str, Any]) -> db.User | None:
    """The users row for a verified session — created on first sighting,
    with the signup grant, atomically. None when no identity can be
    established (missing sub, unresolvable email, or an email already
    claimed by a different account)."""
    if db.FALLBACK_REASON is not None:
        raise AccountsUnavailableError(db.FALLBACK_REASON)
    sub = str(claims.get("sub") or "")
    if not sub:
        return None
    with db.session() as s:
        existing = s.query(db.User).filter(db.User.clerk_user_id == sub).one_or_none()
    if existing is not None:
        return existing

    resolved = clerk.resolve_email(claims)
    if resolved is None:
        # no email claim and no CLERK_SECRET_KEY to look it up — the
        # traction record never gets a row without a real address
        log.error(
            "cannot resolve email for new account — add an `email` claim to the "
            "Clerk session token or set CLERK_SECRET_KEY"
        )
        return None
    email, verified = resolved

    with db.session() as s:
        try:
            user = db.User(
                id=uuid.uuid4().hex[:12],
                email=email,
                clerk_user_id=sub,
                verified_at=datetime.now(UTC) if verified else None,
            )
            s.add(user)
            s.flush()
            s.add(
                db.CreditLedger(
                    user_id=user.id, delta=signup_grant_credits(), reason=GRANT_REASON
                )
            )
            s.commit()
            log.info("new account %s — signup grant %d", user.id, signup_grant_credits())
            return user
        except IntegrityError:
            s.rollback()
            # lost a race to ourselves (same sub, two first requests) — the
            # winner's row is the account
            racer = s.query(db.User).filter(db.User.clerk_user_id == sub).one_or_none()
            if racer is not None:
                return racer
            # same email under a DIFFERENT sub — a stale local row from a
            # re-created Clerk instance; refuse identity rather than guess
            log.error("email already registered to another account (sub %s)", sub)
            return None
