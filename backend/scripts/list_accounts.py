"""List every account and its key stats — an owner diagnostic for the launch
metrics ("why does total accounts say N?"). READ-ONLY: it writes nothing.

Shows which DB it's reading (so you know it's the right one), then every
account with when it was created, whether it's verified, its credit balance,
and how many runs it owns — enough to tell your real signup from test rows.

    cd backend
    PYTHONPATH=. uv run python scripts/list_accounts.py

Point it at a specific database by exporting DATABASE_URL first (e.g. your
Neon/Railway URL) — otherwise it uses the same env the app loads.
"""

from __future__ import annotations

from app.config import load_local_env

load_local_env()

from sqlalchemy import func  # noqa: E402

from app import db  # noqa: E402


def main() -> None:
    db.init_db()
    print(f"reading: {db.status()}")
    if db.FALLBACK_REASON is not None:
        raise SystemExit(
            f"accounts DB unavailable ({db.FALLBACK_REASON}) — set DATABASE_URL to the real DB"
        )
    with db.session() as s:
        users = s.query(db.User).order_by(db.User.created_at).all()
        print(f"\n{len(users)} account(s):\n")
        print(f"  {'email':<40} {'created (UTC)':<17} {'verif':<6} {'credits':>7} {'runs':>5}")
        print("  " + "-" * 78)
        for u in users:
            created = u.created_at.strftime("%Y-%m-%d %H:%M") if u.created_at else "?"
            verified = "yes" if u.verified_at else "no"
            credits = db.credit_balance_tx(s, u.id)
            runs = (
                s.query(func.count(db.Run.id)).filter(db.Run.user_id == u.id).scalar() or 0
            )
            print(f"  {u.email:<40} {created:<17} {verified:<6} {credits:>7} {runs:>5}")


if __name__ == "__main__":
    main()
