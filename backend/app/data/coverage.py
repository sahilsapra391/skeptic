"""Lake coverage — the real numbers behind /api/data/coverage.

Successor to collector/coverage.py (the M1 stand-in script), returning JSON
for the Data Observatory and the composer's coverage chips. Everything here
is computed from the lake; nothing is asserted that an object listing can't
prove (guardrail #6).
"""

from __future__ import annotations

import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any

import pandas as pd

from app.data import chains, gex_signals, ivs_signals, r2, resolution

TICKERS = ["SPY", "QQQ", "IWM"]
EOD_SOURCES = ["ivolatility", "alphavantage", "yahoo", "dolthub"]
INTRADAY_SOURCES = ["ivolatility", "cboe_delayed", "yahoo"]

# What the 5-minute record actually covers (mirrors app/data/intraday.py) —
# every intraday surface discloses the slice, per guardrail #6.
INTRADAY_SLICE_NOTE = (
    "5-min record is a short-DTE ATM slice: 0–2 trading-DTE, ATM±$8 "
    "(iVolatility NBBO; CBOE minute snapshots forward, ~15-min delayed)"
)

# fields the Observatory grades per source (D1d): what share of rows
# actually carry each field — gaps visible, not discovered mid-backtest
CHAIN_QUALITY_FIELDS = [
    "bid", "ask", "iv", "delta", "gamma", "theta", "vega", "rho",
    "volume", "open_interest",
]

# (built_at, payload) written as ONE tuple — readers can never pair a fresh
# timestamp with an older payload the way two separate keys could
_CACHE: dict[str, tuple[float, dict[str, Any] | None]] = {"snap": (0.0, None)}
CACHE_SECONDS = 300
# past TTL but under this ceiling, answer from cache while ONE background
# thread rebuilds — the numbers are real computed coverage with their age
# disclosed by generated_at, and the rebuild lands within seconds. Past the
# ceiling (long idle) build in the foreground: a very old recorder heartbeat
# must never be presented as current.
STALE_SERVE_SECONDS = 900
_BUILD_WORKERS = 16
_build_lock = threading.Lock()


def _range(dates: list[str]) -> dict[str, Any] | None:
    if not dates:
        return None
    return {"sessions": len(dates), "first": dates[0], "last": dates[-1]}


def _latest_snapshot_ts(s3: Any, source: str, ticker: str, dates: list[str]) -> str | None:
    """Timestamp of the newest snap_*.parquet under the latest date prefix."""
    if not dates:
        return None
    keys = r2.list_keys(
        s3, f"options_intraday/source={source}/ticker={ticker}/date={dates[-1]}/"
    )
    stamps = []
    for k in keys:
        m = re.search(r"snap_(\d{8}T\d{4}Z)", k)
        if m:
            stamps.append(m.group(1))
    if not stamps:
        return None
    return datetime.strptime(max(stamps), "%Y%m%dT%H%MZ").replace(tzinfo=UTC).isoformat()


def _quarantined_count(dolthub_state: dict[str, Any]) -> int:
    """Both integrity gates: parity-vs-close (quarantined_stale) and
    cross-source shape staleness (quarantined_stale_shape)."""
    stale = dolthub_state.get("quarantined_stale") or {}
    shape = dolthub_state.get("quarantined_stale_shape") or {}
    return len(set(stale) | set(shape))


def _blind_spots(dolthub_state: dict[str, Any], minute: dict[str, Any]) -> list[dict[str, str]]:
    """Named blind spots for the Observatory. Static facts come from the data
    evals (docs/DOLTHUB-EVAL.md, docs/DATA-PIPELINE.md §7); counts are live."""
    spots: list[dict[str, str]] = [
        {
            "id": "dolthub-mwf-era",
            "text": "SPY EOD history is Mon/Wed/Fri-granular before 2024-09 "
            "(checkpoint marks, not daily marks)",
        },
        {
            "id": "dolthub-2024-08-outage",
            "text": "Archive outage 2024-07-31 → 2024-08-09 spans the 2024-08-05 "
            "volatility spike",
        },
        {
            "id": "qqq-iwm-eod-depth",
            "text": "QQQ/IWM EOD chains begin 2026-07-01 — no free source reaches earlier",
        },
        {
            "id": "minute-lake-frozen",
            "text": "Alpaca minute bars are a frozen window: 2024-02 → 2026-06 "
            "(OPRA entitlement unavailable)",
        },
        {
            "id": "2026-07-01-eod-only",
            "text": "2026-07-01 has EOD coverage only — the intraday recorder "
            "starts 2026-07-02",
        },
        {
            "id": "recorder-best-effort",
            "text": "Intraday recorder runs on the owner's machine — uptime is "
            "best-effort and gaps are recorded, not hidden",
        },
        {
            "id": "intraday-slice",
            "text": INTRADAY_SLICE_NOTE
            + " — wider strikes and longer tenors have EOD coverage only",
        },
    ]
    quarantined = _quarantined_count(dolthub_state)
    if quarantined:
        spots.insert(
            2,
            {
                "id": "dolthub-quarantine",
                "text": f"{quarantined} archive sessions quarantined for date-stamp "
                "integrity (flag-and-exclude; objects retained for audit)",
            },
        )
    return spots


def _chain_quality(ticker: str) -> dict[str, Any] | None:
    """Per-source field completeness + monthly median spread, computed from
    the LOCAL chains cache written by the engine loader — this endpoint
    never triggers a full lake pull. No cache yet → honestly absent
    (guardrail #6: nothing asserted that the lake hasn't already proven)."""
    cache_file = chains.CACHE_DIR / f"chains_{ticker}.parquet"
    if not cache_file.exists():
        return None
    try:
        df = pd.read_parquet(cache_file)
    except Exception:
        return None
    if df.empty or "source" not in df.columns:
        return None

    out: dict[str, Any] = {"rows": int(len(df)), "sources": {}}
    for source, group in df.groupby("source"):
        fields = {
            col: round(float(group[col].notna().mean()), 4)
            for col in CHAIN_QUALITY_FIELDS
            if col in group.columns
        }
        out["sources"][str(source)] = {"rows": int(len(group)), "fields": fields}

    bid = pd.to_numeric(df["bid"], errors="coerce") if "bid" in df.columns else None
    ask = pd.to_numeric(df["ask"], errors="coerce") if "ask" in df.columns else None
    if bid is not None and ask is not None:
        mid = (bid + ask) / 2.0
        ok = bid.notna() & ask.notna() & (mid > 0)
        if bool(ok.any()):
            month = pd.to_datetime(df.loc[ok, "trading_date"]).dt.strftime("%Y-%m")
            spread = ((ask - bid) / mid)[ok]
            monthly = spread.groupby(month).median().sort_index()
            out["monthly_median_spread_pct"] = [
                {"month": str(m), "v": round(float(v) * 100, 2)} for m, v in monthly.items()
            ]
    return out


def _ivol_year_range(keys: list[str]) -> dict[str, Any] | None:
    """IVX / HV year-file coverage from an already-fetched listing."""
    years = sorted({m.group(1) for k in keys if (m := re.search(r"year=(\d{4})", k))})
    return {"years": len(years), "first": years[0], "last": years[-1]} if years else None



def _ivs_signals_range(df: pd.DataFrame | None) -> dict[str, Any] | None:
    """Window of the derived vol-surface signal artifact (F4) — guardrail
    #6: any surface offering skew/term filters shows the window they were
    derived on, per signal (a session can carry one and not the other)."""
    if df is None or df.empty or "date" not in df.columns:
        return None
    dates = df["date"].astype(str)
    def _n(col: str) -> int:
        return int(df[col].notna().sum()) if col in df.columns else 0
    return {
        "sessions": int(len(df)),
        "first": str(dates.min()),
        "last": str(dates.max()),
        "skew_sessions": _n("skew_25d"),
        "term_sessions": _n("term_slope_30_90"),
    }

def _dealer_positioning_range(df: pd.DataFrame | None) -> dict[str, Any] | None:
    """Window of the banked UW greek_exposure series (F1) — guardrail #6:
    the surface offering GEX/DEX filters shows the window they read, and
    the pre-run refusal quotes the same first session."""
    if df is None or df.empty or "date" not in df.columns:
        return None
    dates = df["date"].astype(str)

    def _n(cols: tuple[str, str]) -> int:
        if not all(c in df.columns for c in cols):
            return 0
        a = pd.to_numeric(df[cols[0]], errors="coerce")
        b = pd.to_numeric(df[cols[1]], errors="coerce")
        return int((a.notna() & b.notna()).sum())

    return {
        "sessions": int(len(df)),
        "first": str(dates.min()),
        "last": str(dates.max()),
        "gex_sessions": _n(("call_gamma", "put_gamma")),
        "dex_sessions": _n(("call_delta", "put_delta")),
    }


def build_coverage() -> dict[str, Any]:
    s3 = r2.r2_client()
    now = datetime.now(UTC)

    # Every independent lake read goes through one pool — the identical
    # reads producing the identical numbers, just concurrent. ~40 sequential
    # R2 round-trips was the whole reason the Observatory's first paint took
    # seconds. boto3 clients are thread-safe; all tasks are read-only.
    with ThreadPoolExecutor(max_workers=_BUILD_WORKERS) as pool:
        eod_f = {
            (src, t): pool.submit(r2.list_chain_dates, s3, src, t)
            for src in EOD_SOURCES
            for t in TICKERS
        }
        dolthub_f = pool.submit(r2.get_json, s3, "state/dolthub_backfill.json", {})
        minute_f = {
            t: pool.submit(
                r2.list_date_prefixes, s3, f"options_minute/source=alpaca/ticker={t}/"
            )
            for t in TICKERS
        }
        intraday_f = {
            (src, t): pool.submit(
                r2.list_date_prefixes, s3, f"options_intraday/source={src}/ticker={t}/"
            )
            for src in INTRADAY_SOURCES
            for t in TICKERS
        }
        targets = [(t, f"underlying/ticker={t}/daily.parquet") for t in TICKERS]
        targets.append(("VIX", "reference/vix_daily.parquet"))
        underlying_f = {sym: pool.submit(r2.get_parquet, s3, key) for sym, key in targets}
        ivol_f = {
            (t, name): pool.submit(r2.list_keys, s3, f"reference/ivol/{name}/ticker={t}/")
            for t in TICKERS
            for name in ("ivx", "hv")
        }
        ivs_signals_f = {
            t: pool.submit(
                r2.get_parquet, s3, ivs_signals.SIGNALS_KEY.format(ticker=t)
            )
            for t in TICKERS
        }
        dealer_f = {
            t: pool.submit(
                r2.get_parquet, s3, gex_signals.GEX_SERIES_KEY.format(ticker=t)
            )
            for t in TICKERS
        }
        quality_f = pool.submit(r2.get_json, s3, "state/quality_flags.json", {})
        priorities_f = pool.submit(r2.get_json, s3, "state/collection_priorities.json", None)
        new_sources_f = pool.submit(r2.get_json, s3, "state/source_coverage.json", None)
        resolution_f = {t: pool.submit(resolution.summary, s3, t) for t in TICKERS}
        chain_quality_f = {t: pool.submit(_chain_quality, t) for t in TICKERS}

        intraday: dict[str, dict[str, Any]] = {
            src: {t: _range(intraday_f[(src, t)].result()) for t in TICKERS}
            for src in INTRADAY_SOURCES
        }
        # dependent read: the newest snapshot under the latest cboe date prefix
        snap_f = {
            t: pool.submit(
                _latest_snapshot_ts, s3, "cboe_delayed", t,
                intraday_f[("cboe_delayed", t)].result(),
            )
            for t in TICKERS
            if intraday["cboe_delayed"][t]
        }
        for t, fut in snap_f.items():
            intraday["cboe_delayed"][t]["last_snapshot_ts"] = fut.result()

        eod: dict[str, dict[str, Any]] = {
            src: {t: _range(eod_f[(src, t)].result()) for t in TICKERS} for src in EOD_SOURCES
        }
        minute = {t: _range(minute_f[t].result()) for t in TICKERS}
        underlying: dict[str, Any] = {}
        for symbol, _key in targets:
            df = underlying_f[symbol].result()
            if df is None or df.empty:
                underlying[symbol] = None
            else:
                underlying[symbol] = {
                    "rows": int(len(df)),
                    "first": str(df["date"].min().date()),
                    "last": str(df["date"].max().date()),
                }
        ivol_analytics = {
            t: {name: _ivol_year_range(ivol_f[(t, name)].result()) for name in ("ivx", "hv")}
            for t in TICKERS
        }
        ivs_signal_cov = {
            t: _ivs_signals_range(ivs_signals_f[t].result()) for t in TICKERS
        }
        dealer_cov = {
            t: _dealer_positioning_range(dealer_f[t].result()) for t in TICKERS
        }
        chain_quality = {t: chain_quality_f[t].result() for t in TICKERS}
        resolution_mix = {t: resolution_f[t].result() for t in TICKERS}
        dolthub_state = dolthub_f.result()
        quality = quality_f.result()
        collection_priorities = priorities_f.result()
        new_sources = new_sources_f.result()

    verified = sorted(dolthub_state.get("done", []))
    if eod["dolthub"].get("SPY") and verified:
        # the lake's logical view excludes quarantined sessions
        eod["dolthub"]["SPY"] = {
            "sessions": len(verified),
            "first": verified[0],
            "last": verified[-1],
            "quarantined": _quarantined_count(dolthub_state),
        }

    # the EOD record: nightly Yahoo snapshots (source of record per the
    # DECIDED block in DATA-PIPELINE.md)
    record = eod["yahoo"].get("SPY") or {"sessions": 0, "first": None, "last": None}

    # per-ticker chain window across sources (what a backtest can actually use)
    chain_windows: dict[str, Any] = {}
    for ticker in TICKERS:
        firsts = [e["first"] for src in EOD_SOURCES if (e := eod[src].get(ticker))]
        lasts = [e["last"] for src in EOD_SOURCES if (e := eod[src].get(ticker))]
        sessions = sum(e["sessions"] for src in EOD_SOURCES if (e := eod[src].get(ticker)))
        chain_windows[ticker] = (
            {"first": min(firsts), "last": max(lasts), "sessions": sessions} if firsts else None
        )

    return {
        "generated_at": now.isoformat(),
        "record_days": record["sessions"],
        "record_latest": record["last"],
        "chains": chain_windows,
        "eod": eod,
        "minute_bars": minute,
        "intraday": intraday,
        "underlying": underlying,
        "chain_quality": chain_quality,
        "ivol_analytics": ivol_analytics,
        "ivs_signals": ivs_signal_cov,
        "dealer_positioning": dealer_cov,
        "intraday_slice": INTRADAY_SLICE_NOTE,
        "quality": quality,
        # D3d: the weekly demand ranking (build_priorities.py) — what the
        # collectors should want next, shown as the "collection wants" line
        "collection_priorities": collection_priorities,
        "dolthub": {
            "verified_sessions": len(verified),
            "quarantined": _quarantined_count(dolthub_state),
            "archive_gaps": len(dolthub_state.get("missing_in_archive") or []),
            "commit": dolthub_state.get("commit_hash"),
        },
        # F0 (ENGINE-V4): per-session resolution mix + new-source windows.
        # Both are collector-built artifacts (state/resolution_map/*,
        # state/source_coverage.json) — cheap reads, honest None until the
        # ledger has run. Additive keys only; nothing above changes shape.
        "resolution_mix": resolution_mix,
        "new_sources": new_sources,
        "blind_spots": _blind_spots(dolthub_state, minute),
        "sources_status": {
            "yahoo_eod": bool(eod["yahoo"].get("SPY")),
            "dolthub_backfill": bool(eod["dolthub"].get("SPY")),
            "alpaca_minute": bool(minute.get("SPY")),
            "alphavantage": "dormant",  # premium-gated; resumes automatically if entitled
            "intraday_recorder": bool(intraday["cboe_delayed"].get("SPY")),
        },
    }


def _refresh_in_background() -> None:
    """Rebuild the cache off the request path; the lock keeps it to ONE
    rebuild at a time (a stampede of refresh threads is exactly the memory-
    concurrency class of bug this codebase has been burned by)."""
    if not _build_lock.acquire(blocking=False):
        return  # a rebuild is already in flight

    def _run() -> None:
        try:
            payload = build_coverage()
            _CACHE["snap"] = (time.time(), payload)
        except Exception:  # noqa: BLE001 — stale-but-real keeps serving
            logging.getLogger("coverage").exception("background coverage rebuild failed")
        finally:
            _build_lock.release()

    threading.Thread(target=_run, name="coverage-refresh", daemon=True).start()


def coverage_cached() -> dict[str, Any]:
    built_at, payload = _CACHE["snap"]
    age = time.time() - built_at
    if payload is not None and age < CACHE_SECONDS:
        return payload
    if payload is not None and age < CACHE_SECONDS + STALE_SERVE_SECONDS:
        _refresh_in_background()
        return payload
    # nothing servable — build in the foreground, single-flight
    with _build_lock:
        built_at, payload = _CACHE["snap"]
        if payload is not None and time.time() - built_at < CACHE_SECONDS:
            return payload
        payload = build_coverage()
        _CACHE["snap"] = (time.time(), payload)
        return payload


def warm_coverage() -> None:
    """Boot prewarm (main.py runs this in a daemon thread) so the first
    Observatory visit after a deploy answers from memory."""
    try:
        coverage_cached()
    except Exception:  # noqa: BLE001 — no creds / empty lake: routes report it
        pass


def underlying_series(ticker: str, days: int) -> list[dict[str, Any]]:
    """Last N daily closes for the chart-teach composer (real lake data)."""
    s3 = r2.r2_client()
    df = r2.get_parquet(s3, f"underlying/ticker={ticker}/daily.parquet")
    if df is None or df.empty:
        return []
    df = df.sort_values("date").tail(days)
    return [
        {"date": str(row["date"].date()), "close": float(row["close"])}
        for _, row in df.iterrows()
    ]
