"""Lake coverage — the real numbers behind /api/data/coverage.

Successor to collector/coverage.py (the M1 stand-in script), returning JSON
for the Data Observatory and the composer's coverage chips. Everything here
is computed from the lake; nothing is asserted that an object listing can't
prove (guardrail #6).
"""

from __future__ import annotations

import re
import time
from datetime import UTC, datetime
from typing import Any

import pandas as pd

from app.data import chains, ivs_signals, r2, resolution

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

_CACHE: dict[str, Any] = {"at": 0.0, "payload": None}
CACHE_SECONDS = 300


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


def _ivol_analytics_ranges(s3: Any) -> dict[str, Any]:
    """IVX / HV year-file coverage per ticker (cheap listings)."""
    out: dict[str, Any] = {}
    for ticker in TICKERS:
        entry: dict[str, Any] = {}
        for name in ("ivx", "hv"):
            years = sorted(
                {
                    m.group(1)
                    for k in r2.list_keys(s3, f"reference/ivol/{name}/ticker={ticker}/")
                    if (m := re.search(r"year=(\d{4})", k))
                }
            )
            entry[name] = (
                {"years": len(years), "first": years[0], "last": years[-1]} if years else None
            )
        out[ticker] = entry
    return out


def _ivs_signals_ranges(s3: Any) -> dict[str, Any]:
    """Window of the derived vol-surface signal artifact (F4) — guardrail
    #6: any surface offering skew/term filters shows the window they were
    derived on, per signal (a session can carry one and not the other)."""
    out: dict[str, Any] = {}
    for ticker in TICKERS:
        df = r2.get_parquet(s3, ivs_signals.SIGNALS_KEY.format(ticker=ticker))
        if df is None or df.empty or "date" not in df.columns:
            out[ticker] = None
            continue
        dates = df["date"].astype(str)
        out[ticker] = {
            "sessions": int(len(df)),
            "first": str(dates.min()),
            "last": str(dates.max()),
            "skew_sessions": int(df["skew_25d"].notna().sum())
            if "skew_25d" in df.columns else 0,
            "term_sessions": int(df["term_slope_30_90"].notna().sum())
            if "term_slope_30_90" in df.columns else 0,
        }
    return out


def build_coverage() -> dict[str, Any]:
    s3 = r2.r2_client()
    now = datetime.now(UTC)

    eod: dict[str, dict[str, Any]] = {}
    for source in EOD_SOURCES:
        eod[source] = {}
        for ticker in TICKERS:
            eod[source][ticker] = _range(r2.list_chain_dates(s3, source, ticker))

    dolthub_state = r2.get_json(s3, "state/dolthub_backfill.json", {})
    verified = sorted(dolthub_state.get("done", []))
    if eod["dolthub"].get("SPY") and verified:
        # the lake's logical view excludes quarantined sessions
        eod["dolthub"]["SPY"] = {
            "sessions": len(verified),
            "first": verified[0],
            "last": verified[-1],
            "quarantined": _quarantined_count(dolthub_state),
        }

    minute: dict[str, Any] = {}
    for ticker in TICKERS:
        minute[ticker] = _range(
            r2.list_date_prefixes(s3, f"options_minute/source=alpaca/ticker={ticker}/")
        )

    intraday: dict[str, dict[str, Any]] = {}
    for source in INTRADAY_SOURCES:
        intraday[source] = {}
        for ticker in TICKERS:
            dates = r2.list_date_prefixes(
                s3, f"options_intraday/source={source}/ticker={ticker}/"
            )
            entry = _range(dates)
            if entry and source == "cboe_delayed":
                entry["last_snapshot_ts"] = _latest_snapshot_ts(s3, source, ticker, dates)
            intraday[source][ticker] = entry

    underlying: dict[str, Any] = {}
    targets = [(t, f"underlying/ticker={t}/daily.parquet") for t in TICKERS]
    targets.append(("VIX", "reference/vix_daily.parquet"))
    for symbol, key in targets:
        df = r2.get_parquet(s3, key)
        if df is None or df.empty:
            underlying[symbol] = None
        else:
            underlying[symbol] = {
                "rows": int(len(df)),
                "first": str(df["date"].min().date()),
                "last": str(df["date"].max().date()),
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
        "chain_quality": {t: _chain_quality(t) for t in TICKERS},
        "ivol_analytics": _ivol_analytics_ranges(s3),
        "ivs_signals": _ivs_signals_ranges(s3),
        "intraday_slice": INTRADAY_SLICE_NOTE,
        "quality": r2.get_json(s3, "state/quality_flags.json", {}),
        # D3d: the weekly demand ranking (build_priorities.py) — what the
        # collectors should want next, shown as the "collection wants" line
        "collection_priorities": r2.get_json(s3, "state/collection_priorities.json", None),
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
        "resolution_mix": {t: resolution.summary(s3, t) for t in TICKERS},
        "new_sources": r2.get_json(s3, "state/source_coverage.json", None),
        "blind_spots": _blind_spots(dolthub_state, minute),
        "sources_status": {
            "yahoo_eod": bool(eod["yahoo"].get("SPY")),
            "dolthub_backfill": bool(eod["dolthub"].get("SPY")),
            "alpaca_minute": bool(minute.get("SPY")),
            "alphavantage": "dormant",  # premium-gated; resumes automatically if entitled
            "intraday_recorder": bool(intraday["cboe_delayed"].get("SPY")),
        },
    }


def coverage_cached() -> dict[str, Any]:
    if _CACHE["payload"] is not None and time.time() - _CACHE["at"] < CACHE_SECONDS:
        return _CACHE["payload"]  # type: ignore[no-any-return]
    payload = build_coverage()
    _CACHE.update(at=time.time(), payload=payload)
    return payload


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
