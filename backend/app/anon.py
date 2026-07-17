"""Anonymous-trial armor (launch L4 — the public-launch blocker).

An anonymous visitor gets exactly ONE real backtest, defended in layers so
a doctored client can't turn the engine into free compute:

  1. Cloudflare Turnstile, verified server-side  (not a bot)
  2. one run per SIGNED anon token               (the honest visitor)
  3. one run per IP window                        (cookie-clearers)
  4. a global daily budget                        (the ceiling)
  5. constrained to daily clock + <=3y window     (fast path; rejected if
                                                    a doctored client asks
                                                    for more)

Signed-in accounts and the service principal skip ALL of this — the armor
is only for the anonymous free-run path. No raw token or IP is ever stored;
only salted/HMAC'd hashes. The signature on the anon token lets us reject a
forged token before it ever touches the database.

Dev / pre-config: with no TURNSTILE_SECRET the human check is skipped (like
the mailer), so the flow works locally without Cloudflare keys.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
from datetime import UTC, datetime, timedelta

import requests
from fastapi import HTTPException, Request

from app import db
from app.models.spec import Clock, StrategySpec

log = logging.getLogger("anon")

ANON_COOKIE = "skeptic_anon"
TURNSTILE_VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"

# constraint set (PRD D4): anonymous runs are the fast path
MAX_ANON_WINDOW_DAYS = 366 * 3 + 1  # ~3 years, inclusive slack


def _secret() -> bytes:
    # dedicated signing secret, else fall back to the service token so a
    # deploy that sets only SKEPTIC_ACCESS_TOKEN still gets unforgeable
    # anon tokens (never a hardcoded default)
    raw = os.environ.get("SKEPTIC_ANON_SECRET") or os.environ.get("SKEPTIC_ACCESS_TOKEN") or ""
    return raw.encode()


def _daily_budget() -> int:
    try:
        return max(0, int(os.environ.get("SKEPTIC_ANON_DAILY_BUDGET", "200")))
    except ValueError:
        return 200


def _ip_window() -> timedelta:
    try:
        return timedelta(hours=max(1, int(os.environ.get("SKEPTIC_ANON_IP_WINDOW_HOURS", "24"))))
    except ValueError:
        return timedelta(hours=24)


# ----------------------------------------------------------- identity


def _sign(payload: str) -> str:
    return hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()[:32]


def new_token() -> tuple[str, str]:
    """A fresh signed anon token and its storage hash: '<rand>.<sig>'."""
    rand = secrets.token_urlsafe(24)
    token = f"{rand}.{_sign(rand)}"
    return token, token_hash(token)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def verified_hash(token: str | None) -> str | None:
    """The storage hash of a token whose signature checks out — None for a
    missing, malformed, or FORGED token (rejected before the DB)."""
    if not token or "." not in token:
        return None
    rand, _, sig = token.rpartition(".")
    if not rand or not hmac.compare_digest(sig, _sign(rand)):
        return None
    return token_hash(token)


def client_ip(request: Request) -> str:
    from app.ratelimit import client_ip as _ip

    return _ip(request)


def ip_hash(ip: str) -> str:
    return hmac.new(_secret(), ip.encode(), hashlib.sha256).hexdigest()


# ----------------------------------------------------------- turnstile


def turnstile_configured() -> bool:
    return bool(os.environ.get("TURNSTILE_SECRET"))


_warned_no_turnstile = False


def verify_turnstile(token: str | None, ip: str) -> bool:
    """True when the human check passes — or when Turnstile isn't configured
    (dev / pre-launch), so the flow works without Cloudflare keys."""
    secret = os.environ.get("TURNSTILE_SECRET")
    if not secret:
        # deploy-safety: nothing forces the human check on for public launch,
        # so warn ONCE the first time an anon run proceeds without it — a
        # silent bot could otherwise drain the free-run budget
        global _warned_no_turnstile
        if not _warned_no_turnstile:
            _warned_no_turnstile = True
            log.warning(
                "anon armor: TURNSTILE_SECRET is unset — the human check is "
                "SKIPPED. Set it before public launch, or bots can spend the "
                "anonymous free-run budget."
            )
        return True
    if not token:
        return False
    try:
        r = requests.post(
            TURNSTILE_VERIFY_URL,
            data={"secret": secret, "response": token, "remoteip": ip},
            timeout=10,
        )
        return bool(r.status_code == 200 and r.json().get("success"))
    except requests.RequestException:
        log.exception("turnstile verify failed")
        return False


# ----------------------------------------------------------- constraints


def enforce_constraints(spec: StrategySpec) -> None:
    """Anonymous runs are the fast path: daily clock, <=3-year window. A
    doctored client asking for more is REJECTED, not silently clamped."""
    if spec.backtest.clock is not Clock.DAILY:
        raise HTTPException(
            status_code=422,
            detail="free trial runs use the daily clock — create a free "
            "account to run intraday (5-minute) backtests",
        )
    long_window = HTTPException(
        status_code=422,
        detail="free trial runs cover up to a 3-year window — create a "
        "free account to test the full history",
    )
    start, end = spec.backtest.start, spec.backtest.end
    # Open-ended windows are the COMMON case, not an edge: every preset window
    # sends end=None (runs to the latest session ~= today) and "all" sends
    # start=None too (full ~20-year history). Resolve both before measuring —
    # otherwise the span check short-circuits on the None and the <=3y cap
    # silently never fires for the normal client.
    if start is None:
        raise long_window  # open-ended start = full history, always past the cap
    eff_end = end if end is not None else datetime.now(UTC).date()
    if (eff_end - start).days > MAX_ANON_WINDOW_DAYS:
        raise long_window


# ----------------------------------------------------------- gating


def check_limits(token_h: str | None, ip_h: str) -> str:
    """'ok' | 'used_token' | 'used_ip' | 'budget'. Read-only; the caller
    records the trial only after the run row is created.

    This is check-then-act, not atomic: a burst of concurrent first-run POSTs
    from one IP (no cookie yet, so each mints a distinct token) can each read
    the counts as empty and slip through before the first commits. That window
    is deliberately left un-locked — the residual is bounded on every side: the
    per-IP window blocks the NEXT burst, the global daily budget caps the total
    (no unbounded free compute), and the engine serializes every run behind its
    lock (a flood can't amplify compute past the budget). A DB lock here would
    add contention to the hot path for a threat the budget already ceilings."""
    now = datetime.now(UTC)
    with db.session() as s:
        # global daily ceiling first — cheapest signal, protects the engine
        since_midnight = now - timedelta(hours=24)
        total = (
            s.query(db.AnonTrial).filter(db.AnonTrial.created_at >= since_midnight).count()
        )
        if total >= _daily_budget():
            return "budget"
        if token_h is not None:
            if s.query(db.AnonTrial).filter(db.AnonTrial.token_hash == token_h).count():
                return "used_token"
        window_start = now - _ip_window()
        used_ip = (
            s.query(db.AnonTrial)
            .filter(db.AnonTrial.ip_hash == ip_h, db.AnonTrial.created_at >= window_start)
            .count()
        )
        if used_ip:
            return "used_ip"
    return "ok"


def record_trial(token_h: str, ip_h: str, run_id: str) -> None:
    with db.session() as s:
        s.add(db.AnonTrial(token_hash=token_h, ip_hash=ip_h, run_id=run_id))
        s.commit()


def claim_anon_runs(token: str | None, user_id: str) -> list[str]:
    """Re-parent the runs made under this device's anon token to a new
    account at signup (the conversion moment) — only UNOWNED user-origin
    runs. Returns the run ids ACTUALLY claimed (so a caller can count them
    without double-counting an already-owned run)."""
    token_h = verified_hash(token)
    if token_h is None:
        return []
    with db.session() as s:
        trial_ids = [
            r for (r,) in s.query(db.AnonTrial.run_id).filter(db.AnonTrial.token_hash == token_h)
        ]
        if not trial_ids:
            return []
        claimable = [
            r
            for (r,) in s.query(db.Run.id).filter(
                db.Run.id.in_(trial_ids),
                db.Run.user_id.is_(None),
                db.Run.origin == "user",
            )
        ]
        if claimable:
            s.query(db.Run).filter(db.Run.id.in_(claimable)).update(
                {db.Run.user_id: user_id}, synchronize_session=False
            )
            s.commit()
    return claimable


def queue_position(run_id: str) -> int:
    """Runs ahead of this one in the serialized engine queue (0 = next/now).
    Honest 'N runs ahead of you' for the anon wait."""
    with db.session() as s:
        row = s.get(db.Run, run_id)
        if row is None:
            return 0
        return (
            s.query(db.Run)
            .filter(
                db.Run.status.in_(["queued", "running"]),
                db.Run.created_at < row.created_at,
            )
            .count()
        )
