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
import os
import sys
from dataclasses import dataclass
from datetime import date

from app.config import load_local_env

load_local_env()

from app import db  # noqa: E402
from app.data import r2  # noqa: E402

log = logging.getLogger("nightly")

# Auto re-runs are capped per night (owner decision) — the queue drains
# across nights rather than storming Railway and the LLM budget at once.
AUTO_RERUNS_PER_NIGHT = 3

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
        # a child that ERRORED did not upgrade anything (review finding: a
        # staleness-refused re-run must not close its parent's unlock path
        # forever). Bounded retry: after 3 errored attempts the parent is
        # retired LOUDLY — a permanently-refused spec (e.g. conditioned on
        # a frozen UW series) must not burn a nightly slot every night.
        children = (
            s.query(db.Run.parent_run_id, db.Run.status)
            .filter(db.Run.parent_run_id.isnot(None), db.Run.origin == "auto_unlock")
            .all()
        )
        superseded = {pid for pid, status in children if status != "error"}
        error_counts: dict[str, int] = {}
        for pid, status in children:
            if status == "error":
                error_counts[pid] = error_counts.get(pid, 0) + 1
        for pid, n in error_counts.items():
            if n >= 3 and pid not in superseded:
                log.warning("unlock retired after %d errored re-runs: %s "
                            "(see the child runs' errors — likely a signal "
                            "feed frozen behind the requested window)", n, pid)
                superseded.add(pid)
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


def execute_unlocks(decisions: list[UnlockDecision]) -> int:
    """Submit capped re-runs through the backend API (Railway executes —
    warm caches, LLM key, same DB; this script never runs the engine).
    Returns how many were submitted."""
    import requests

    base = os.environ.get("SKEPTIC_API_URL", "").rstrip("/")
    if not base:
        log.error("SKEPTIC_API_URL not set — cannot execute re-runs")
        return 0
    headers = {}
    token = os.environ.get("SKEPTIC_ACCESS_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    ready = [d for d in decisions if d.should_rerun][:AUTO_RERUNS_PER_NIGHT]
    submitted = 0
    for d in ready:
        with db.session() as s:
            row = s.get(db.Run, d.run_id)
            spec_json = row.spec_json if row else None
        if not spec_json:
            continue
        resp = requests.post(
            f"{base}/api/backtest",
            json={
                "spec": json.loads(spec_json),
                "origin": "auto_unlock",
                "parent_run_id": d.run_id,
                "auto_note": f"{d.new_sessions} new sessions",
            },
            headers=headers,
            timeout=30,
        )
        if resp.status_code == 200:
            submitted += 1
            log.info("auto re-run submitted for %s → %s", d.run_id,
                     resp.json().get("run_id"))
        else:
            log.error("submit failed for %s: HTTP %s %s", d.run_id,
                      resp.status_code, resp.text[:160])
    return submitted


# Receipt drain pacing (owner amendment 1): the workflow window is already
# off-peak (07:00 UTC ≈ 02:00 ET); replays are additionally SERIALIZED —
# one at a time, polled to completion, with this delay between submissions
# — so the drain can never collide with a live backtest.
RECEIPT_DELAY_SECONDS = 60
RECEIPT_POLL_SECONDS = 10
RECEIPT_POLL_TIMEOUT = 1200  # a stuck replay stops the drain, loudly


def eligible_for_receipt() -> list[str]:
    """DONE user daily-clock runs, replayable within the intraday slice,
    with no receipt yet."""
    from app.api.replay import replay_eligible_spec

    out: list[str] = []
    with db.session() as s:
        rows = (
            s.query(db.Run.id, db.Run.spec_json, db.Run.receipts_json, db.Run.origin)
            .filter(db.Run.status == "done", db.Run.spec_json.isnot(None))
            .all()
        )
    for run_id, spec_json, receipts_json, origin in rows:
        if (origin or "user") != "user" or receipts_json:
            continue
        try:
            if replay_eligible_spec(json.loads(spec_json)):
                out.append(run_id)
        except Exception:
            continue
    return out


def drain_receipts(delay: int = RECEIPT_DELAY_SECONDS) -> int:
    """Serialized replay submissions via the on-demand endpoint — ALL
    eligible runs (owner decision), one at a time, polled to completion."""
    import time

    import requests

    base = os.environ.get("SKEPTIC_API_URL", "").rstrip("/")
    if not base:
        log.error("SKEPTIC_API_URL not set — cannot drain receipts")
        return 0
    headers = {}
    token = os.environ.get("SKEPTIC_ACCESS_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    done = 0
    for run_id in eligible_for_receipt():
        resp = requests.post(f"{base}/api/runs/{run_id}/replay",
                             headers=headers, timeout=30)
        if resp.status_code == 409:
            log.info("receipt %s: not replayable (%s)", run_id, resp.text[:120])
            continue
        if resp.status_code != 200:
            log.error("receipt submit failed for %s: HTTP %s", run_id, resp.status_code)
            continue
        replay_id = resp.json().get("run_id")
        log.info("receipt replay %s → %s (polling)", run_id, replay_id)
        waited = 0
        while waited < RECEIPT_POLL_TIMEOUT:
            status = requests.get(f"{base}/api/runs/{replay_id}",
                                  headers=headers, timeout=30).json().get("status")
            if status in ("done", "error"):
                break
            time.sleep(RECEIPT_POLL_SECONDS)
            waited += RECEIPT_POLL_SECONDS
        else:
            log.error("receipt replay %s stuck past %ss — stopping the drain",
                      replay_id, RECEIPT_POLL_TIMEOUT)
            return done
        done += 1
        time.sleep(delay)  # amendment 1: fixed gap between submissions
    return done


def main() -> int:
    logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                        format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--execute", action="store_true",
                    help="submit capped re-runs via the API (default: dry-run)")
    args = ap.parse_args()

    # A scan against the wrong database is a SUCCESS-shaped no-op: db.py falls
    # back to a local SQLite file when DATABASE_URL is unset, so every night
    # would log "no refused runs waiting" and exit 0 against an empty schema,
    # with nothing to distinguish it from a genuinely quiet night. That is the
    # same silent-green class as the outage that moved this job to the VM (the
    # VM's .env predates this var), so refuse instead of guessing.
    if not os.environ.get("DATABASE_URL"):
        log.error("DATABASE_URL is not set — refusing to scan a fallback SQLite "
                  "database and report it as a clean night. Set it in the "
                  "environment (VM: /opt/skeptic/collector/.env, see "
                  "collector/.env.example).")
        return 1

    db.init_db()
    decisions = scan_unlocks()
    if not decisions:
        log.info("unlock scan: no refused runs waiting")
    else:
        for d in decisions:
            marker = "RE-RUN" if d.should_rerun else "still waiting"
            log.info("[%s] %s %s@%s — %s", marker, d.run_id, d.ticker, d.clock, d.reason)
        log.info("unlock scan: %d waiting, %d ready",
                 len(decisions), sum(1 for d in decisions if d.should_rerun))
    receipt_queue = eligible_for_receipt()
    log.info("receipt queue: %d eligible daily run(s) without a receipt",
             len(receipt_queue))
    if args.execute:
        if decisions:
            n = execute_unlocks(decisions)
            log.info("submitted %d auto re-run(s) (cap %d/night)",
                     n, AUTO_RERUNS_PER_NIGHT)
        # the receipt drain runs every night regardless of the unlock queue
        r = drain_receipts()
        log.info("receipt drain: %d replay(s) completed", r)
    return 0


if __name__ == "__main__":
    sys.exit(main())
