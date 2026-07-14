"""Shared machinery for on-demand background jobs over a STORED run.

Two endpoints re-run a completed run's engine deterministically and attach
the outcome beside it — the fill audit (runs.py) and the notebook reproduce
(notebook.py). Both need the same two pieces, consolidated here 2026-07-14
once #96/#98 released runs.py:

* `claim_run_job` — single-flight admission: one job per run at a time,
  409 while one is honestly in flight, takeover once a marker is stale
  (a worker died mid-job). The claim is an atomic compare-and-swap on the
  marker column, so two racing POSTs can never both start an engine run
  (the old read-check-write let them).
* `pinned_engine_rerun` — the re-run scaffold: pin the ORIGINAL effective
  window from the stored honesty report, then run the engine behind
  ENGINE_LOCK with refresh=False stores.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, NamedTuple, cast

from fastapi import HTTPException
from sqlalchemy import func, select, update

from app import db
from app.engine.concurrency import ENGINE_LOCK, release_memory
from app.models.spec import StrategySpec

if TYPE_CHECKING:
    from datetime import date

    from sqlalchemy.engine import CursorResult

    from app.engine.types import RunResult

# a job is one engine re-run; a running marker older than this survived a
# worker death mid-job and may be taken over
STALE_TAKEOVER_MINUTES = 30


def marker_age_minutes(started_at: Any, stale_minutes: int) -> float:
    """Minutes since an ISO start stamp. A stamp that cannot be read counts
    as just past `stale_minutes`: a marker whose age cannot be established
    must never wedge its slot shut. Shared by every marker staleness check
    (job claims here, the narration release in runs.py)."""
    try:
        return (datetime.now(UTC)
                - datetime.fromisoformat(str(started_at))
                ).total_seconds() / 60
    except (TypeError, ValueError):
        # ValueError: unparseable stamp. TypeError: a tz-NAIVE stamp parses
        # fine and then the aware-minus-naive subtraction raises — persisted
        # stamps from older writers must read as stale, never 500 a GET
        return float(stale_minutes + 1)


def claim_run_job(run_id: str, *, column: str, running_status: str,
                  verb: str) -> None:
    """Admit ONE background job for this run or raise: 404 when there is no
    completed run to work on, 409 while a fresh job marker says one is
    already in flight. On admission the running marker (status + start
    stamp) is written to `column`.

    The write is a compare-and-swap against the marker value the staleness
    check was made on — the losing side of a race sees rowcount 0 and gets
    the same 409 a straight read would have produced a moment later."""
    marker_col = getattr(db.Run, column)
    # read and swap run in SEPARATE short transactions on purpose: holding
    # the read open across the UPDATE adds nothing (the atomicity lives in
    # the swap's WHERE), and on SQLite it would escalate a shared read lock
    # into the write — the classic busy-timeout deadlock shape
    with db.session() as s:
        row = s.execute(
            # the spec is only checked for presence — don't ship its body
            select(db.Run.status,
                   func.coalesce(db.Run.spec_json, "") != "",
                   marker_col)
            .where(db.Run.id == run_id)
        ).one_or_none()
    if row is None or row[0] != "done" or not row[1]:
        raise HTTPException(status_code=404,
                            detail=f"no completed run to {verb}")
    prior: str | None = row[2]
    if prior:
        try:
            marker = json.loads(prior)
        except ValueError:
            marker = {}
        if not isinstance(marker, dict):
            marker = {}
        if (marker.get("status") == running_status
                and marker_age_minutes(marker.get("started_at"),
                                       STALE_TAKEOVER_MINUTES)
                < STALE_TAKEOVER_MINUTES):
            raise HTTPException(status_code=409,
                                detail=f"{verb} already running")
    claim = json.dumps({"status": running_status,
                        "started_at": datetime.now(UTC).isoformat()})
    with db.session() as s:
        # DML through Session.execute is a CursorResult at runtime; the
        # stubs type it as Result, which hides rowcount — hence the cast.
        # `marker_col == prior` renders IS NULL when prior is None.
        result = cast("CursorResult[Any]", s.execute(
            update(db.Run)
            .where(db.Run.id == run_id, marker_col == prior)
            .values({column: claim})
            .execution_options(synchronize_session=False)
        ))
        s.commit()
    if result.rowcount != 1:
        # someone else claimed (or finished) between our read and the swap
        raise HTTPException(status_code=409,
                            detail=f"{verb} already running")


class PinnedRerun(NamedTuple):
    spec: StrategySpec
    result: RunResult
    eff_start: str | None
    eff_end: str | None


def pinned_engine_rerun(
    spec_doc: dict[str, Any],
    stats: dict[str, Any],
    *,
    pinned_resolutions: dict[date, str] | None = None,
) -> PinnedRerun:
    """Deterministically re-run a stored run's spec for verification jobs.

    Pins the re-run to the ORIGINAL effective window from the stored
    honesty report (the fill audit's review BLOCKER #1 lesson: an open end
    would let the lake's newest sessions extend the sim, and the job would
    describe a different run than the one it verifies).

    Loads AND the re-run happen behind ENGINE_LOCK — two overlapping store
    loads/peaks are the OOM concurrency class — with refresh=False: these
    jobs compare against the stored run, and a TTL rebuild mid-job would
    swap the lake under them. The warm store the run used is the honest
    input; a cold container still builds once, and each job's own drift
    guards catch whatever that build changes."""
    report = stats.get("honesty_report") or {}
    eff_start = report.get("effective_start")
    eff_end = report.get("effective_end")
    if eff_start:
        spec_doc.setdefault("backtest", {})["start"] = eff_start
    if eff_end:
        spec_doc.setdefault("backtest", {})["end"] = eff_end
    spec = StrategySpec.model_validate(spec_doc)

    from app.data.chains import load_market_store
    from app.engine.runner import run_backtest

    with ENGINE_LOCK:
        try:
            store = load_market_store(spec.underlying.ticker.value,
                                      refresh=False)
            intraday = None
            if spec.backtest.clock.value != "daily":
                from app.data.intraday import load_intraday_store

                intraday = load_intraday_store(spec.underlying.ticker.value)
            result = run_backtest(spec, store, intraday,
                                  pinned_resolutions=pinned_resolutions)
        finally:
            release_memory()
    return PinnedRerun(spec, result, eff_start, eff_end)
