"""V-24 audit: how often did the pre-run dial rebuild silently rewrite a
strike rule?

    uv run --project backend python scripts/audit_strike_width_rewrites.py
    uv run --project backend python scripts/audit_strike_width_rewrites.py --json
    uv run --project backend python scripts/audit_strike_width_rewrites.py \
        --since 2026-07-01 --until 2026-08-16

ENV CONTRACT
    DATABASE_URL   the database to inspect. Neon Postgres in production;
                   falls back to the local SQLite dev file when unset, which
                   is almost never what you want for this audit.

WHAT THIS NUMBER IS (V-76)
    Not a bug-frequency curiosity. It counts STORED RUNS WHOSE `spec_json`
    RECORDS A STRIKE RULE THE USER DID NOT CHOOSE, and whose backtest results
    reflect that rule. Those runs are permanently wrong. `draftToSpec` rebuilt
    `position.legs` from the structure + delta dials on every edit, so a spec
    whose strike rule the dials could not express was silently rewritten the
    moment any unrelated dial moved: an `offset_pct` strike became delta 0.30,
    and the engine then backtested the delta.

    PR-0 (V-17) stops this happening again. It does NOT fix the affected runs
    and must not try. No repair, no flag, no backfill — here or in A1 — without
    an explicit decision from the owner after reading the number and the ids.

DETECTION (exact, no false positives)
    `spec_to_draft` records a display `strikeLabel` for non-delta strikes and
    leaves it null for delta ones. The STRIKE dial nulls that label the instant
    it is touched. So a stored `confirmed.draft.strikeLabel` that is still set,
    on a run whose final spec has a `delta` lead-leg method, is proof the
    rebuild changed the rule with no user edit behind it.

READ ONLY (V-62)
    There is no INSERT/UPDATE/DELETE path in this file and no repair mode
    behind any flag. Postgres connections are pinned to a read-only
    transaction; SQLite is opened with `mode=ro`.

V-71 / V-86: take a baseline BEFORE PR-0 merges, then run TWO follow-ups after
it merges, because "the count stopped growing" is two questions:

  (a) SANITY CHECK — bounded to the baseline's end date. Must be IDENTICAL to
      the baseline. A difference means detection itself changed, not the bug.
  (b) THE ACTUAL TEST — unbounded. Any detection newer than the baseline's end
      date is a NEW instance arising after the fix, so the fix did not hold.

The script prints both invocations, filled in, at the end of every run.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text

_DEFAULT_SQLITE = f"sqlite:///{Path(__file__).resolve().parents[1] / 'backend' / 'runs.db'}"

# mirrors backend/app/parser/parse.py spec_to_draft — a lead leg whose method
# is anything but "delta" is the class that cannot survive a dial rebuild
NON_DELTA_METHODS_NOTE = "offset_pct and any other non-delta strike rule"


def _database_url() -> str:
    return os.environ.get("DATABASE_URL", _DEFAULT_SQLITE)


def _exclusive_upper(until: str) -> str:
    """`--until 2026-08-16` means "through the whole of the 16th".

    `created_at <= '2026-08-16'` compares a timestamp against a bare date and
    silently drops every run that day, because '2026-08-16 18:53' sorts after
    '2026-08-16'. A bare date therefore becomes an EXCLUSIVE bound at the next
    midnight. An explicit timestamp is taken literally.
    """
    try:
        return (date.fromisoformat(until) + timedelta(days=1)).isoformat()
    except ValueError:
        return until  # a full timestamp — the caller meant exactly that instant


def _rows(url: str, since: str | None, until: str | None) -> list[tuple[Any, ...]]:
    """SELECT only. Read-only at the connection level where the driver allows."""
    if url.startswith("sqlite"):
        # sqlite:///abs/path -> file:abs/path?mode=ro
        path = url.split("sqlite:///", 1)[1]
        url = f"sqlite:///file:{path}?mode=ro&uri=true"
    engine = create_engine(url)
    where: list[str] = []
    params: dict[str, str] = {}
    if since:
        where.append("created_at >= :since")
        params["since"] = since
    if until:
        where.append("created_at < :until")
        params["until"] = _exclusive_upper(until)
    clause = (" where " + " and ".join(where)) if where else ""
    sql = f"select id, created_at, spec_json, provenance_json from runs{clause}"
    with engine.connect() as conn:
        if not url.startswith("sqlite"):
            conn.execute(text("set transaction read only"))
        return list(conn.execute(text(sql), params))


def _lead_method(spec_json: str | None) -> str | None:
    if not spec_json:
        return None
    try:
        spec = json.loads(spec_json)
        legs = (spec.get("position") or {}).get("legs") or []
        return ((legs[0] or {}).get("strike_selection") or {}).get("method")
    except Exception:
        return None


def _confirmed_draft(provenance_json: str | None) -> dict[str, Any] | None:
    if not provenance_json:
        return None
    try:
        confirmed = (json.loads(provenance_json) or {}).get("confirmed") or {}
    except Exception:
        return None
    draft = confirmed.get("draft")
    return draft if isinstance(draft, dict) else None


def audit(url: str, since: str | None, until: str | None) -> dict[str, Any]:
    total = with_provenance = 0
    eligible: list[str] = []
    detected: list[dict[str, Any]] = []
    newest: str | None = None

    for run_id, created_at, spec_json, provenance_json in _rows(url, since, until):
        total += 1
        stamp = str(created_at)
        if newest is None or stamp > newest:
            newest = stamp
        if provenance_json:
            with_provenance += 1
        draft = _confirmed_draft(provenance_json)
        if draft is None:
            continue
        eligible.append(run_id)
        if draft.get("strikeLabel") is not None and _lead_method(spec_json) == "delta":
            detected.append(
                {
                    "run_id": run_id,
                    "created_at": str(created_at),
                    "original_rule": draft.get("strikeLabel"),
                    "rewritten_to": f"delta {draft.get('strikeDelta')}",
                }
            )

    return {
        "window": {
            "since": since,
            "until": until,
            # V-85: the bound actually applied, so the V-71 comparison never
            # depends on remembering which flags were passed a week earlier
            "resolved_until_exclusive": _exclusive_upper(until) if until else None,
            "bounded": bool(since or until),
        },
        "newest_run_seen": newest,
        "total_runs": total,
        "runs_with_any_provenance": with_provenance,
        "eligible_runs": len(eligible),
        "not_inspectable": total - len(eligible),
        "detected": detected,
    }


def _print_text(result: dict[str, Any]) -> None:
    w = result["window"]
    total = result["total_runs"]
    eligible = result["eligible_runs"]
    detected = result["detected"]

    # V-85: state the invocation before any number. The V-71 comparison spans
    # days, and "which flags did I pass last time" is not a thing to remember.
    print()
    print("WINDOW APPLIED")
    if w["since"]:
        print(f"  since (inclusive)   : {w['since']}   [from --since {w['since']}]")
    else:
        print("  since (inclusive)   : beginning of time   [default, no --since]")
    if w["until"]:
        print(f"  until (exclusive)   : {w['resolved_until_exclusive']}   "
              f"[from --until {w['until']}, resolved to cover that whole day]")
    else:
        print("  until               : unbounded   [default, no --until]")
    print(f"  newest run seen     : {result['newest_run_seen'] or 'none'}")

    # V-66: the denominator comes BEFORE the count. A bare "0 detected" is
    # unreadable when the detection rule can only see runs that recorded a
    # confirmed draft.
    print()
    print(f"total runs in window            : {total}")
    print(f"  with any provenance record    : {result['runs_with_any_provenance']}")
    print(f"  with a confirmed.draft        : {eligible}   <- the eligible set")
    print(f"  not inspectable               : {result['not_inspectable']}")
    print()
    print(f"DETECTED strike rewrites        : {len(detected)} of {eligible} eligible")
    for d in detected:
        print(
            f"    {d['run_id']}  {d['created_at']}  "
            f"{d['original_rule']!r} -> {d['rewritten_to']}"
        )
    print()
    print(f"  {len(detected)} detected among {eligible} eligible runs; "
          f"{result['not_inspectable']} runs not inspectable.")
    if detected:
        # V-76: say what the number means where the number is.
        print("  These runs' stored specs record a strike rule the user did not")
        print("  choose, and their results were computed on that rule. They are")
        print("  permanently wrong. PR-0 stops it recurring; it does not fix them.")

    # V-81: an empty denominator is not a clean bill of health.
    if eligible == 0:
        print()
        print("  VACUOUS RESULT — the eligible set is empty, so this audit cannot")
        print("  answer the question on stored data. '0 of 0' is not evidence the")
        print("  fix worked; the V-18 round-trip guard in CI is what proves that.")
        print("  This run's value is the baseline for the post-PR-0 comparison.")

    # V-86: the post-PR-0 comparison is TWO numbers, and only the second is
    # the actual test. Print the exact follow-up invocations rather than
    # leaving them to be reconstructed.
    print()
    print("V-71 FOLLOW-UP — run BOTH after PR-0 merges")
    newest = result["newest_run_seen"]
    if newest:
        end = newest[:10]
        print("  (a) SANITY CHECK, bounded to this same end date:")
        print(f"        --until {end}")
        print("      Must be IDENTICAL to this run. A difference means detection")
        print("      itself changed, not that the bug did.")
        print("  (b) THE ACTUAL TEST, unbounded:")
        print("        (no flags)")
        print(f"      Any detection with created_at after {newest} is a NEW instance")
        print("      arising after the fix, which means the fix did not hold.")
    else:
        print("  No runs in window, so there is no baseline to compare against yet.")

    # V-82: an A1 planning input, not just an audit line.
    if total:
        share = 100.0 * eligible / total
        print()
        print("A1 PLANNING INPUT — provenance coverage")
        print(f"  {eligible}/{total} runs ({share:.0f}%) carry a confirmed.draft.")
        if share < 50:
            print("  MOST runs have no stored draft, so V-28's server-side projection")
            print("  is the MAJORITY path for 'Run a variant', not the worst case.")
            print("  That promotes the tier classifier from safety net to primary")
            print("  code path, and A1's test weight should sit there accordingly.")
        else:
            print("  The stored-draft path is the common case; the V-28 projection")
            print("  path stays the edge case A1 treats it as.")

    # V-63: the blind spot travels WITH the number, not in a doc alongside it.
    print()
    print("DETECTION COVERAGE")
    print(f"  covered     : strike rule class ({NON_DELTA_METHODS_NOTE}) rewritten to delta.")
    print("  NOT covered : spread width. The draft carries no width field, so the")
    print("                parser's original width is unrecoverable and a $10 -> $5")
    print("                rewrite left no trace anywhere in stored data.")
    print("  NOT covered : tenor band. The rebuild formula and a legitimate parser")
    print("                band can coincide, so any tenor count would be a guess.")
    print("  THIS NUMBER IS A FLOOR, not a total.")
    print()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--since", help="ISO date, inclusive lower bound on created_at")
    ap.add_argument("--until", help="ISO date, INCLUSIVE of that whole day")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of text")
    args = ap.parse_args()

    print("READ ONLY. This script does not write.")

    url = _database_url()
    if not args.json:
        kind = "local SQLite" if url.startswith("sqlite") else "postgres"
        print(f"source: {kind}")
        if url.startswith("sqlite"):
            print("WARNING: DATABASE_URL is unset, so this is the local dev file.")
            print("         The number you want lives in production.")

    result = audit(url, args.since, args.until)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        _print_text(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
