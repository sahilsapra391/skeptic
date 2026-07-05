"""ENGINE-V3 D3d: what should the collectors want next?

Weekly aggregation of three demand signals into a single ranked list —
`state/collection_priorities.json` — surfaced on the Observatory as the
"collection wants" line. The ranking is DERIVED, never hand-ordered, and
the scoring is a reviewed constant of this script (standing guardrail:
scoring changes only via reviewed PRs). Collector consumption of the
ranking is D4 follow-up work; this pass only states the demand.

Signals:
1. UNLOCK DEMAND — refused runs waiting on data (runs.unlock_json).
   Each waiting run is a user who asked a question the lake couldn't
   answer: the strongest signal, +5 per run per {ticker, clock}.
2. SKIP PRESSURE — data-shaped skip reasons across recent done runs
   (payload trade logs): +1 per 25 skips, capped at +5 per reason.
3. STRUCTURAL GAPS — from the coverage ledger and calibration state:
   thin EOD history (< 100 sessions), lagging intraday capture (< half
   the deepest ticker), and a thin calibration overlap (short-DTE EOD
   history — the D3c lake finding).

Aggregates only; no chain rows.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import Counter
from datetime import UTC, datetime
from typing import Any

from app.config import load_local_env

load_local_env()

from app import db  # noqa: E402
from app.data import r2  # noqa: E402

log = logging.getLogger("priorities")

PRIORITIES_KEY = "state/collection_priorities.json"
LEDGER_KEY = "state/coverage_ledger.parquet"
CAL_STATE_KEY = "state/calibration_latest.json"

# Reviewed scoring constants.
SCORE_PER_WAITING_RUN = 5
SKIPS_PER_POINT = 25
SKIP_SCORE_CAP = 5
THIN_EOD_SESSIONS = 100
THIN_EOD_SCORE = 4
LAGGING_INTRADAY_SCORE = 3
THIN_CALIBRATION_SCORE = 4
RECENT_RUNS_SCANNED = 200

# Skip reasons that point at missing DATA (vs strategy conditions that
# legitimately didn't trigger — max_concurrent, conditions_not_met,
# risk_* and vega_cap are the strategy speaking, not the lake). The
# vocabulary is the engine's (engine.py / fills.py / selection.py).
DATA_SKIP_REASONS = {
    "no_chain_data",
    "no_underlying_close",
    "no_expiration_in_window",
    "missing_quote",
    "crossed_market",
    "zero_bid_short",
    "illiquid_spread",
    "illiquid_oi",
    "illiquid_volume",
    "no_strike_candidates",
    "no_delta_data",
    "no_wing_strike",
    "wing_width_unavailable",
    "vega_unavailable",
    "selection_failed",
}


def unlock_demand() -> Counter[tuple[str, str]]:
    """(ticker, clock) → count of refused runs waiting on data."""
    demand: Counter[tuple[str, str]] = Counter()
    with db.session() as s:
        rows = (
            s.query(db.Run.unlock_json)
            .filter(db.Run.unlock_json.isnot(None))
            .all()
        )
    for (unlock_json,) in rows:
        try:
            u = json.loads(unlock_json)
            demand[(str(u.get("ticker", "?")), str(u.get("clock", "daily")))] += 1
        except Exception:
            continue
    return demand


def skip_pressure() -> Counter[str]:
    """Data-shaped skip reasons across the most recent done runs."""
    pressure: Counter[str] = Counter()
    with db.session() as s:
        rows = (
            s.query(db.Run.payload_json)
            .filter(db.Run.status == "done", db.Run.payload_json.isnot(None))
            .order_by(db.Run.created_at.desc())
            .limit(RECENT_RUNS_SCANNED)
            .all()
        )
    for (payload_json,) in rows:
        try:
            trades = json.loads(payload_json).get("trades") or []
        except Exception:
            continue
        for row in trades:
            if row.get("skip") and row.get("n") in DATA_SKIP_REASONS:
                pressure[row["n"]] += 1
    return pressure


def structural_gaps(s3: Any) -> list[dict[str, Any]]:
    """Ledger-derived gaps + the calibration overlap, scored."""
    wants: list[dict[str, Any]] = []
    ledger = r2.get_parquet(s3, LEDGER_KEY)
    if ledger is not None and not ledger.empty:
        latest = ledger.sort_values("ts").groupby("ticker").last()
        eod = latest["eod_sessions"].to_dict()
        intraday = latest["intraday_sessions"].to_dict()
        deepest = max(intraday.values()) if len(intraday) else 0
        for ticker, sessions in sorted(eod.items()):
            if sessions < THIN_EOD_SESSIONS:
                wants.append({
                    "want": f"EOD chain history for {ticker}",
                    "why": f"only {int(sessions)} EOD chain sessions in the lake "
                           f"(< {THIN_EOD_SESSIONS}) — multi-year daily backtests "
                           "are impossible",
                    "score": THIN_EOD_SCORE,
                })
        for ticker, sessions in sorted(intraday.items()):
            if deepest and sessions < deepest / 2:
                wants.append({
                    "want": f"extend the 5-min capture for {ticker}",
                    "why": f"{int(sessions)} intraday sessions vs {int(deepest)} on "
                           "the deepest ticker — 5-min verdicts lag here",
                    "score": LAGGING_INTRADAY_SCORE,
                })
    cal = r2.get_json(s3, CAL_STATE_KEY, None)
    if cal is not None:
        n = int(cal.get("n") or 0)
        floor = 500  # calibrate_fill_model.CAL_BASE_MIN_N (kept literal: no import cycle)
        if n < floor:
            wants.append({
                "want": "short-dated EOD chains (keep the Yahoo 0–60 DTE snapshot running)",
                "why": f"fill-model calibration has n={n} overlapping contract-day "
                       f"sides (< {floor}) — the historical EOD record carries no "
                       "<11 DTE expirations, so calibration and two-sided verdict "
                       "receipts both wait on this capture",
                "score": THIN_CALIBRATION_SCORE,
            })
    return wants


def build() -> dict[str, Any]:
    s3 = r2.r2_client()
    wants: list[dict[str, Any]] = []

    for (ticker, clock), count in sorted(unlock_demand().items()):
        wants.append({
            "want": f"more covered sessions for {ticker} @ {clock}",
            "why": f"{count} refused verdict(s) waiting to auto-unlock on new data",
            "score": count * SCORE_PER_WAITING_RUN,
        })
    for reason, count in skip_pressure().most_common():
        wants.append({
            "want": f"fewer '{reason}' skips (data depth/quality)",
            "why": f"{count} entry skips with reason {reason} across the last "
                   f"{RECENT_RUNS_SCANNED} runs",
            "score": min(count // SKIPS_PER_POINT + 1, SKIP_SCORE_CAP),
        })
    wants.extend(structural_gaps(s3))

    wants.sort(key=lambda w: (-w["score"], w["want"]))
    for i, w in enumerate(wants, start=1):
        w["rank"] = i
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "priorities": wants,
        "scoring": {
            "per_waiting_run": SCORE_PER_WAITING_RUN,
            "skips_per_point": SKIPS_PER_POINT,
            "thin_eod_sessions": THIN_EOD_SESSIONS,
        },
    }


def main() -> int:
    logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                        format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--execute", action="store_true",
                    help="write state/collection_priorities.json to R2 "
                         "(default: dry-run print)")
    args = ap.parse_args()

    db.init_db()
    payload = build()
    print(json.dumps(payload, indent=1))
    if args.execute:
        r2.put_json(r2.r2_client(), PRIORITIES_KEY, payload)
        log.info("wrote %s (%d wants)", PRIORITIES_KEY, len(payload["priorities"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
