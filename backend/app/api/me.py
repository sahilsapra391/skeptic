"""Account surface (launch L1): who am I, and what's my balance.

The balance is computed from the append-only ledger on every read — there
is no stored balance to drift (PRD E).
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, Depends

from app import db
from app.auth import is_admin, require_user
from app.ratelimit import rate_limited

router = APIRouter()

_rate = rate_limited(
    "me",
    limit=int(os.environ.get("SKEPTIC_ME_RATE_LIMIT", "120")),
    window_s=60,
)


@router.get("/me")
def me(
    user: db.User = Depends(require_user),  # noqa: B008 — FastAPI dependency
    _: None = Depends(_rate),
) -> dict[str, Any]:
    return {
        "email": user.email,
        "credits": db.credit_balance(user.id),
        "verified": user.verified_at is not None,
        "createdAt": user.created_at.isoformat() if user.created_at else None,
        # launch L5: drives the admin-only nav link + /admin page gate. Derived
        # from the env allowlist, so it flips off the instant the email leaves it.
        "admin": is_admin(user),
    }
