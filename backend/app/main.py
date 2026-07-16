"""Skeptic backend — FastAPI app (TECH-SPEC §3).

Live now: /api/health, /api/data/coverage, /api/data/underlying/{ticker}
(real, computed from the R2 lake). The run pipeline (/api/parse,
/api/backtest, /api/runs…) returns 501 until milestones M2–M4 land — the
frontend labels anything it shows in their place as demo data, never as
results.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from app.config import load_local_env

load_local_env()


def _warm_lake() -> None:
    """Prewarm the SPY chain store, the default chart views and the coverage
    payload in the background so neither the first user run nor the first
    chart/Observatory paint on a fresh container pays the cold R2 pull."""
    import threading

    from app.data.bars import warm_charts
    from app.data.chains import warm_store
    from app.data.coverage import warm_coverage
    from app.data.r2 import r2_configured

    if r2_configured():
        threading.Thread(target=warm_store, name="lake-warmer", daemon=True).start()
        threading.Thread(target=warm_charts, name="chart-warmer", daemon=True).start()
        threading.Thread(target=warm_coverage, name="coverage-warmer", daemon=True).start()


_warm_lake()

from app.api import data as data_api  # noqa: E402
from app.api import me as me_api  # noqa: E402
from app.api import notebook as notebook_api  # noqa: E402
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
    """Gate + identity (launch L1, app/auth). The service-token semantics
    are unchanged from the single-user era (TECH-SPEC §10): with
    SKEPTIC_ACCESS_TOKEN set, the exact bearer passes everything; a
    verified user session additionally passes the small user surface.
    If the token is unset (local dev), requests pass; health stays open
    for probes. Identity resolution can touch the DB and (once, at account
    creation) the Clerk API, so it runs off the event loop."""
    if not request.url.path.startswith("/api") or request.url.path == "/api/health":
        return await call_next(request)

    from fastapi.concurrency import run_in_threadpool

    from app import auth

    try:
        ctx = await run_in_threadpool(auth.authenticate, request)
    except auth.AccountsUnavailableError:
        return Response(
            status_code=503,
            content='{"detail":"accounts are unavailable — the accounts database is '
            'unreachable right now; charts and existing runs stay up"}',
            media_type="application/json",
        )
    request.state.auth = ctx
    if not auth.gate_allows(request.url.path, ctx):
        return Response(status_code=401, content='{"detail":"unauthorized"}',
                        media_type="application/json")
    return await call_next(request)


@app.get("/api/health")
def health() -> dict[str, object]:
    import os

    from app import db
    from app.auth import clerk
    from app.data.r2 import r2_configured
    from app.honesty.stages import MIN_TRADES
    from app.honesty.verdict import DEFAULT_MODEL, PARSER_MODEL

    llm = bool(os.environ.get("OPENROUTER_API_KEY"))
    return {
        "status": "ok",
        "r2_configured": r2_configured(),
        "db": db.status(),
        "accounts": "live — managed auth (Clerk)" if clerk.configured()
        else "single-user (no CLERK_ISSUER)",
        "engine": "live — EOD engine + full honesty gauntlet",
        "parser": "live — English → spec, questions when ambiguous" if llm
        else "needs OPENROUTER_API_KEY",
        "verdict_llm": "live — validated narration, template fallback" if llm
        else "template only (no key)",
        "ask": "live — grounded Q&A on finished runs" if llm else "needs OPENROUTER_API_KEY",
        "model": os.environ.get("OPENROUTER_MODEL", DEFAULT_MODEL),  # narration
        "parser_model": os.environ.get("OPENROUTER_PARSER_MODEL", PARSER_MODEL),
        "min_trades": MIN_TRADES,
    }


app.include_router(data_api.router, prefix="/api/data", tags=["data"])
app.include_router(runs_api.router, prefix="/api", tags=["runs"])
app.include_router(notebook_api.router, prefix="/api", tags=["notebook"])
app.include_router(me_api.router, prefix="/api", tags=["me"])


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> Response:
    """Every refusal must reach the user as JSON. A raw json.dumps chokes on
    non-serializable detail (pydantic ctx once carried live ValueError objects)
    and the handler's own crash turned honest 422s into bare plaintext 500s —
    the UI showed `500: {}` instead of the explanation (2026-07-07)."""
    import json

    from fastapi.encoders import jsonable_encoder

    try:
        content = json.dumps({"detail": jsonable_encoder(exc.detail)})
    except Exception:
        content = json.dumps({"detail": str(exc.detail)})
    return Response(
        status_code=exc.status_code,
        content=content,
        media_type="application/json",
        headers=exc.headers,
    )
