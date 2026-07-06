"""Skeptic backend — FastAPI app (TECH-SPEC §3).

Live now: /api/health, /api/data/coverage, /api/data/underlying/{ticker}
(real, computed from the R2 lake). The run pipeline (/api/parse,
/api/backtest, /api/runs…) returns 501 until milestones M2–M4 land — the
frontend labels anything it shows in their place as demo data, never as
results.
"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from app.config import load_local_env

load_local_env()


def _warm_lake() -> None:
    """Prewarm the SPY chain store in the background so the first user run
    on a fresh container doesn't pay the cold R2 pull (minutes)."""
    import threading

    from app.data.chains import warm_store
    from app.data.r2 import r2_configured

    if r2_configured():
        threading.Thread(target=warm_store, name="lake-warmer", daemon=True).start()


_warm_lake()

from app.api import data as data_api  # noqa: E402
from app.api import runs as runs_api  # noqa: E402
from app.db import init_db  # noqa: E402


def _sweep_orphaned_runs() -> None:
    """Runs execute as in-process background tasks — a run still marked
    queued/running at BOOT died with the previous process (OOM, deploy,
    crash) and can never finish. Mark it honestly instead of letting the
    UI spin forever (incident 2026-07-06: a 40-minute phantom 'running')."""
    from app import db

    try:
        with db.session() as s:
            stuck = (
                s.query(db.Run)
                .filter(db.Run.status.in_(["queued", "running"]))
                .all()
            )
            for run in stuck:
                run.status = "error"
                run.error = (
                    "interrupted — the service restarted mid-run (out of memory "
                    "or a deploy); no results were computed. Re-run when ready."
                )
                s.add(db.RunEvent(run_id=run.id, stage=run.stage or 0,
                                  label="interrupted by service restart"))
            if stuck:
                s.commit()
    except Exception:  # a sweep failure must never block boot
        import logging

        logging.getLogger("main").exception("orphaned-run sweep failed")


app = FastAPI(title="Skeptic", version="0.1.0")
init_db()
_sweep_orphaned_runs()

app.add_middleware(GZipMiddleware, minimum_size=2048)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def bearer_auth(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """Single-user bearer token (TECH-SPEC §10). If SKEPTIC_ACCESS_TOKEN is
    unset (local dev), requests pass; health stays open for probes."""
    token = os.environ.get("SKEPTIC_ACCESS_TOKEN")
    if token and request.url.path.startswith("/api") and request.url.path != "/api/health":
        supplied = request.headers.get("authorization", "")
        if supplied != f"Bearer {token}":
            return Response(status_code=401, content='{"detail":"unauthorized"}',
                            media_type="application/json")
    return await call_next(request)


@app.get("/api/health")
def health() -> dict[str, object]:
    import os

    from app import db
    from app.data.r2 import r2_configured
    from app.honesty.stages import MIN_TRADES
    from app.honesty.verdict import DEFAULT_MODEL

    llm = bool(os.environ.get("OPENROUTER_API_KEY"))
    return {
        "status": "ok",
        "r2_configured": r2_configured(),
        "db": db.status(),
        "engine": "live — EOD engine + full honesty gauntlet",
        "parser": "live — English → spec, questions when ambiguous" if llm
        else "needs OPENROUTER_API_KEY",
        "verdict_llm": "live — validated narration, template fallback" if llm
        else "template only (no key)",
        "ask": "live — grounded Q&A on finished runs" if llm else "needs OPENROUTER_API_KEY",
        "model": os.environ.get("OPENROUTER_MODEL", DEFAULT_MODEL),
        "min_trades": MIN_TRADES,
    }


app.include_router(data_api.router, prefix="/api/data", tags=["data"])
app.include_router(runs_api.router, prefix="/api", tags=["runs"])


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> Response:
    import json

    return Response(
        status_code=exc.status_code,
        content=json.dumps({"detail": exc.detail}),
        media_type="application/json",
    )
