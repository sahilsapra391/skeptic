"""Self-rolled credential + session machinery (launch L1b, owner decision:
no third party in the login path).

Security posture (build plan, non-negotiable):
- Plaintext passwords NEVER persist — not in the DB, not in logs, not in
  error traces. They exist only as function arguments here, hashed with
  argon2id (per-user salt is argon2's own) before anything touches storage.
- Session and email tokens are opaque random values; the DB stores only
  SHA-256 digests, so a leaked table replays nothing.
- Verification is constant-time via passlib; login failures are uniform
  ("wrong email or password") — no account-existence oracle.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from passlib.context import CryptContext

from app import db

log = logging.getLogger("auth.password")

SESSION_TTL = timedelta(days=30)
VERIFY_TTL = timedelta(days=3)
MIN_PASSWORD_LEN = 10

# argon2id with passlib defaults (m=64MiB, t=3, p=4 as of argon2-cffi's
# RFC-9106 low-memory profile) — tuned fine for the 8GB box given the
# per-IP+account login rate limits
_pwd = CryptContext(schemes=["argon2"], deprecated="auto")


def hash_password(password: str) -> str:
    return str(_pwd.hash(password))


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bool(_pwd.verify(password, password_hash))
    except Exception:  # malformed hash (e.g. a managed-auth-era NULL) — refuse
        return False


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# ------------------------------------------------------------- sessions


def create_session(user_id: str) -> str:
    """Returns the raw token for the cookie; only its digest is stored."""
    token = secrets.token_urlsafe(32)
    with db.session() as s:
        s.add(
            db.AuthSession(
                token_hash=_digest(token),
                user_id=user_id,
                expires_at=datetime.now(UTC) + SESSION_TTL,
            )
        )
        s.commit()
    return token


def resolve_session(token: str) -> db.User | None:
    """The live user behind a session token — None for unknown, expired,
    or revoked sessions (fail closed, no distinction leaked)."""
    if not token:
        return None
    with db.session() as s:
        sess = s.get(db.AuthSession, _digest(token))
        if sess is None or sess.revoked_at is not None:
            return None
        expires = sess.expires_at
        if expires.tzinfo is None:  # SQLite round-trips naive datetimes
            expires = expires.replace(tzinfo=UTC)
        if expires <= datetime.now(UTC):
            return None
        return s.get(db.User, sess.user_id)


def revoke_session(token: str) -> None:
    with db.session() as s:
        sess = s.get(db.AuthSession, _digest(token))
        if sess is not None and sess.revoked_at is None:
            sess.revoked_at = datetime.now(UTC)
            s.commit()


# ------------------------------------------------------- email verification


def issue_verify_token(user_id: str) -> str:
    token = secrets.token_urlsafe(32)
    with db.session() as s:
        s.add(
            db.EmailToken(
                token_hash=_digest(token),
                user_id=user_id,
                purpose="verify",
                expires_at=datetime.now(UTC) + VERIFY_TTL,
            )
        )
        s.commit()
    return token


def consume_verify_token(token: str) -> db.User | None:
    """Single-use: marks the token used and stamps users.verified_at.
    Returns the verified user, or None (invalid/expired/used)."""
    if not token:
        return None
    now = datetime.now(UTC)
    with db.session() as s:
        row = s.get(db.EmailToken, _digest(token))
        if row is None or row.purpose != "verify" or row.used_at is not None:
            return None
        expires = row.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=UTC)
        if expires <= now:
            return None
        user = s.get(db.User, row.user_id)
        if user is None:
            return None
        row.used_at = now
        if user.verified_at is None:
            user.verified_at = now
        s.commit()
        return user


# --------------------------------------------------------------- signup


def create_account(email: str, password: str) -> db.User | None:
    """User row + argon2id hash + the one-time signup grant, atomically —
    the same invariants the managed-auth path had: the account cannot
    exist without its grant, and the ledger's partial unique index makes
    a double grant impossible. None = email already registered."""
    from sqlalchemy.exc import IntegrityError

    from app.auth.accounts import GRANT_REASON, signup_grant_credits

    grant = signup_grant_credits()
    with db.session() as s:
        try:
            user = db.User(
                id=uuid.uuid4().hex[:12],
                email=email.strip().lower(),
                password_hash=hash_password(password),
            )
            s.add(user)
            s.flush()
            s.add(db.CreditLedger(user_id=user.id, delta=grant, reason=GRANT_REASON))
            s.commit()
            log.info("new account %s — signup grant %d", user.id, grant)
            return user
        except IntegrityError:
            s.rollback()
            return None


def user_by_email(email: str) -> db.User | None:
    with db.session() as s:
        return (
            s.query(db.User)
            .filter(db.User.email == email.strip().lower())
            .one_or_none()
        )
