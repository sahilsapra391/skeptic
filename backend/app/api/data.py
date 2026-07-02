"""Data routes — real lake coverage and underlying series."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

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
