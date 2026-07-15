"""EOD chain loader: R2 lake → MarketStore for the engine.

Source precedence per (ticker, trading_date): ivolatility > alphavantage >
cboe_eod > yahoo > dolthub (DATA-PIPELINE §4). iVolatility outranks
everything: it is the only source carrying vendor-computed greeks on every
row, backfilled 20 years deep. cboe_eod is the FORWARD record (owner
decision 2026-07-08): the recorder's last close snapshot per session —
full chain, vendor greeks/IV/OI, quotes ~15 min delayed, a property of the
source disclosed exactly like cboe_minute intraday. It outranks Yahoo
(60-DTE cap, no vendor greeks) and loses to the vendor EOD records.
Dolthub sessions honor the quarantine — only dates in
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

import hashlib
import json
import math
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from typing import Any, cast

import pandas as pd

from app.data import (
    flow_signals,
    gex_signals,
    greeks,
    inhouse_signals,
    ivol_analytics,
    ivs_signals,
    r2,
)
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
# v3: cboe_eod joined the precedence chain — cached winners change.
CACHE_SCHEMA_VERSION = 3


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
    cboe = {
        d: f"options/source=cboe_eod/ticker={ticker}/date={d}/chain.parquet"
        for d in r2.list_chain_dates(s3, "cboe_eod", ticker)
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
    winners.update(cboe)  # cboe_eod beats yahoo: full chain + vendor greeks
    winners.update(av)  # av beats cboe_eod (true close marks, no feed delay)
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


def _manifest_of(winners: dict[str, str]) -> dict[str, Any]:
    """Identity of the winner SET, not just its size: the digest covers the
    full key paths (which embed the source), so a same-date winner
    replacement (e.g. the iVol regroup outranking cboe_eod on existing
    dates) changes the manifest even though {n, last} would not."""
    digest = hashlib.sha1("\n".join(sorted(winners.values())).encode()).hexdigest()
    return {
        "v": CACHE_SCHEMA_VERSION,
        "n": len(winners),
        "last": max(winners) if winners else None,
        "digest": digest[:16],
    }


def _load_combined(
    ticker: str,
    spot_by_date: dict[object, float],
    rates: pd.DataFrame | None,
    winners: dict[str, str] | None = None,
) -> pd.DataFrame:
    s3 = r2.r2_client()
    if winners is None:
        winners = _chain_keys(s3, ticker)
    manifest = _manifest_of(winners)

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


# In-process store cache with a freshness check (self-improvement thesis:
# nightly collections must reach a long-lived container's NEXT run without
# a redeploy). Freshness rules, all review-hardened:
#   * refresh=True is for the ENGINE path only (serialized behind
#     ENGINE_LOCK by its callers) — a rebuild can never overlap a run
#     holding the old store. Request-path callers (estimate, fill audit,
#     warm_store) pass refresh=False: they serve the cached store as-is
#     and only ever pay the ONE cold build (pre-TTL behavior, unchanged).
#   * the check path never binds the old store to a local — on a manifest
#     mismatch the cache entry is dropped and the old store is collectable
#     BEFORE the rebuild allocates (the 2026-07-06 OOM class).
#   * the stored manifest is computed from the PRE-build listing: an
#     object landing mid-build makes the next check mismatch and rebuild
#     again — the cache converges fresh, never pins an incomplete store.
#   * a failed rebuild leaves the cache empty (the old store was freed by
#     design); the failure surfaces to the caller and the next engine call
#     rebuilds cold — loud, never a silent stale serve.
_STORE_CACHE: dict[str, tuple[float, dict[str, Any], MarketStore]] = {}
STORE_TTL_SECONDS = 1800
_REBUILD_LOCK = threading.Lock()


def load_market_store(ticker: str, *, refresh: bool = True) -> MarketStore:
    now = time.time()
    entry = _STORE_CACHE.get(ticker)
    winners: dict[str, str] | None = None
    if entry is not None:
        if not refresh or now - entry[0] <= STORE_TTL_SECONDS:
            return entry[2]
        try:
            winners = _chain_keys(r2.r2_client(), ticker)
        except Exception:
            # can't check (transient R2) — keep serving, retry next TTL
            _STORE_CACHE[ticker] = (now, entry[1], entry[2])
            return entry[2]
        if _manifest_of(winners) == entry[1]:
            _STORE_CACHE[ticker] = (now, entry[1], entry[2])
            return entry[2]
        entry = None  # unpin the old store before the rebuild allocates
    with _REBUILD_LOCK:  # single-flight; re-check under the lock
        entry = _STORE_CACHE.get(ticker)
        if entry is not None and time.time() - entry[0] <= STORE_TTL_SECONDS:
            return entry[2]
        entry = None
        _STORE_CACHE.pop(ticker, None)  # free the old store BEFORE building
        if winners is None:
            winners = _chain_keys(r2.r2_client(), ticker)
        manifest = _manifest_of(winners)  # PRE-build: mid-build writes are
        # absent from it, so the next TTL check mismatches and re-converges
        store = _build_market_store(ticker, winners)
        _STORE_CACHE[ticker] = (time.time(), manifest, store)
        return store


def warm_store(ticker: str = "SPY") -> None:
    """Fire-and-forget prewarm so the FIRST user run doesn't pay the cold
    R2 pull (minutes on a fresh deploy)."""
    try:
        load_market_store(ticker, refresh=False)
    except Exception:  # no creds / empty lake — the run path reports it
        pass


def _finite_series(series: dict[date, float]) -> dict[date, float]:
    """A non-finite row is an HONEST ABSENCE: dropped here, before any
    *_dates list is built. Every evaluability surface — the staleness and
    signal-coverage refusals, the composer's rank unlock dates — reasons
    from those date lists, so a date must never point at a value the
    engine refuses. The loaders drop NaN already, but pandas' NaN-only
    filters (dropna/notna/isna) all keep ±inf, and in-house derived
    arithmetic can emit either."""
    return {d: v for d, v in series.items() if math.isfinite(v)}


def _build_market_store(ticker: str, winners: dict[str, str] | None = None) -> MarketStore:
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

    combined = _load_combined(ticker, cast(dict[object, float], u_close), rates, winners)
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
    # F4 series loads in its OWN guard: a corrupt skew artifact must not
    # zero the IVX/HV a v2 strategy actually asked for (review finding)
    try:
        skew_25d, term_slope = ivs_signals.load_ivs_signals(r2.r2_client(), ticker)
    except Exception:
        skew_25d, term_slope = {}, {}
    try:
        net_gex, net_dex = gex_signals.load_dealer_exposure(r2.r2_client(), ticker)
    except Exception:
        net_gex, net_dex = {}, {}
    try:
        net_premium, pcr, nope_eod, mpd = flow_signals.load_flow_signals(
            r2.r2_client(), ticker)
    except Exception:
        net_premium, pcr, nope_eod, mpd = {}, {}, {}, {}
    # tide is an independent artifact — its failure must not zero the
    # per-ticker flow series (review finding F2/F3 #9)
    try:
        tide = flow_signals.load_market_tide(r2.r2_client())
    except Exception:
        tide = {}

    # Forward-record splices (owner decision 2026-07-08, no vendor
    # subscriptions): the frozen vendor series continue STRICTLY FORWARD
    # via the in-house derivations — unit-compatible series only, each
    # continuation measured on the vendor overlap before it shipped:
    #   ivx_30d  ← in-house 30d ATM IV      (overlap gap 0.09 vol pts)
    #   hv_30d   ← in-house HV              (overlap MAE 0.0002 — exact fit)
    #   skew/term ← in-house chain fit      (overlap gaps 0.21 / 0.02)
    #   pcr/max-pain ← in-house chain calc  (overlap: <1% / identical)
    # net_gex/net_dex are NOT spliced: the in-house convention disagreed
    # with the vendor's sign on the overlap — banked + cross-validated
    # only, never a continuation. net_premium/NOPE/tide have no free
    # substitute and freeze (the tail-staleness guard names that at run
    # time). Splice dates land on the store for run-payload disclosure.
    try:
        inhouse = inhouse_signals.load_chain_signals(r2.r2_client(), ticker)
    except Exception:
        inhouse = {}
    try:
        hv_inhouse = inhouse_signals.load_hv_inhouse(r2.r2_client(), ticker)
    except Exception:
        hv_inhouse = {}
    splices: dict[str, date] = {}

    def _splice(series: dict[date, float], key: str,
                forward: dict[date, float]) -> dict[date, float]:
        merged, seam = inhouse_signals.splice_forward(series, forward)
        if seam is not None:
            splices[key] = seam
        return merged

    ivx_30d = _splice(ivx_30d, "ivx_30d", {
        d: v / 100.0 for d, v in inhouse.get("atm_iv_30d", {}).items()})
    hv_30d = _splice(hv_30d, "hv_30d", hv_inhouse)
    skew_25d = _splice(skew_25d, "skew_25d", inhouse.get("skew_25d", {}))
    term_slope = _splice(term_slope, "term_structure_slope",
                         inhouse.get("term_slope_30_90", {}))
    pcr = _splice(pcr, "put_call_ratio", inhouse.get("put_call_ratio", {}))
    mpd = _splice(mpd, "max_pain_distance_pct",
                  inhouse.get("max_pain_dist_pct", {}))

    # every analytic series through the finite gate BEFORE its date list
    # exists (see _finite_series); vix_dates is rebuilt because the pair
    # was constructed together above. underlying open/close stay raw —
    # sessions drive bar iteration and must not silently lose days.
    vix_close = _finite_series(vix_close)
    vix_dates = sorted(vix_close)
    atm_iv = _finite_series(atm_iv)
    ivx_30d = _finite_series(ivx_30d)
    hv_30d = _finite_series(hv_30d)
    skew_25d = _finite_series(skew_25d)
    term_slope = _finite_series(term_slope)
    net_gex = _finite_series(net_gex)
    net_dex = _finite_series(net_dex)
    net_premium = _finite_series(net_premium)
    pcr = _finite_series(pcr)
    nope_eod = _finite_series(nope_eod)
    mpd = _finite_series(mpd)
    tide = _finite_series(tide)

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
        skew_dates=sorted(skew_25d),
        skew_25d=skew_25d,
        term_dates=sorted(term_slope),
        term_slope=term_slope,
        gex_dates=sorted(net_gex),
        net_gex=net_gex,
        dex_dates=sorted(net_dex),
        net_dex=net_dex,
        flow_dates=sorted(net_premium),
        net_premium=net_premium,
        pcr_dates=sorted(pcr),
        put_call_ratio=pcr,
        nope_dates=sorted(nope_eod),
        nope_eod=nope_eod,
        mpd_dates=sorted(mpd),
        max_pain_dist=mpd,
        tide_dates=sorted(tide),
        market_tide=tide,
        splices=splices,
    )
