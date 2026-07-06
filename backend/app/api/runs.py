"""Run pipeline routes.

M2+M3: POST /api/backtest runs the engine AND the full honesty gauntlet
(OOS split, walk-forward, Monte Carlo, sensitivity, DSR, regime guard),
then the verdict writer — trust levels computed deterministically, every
narrated number validated against the stats payload.
/api/runs/{id}/ask answers questions grounded in the stored stats bundle
(same numeric validator); /api/parse (M4) stays an explicit 501.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel, ValidationError

from app import db
from app.api.payload import build_run_payload, run_summary
from app.models.spec import StrategySpec

log = logging.getLogger("runs")
router = APIRouter()

_PENDING_PARSE_KEY = (
    "the NL parser needs OPENROUTER_API_KEY — without it nothing is "
    "guessed server-side, ever."
)
_PENDING_ASK_KEY = (
    "grounded Q&A needs OPENROUTER_API_KEY — no key, no answers, "
    "and no numbers are invented in the meantime."
)
_PENDING_ASK_STATS = (
    "this run predates grounded Q&A (no stored stats bundle) — "
    "re-run the strategy to ask about it."
)
_PENDING_SWEEP = (
    "standalone sweeps arrive with the compare/sweep UI — the gauntlet already "
    "runs a ±20% sensitivity sweep on every backtest."
)


class ParseRequest(BaseModel):
    text: str
    answers: dict[str, Any] | None = None


VALID_ORIGINS = {"user", "auto_unlock", "receipt"}
AUTO_NOTE_MAX = 120


class BacktestRequest(BaseModel):
    spec: dict[str, Any]
    seed: int | None = None
    # D3: automatic runs declare themselves — origin drives the trial-
    # counter policy and the Library's upgrade markers
    origin: str = "user"
    parent_run_id: str | None = None
    auto_note: str | None = None  # e.g. "62 new sessions" (server-truncated)


def _inherit_trials(parent_run_id: str | None, family: str) -> int:
    """Trial count for an AUTO re-run (owner decision, HONESTY.md): the same
    spec on more data is NOT a new try at the family — no bump. Prefer the
    parent's recorded trial count; fall back to the current counter value
    read without incrementing."""
    if parent_run_id:
        with db.session() as s:
            parent = s.get(db.Run, parent_run_id)
            if parent is not None and parent.stats_json:
                try:
                    trials = json.loads(parent.stats_json)["honesty_report"]["dsr"]["trials"]
                    return max(int(trials), 1)
                except Exception:
                    pass
    with db.session() as s:
        row = s.get(db.TrialCounter, family)
        return max(row.trials if row else 1, 1)


def _execute_run(run_id: str, auto_note: str | None = None) -> None:
    """Background job: load the lake, run the engine, store the payload."""
    with db.session() as s:
        run = s.get(db.Run, run_id)
        if run is None:
            return
        run.status = "running"
        run.stage = 0
        s.add(db.RunEvent(run_id=run_id, stage=0, label="backtest running"))
        s.commit()
        spec_json = run.spec_json
        origin = run.origin or "user"
        parent_run_id = run.parent_run_id

    try:
        spec = StrategySpec.model_validate(json.loads(spec_json))
        from app.data.chains import load_market_store
        from app.engine.runner import run_backtest
        from app.honesty.gauntlet import run_gauntlet
        from app.honesty.verdict import write_verdicts

        store = load_market_store(spec.underlying.ticker.value)
        intraday = None
        if spec.backtest.clock.value != "daily":
            from app.data.intraday import load_intraday_store

            intraday = load_intraday_store(spec.underlying.ticker.value)

        def _progress(done: int, total: int) -> None:
            # a full-history 5-min run takes minutes; prove life in the
            # event log so a stuck stage is distinguishable from work
            with db.session() as ps:
                ps.add(db.RunEvent(run_id=run_id, stage=0,
                                   label=f"simulating — {done}/{total} sessions"))
                ps.commit()

        engine_t0 = time.monotonic()
        result = run_backtest(spec, store, intraday, progress=_progress)
        engine_seconds = time.monotonic() - engine_t0

        # every HUMAN attempt at a family is a trial — the multiple-testing
        # bias the deflated Sharpe corrects for (TECH-SPEC §6.5). AUTO
        # re-runs are the same spec on more data — no new choice was made,
        # so they inherit the parent's count instead of bumping (owner
        # decision; docs/HONESTY.md).
        family = f"{spec.underlying.ticker.value}:{spec.position.structure.value}"
        if origin in ("auto_unlock", "receipt"):
            trials = _inherit_trials(parent_run_id, family)
        else:
            trials = db.bump_trials(family)

        previews: list[dict[str, str]] = []

        def on_stage(stage: int, label: str, preview: dict[str, str] | None = None) -> None:
            if preview:
                previews.append(preview)
            with db.session() as s2:
                r2 = s2.get(db.Run, run_id)
                if r2 is not None:
                    r2.stage = stage
                    r2.previews_json = json.dumps(previews)
                    s2.add(db.RunEvent(run_id=run_id, stage=stage, label=label))
                    s2.commit()

        gauntlet_t0 = time.monotonic()
        report = run_gauntlet(spec, store, result, trials=trials, on_stage=on_stage,
                              intraday=intraday)
        gauntlet_seconds = time.monotonic() - gauntlet_t0
        # D3a: refused verdicts store their unlock needs structured — the
        # nightly auto-unlock scan reasons from these
        from app.honesty.stages import unlock_conditions

        unlock = unlock_conditions(report, spec)
        verdict_t0 = time.monotonic()
        verdict, retail_verdict = write_verdicts(report)
        verdict_seconds = time.monotonic() - verdict_t0
        payload = build_run_payload(run_id, spec, result, report, verdict, retail_verdict)
        # the stats bundle is the ONLY material grounded Q&A may quote from
        stats = {
            "metrics": result.metrics,
            "filled": result.filled,
            "skipped": result.skipped,
            "initial_capital": spec.backtest.initial_capital,
            "final_equity": result.equity[-1] if result.equity else None,
            "honesty_report": report.model_dump(),
        }
        # measured run cost — the pre-run time estimates are medians over
        # these rows (per clock, on THIS box), never invented numbers
        perf = {
            "clock": spec.backtest.clock.value,
            "sessions": report.coverage.chain_sessions,
            "engine_s": round(engine_seconds, 2),
            "gauntlet_s": round(gauntlet_seconds, 2),
            # ~constant per run regardless of window (LLM narration) — the
            # estimate adds it so wall-clock predictions stay honest
            "verdict_s": round(verdict_seconds, 2),
            "conditions": bool(spec.entry.conditions),
        }
        with db.session() as s:
            run = s.get(db.Run, run_id)
            if run is None:
                return
            run.status = "done"
            run.stage = 6
            run.perf_json = json.dumps(perf)
            if origin == "auto_unlock":
                payload["meta"] += " · auto-upgraded" + (f" ({auto_note})" if auto_note else "")
            run.payload_json = json.dumps(payload)
            run.stats_json = json.dumps(stats)
            run.unlock_json = unlock.model_dump_json() if unlock else None
            created = run.created_at.strftime("%b %-d ’%y") if run.created_at else ""
            summary = run_summary(run_id, payload, created)
            if origin == "auto_unlock":
                summary["upgradeOf"] = parent_run_id
                summary["autoNote"] = (
                    f"re-ran automatically — {auto_note}" if auto_note
                    else "re-ran automatically on new data"
                )
            elif origin == "receipt":
                summary["upgradeOf"] = parent_run_id
                summary["autoNote"] = "5-min replay (verdict receipt)"
            run.summary_json = json.dumps(summary)
            s.add(db.RunEvent(run_id=run_id, stage=6, label="gauntlet complete"))
            # the superseded refusal points forward to its upgrade
            if origin == "auto_unlock" and parent_run_id:
                parent = s.get(db.Run, parent_run_id)
                if parent is not None and parent.summary_json:
                    try:
                        psum = json.loads(parent.summary_json)
                        psum["supersededBy"] = run_id
                        parent.summary_json = json.dumps(psum)
                    except Exception:
                        pass
            # D3c: a completed replay writes its receipt onto the ORIGINAL
            # run — appended, never overwriting the stored verdict
            if origin == "receipt" and parent_run_id:
                parent = s.get(db.Run, parent_run_id)
                if parent is not None and parent.stats_json:
                    try:
                        from app.api.replay import build_receipt

                        receipt = build_receipt(
                            run_id,
                            json.loads(parent.stats_json),
                            stats,
                            payload,
                            datetime.now(UTC).isoformat(),
                        )
                        existing = (
                            json.loads(parent.receipts_json)
                            if parent.receipts_json else []
                        )
                        existing.append(receipt)
                        parent.receipts_json = json.dumps(existing)
                    except Exception:
                        log.exception("receipt attach failed for %s", parent_run_id)
            s.commit()
    except Exception as exc:
        log.exception("run %s failed", run_id)
        with db.session() as s:
            run = s.get(db.Run, run_id)
            if run is not None:
                run.status = "error"
                run.error = f"{type(exc).__name__}: {exc}"
                s.add(db.RunEvent(run_id=run_id, stage=run.stage, label="failed"))
                s.commit()


@router.post("/parse")
def parse(req: ParseRequest) -> dict[str, Any]:
    if not req.text.strip():
        raise HTTPException(status_code=422, detail="empty strategy text")

    from app.parser.parse import parse_strategy, spec_to_draft

    answers = {str(k): str(v) for k, v in (req.answers or {}).items()}
    outcome = parse_strategy(req.text, answers or None)
    if outcome is None:
        raise HTTPException(status_code=501, detail=_PENDING_PARSE_KEY)
    if outcome.status == "questions":
        return {
            "status": "questions",
            "demo": False,
            "questions": [q.model_dump() for q in outcome.questions],
        }
    assert outcome.spec is not None
    return {
        "status": "spec",
        "demo": False,
        "draft": spec_to_draft(outcome.spec, req.text.strip()),
        "spec": outcome.spec,
    }


@router.post("/backtest")
def backtest(req: BacktestRequest, tasks: BackgroundTasks) -> dict[str, Any]:
    try:
        spec = StrategySpec.model_validate(req.spec)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc
    if req.seed is not None:
        spec.backtest.seed = req.seed

    if req.origin not in VALID_ORIGINS:
        raise HTTPException(status_code=422, detail=f"unknown origin {req.origin!r}")

    run_id = uuid.uuid4().hex[:12]
    with db.session() as s:
        s.add(
            db.Run(
                id=run_id,
                status="queued",
                stage=0,
                seed=spec.backtest.seed,
                spec_json=spec.model_dump_json(),
                origin=req.origin,
                parent_run_id=req.parent_run_id,
            )
        )
        s.commit()
    note = (req.auto_note or "")[:AUTO_NOTE_MAX] or None
    tasks.add_task(_execute_run, run_id, note)
    return {"run_id": run_id, "demo": False, "status": "queued"}


@router.get("/runs")
def list_runs() -> dict[str, Any]:
    """Library listing. Reads ONLY the small summary column — pulling 50
    full payloads (equity series and all) per listing is how a database
    transfer quota dies. Queued/running runs get an ephemeral summary so
    navigating away from the progress screen never 'loses' a run."""
    with db.session() as s:
        rows = (
            s.query(
                db.Run.id,
                db.Run.created_at,
                db.Run.status,
                db.Run.stage,
                db.Run.summary_json,
                db.Run.spec_json,
            )
            .filter(db.Run.status.in_(["queued", "running", "done"]))
            .order_by(db.Run.created_at.desc())
            .limit(50)
            .all()
        )
        runs: list[dict[str, Any]] = []
        backfilled = False
        for run_id, created_at, status, stage, summary_json, spec_json in rows:
            if status in ("queued", "running"):
                spec = json.loads(spec_json)
                created = created_at.strftime("%b %-d ’%y") if created_at else ""
                runs.append(
                    {
                        "id": run_id,
                        "demo": False,
                        "status": "running",
                        "stage": stage or 0,
                        "name": spec.get("meta", {}).get("name", run_id),
                        "meta": f"started {created}" if created else "in progress",
                        "quote": "",
                        "kind": "verdict",
                    }
                )
                continue
            if summary_json:
                runs.append(json.loads(summary_json))
                continue
            # stored before summary_json existed — build once, persist, done
            run = s.get(db.Run, run_id)
            if run is None or not run.payload_json:
                continue
            created = created_at.strftime("%b %-d ’%y") if created_at else ""
            summary = run_summary(run_id, json.loads(run.payload_json), created)
            run.summary_json = json.dumps(summary)
            backfilled = True
            runs.append(summary)
        if backfilled:
            s.commit()
    return {"runs": runs, "demo": False}


@router.get("/runs/{run_id}")
def get_run(run_id: str) -> dict[str, Any]:
    with db.session() as s:
        run = s.get(db.Run, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")
    if run.status == "done" and run.payload_json:
        payload = dict(json.loads(run.payload_json))
        # D3c: receipts arrive AFTER the payload froze — merged at read
        # time; the stored verdict/trust inside the payload is untouched
        if run.receipts_json:
            try:
                payload["receipts"] = json.loads(run.receipts_json)
            except Exception:
                pass
        spec_dict = json.loads(run.spec_json) if run.spec_json else {}
        from app.api.replay import replay_eligible_spec

        payload["replayEligible"] = (
            (run.origin or "user") == "user" and replay_eligible_spec(spec_dict)
        )
        return payload
    if run.status == "error":
        return {"id": run_id, "demo": False, "status": "error",
                "error": run.error or "run failed", "stage": run.stage}
    spec = json.loads(run.spec_json)
    return {
        "id": run_id,
        "demo": False,
        "status": "running",
        "stage": run.stage,
        "name": spec.get("meta", {}).get("name", run_id),
        # real intermediate stats from finished stages — the progress teasers
        "previews": json.loads(run.previews_json) if run.previews_json else [],
    }


class AskRequest(BaseModel):
    question: str
    verbiage: str | None = None  # "institutional" (default) | "retail"


@router.post("/runs/{run_id}/replay")
def replay_run(run_id: str, tasks: BackgroundTasks) -> dict[str, Any]:
    """On-demand verdict receipt (D3c, owner amendment 1): replay THIS
    daily run at the 5-minute clock, right now. The receipt attaches to
    the original when the replay completes; the stored verdict is never
    rewritten."""
    from app.api.replay import build_replay_spec, replay_eligible_spec

    with db.session() as s:
        run = s.get(db.Run, run_id)
        if run is None or run.status != "done" or not run.spec_json:
            raise HTTPException(status_code=404, detail="no completed run to replay")
        spec_dict = json.loads(run.spec_json)
    if not replay_eligible_spec(spec_dict):
        raise HTTPException(
            status_code=409,
            detail="not replayable: only daily-clock specs whose whole tenor "
                   "band fits the intraday slice (max_dte ≤ 2) can face the "
                   "5-minute record like-for-like",
        )
    try:
        replay_spec = StrategySpec.model_validate(build_replay_spec(spec_dict))
    except ValidationError as exc:
        raise HTTPException(status_code=409, detail=exc.errors()[:3]) from exc

    new_id = uuid.uuid4().hex[:12]
    with db.session() as s:
        s.add(db.Run(id=new_id, status="queued", stage=0,
                     seed=replay_spec.backtest.seed,
                     spec_json=replay_spec.model_dump_json(),
                     origin="receipt", parent_run_id=run_id))
        s.commit()
    tasks.add_task(_execute_run, new_id, None)
    return {"run_id": new_id, "demo": False, "status": "queued", "parent": run_id}


@router.post("/runs/{run_id}/ask")
def ask(run_id: str, req: AskRequest) -> dict[str, Any]:
    if not req.question.strip():
        raise HTTPException(status_code=422, detail="empty question")
    with db.session() as s:
        run = s.get(db.Run, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")
    if run.status != "done" or not run.stats_json:
        raise HTTPException(status_code=501, detail=_PENDING_ASK_STATS)

    from app.honesty.ask import answer_question

    answer = answer_question(
        req.question, json.loads(run.stats_json), retail=req.verbiage == "retail"
    )
    if answer is None:
        raise HTTPException(status_code=501, detail=_PENDING_ASK_KEY)
    return {"answer": answer, "demo": False}


@router.post("/sweep")
def sweep() -> dict[str, Any]:
    raise HTTPException(status_code=501, detail=_PENDING_SWEEP)
