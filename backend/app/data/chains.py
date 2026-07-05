"""EOD chain loader: R2 lake → MarketStore for the engine.

Source precedence per (ticker, trading_date): ivolatility > alphavantage >
yahoo > dolthub (DATA-PIPELINE §4). iVolatility outranks everything: it is
the only source carrying vendor-computed greeks on every row, backfilled
20 years deep. Dolthub sessions honor the quarantine — only dates in
state/dolthub_backfill.json's `done` list are loaded (the lake's logical
view; flag-and-exclude, DOLTHUB-EVAL addendum).

The lake stores one object per session, so a full SPY window is ~1,100
small parquet files; they are fetched concurrently and the combined frame
is cached on local disk, keyed by a listing manifest, so repeat runs are
sub-second. Deviation from TECH-SPEC §4 (DuckDB): thread-parallel object
reads beat httpfs globbing on this one-object-per-date layout; logged in
BUILD-LOG.
"""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from typing import Any, cast

import pandas as pd

from app.data import greeks, ivol_analytics, r2
from app.engine.market import MarketStore
from app.engine.types import ContractKey, Quote

CACHE_DIR = Path(__file__).resolve().parents[2] / ".cache"
FETCH_WORKERS = 24
# Full canonical width (D1a): the engine stops discarding greeks, liquidity
# and provenance. `source` rides along for the Observatory's per-source
# field-completeness view (D1d). Spot stays per-day in MarketStore — the
# lake's documented join — but the column is read here because computing
# missing greeks needs the row spot when a source carries one.
COLUMNS = [
    "trading_date", "expiration", "right", "strike", "bid", "ask", "last",
    "volume", "open_interest", "iv", "delta", "gamma", "theta", "vega", "rho",
    "greeks_source", "spot", "source",
]
NUMERIC_COLUMNS = [
    "strike", "bid", "ask", "last", "volume", "open_interest", "iv",
    "delta", "gamma", "theta", "vega", "rho", "spot",
]
# Bump when COLUMNS or the computed-greeks pass changes shape/semantics:
# a mismatched manifest rebuilds the on-disk cache automatically.
CACHE_SCHEMA_VERSION = 2


def _latest_yahoo_keys(s3: Any, ticker: str) -> dict[str, str]:
    """date -> newest snap key (multiple snapshots may exist per date)."""
    keys = r2.list_keys(s3, f"options/source=yahoo/ticker={ticker}/")
    per_date: dict[str, str] = {}
    for key in keys:
        m = re.search(r"date=(\d{4}-\d{2}-\d{2})", key)
        if m:
            d = m.group(1)
            if d not in per_date or key > per_date[d]:
                per_date[d] = key
    return per_date


def _chain_keys(s3: Any, ticker: str) -> dict[str, str]:
    """Winning object key per trading date, after precedence + quarantine."""
    ivol = {
        d: f"options/source=ivolatility/ticker={ticker}/date={d}/chain.parquet"
        for d in r2.list_chain_dates(s3, "ivolatility", ticker)
    }
    av = {
        d: f"options/source=alphavantage/ticker={ticker}/date={d}/chain.parquet"
        for d in r2.list_chain_dates(s3, "alphavantage", ticker)
    }
    yahoo = _latest_yahoo_keys(s3, ticker)
    dolthub_dates = set(r2.list_chain_dates(s3, "dolthub", ticker))
    verified = set(r2.get_json(s3, "state/dolthub_backfill.json", {}).get("done", []))
    dolthub = {
        d: f"options/source=dolthub/ticker={ticker}/date={d}/chain.parquet"
        for d in dolthub_dates
        if d in verified
    }
    winners: dict[str, str] = {}
    winners.update(dolthub)
    winners.update(yahoo)  # yahoo beats dolthub
    winners.update(av)  # av beats yahoo
    winners.update(ivol)  # ivolatility beats all — vendor greeks on every row
    return winners


def _fetch_frames(s3: Any, keys: list[str]) -> list[pd.DataFrame]:
    def one(key: str) -> pd.DataFrame | None:
        df = r2.get_parquet(s3, key)
        if df is None or df.empty:
            return None
        cols = [c for c in COLUMNS if c in df.columns]
        out = df[cols].copy()
        for col in NUMERIC_COLUMNS:
            if col in out.columns:
                out[col] = pd.to_numeric(out[col], errors="coerce")
        return out

    with ThreadPoolExecutor(max_workers=FETCH_WORKERS) as pool:
        frames = list(pool.map(one, keys))
    return [f for f in frames if f is not None]


def _load_combined(
    ticker: str,
    spot_by_date: dict[object, float],
    rates: pd.DataFrame | None,
) -> pd.DataFrame:
    s3 = r2.r2_client()
    winners = _chain_keys(s3, ticker)
    manifest = {
        "v": CACHE_SCHEMA_VERSION,
        "n": len(winners),
        "last": max(winners) if winners else None,
    }

    CACHE_DIR.mkdir(exist_ok=True)
    cache_file = CACHE_DIR / f"chains_{ticker}.parquet"
    meta_file = CACHE_DIR / f"chains_{ticker}.json"
    if cache_file.exists() and meta_file.exists():
        try:
            if json.loads(meta_file.read_text()) == manifest:
                return pd.read_parquet(cache_file)
        except Exception:
            pass

    if not winners:
        return pd.DataFrame(columns=COLUMNS)
    frames = _fetch_frames(s3, list(winners.values()))
    combined = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=COLUMNS)
    # fill greeks the source didn't carry (Yahoo rows) BEFORE caching, so the
    # computed values are part of the cached artifact and its version key
    combined = greeks.fill_missing_greeks(combined, ticker, spot_by_date, rates)
    try:
        combined.to_parquet(cache_file, index=False)
        meta_file.write_text(json.dumps(manifest))
    except Exception:
        pass  # cache is an optimization, never a requirement
    return combined


def _underlying_frames(
    ticker: str,
) -> tuple[pd.DataFrame | None, pd.DataFrame | None, pd.DataFrame | None]:
    s3 = r2.r2_client()
    daily = r2.get_parquet(s3, f"underlying/ticker={ticker}/daily.parquet")
    vix = r2.get_parquet(s3, "reference/vix_daily.parquet")
    rates = r2.get_parquet(s3, "reference/rates_dgs3mo.parquet")
    return daily, vix, rates


# in-process store cache: the parquet parse is seconds of work a warm
# container never needs to repeat (thread-safe enough for one writer)
_STORE_CACHE: dict[str, MarketStore] = {}


def load_market_store(ticker: str) -> MarketStore:
    cached = _STORE_CACHE.get(ticker)
    if cached is not None:
        return cached
    store = _build_market_store(ticker)
    _STORE_CACHE[ticker] = store
    return store


def warm_store(ticker: str = "SPY") -> None:
    """Fire-and-forget prewarm so the FIRST user run doesn't pay the cold
    R2 pull (minutes on a fresh deploy)."""
    try:
        load_market_store(ticker)
    except Exception:  # no creds / empty lake — the run path reports it
        pass


def _build_market_store(ticker: str) -> MarketStore:
    daily, vix, rates = _underlying_frames(ticker)
    if daily is None or daily.empty:
        raise RuntimeError(f"no underlying dailies in the lake for {ticker}")

    daily = daily.sort_values("date")
    sessions = [pd.Timestamp(d).date() for d in daily["date"]]
    u_open = {d: float(o) for d, o in zip(sessions, daily["open"], strict=False)}
    u_close = {d: float(c) for d, c in zip(sessions, daily["close"], strict=False)}

    vix_dates: list[date] = []
    vix_close: dict[date, float] = {}
    if vix is not None and not vix.empty:
        vix = vix.sort_values("date")
        vix_dates = [pd.Timestamp(d).date() for d in vix["date"]]
        vix_close = {d: float(c) for d, c in zip(vix_dates, vix["close"], strict=False)}

    combined = _load_combined(ticker, cast(dict[object, float], u_close), rates)
    chains: dict[date, dict[ContractKey, Quote]] = {}
    atm_iv: dict[date, float] = {}
    if not combined.empty:
        combined = combined.dropna(subset=["trading_date", "expiration", "strike", "right"])
        combined["trading_date"] = pd.to_datetime(combined["trading_date"]).dt.date
        combined["expiration"] = pd.to_datetime(combined["expiration"]).dt.date

        def num(value: Any) -> float | None:
            return None if value is None or pd.isna(value) else float(value)

        def num_i(value: Any) -> int | None:
            return None if value is None or pd.isna(value) else int(value)

        def text(value: Any) -> str | None:
            return None if value is None or pd.isna(value) else str(value)

        for day, group in combined.groupby("trading_date"):
            per: dict[ContractKey, Quote] = {}
            for rec in group.to_dict("records"):
                key = ContractKey(
                    expiration=cast(date, rec["expiration"]),
                    right=str(rec["right"]),
                    strike=float(rec["strike"]),
                )
                per[key] = Quote(
                    bid=num(rec.get("bid")),
                    ask=num(rec.get("ask")),
                    delta=num(rec.get("delta")),
                    iv=num(rec.get("iv")),
                    gamma=num(rec.get("gamma")),
                    theta=num(rec.get("theta")),
                    vega=num(rec.get("vega")),
                    rho=num(rec.get("rho")),
                    volume=num_i(rec.get("volume")),
                    open_interest=num_i(rec.get("open_interest")),
                    last=num(rec.get("last")),
                    greeks_source=text(rec.get("greeks_source")),
                )
            d = cast(date, day)
            chains[d] = per
            spot = u_close.get(d)
            if spot is not None:
                with_iv = [(abs(k.strike - spot), q.iv) for k, q in per.items() if q.iv is not None]
                if with_iv:
                    atm_iv[d] = float(min(with_iv)[1] or 0.0)

    # vendor IVX/HV series (D1c) — honest absences when not banked
    try:
        s3 = r2.r2_client()
        ivx_30d = ivol_analytics.load_ivx_30d(s3, ticker)
        hv_30d = ivol_analytics.load_hv_30d(s3, ticker)
    except Exception:
        ivx_30d, hv_30d = {}, {}

    return MarketStore(
        ticker=ticker,
        sessions=sessions,
        underlying_open=u_open,
        underlying_close=u_close,
        chains=chains,
        chain_dates=sorted(chains),
        vix_dates=vix_dates,
        vix_close=vix_close,
        atm_iv=atm_iv,
        ivx_dates=sorted(ivx_30d),
        ivx_30d=ivx_30d,
        hv_dates=sorted(hv_30d),
        hv_30d=hv_30d,
    )
