"""Run pipeline routes — honest 501 stubs until M2 (engine), M3 (honesty
layer) and M4 (parser) land. Spec validation is live so the IR contract is
enforced from day one."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ValidationError

from app.models.spec import StrategySpec

router = APIRouter()

_PENDING = (
    "not built yet — the backtest engine (M2), honesty layer (M3) and NL "
    "parser (M4) are upcoming milestones (docs/BUILD-PLAN.md). Nothing is "
    "simulated server-side until they exist."
)


class ParseRequest(BaseModel):
    text: str
    answers: dict[str, Any] | None = None


class BacktestRequest(BaseModel):
    spec: dict[str, Any]
    seed: int | None = None


@router.post("/parse")
def parse(_req: ParseRequest) -> dict[str, Any]:
    raise HTTPException(status_code=501, detail=f"/api/parse {_PENDING}")


@router.post("/backtest")
def backtest(req: BacktestRequest) -> dict[str, Any]:
    # the IR contract is enforced even before the engine exists: an invalid
    # spec is a 422 today, exactly as it will be after M2
    try:
        StrategySpec.model_validate(req.spec)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc
    raise HTTPException(status_code=501, detail=f"/api/backtest {_PENDING}")


@router.get("/runs")
def list_runs() -> dict[str, Any]:
    raise HTTPException(status_code=501, detail=f"/api/runs {_PENDING}")


@router.get("/runs/{run_id}")
def get_run(run_id: str) -> dict[str, Any]:
    raise HTTPException(status_code=501, detail=f"/api/runs/{run_id} {_PENDING}")


@router.post("/runs/{run_id}/ask")
def ask(run_id: str) -> dict[str, Any]:
    raise HTTPException(status_code=501, detail=f"/api/runs/{run_id}/ask {_PENDING}")


@router.post("/sweep")
def sweep() -> dict[str, Any]:
    raise HTTPException(status_code=501, detail=f"/api/sweep {_PENDING}")
