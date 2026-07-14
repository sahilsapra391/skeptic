"""Notebook export + pinned reproduce (parity Tier 1).

The machinery this module first duplicated while runs.py was owned by a
parallel session is consolidated now (2026-07-14): single-flight admission
and the pinned-window engine re-run live in app.api.jobs, the OOM lock in
app.engine.concurrency — a reproduce holds the same ENGINE_LOCK as runs
and audits, so it can never overlap them. The run's merged payload still
comes from get_run (receipts + provenance merge included).

Reproduce contract (the plan's Tier 1 item 2, D3 receipts semantics):
same spec, same seed, the ORIGINAL effective window pinned, and — at the
intraday clock — the RECORDED per-session resolution map pinned. A fresh
app run may resolve finer as the lake deepens; a replay never silently
re-resolves. Divergence (a pinned-minute session that can no longer build
a minute grid; a stat outside tolerance; a changed build) is REPORTED,
never papered over. Stored like receipts/audit: the run's verdict is
never rewritten.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Response
from fastapi.responses import JSONResponse
from sqlalchemy import select

from app import db
from app.api.jobs import claim_run_job, pinned_engine_rerun
from app.api.payload import sweep_coverage_notes
from app.api.provenance import derived_boxes

log = logging.getLogger("notebook")

router = APIRouter()

_RUNNING = "reproducing"

# metrics compared within tolerance; counts compared exactly. Relative
# 1e-6 absorbs float noise across platforms while catching any real
# drift (a single different fill moves these by orders more).
_COMPARE_METRICS = ("cagr", "sharpe", "sortino", "max_drawdown",
                    "win_rate", "profit_factor")
_REL_TOL = 1e-6


def _api_base() -> str:
    import os

    return os.environ.get("SKEPTIC_PUBLIC_API", "https://api.skeptic.fyi")


def _export_inputs(run_id: str) -> tuple[dict[str, Any], dict[str, Any],
                                         list[str] | None]:
    """Everything both export formats render from: the merged display
    payload, the validated spec doc, and the F8 sweep-coverage notes
    (which live on the stored honesty report, frozen at completion, not
    in the display payload — baked into the exports so the coverage
    story travels with the file)."""
    from app.api.runs import get_run

    # direct call, not HTTP — FastAPI won't resolve get_run's Query-typed
    # min_trades default, so pass None explicitly (= the omitted-param
    # behavior: the user's stored evidence-bar setting re-grades the read,
    # #98). The exports then show exactly what the app shows.
    payload = get_run(run_id, min_trades=None)  # 404s for us; merges
    # receipts + provenance
    if payload.get("status") != "done":
        raise HTTPException(status_code=409,
                            detail="run not finished — nothing to export yet")
    with db.session() as s:
        # column-scoped: get_run already hydrated the full row (multi-MB
        # payload_json included) — pull only the two small columns it
        # doesn't expose rather than re-fetching everything (review finding)
        row = s.execute(
            select(db.Run.spec_json, db.Run.stats_json)
            .where(db.Run.id == run_id)
        ).one_or_none()
    if row is None or not row.spec_json:
        raise HTTPException(status_code=404, detail="run not found")
    spec_doc = json.loads(row.spec_json)
    stats = json.loads(row.stats_json) if row.stats_json else {}
    # WHICH keys constitute the F8 sweep-coverage disclosure is
    # payload.py's call (the one selector), never a list baked here
    sweep_notes = sweep_coverage_notes(
        (stats.get("honesty_report") or {}).get("sensitivity"))
    return payload, spec_doc, sweep_notes or None


@router.get("/runs/{run_id}/notebook")
def export_notebook(run_id: str) -> JSONResponse:
    """The run's story as an .ipynb — provenance first, then the numbers,
    then the honesty gauntlet, then the pinned re-execution proof."""
    from app.notebook.builder import build_notebook

    payload, spec_doc, sweep_notes = _export_inputs(run_id)
    notebook = build_notebook(
        run_id=run_id,
        name=payload.get("name") or "Skeptic run",
        payload=payload,
        provenance=payload.get("provenance") or {},
        grid=derived_boxes(spec_doc),
        api_base=_api_base(),
        sweep_notes=sweep_notes,
    )
    return JSONResponse(
        content=notebook,
        media_type="application/x-ipynb+json",
        headers={"Content-Disposition":
                 f'attachment; filename="skeptic-run-{run_id}.ipynb"'},
    )


@router.get("/runs/{run_id}/report")
def export_report(run_id: str) -> Response:
    """The run's story as a standalone HTML document (owner ask
    2026-07-14: a format anyone can open) — same story as the notebook,
    static from the stored run, print-to-PDF clean. Served inline so a
    browser renders it; the filename rides along for saves."""
    from app.notebook.report import build_report

    payload, spec_doc, sweep_notes = _export_inputs(run_id)
    document = build_report(
        run_id=run_id,
        name=payload.get("name") or "Skeptic run",
        payload=payload,
        provenance=payload.get("provenance") or {},
        grid=derived_boxes(spec_doc),
        sweep_notes=sweep_notes,
    )
    return Response(
        content=document,
        media_type="text/html; charset=utf-8",
        headers={
            "Content-Disposition":
                f'inline; filename="skeptic-run-{run_id}.html"',
            # defense-in-depth: the document is fully static — even if
            # escaping ever regressed, nothing may execute or phone out.
            # Fonts are the one sanctioned external fetch (typography
            # directive); everything else is inline or forbidden.
            "Content-Security-Policy":
                "default-src 'none'; style-src 'unsafe-inline' "
                "https://fonts.googleapis.com; font-src "
                "https://fonts.gstatic.com; img-src data:",
        },
    )


def _expand_resolution_runs(runs: list[dict[str, Any]] | None) -> dict[date, str]:
    """Compressed [{first,last,sessions,resolution}] → {session: resolution}.
    Every calendar day in [first, last] is pinned — uncovered days never
    build a slice, so over-pinning them is inert."""
    pinned: dict[date, str] = {}
    for r in runs or []:
        try:
            first = date.fromisoformat(str(r["first"]))
            last = date.fromisoformat(str(r["last"]))
            resolution = str(r["resolution"])
        except (KeyError, ValueError, TypeError):
            continue
        day = first
        while day <= last:
            pinned[day] = resolution
            day += timedelta(days=1)
    return pinned


@router.post("/runs/{run_id}/reproduce")
def reproduce_run(run_id: str, tasks: BackgroundTasks) -> dict[str, Any]:
    claim_run_job(run_id, column="reproduce_json",
                  running_status=_RUNNING, verb="reproduce")
    tasks.add_task(_execute_reproduce, run_id)
    return {"run_id": run_id, "status": _RUNNING}


@router.get("/runs/{run_id}/reproduce")
def reproduce_status(run_id: str) -> dict[str, Any]:
    with db.session() as s:
        run = s.get(db.Run, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="run not found")
        if not run.reproduce_json:
            return {"run_id": run_id, "status": "never_run"}
        try:
            return {"run_id": run_id, **json.loads(run.reproduce_json)}
        except ValueError:
            return {"run_id": run_id, "status": "corrupt_record"}


def _write_reproduce(run_id: str, record: dict[str, Any]) -> None:
    with db.session() as s:
        run = s.get(db.Run, run_id)
        if run is not None:
            run.reproduce_json = json.dumps(
                {**record, "finished_at": datetime.now(UTC).isoformat()})
            s.commit()


def _compare_row(key: str, stored: Any, fresh: Any,
                 exact: bool = False) -> dict[str, Any]:
    """ONE comparison policy for every stat (review finding: two adjacent
    loops had drifted on None handling and scale). Both-None is a match;
    a stat the STORED run never recorded is unevaluable (ok=None, excluded
    from `match` — an old stats bundle is a shape gap, not a drift); a
    stat the fresh run failed to produce IS a mismatch."""
    row: dict[str, Any] = {"stat": key, "stored": stored, "fresh": fresh}
    if stored is None and fresh is None:
        row["ok"] = True
    elif stored is None:
        row["ok"] = None
        row["note"] = "not recorded on the stored run — not comparable"
    elif fresh is None:
        row["ok"] = False
    elif exact:
        row["ok"] = stored == fresh
    else:
        scale = max(abs(float(stored)), abs(float(fresh)), 1e-12)
        row["ok"] = abs(float(stored) - float(fresh)) / scale <= _REL_TOL
    return row


def _divergence_report(
    recorded_runs: list[dict[str, Any]] | None,
    actual: dict[date, str],
) -> list[dict[str, Any]]:
    """Recorded compressed resolution runs vs the replay's per-session
    record. Per-day pin-vs-actual alone is blind in two ways the review
    caught: a compressed run spans uncovered gap days, so a day the lake
    backfilled AFTER the original run replays inside the range unnoticed
    (count check catches it — each recorded run carries its session
    count), and a session the replay no longer covers never appears in
    the replay map at all (same count check). Sessions outside every
    recorded range are named individually."""
    if not recorded_runs:
        # nothing recorded (daily clock, or a pre-FX.1 intraday run whose
        # notebook already discloses "window and seed only") — there is no
        # map to diverge FROM; the stat comparison still stands guard
        return []
    out: list[dict[str, Any]] = []
    ranges: list[tuple[date, date]] = []
    for r in recorded_runs:
        try:
            first = date.fromisoformat(str(r["first"]))
            last = date.fromisoformat(str(r["last"]))
            resolution = str(r["resolution"])
            sessions = int(r["sessions"])
        except (KeyError, ValueError, TypeError):
            continue
        ranges.append((first, last))
        replayed = {d: res for d, res in actual.items() if first <= d <= last}
        flipped = sorted(d.isoformat() for d, res in replayed.items()
                         if res != resolution)
        if flipped:
            out.append({"range": f"{first} → {last}", "pinned": resolution,
                        "issue": f"resolution flipped on {', '.join(flipped)}"})
        if len(replayed) != sessions:
            out.append({
                "range": f"{first} → {last}", "pinned": resolution,
                "issue": (f"recorded {sessions} covered session(s), replay "
                          f"covered {len(replayed)} — the lake changed "
                          "inside this range since the original run"),
            })
    for day in sorted(actual):
        if not any(first <= day <= last for first, last in ranges):
            out.append({"session": day.isoformat(),
                        "issue": "covered by the replay but not by the "
                                 "original run's record"})
    return out


def _execute_reproduce(run_id: str) -> None:
    import os

    try:
        with db.session() as s:
            run = s.get(db.Run, run_id)
            if run is None or not run.spec_json:
                return
            spec_doc = json.loads(run.spec_json)
            stats = json.loads(run.stats_json) if run.stats_json else {}
            # extract ONLY the compressed resolution runs — the parsed
            # payload can be MBs (equity series, full trade log) and must
            # not stay pinned across the engine run (OOM-guard directive;
            # review finding — the audit baseline never loads it at all)
            recorded_runs = None
            if run.payload_json:
                recorded_runs = (json.loads(run.payload_json) or {}
                                 ).get("resolutionRuns")
            # provenance is merged into the payload at READ time, not
            # stored inside payload_json — read the column directly
            try:
                prov = json.loads(run.provenance_json) if run.provenance_json else {}
            except ValueError:
                prov = {}

        pinned = _expand_resolution_runs(recorded_runs)

        # window pin + lock + refresh=False stores: the shared verification
        # scaffold (app.api.jobs); reproduce additionally pins the RECORDED
        # per-session resolution map — a replay never silently re-resolves
        spec, result, eff_start, eff_end = pinned_engine_rerun(
            spec_doc, stats, pinned_resolutions=pinned or None)

        stored_metrics = stats.get("metrics") or {}
        fresh_metrics = result.metrics or {}
        compared = [
            _compare_row(key, stored_metrics.get(key), fresh_metrics.get(key))
            for key in _COMPARE_METRICS
        ]
        compared.append(_compare_row("filled", stats.get("filled"),
                                     result.filled, exact=True))
        compared.append(_compare_row(
            "final_equity", stats.get("final_equity"),
            result.equity[-1] if result.equity else None))
        mismatches = [row["stat"] for row in compared if row["ok"] is False]
        unevaluable = [row["stat"] for row in compared if row["ok"] is None]

        divergence = _divergence_report(
            recorded_runs, result.resolution_by_session or {})

        build_then = (prov.get("mechanics") or {}).get("build", {}).get("commit")
        _write_reproduce(run_id, {
            "status": "done",
            "match": not mismatches and not divergence,
            "mismatched": mismatches or None,
            "unevaluable": unevaluable or None,
            "tolerance": f"relative {_REL_TOL:g} on metrics; counts exact",
            "compared": compared,
            "resolution_divergence": divergence or None,
            "pinned_sessions": len(pinned),
            "window": {"start": eff_start, "end": eff_end},
            "seed": spec.backtest.seed,
            "build_then": build_then,
            "build_now": os.environ.get("RAILWAY_GIT_COMMIT_SHA"),
        })
    except Exception as exc:
        log.exception("reproduce failed for %s", run_id)
        _write_reproduce(run_id, {
            "status": "error",
            "error": f"reproduce failed: {str(exc).strip().splitlines()[0][:300]}",
        })
