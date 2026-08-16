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
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text

_DEFAULT_SQLITE = f"sqlite:///{Path(__file__).resolve().parents[1] / 'backend' / 'runs.db'}"

# mirrors backend/app/parser/parse.py spec_to_draft — a lead leg whose method
# is anything but "delta" is the class that cannot survive a dial rebuild
NON_DELTA_METHODS_NOTE = "offset_pct and any other non-delta strike rule"


def _database_url() -> str:
    return os.environ.get("DATABASE_URL", _DEFAULT_SQLITE)


class WindowArgumentError(ValueError):
    """A --since / --until value that is not one of the two accepted forms.

    Raised BEFORE anything is parsed loosely, queried, or printed. `main`
    turns it into a non-zero exit with the offending value named.
    """


# Exactly two accepted shapes. Matched explicitly rather than inferred from
# whether some parser happened to raise — inference from a failed parse is
# historical defect 3 (see the module docstring).
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(\.\d{1,6})?$")

_ACCEPTED_FORMS = (
    "accepted forms: a date YYYY-MM-DD, zero-padded, meaning that whole day "
    "inclusive; or a timestamp YYYY-MM-DD HH:MM:SS[.ffffff], meaning that "
    "exact instant inclusive"
)


def _classify(label: str, value: str) -> tuple[str, str]:
    """Reject before parsing. Returns (form, value) or raises.

    Nothing that fails these two patterns reaches SQL or a string comparison.
    `2026-7-16` is not silently a timestamp; it is a typo, and a typo that
    silently widens the window is worse than a crash, because the banner then
    reports the wrong window with total confidence.
    """
    if _DATE_RE.match(value):
        try:
            date.fromisoformat(value)
        except ValueError as exc:
            raise WindowArgumentError(
                f"--{label} {value!r} is shaped like a date but is not a real "
                f"calendar date. {_ACCEPTED_FORMS}"
            ) from exc
        return "date", value
    if _TIMESTAMP_RE.match(value):
        try:
            datetime.fromisoformat(value)
        except ValueError as exc:
            raise WindowArgumentError(
                f"--{label} {value!r} is shaped like a timestamp but is not a "
                f"real one. {_ACCEPTED_FORMS}"
            ) from exc
        return "timestamp", value
    raise WindowArgumentError(
        f"--{label} {value!r} is not a recognised date or timestamp. "
        f"{_ACCEPTED_FORMS}"
    )


def _lower_bound(form: str, value: str) -> datetime:
    return (
        datetime.fromisoformat(f"{value} 00:00:00")
        if form == "date"
        else datetime.fromisoformat(value)
    )


def resolve_window(since: str | None, until: str | None) -> dict[str, Any]:
    """V-92 / V-97: the ONE derivation of this script's boundary semantics,
    and the only place a --since / --until value is validated.

        --until <date>            INCLUSIVE of that entire day
        --until <full timestamp>  INCLUSIVE of that exact instant
        anything else             WindowArgumentError, non-zero exit

    Both the query and the printed banner read this result, so the window
    reported is by construction the window queried.

    A date needs the exclusive next-midnight form because `created_at <=
    '2026-07-17'` compares a timestamp against a date and drops the whole day:
    '2026-07-17 18:53' sorts after '2026-07-17'. A timestamp needs `<=` to
    keep the inclusive meaning it has always had.
    """
    since_form = since_value = None
    if since is not None:
        since_form, since_value = _classify("since", since)

    until_form = until_value = until_op = None
    if until is not None:
        until_form, until_raw = _classify("until", until)
        if until_form == "date":
            # the whole day, expressed as an exclusive bound at next midnight
            until_value = (date.fromisoformat(until_raw) + timedelta(days=1)).isoformat()
            until_op = "<"
        else:
            until_value = until_raw
            until_op = "<="

    if since_form and until_form:
        # An inverted window silently returns nothing, which this script would
        # then report as "0 detected" — the most dangerous false negative it
        # has, given the number gates a merge.
        lower = _lower_bound(since_form, since_value or "")
        upper = (
            datetime.fromisoformat(f"{until_value} 00:00:00")
            if until_form == "date"
            else datetime.fromisoformat(until_value or "")
        )
        if lower >= upper:
            raise WindowArgumentError(
                f"--since {since!r} is not before --until {until!r}: the window "
                "is empty or inverted, which would report zero runs as though "
                "none existed"
            )

    return {
        "since": since,
        "since_form": since_form,
        "until": until,
        "until_value": until_value,
        "until_op": until_op,
        "until_form": until_form,
        "bounded": bool(since or until),
    }


# V-100: the comparison operator is chosen from a fixed table keyed by the
# validated form, so no caller-supplied string is ever interpolated into SQL.
# "fine for every current caller" is the argument that ends with a script
# pointed at production interpolating something a caller supplied; V-62 made
# the write path structurally impossible and the read path gets the same
# treatment rather than a convention in a comment.
_UNTIL_CLAUSE = {
    "date": "created_at < :until",
    "timestamp": "created_at <= :until",
}


def _read_only_url(url: str) -> str:
    if url.startswith("sqlite"):
        # sqlite:///abs/path -> file:abs/path?mode=ro
        path = url.split("sqlite:///", 1)[1]
        return f"sqlite:///file:{path}?mode=ro&uri=true"
    return url


def _read(url: str, window: dict[str, Any]) -> tuple[list[tuple[Any, ...]], str | None]:
    """SELECT only, on ONE read-only connection (V-101).

    Returns the windowed rows AND the database-wide newest timestamp. The
    latter is deliberately window-INDEPENDENT (V-91): a cutoff derived from a
    bounded scan is what would make runs predating the fix read as new
    instances after it.
    """
    where: list[str] = []
    params: dict[str, str] = {}
    if window["since"]:
        where.append("created_at >= :since")
        params["since"] = window["since"]
    if window["until_form"]:
        where.append(_UNTIL_CLAUSE[window["until_form"]])
        params["until"] = window["until_value"]
    clause = (" where " + " and ".join(where)) if where else ""

    engine = create_engine(_read_only_url(url))
    with engine.connect() as conn:
        if not url.startswith("sqlite"):
            conn.execute(text("set transaction read only"))
        rows = list(
            conn.execute(
                text(
                    "select id, created_at, spec_json, provenance_json "
                    f"from runs{clause}"
                ),
                params,
            )
        )
        newest = conn.execute(text("select max(created_at) from runs")).first()
    return rows, (str(newest[0]) if newest and newest[0] is not None else None)


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
    window = resolve_window(since, until)

    rows, newest_in_database = _read(url, window)
    for run_id, created_at, spec_json, provenance_json in rows:
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
        # V-85 / V-92: the resolved window, and the SAME object the query used
        "window": window,
        "newest_in_window": newest,
        # V-91: window-independent, so the V-71 cutoff cannot be skewed by a
        # bounded baseline
        "newest_in_database": newest_in_database,
        "total_runs": total,
        "runs_with_any_provenance": with_provenance,
        "eligible_runs": len(eligible),
        "not_inspectable": total - len(eligible),
        "detected": detected,
    }


def follow_up_invocations(result: dict[str, Any]) -> dict[str, str]:
    """V-90: byte-for-byte runnable commands, every flag resolved to a concrete
    value. A printed command that differs from the one that produced it carries
    authority it has not earned — an omitted --since is exactly how the V-71
    sanity check ends up comparing two different windows and calling the
    difference a change in detection."""
    w = result["window"]
    base = "uv run --project backend python scripts/audit_strike_width_rewrites.py"
    flags = []
    if w["since"]:
        flags.append(f"--since {w['since']}")
    if w["until"]:
        flags.append(f"--until {w['until']}")
    sanity = " ".join([base, *flags]) if flags else base
    return {"sanity": sanity, "actual": base}


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
        print(f"  since               : {w['since']} (inclusive)   [--since {w['since']}]")
    else:
        print("  since               : beginning of time   [default, no --since]")
    if w["until"]:
        shape = (
            f"covers all of {w['until']}"
            if w["until_form"] == "date"
            else f"inclusive of {w['until']}"
        )
        print(f"  until               : created_at {w['until_op']} {w['until_value']}"
              f"   [--until {w['until']}, {shape}]")
    else:
        print("  until               : unbounded   [default, no --until]")
    # V-91: both, labelled. The database-wide figure is the one the V-71
    # cutoff uses, because a bounded scan's maximum would make runs that
    # predate the fix look like new instances after it.
    print(f"  newest in window    : {result['newest_in_window'] or 'none'}")
    print(f"  newest in DATABASE  : {result['newest_in_database'] or 'none'}")

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
    cmds = follow_up_invocations(result)
    cutoff = result["newest_in_database"]
    print("  (a) SANITY CHECK — this exact command, unchanged:")
    print(f"        {cmds['sanity']}")
    print("      Must be IDENTICAL to this run. A difference means detection")
    print("      itself changed, not that the bug did.")
    print("  (b) THE ACTUAL TEST — unbounded:")
    print(f"        {cmds['actual']}")
    if cutoff:
        print(f"      Any detection with created_at after {cutoff} is a NEW")
        print("      instance arising after the fix, so the fix did not hold.")
        print("      That cutoff is the newest run in the DATABASE, not in this")
        print("      window, so bounding the baseline cannot skew it.")
    else:
        print("      The database is empty, so there is no cutoff to compare against.")

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

    try:
        result = audit(url, args.since, args.until)
    except WindowArgumentError as exc:
        # V-97: exit non-zero with the offending value. The banner must NEVER
        # print on a rejected input — a confident "WINDOW APPLIED" block above
        # a wrong window is the whole problem.
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        _print_text(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
