"""Run pipeline routes.

M2+M3: POST /api/backtest runs the engine AND the full honesty gauntlet
(OOS split, walk-forward, Monte Carlo, sensitivity, DSR, regime guard),
then the verdict writer — trust levels computed deterministically, every
narrated number validated against the stats payload. /api/parse (M4) and
/api/runs/{id}/ask (M4) stay explicit 501s.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel, ValidationError

from app import db
from app.api.payload import build_run_payload, run_summary
from app.models.spec import StrategySpec

log = logging.getLogger("runs")
router = APIRouter()

_PENDING_PARSE = (
    "/api/parse is not built yet — the NL parser is milestone M4 "
    "(docs/BUILD-PLAN.md). Nothing is guessed server-side until it exists."
)
_PENDING_ASK = (
    "grounded Q&A lands with the NL parser (M4) — "
    "no numbers are invented in the meantime."
)
_PENDING_SWEEP = (
    "standalone sweeps arrive with the compare/sweep UI — the gauntlet already "
    "runs a ±20% sensitivity sweep on every backtest."
)


class ParseRequest(BaseModel):
    text: str
    answers: dict[str, Any] | None = None


class BacktestRequest(BaseModel):
    spec: dict[str, Any]
    seed: int | None = None


def _execute_run(run_id: str) -> None:
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

    try:
        spec = StrategySpec.model_validate(json.loads(spec_json))
        from app.data.chains import load_market_store
        from app.engine.runner import run_backtest
        from app.honesty.gauntlet import run_gauntlet
        from app.honesty.verdict import write_verdict

        store = load_market_store(spec.underlying.ticker.value)
        result = run_backtest(spec, store)

        # every attempt at a family is a trial — the multiple-testing bias
        # the deflated Sharpe corrects for (TECH-SPEC §6.5)
        family = f"{spec.underlying.ticker.value}:{spec.position.structure.value}"
        trials = db.bump_trials(family)

        def on_stage(stage: int, label: str) -> None:
            with db.session() as s2:
                r2 = s2.get(db.Run, run_id)
                if r2 is not None:
                    r2.stage = stage
                    s2.add(db.RunEvent(run_id=run_id, stage=stage, label=label))
                    s2.commit()

        report = run_gauntlet(spec, store, result, trials=trials, on_stage=on_stage)
        verdict = write_verdict(report)
        payload = build_run_payload(run_id, spec, result, report, verdict)
        with db.session() as s:
            run = s.get(db.Run, run_id)
            if run is None:
                return
            run.status = "done"
            run.stage = 6
            run.payload_json = json.dumps(payload)
            s.add(db.RunEvent(run_id=run_id, stage=6, label="gauntlet complete"))
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
def parse(_req: ParseRequest) -> dict[str, Any]:
    raise HTTPException(status_code=501, detail=_PENDING_PARSE)


@router.post("/backtest")
def backtest(req: BacktestRequest, tasks: BackgroundTasks) -> dict[str, Any]:
    try:
        spec = StrategySpec.model_validate(req.spec)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc
    if req.seed is not None:
        spec.backtest.seed = req.seed

    run_id = uuid.uuid4().hex[:12]
    with db.session() as s:
        s.add(
            db.Run(
                id=run_id,
                status="queued",
                stage=0,
                seed=spec.backtest.seed,
                spec_json=spec.model_dump_json(),
            )
        )
        s.commit()
    tasks.add_task(_execute_run, run_id)
    return {"run_id": run_id, "demo": False, "status": "queued"}


@router.get("/runs")
def list_runs() -> dict[str, Any]:
    with db.session() as s:
        rows = (
            s.query(db.Run)
            .filter(db.Run.status == "done")
            .order_by(db.Run.created_at.desc())
            .limit(50)
            .all()
        )
    runs = []
    for row in rows:
        payload = json.loads(row.payload_json) if row.payload_json else {}
        created = row.created_at.strftime("%b %-d ’%y") if row.created_at else ""
        runs.append(run_summary(row.id, payload, created))
    return {"runs": runs, "demo": False}


@router.get("/runs/{run_id}")
def get_run(run_id: str) -> dict[str, Any]:
    with db.session() as s:
        run = s.get(db.Run, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")
    if run.status == "done" and run.payload_json:
        return dict(json.loads(run.payload_json))
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
    }


@router.post("/runs/{run_id}/ask")
def ask(run_id: str) -> dict[str, Any]:
    raise HTTPException(status_code=501, detail=_PENDING_ASK)


@router.post("/sweep")
def sweep() -> dict[str, Any]:
    raise HTTPException(status_code=501, detail=_PENDING_SWEEP)
