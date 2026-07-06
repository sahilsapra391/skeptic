"""Data routes — real lake coverage and underlying series."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.data import bars as bars_mod
from app.data import coverage
from app.data.r2 import R2NotConfigured

router = APIRouter()

_R2_HINT = (
    "R2 credentials not configured on the backend — set R2_ACCOUNT_ID, "
    "R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET. Coverage is never "
    "faked; without the lake this endpoint refuses."
)


@router.get("/coverage")
def get_coverage() -> dict[str, Any]:
    try:
        return coverage.coverage_cached()
    except R2NotConfigured as exc:
        raise HTTPException(status_code=503, detail=f"{_R2_HINT} ({exc})") from exc


@router.get("/bars/{ticker}")
def get_bars(
    ticker: str,
    interval: str = Query(default="5m"),
    window: str = Query(default="1w"),
    indicators: str = Query(default=""),
    before: str | None = Query(default=None, description="page: bars strictly before this ts"),
    limit: int | None = Query(default=None, ge=50, le=5000),
) -> dict[str, Any]:
    ticker = ticker.upper()
    if ticker not in bars_mod.TICKERS:
        raise HTTPException(status_code=404, detail=f"unsupported ticker {ticker}")
    if interval not in bars_mod.INTERVALS:
        raise HTTPException(
            status_code=422,
            detail=f"interval must be one of {bars_mod.INTERVALS} — tick intervals "
            "don't exist: the lake has no tick data and no $0 source provides it",
        )
    if window not in bars_mod.WINDOWS:
        raise HTTPException(status_code=422, detail=f"window must be one of {bars_mod.WINDOWS}")
    specs = [s for s in indicators.split(",") if s.strip()]
    try:
        return bars_mod.get_bars(ticker, interval, window, specs, before=before, limit=limit)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=f"bad `before` timestamp: {exc}") from exc
    except R2NotConfigured as exc:
        raise HTTPException(status_code=503, detail=f"{_R2_HINT} ({exc})") from exc


_WINDOW_YEARS = {"1y": 1, "3y": 3, "5y": 5, "10y": 10}
_ESTIMATE_SAMPLE = 8  # rolling window of measured runs per clock


@router.get("/estimate")
def get_estimate(
    ticker: str = Query(default="SPY"),
    clock: str = Query(default="daily"),
) -> dict[str, Any]:
    """Pre-run window options with session counts (real coverage) and time
    estimates (medians over MEASURED runs on this box — perf_json). When
    nothing has been measured yet at this clock, the estimate is honestly
    null and the first run calibrates; never an invented number."""
    import json as _json
    import statistics
    from datetime import date, timedelta

    from app import db
    from app.data.chains import load_market_store

    ticker = ticker.upper()
    if ticker not in coverage.TICKERS:
        raise HTTPException(status_code=404, detail=f"unsupported ticker {ticker}")
    if clock not in ("daily", "5min"):
        raise HTTPException(status_code=422, detail="clock must be daily or 5min")

    # sessions the engine would actually simulate, per window
    try:
        if clock == "5min":
            from app.data.intraday import load_intraday_store

            sessions = load_intraday_store(ticker).sessions()
        else:
            sessions = load_market_store(ticker).chain_dates
    except R2NotConfigured as exc:
        raise HTTPException(status_code=503, detail=f"{_R2_HINT} ({exc})") from exc

    # measured throughput: median total-seconds-per-session over the most
    # recent completed runs at this clock (engine + gauntlet, same box)
    rates: list[float] = []
    verdict_costs: list[float] = []
    with db.session() as s:
        rows = (
            s.query(db.Run.perf_json)
            .filter(db.Run.status == "done", db.Run.perf_json.isnot(None))
            .order_by(db.Run.created_at.desc())
            .limit(50)
            .all()
        )
    for (perf_json,) in rows:
        try:
            p = _json.loads(perf_json)
        except Exception:
            continue
        n = int(p.get("sessions") or 0)
        if p.get("clock") == clock and n > 0:
            rates.append((float(p["engine_s"]) + float(p["gauntlet_s"])) / n)
            # verdict narration is ~constant per run, not per session
            verdict_costs.append(float(p.get("verdict_s") or 0.0))
        if len(rates) >= _ESTIMATE_SAMPLE:
            break
    rate = statistics.median(rates) if rates else None
    verdict_const = statistics.median(verdict_costs) if verdict_costs else 0.0

    today = date.today()
    options: list[dict[str, Any]] = []
    for key, years in _WINDOW_YEARS.items():
        cutoff = today - timedelta(days=round(years * 365.25))
        n = sum(1 for d in sessions if d >= cutoff)
        options.append({
            "key": key,
            "sessions": n,
            "est_seconds": round(n * rate + verdict_const) if rate is not None and n else None,
        })
    options.append({
        "key": "all",
        "sessions": len(sessions),
        "est_seconds": (
            round(len(sessions) * rate + verdict_const)
            if rate is not None and sessions
            else None
        ),
    })
    return {
        "ticker": ticker,
        "clock": clock,
        "first_session": str(sessions[0]) if sessions else None,
        "options": options,
        "basis": {
            "measured_runs": len(rates),
            "note": (
                f"median of the last {len(rates)} measured {clock} run(s) on this server"
                if rates
                else f"no measured {clock} runs yet — the first run calibrates"
            ),
        },
    }


@router.get("/underlying/{ticker}")
def get_underlying(
    ticker: str, days: int = Query(default=252, ge=10, le=2000)
) -> dict[str, Any]:
    ticker = ticker.upper()
    if ticker not in coverage.TICKERS:
        raise HTTPException(status_code=404, detail=f"unsupported ticker {ticker}")
    try:
        series = coverage.underlying_series(ticker, days)
    except R2NotConfigured as exc:
        raise HTTPException(status_code=503, detail=f"{_R2_HINT} ({exc})") from exc
    if not series:
        raise HTTPException(status_code=404, detail=f"no underlying dailies for {ticker}")
    return {"ticker": ticker, "series": series}
