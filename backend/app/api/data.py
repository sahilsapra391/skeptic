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
