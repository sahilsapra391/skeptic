#!/usr/bin/env python3
"""nightly_improve.py — the auto-improvement pass (ENGINE-V3 D3).

D3a ships the UNLOCK SCAN in dry-run: read refused runs' structured
unlock_json, compare against what the lake covers NOW, and report which
verdicts could be upgraded. D3b adds --execute (submits re-runs through
the backend API, capped per night); D3c adds the receipt pass.

Run:  cd backend && PYTHONPATH=. uv run python scripts/nightly_improve.py
      (add --execute in D3b; `make nightly` wraps this)
Env:  R2_* (lake listings) + DATABASE_URL (runs). Local dev inherits both
      from collector/.env via app.config.load_local_env.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass
from datetime import date

from app.config import load_local_env

load_local_env()

from app import db  # noqa: E402
from app.data import r2  # noqa: E402

log = logging.getLogger("nightly")

# A refused run is worth re-running once this many NEW covered sessions
# have arrived since the refusal — below that, thin-sample refusals would
# re-run nightly and re-refuse nightly. Reviewed constant, like every
# threshold (see docs/HONESTY.md).
UNLOCK_MIN_NEW_SESSIONS = 20


@dataclass
class UnlockDecision:
    run_id: str
    ticker: str
    clock: str
    new_sessions: int
    reason: str
    should_rerun: bool


def _covered_sessions_now(ticker: str, clock: str, start: str, end: str) -> int:
    """Sessions with usable quotes in [start, end] as the lake stands —
    the ENGINE's counting rules, not raw listings: `sessions_at_refusal`
    came from the engine, so the delta must too (raw dolthub listings carry
    ~45 quarantined sessions the engine refuses to load — comparing raw to
    engine counts would mint phantom 'new sessions' and spurious re-runs)."""
    s3 = r2.r2_client()
    if clock == "5min":
        dates: set[str] = set()
        for prefix in ("options_intraday/source=ivolatility",
                       "options_intraday/source=cboe_delayed"):
            dates.update(r2.list_date_prefixes(s3, f"{prefix}/ticker={ticker}/"))
    else:
        from app.data.chains import _chain_keys  # quarantine-honoring winners

        dates = set(_chain_keys(s3, ticker))
    return sum(1 for d in dates if start <= d <= end)


def scan_unlocks(today: date | None = None) -> list[UnlockDecision]:
    """Refused runs whose unlock conditions may now be met. Pure decision
    logic — execution (D3b) is a separate, capped step."""
    decisions: list[UnlockDecision] = []
    with db.session() as s:
        rows = (
            s.query(db.Run.id, db.Run.unlock_json)
            .filter(db.Run.status == "done", db.Run.unlock_json.isnot(None))
            .all()
        )
        superseded = {
            pid for (pid,) in s.query(db.Run.parent_run_id)
            .filter(db.Run.parent_run_id.isnot(None), db.Run.origin == "auto_unlock")
            .all()
        }
    for run_id, unlock_json in rows:
        if run_id in superseded:
            continue  # already upgraded once — its successor carries on
        try:
            unlock = json.loads(unlock_json)
        except Exception:
            continue
        ticker = unlock.get("ticker", "SPY")
        clock = unlock.get("clock", "daily")
        end = unlock.get("requested_end") or date.today().isoformat()
        covered_now = _covered_sessions_now(
            ticker, clock, unlock.get("requested_start", "1990-01-01"), end
        )
        new_sessions = covered_now - int(unlock.get("sessions_at_refusal", 0))
        should = new_sessions >= UNLOCK_MIN_NEW_SESSIONS
        needs = [k for k in ("coverage", "trades", "regimes") if unlock.get(k)]
        decisions.append(UnlockDecision(
            run_id=run_id, ticker=ticker, clock=clock,
            new_sessions=new_sessions,
            reason=(f"{new_sessions} new covered sessions since refusal; "
                    f"waiting on {', '.join(needs) or 'unknown'}"),
            should_rerun=should,
        ))
    return decisions


def main() -> int:
    logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                        format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", default=True,
                    help="report only (D3a: always dry-run; --execute lands in D3b)")
    ap.parse_args()

    db.init_db()
    decisions = scan_unlocks()
    if not decisions:
        log.info("unlock scan: no refused runs waiting")
        return 0
    for d in decisions:
        marker = "WOULD RE-RUN" if d.should_rerun else "still waiting"
        log.info("[%s] %s %s@%s — %s", marker, d.run_id, d.ticker, d.clock, d.reason)
    log.info("unlock scan: %d waiting, %d ready",
             len(decisions), sum(1 for d in decisions if d.should_rerun))
    return 0


if __name__ == "__main__":
    sys.exit(main())
