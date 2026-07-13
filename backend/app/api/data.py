"""Data routes — real lake coverage and underlying series."""

from __future__ import annotations

import threading
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.data import bars as bars_mod
from app.data import coverage, fill_calibration, r2
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
            # refresh=False: a GET must never trigger a store rebuild — that
            # work belongs to the engine path behind _ENGINE_LOCK (the
            # 2026-07-06 OOM class); a ≤30-min-stale session list is fine
            # for a time estimate
            sessions = load_market_store(ticker, refresh=False).chain_dates
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
    # F1: coverage-capped signal bounds — a spec conditioned on these
    # indicators refuses windows starting before the signal's first
    # session, so the composer can bound the window choice PRE-SUBMIT
    # (owner decision 2026-07-07: surface the bound while composing)
    signal_windows: dict[str, Any] = {}
    try:
        # the single small greek_exposure parquet, NOT the market store —
        # a cold 5-min estimate must not block on the full daily chain
        # build just to read two date lists (review finding F1 #4)
        from app.data import r2 as _r2
        from app.data.gex_signals import load_dealer_exposure

        gex, dex = load_dealer_exposure(_r2.r2_client(), ticker)
        all_dates = sorted(set(gex) | set(dex))
        if all_dates:
            base = sorted(gex) if gex else sorted(dex)
            signal_windows["dealer_positioning"] = {
                "first": str(all_dates[0]),
                "last": str(all_dates[-1]),
                # rank indicators stay unevaluable until 126 trailing
                # observations — the composer names the unlock date too
                "rank_first": str(base[125]) if len(base) > 125 else None,
                "indicators": ["gex_level", "gex_rank_1y",
                               "dex_level", "dex_rank_1y"],
            }
        from app.data.flow_signals import load_flow_signals

        # nested guard: a flow-artifact failure must not hide the dealer
        # window computed above (review finding F2/F3 #9)
        try:
            net_prem, _pcr, _nope, _mpd = load_flow_signals(_r2.r2_client(), ticker)
        except Exception:
            net_prem = {}
        flow_dates = sorted(net_prem)
        if flow_dates:
            signal_windows["options_flow"] = {
                "first": str(flow_dates[0]),
                "last": str(flow_dates[-1]),
                "rank_first": (str(flow_dates[125])
                               if len(flow_dates) > 125 else None),
                # per-ticker artifact indicators ONLY — market tide gets
                # its own exact window below (review finding F2/F3 #6)
                "indicators": ["net_premium_level", "net_premium_rank_1y",
                               "nope_level", "nope_rank_1y",
                               "put_call_flow_ratio", "max_pain_distance_pct"],
            }
        try:
            from app.data.flow_signals import load_market_tide

            tide_dates = sorted(load_market_tide(_r2.r2_client()))
        except Exception:
            tide_dates = []
        if tide_dates:
            signal_windows["market_tide"] = {
                "first": str(tide_dates[0]),
                "last": str(tide_dates[-1]),
                "rank_first": (str(tide_dates[125])
                               if len(tide_dates) > 125 else None),
                "indicators": ["market_tide_level", "market_tide_rank_1y"],
            }
    except Exception:  # honest absence — the run-time refusal still guards
        signal_windows = {}

    return {
        "ticker": ticker,
        "clock": clock,
        "first_session": str(sessions[0]) if sessions else None,
        "signal_windows": signal_windows,
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


_FILL_CAL_NOTE = (
    "Measured from the frozen UW option tape (every SPY/QQQ/IWM print with "
    "its NBBO at execution). slip is the fraction of the half-spread "
    "conceded from mid toward the adverse quote — the same quantity the "
    "engine CONFIGURES for fills. Reported only: the engine's configured "
    "slip is unchanged by these numbers; quantiles are histogram-bin "
    "estimates. Multi-leg, mid/no-side and locked/crossed-quote prints are "
    "excluded and counted."
)
_FILL_CAL_CACHE: dict[str, Any] = {"at": 0.0, "payload": None}
_FILL_CAL_TTL = 3600.0  # the artifact is frozen (tape trial over)
_fill_cal_lock = threading.Lock()


@router.get("/fill-calibration")
def get_fill_calibration() -> dict[str, Any]:
    """Pooled fill-slip calibration per ticker (D3d measurement surface)."""
    now = time.time()
    cached: dict[str, Any] | None = _FILL_CAL_CACHE["payload"]
    if cached is not None and now - _FILL_CAL_CACHE["at"] < _FILL_CAL_TTL:
        return cached
    with _fill_cal_lock:
        cached = _FILL_CAL_CACHE["payload"]
        if cached is not None \
                and time.time() - _FILL_CAL_CACHE["at"] < _FILL_CAL_TTL:
            return cached
        try:
            s3 = r2.r2_client()
        except R2NotConfigured as exc:
            raise HTTPException(status_code=503,
                                detail=f"{_R2_HINT} ({exc})") from exc
        tickers: dict[str, Any] = {}
        for t in coverage.TICKERS:
            df = fill_calibration.load_fill_calibration(s3, t)
            tickers[t] = (fill_calibration.pooled_summary(df)
                          if df is not None else None)
        payload = {"note": _FILL_CAL_NOTE, "tickers": tickers}
        # a transient R2 failure reads as None — cache that only briefly,
        # or a blip would serve "not measured" for the full hour
        complete = all(v is not None for v in tickers.values())
        at = time.time() if complete else time.time() - _FILL_CAL_TTL + 60.0
        _FILL_CAL_CACHE.update(at=at, payload=payload)
        return payload
