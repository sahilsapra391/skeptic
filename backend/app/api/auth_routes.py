"""Account endpoints (launch L1b, self-rolled). Every route rate-limited;
login additionally keyed per-account so a distributed guesser can't spread
across IPs. The session rides an httpOnly cookie set HERE (the Next proxy
forwards Set-Cookie); failures are uniform — no account-existence oracle.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from app import db
from app.auth import mailer
from app.auth import password as pw
from app.ratelimit import SlidingWindowLimiter, client_ip, rate_limited

log = logging.getLogger("auth.routes")
router = APIRouter()

SESSION_COOKIE = "skeptic_session"
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# login gets its own per-ACCOUNT window on top of the per-IP dependency —
# 10 attempts per account per 15 minutes, wherever they come from
_account_limiter = SlidingWindowLimiter(limit=10, window_s=900)

# GLOBAL signup backstop: the per-IP window trusts x-forwarded-for, which a
# direct-to-backend caller controls (review finding) — this caps total
# account creation per day regardless of source. The real fix (Turnstile +
# signed anon token) lands with the anon-armor chunk; this bounds the
# damage until then.
_signup_global = SlidingWindowLimiter(
    limit=int(os.environ.get("SKEPTIC_SIGNUP_DAILY_MAX", "500")), window_s=86_400
)

_signup_rate = rate_limited("auth-signup", limit=10, window_s=3600)
_login_rate = rate_limited("auth-login", limit=30, window_s=900)
_verify_rate = rate_limited("auth-verify", limit=30, window_s=3600)
_resend_rate = rate_limited("auth-resend", limit=3, window_s=3600)


class SignupRequest(BaseModel):
    email: str = Field(max_length=320)
    password: str = Field(max_length=200)
    # the claim flow (owner): the runs this device made before the account
    # existed come with it — the conversion moment
    claim_run_ids: list[str] = Field(default_factory=list, max_length=50)


class LoginRequest(BaseModel):
    email: str = Field(max_length=320)
    password: str = Field(max_length=200)


class VerifyRequest(BaseModel):
    token: str = Field(max_length=200)


def _refuse_on_fallback() -> None:
    if db.FALLBACK_REASON is not None:
        raise HTTPException(
            status_code=503,
            detail="accounts are unavailable — the accounts database is "
            "unreachable right now",
        )


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=int(pw.SESSION_TTL.total_seconds()),
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
    )


def _claim_runs(user_id: str, run_ids: list[str]) -> int:
    """Re-parent this device's pre-account runs. Only UNOWNED, user-origin
    runs are claimable — nobody claims someone else's work or a system run.
    The pinned showcase runs are never claimable (claiming one would strip
    it from every visitor's library)."""
    from app.api.runs import example_run_ids

    examples = set(example_run_ids())
    ids = [x.strip() for x in run_ids if x.strip() and x.strip() not in examples][:50]
    if not ids:
        return 0
    with db.session() as s:
        claimed = (
            s.query(db.Run)
            .filter(
                db.Run.id.in_(ids),
                db.Run.user_id.is_(None),
                db.Run.origin == "user",
            )
            .update({db.Run.user_id: user_id}, synchronize_session=False)
        )
        s.commit()
    if claimed:
        log.info("account %s claimed %d run(s)", user_id, claimed)
    return int(claimed)


def _me(user: db.User, *, claimed: int | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {
        "email": user.email,
        "credits": db.credit_balance(user.id),
        "verified": user.verified_at is not None,
    }
    if claimed is not None:
        out["claimedRuns"] = claimed
    return out


@router.post("/auth/signup")
def signup(
    req: SignupRequest,
    response: Response,
    _: None = Depends(_signup_rate),
) -> dict[str, Any]:
    _refuse_on_fallback()
    global_ok, _retry = _signup_global.check("signup")
    if not global_ok:
        raise HTTPException(
            status_code=429,
            detail="sign-ups are paused for the day — try again tomorrow",
        )
    email = req.email.strip().lower()
    if not _EMAIL_RE.match(email):
        raise HTTPException(status_code=422, detail="that doesn't look like an email address")
    if len(req.password) < pw.MIN_PASSWORD_LEN:
        raise HTTPException(
            status_code=422,
            detail=f"password needs at least {pw.MIN_PASSWORD_LEN} characters",
        )
    user = pw.create_account(email, req.password)
    if user is None:
        # same email → sign in instead. This does reveal registration on
        # SIGNUP (unavoidable for a usable form); login stays uniform.
        raise HTTPException(status_code=409, detail="that email already has an account — sign in")
    claimed = _claim_runs(user.id, req.claim_run_ids)
    delivered = mailer.send_verification(user.email, pw.issue_verify_token(user.id))
    _set_session_cookie(response, pw.create_session(user.id))
    out = _me(user, claimed=claimed)
    out["verificationSent"] = delivered
    return out


@router.post("/auth/login")
def login(
    req: LoginRequest,
    response: Response,
    request: Request,
    _: None = Depends(_login_rate),
) -> dict[str, Any]:
    _refuse_on_fallback()
    email = req.email.strip().lower()
    allowed, retry = _account_limiter.check(f"acct:{email}")
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="too many attempts for this account — try again later",
            headers={"Retry-After": str(int(retry) + 1)},
        )
    user = pw.user_by_email(email)
    # verify against a burned dummy hash when the user is unknown so the
    # timing of "no such account" matches "wrong password"
    ok = pw.verify_password(req.password, user.password_hash) if user and user.password_hash \
        else (pw.verify_password(req.password, _DUMMY_HASH) and False)
    if not ok or user is None:
        log.info("failed login from %s", client_ip(request))
        raise HTTPException(status_code=401, detail="wrong email or password")
    _set_session_cookie(response, pw.create_session(user.id))
    return _me(user)


@router.post("/auth/logout")
def logout(request: Request, response: Response) -> dict[str, Any]:
    token = request.cookies.get(SESSION_COOKIE) or request.headers.get("x-skeptic-session", "")
    if token:
        pw.revoke_session(token)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@router.post("/auth/verify")
def verify(req: VerifyRequest, _: None = Depends(_verify_rate)) -> dict[str, Any]:
    _refuse_on_fallback()
    user = pw.consume_verify_token(req.token)
    if user is None:
        raise HTTPException(
            status_code=400,
            detail="this verification link is invalid, used, or expired — "
            "request a fresh one from your account",
        )
    return _me(user)


@router.post("/auth/resend-verification")
def resend(request: Request, _: None = Depends(_resend_rate)) -> dict[str, Any]:
    _refuse_on_fallback()
    from app.auth import resolve_user

    user = resolve_user(request)
    if user is None:
        raise HTTPException(status_code=401, detail="sign in required")
    if user.verified_at is not None:
        return {"ok": True, "verified": True}
    delivered = mailer.send_verification(user.email, pw.issue_verify_token(user.id))
    return {"ok": True, "verified": False, "verificationSent": delivered}


# a real argon2id hash, burned once at import — the unknown-account login
# path verifies against it so its timing matches a wrong password
_DUMMY_HASH = pw.hash_password("skeptic-dummy-timing-pad")
