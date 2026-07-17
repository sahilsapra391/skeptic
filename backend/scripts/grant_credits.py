"""Grant (or claw back) a user's backtest credits via the append-only ledger.

Owner tool for the L2 to L3 window: before Stripe top-ups exist, this is how
you give credits to yourself (account #1) or an early tester. Every change is
an audit row (reason=admin_adjust) — the balance stays SUM(delta), never a
mutable column, so this can never silently corrupt a balance.

    cd backend
    PYTHONPATH=. uv run python scripts/grant_credits.py --email you@example.com --credits 50
    PYTHONPATH=. uv run python scripts/grant_credits.py --email you@example.com --credits -5
    PYTHONPATH=. uv run python scripts/grant_credits.py --email you@example.com --show
"""

from __future__ import annotations

import argparse

from app.config import load_local_env

load_local_env()

from app import db  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description="Grant/adjust a user's backtest credits.")
    ap.add_argument("--email", required=True, help="the account's email address")
    ap.add_argument("--credits", type=int, default=0, help="credits to add (negative claws back)")
    ap.add_argument("--show", action="store_true", help="print the balance and exit, no change")
    args = ap.parse_args()

    db.init_db()
    if db.FALLBACK_REASON is not None:
        raise SystemExit(f"accounts DB unavailable ({db.FALLBACK_REASON}) — refusing to write")

    email = args.email.strip().lower()
    with db.session() as s:
        user = s.query(db.User).filter(db.User.email == email).one_or_none()
        if user is None:
            raise SystemExit(f"no account for {email!r}")
        before = db.credit_balance_tx(s, user.id)
        if args.show or args.credits == 0:
            print(f"{email}: {before} credit(s)")
            return
        s.add(
            db.CreditLedger(
                user_id=user.id, delta=args.credits, reason="admin_adjust", run_id=None
            )
        )
        s.commit()
        after = db.credit_balance_tx(s, user.id)
    print(f"{email}: {before} -> {after} ({args.credits:+d}, admin_adjust)")


if __name__ == "__main__":
    main()
