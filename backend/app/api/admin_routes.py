"""Admin surface (launch L5). Owner-only: award / claw back credits and read
launch telemetry. Admin = an email on SKEPTIC_ADMIN_EMAILS (env) — there is no
self-serve path to becoming one, and every credit change is an audited
admin_adjust row on the append-only ledger (balance stays SUM(delta)).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func

from app import db
from app.auth import require_admin

router = APIRouter()

# admin auth as a dependency → it resolves BEFORE the request body is parsed,
# so an unauthenticated caller gets the 404/401 and never a body-schema 422
_admin = Depends(require_admin)


class GrantRequest(BaseModel):
    email: str = Field(max_length=320)
    # generous bounds for owner testing ("give myself a few thousand"), but
    # capped so a fat-fingered extra zero can't mint millions
    credits: int = Field(ge=-1_000_000, le=1_000_000)


@router.post("/admin/grant-credits")
def grant_credits(req: GrantRequest, _: db.User = _admin) -> dict[str, Any]:
    """Award (or claw back, negative) a user's credits — the web equivalent of
    scripts/grant_credits.py. One audited admin_adjust ledger row."""
    if req.credits == 0:
        raise HTTPException(status_code=422, detail="credits must be non-zero")
    email = req.email.strip().lower()
    with db.session() as s:
        user = s.query(db.User).filter(db.User.email == email).one_or_none()
        if user is None:
            raise HTTPException(status_code=404, detail=f"no account for {email}")
        before = db.credit_balance_tx(s, user.id)
        s.add(
            db.CreditLedger(
                user_id=user.id, delta=req.credits, reason="admin_adjust", run_id=None
            )
        )
        s.commit()
        after = db.credit_balance_tx(s, user.id)
    return {"email": email, "before": before, "after": after, "delta": req.credits}


@router.get("/admin/metrics")
def metrics(_: db.User = _admin) -> dict[str, Any]:
    """Launch telemetry — accounts, runs, the credit economy, anon trials."""
    now = datetime.now(UTC)
    week_ago = now - timedelta(days=7)
    day_ago = now - timedelta(hours=24)
    with db.session() as s:

        def count(model_id: Any, *filters: Any) -> int:
            q = s.query(func.count(model_id))
            for f in filters:
                q = q.filter(f)
            return int(q.scalar() or 0)

        def ledger_sum(reason: str) -> int:
            return int(
                s.query(func.coalesce(func.sum(db.CreditLedger.delta), 0))
                .filter(db.CreditLedger.reason == reason)
                .scalar()
                or 0
            )

        runs_by_status = {
            status: n
            for status, n in s.query(db.Run.status, func.count(db.Run.id))
            .group_by(db.Run.status)
            .all()
        }
        purchases = count(db.CreditLedger.id, db.CreditLedger.reason == "purchase")
        return {
            "accounts": {
                "total": count(db.User.id),
                "verified": count(db.User.id, db.User.verified_at.isnot(None)),
                "signups_7d": count(db.User.id, db.User.created_at >= week_ago),
            },
            "runs": {
                "total": count(db.Run.id),
                "by_status": runs_by_status,
                "signed_in": count(db.Run.id, db.Run.user_id.isnot(None)),
                "last_7d": count(db.Run.id, db.Run.created_at >= week_ago),
            },
            "credits": {
                # a run_debit delta is negative → report spent as a positive count
                "spent": -ledger_sum("run_debit"),
                "signup_granted": ledger_sum("signup_grant"),
                "purchased": ledger_sum("purchase"),
                "admin_adjusted": ledger_sum("admin_adjust"),
                "refunded": ledger_sum("engine_refund"),
                "charged_back": ledger_sum("chargeback"),
                # what's live in the economy right now = SUM over the whole ledger
                "outstanding": int(
                    s.query(func.coalesce(func.sum(db.CreditLedger.delta), 0)).scalar() or 0
                ),
            },
            "revenue": {
                # each purchase is one $10 checkout; chargebacks are counted so
                # gross vs net is honest
                "purchases": purchases,
                "chargebacks": count(db.CreditLedger.id, db.CreditLedger.reason == "chargeback"),
                "gross_usd": purchases * 10,
            },
            "anon_trials": {
                "total": count(db.AnonTrial.id),
                "today": count(db.AnonTrial.id, db.AnonTrial.created_at >= day_ago),
            },
        }
