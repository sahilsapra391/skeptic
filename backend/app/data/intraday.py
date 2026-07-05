"""Intraday (5-minute) data layer — D2a. Lake → SessionSlice for the engine.

Sources, ranked per owner amendment 1 (real NBBO outranks delayed data):
  1. ivol_5min   options_intraday/source=ivolatility/ticker=T/date=D/bars.parquet
                 true 5-min NBBO + vendor greeks on the short-DTE ATM slice
                 (0–2 trading-DTE, ATM±$8), 2018-01 → now and growing.
  2. cboe_minute options_intraday/source=cboe_delayed/.../snap_*.parquet
                 ~15-min DELAYED minute snapshots; forward coverage only,
                 used for sessions ivol does not carry. A session is served
                 by ONE source — provenance is per-session, never blended.
No synthetic quotes: a bar without a quote is a gap (owner decision —
eod_interpolated is excluded from D2).

Timestamps are ET wall-clock, tz-naive, exactly as the lake stores them
(bars 09:30 → 16:15, options close-lag included).

Memory model: a 5-min run touches ~2,120 session objects — far too much for
the all-in-RAM pattern chains.py uses. Sessions load lazily behind a bounded
LRU, backed by an on-disk per-session cache with a versioned manifest
(chains.py's pattern, per session): the sensitivity sweep's ~20 re-runs pay
the network once.
"""

from __future__ import annotations

import json
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from app.data import r2
from app.engine.market import SessionSlice
from app.engine.types import ContractKey, Quote

# The capture slice (mirrors collector/backfill_ivol_intraday.py). Until D2d
# widens sources, this IS the intraday universe — the engine's pre-run
# coverage check (D2b, owner amendment 4) refuses specs that need more.
SLICE_MAX_TRADING_DTE = 2
SLICE_ATM_BAND = 8.0  # dollars around spot
# CBOE snapshots carry the full chain; filtering to the slice keeps sessions
# comparable across sources. Calendar-DTE ≤ 4 approximates 0–2 trading-DTE
# (covers a weekend); documented approximation for the tiny forward corpus.
CBOE_SLICE_MAX_CALENDAR_DTE = 4

CACHE_SCHEMA_VERSION = 1
CACHE_DIR = Path(__file__).resolve().parents[2] / ".cache" / "intraday"
LRU_SESSIONS = 32
CBOE_FETCH_WORKERS = 16

IVOL_OPT = "options_intraday/source=ivolatility"
IVOL_UND = "underlying_intraday/source=ivolatility"
CBOE_OPT = "options_intraday/source=cboe_delayed"

SLICE_COLUMNS = [
    "bar_ts", "expiration", "right", "strike", "bid", "ask", "last",
    "volume", "open_interest", "iv", "delta", "gamma", "theta", "vega", "rho",
]


def _num(v: Any) -> float | None:
    return None if v is None or pd.isna(v) else float(v)


def _num_i(v: Any) -> int | None:
    return None if v is None or pd.isna(v) else int(v)


# ------------------------------------------------------------ transformations

def _ivol_frames(
    s3: Any, ticker: str, d: str
) -> tuple[pd.DataFrame, pd.DataFrame | None] | None:
    opt = r2.get_parquet(s3, f"{IVOL_OPT}/ticker={ticker}/date={d}/bars.parquet")
    if opt is None or opt.empty:
        return None
    und = r2.get_parquet(s3, f"{IVOL_UND}/ticker={ticker}/date={d}/bars.parquet")
    out = pd.DataFrame({
        "bar_ts": pd.to_datetime(opt["minute_ts"]),
        "expiration": opt["expiration"],
        "right": opt["right"],
        "strike": pd.to_numeric(opt["strike"], errors="coerce"),
        "bid": pd.to_numeric(opt["bid"], errors="coerce"),
        "ask": pd.to_numeric(opt["ask"], errors="coerce"),
        "last": None,
        "volume": pd.to_numeric(opt["volume"], errors="coerce"),
        "open_interest": None,
        "iv": pd.to_numeric(opt["iv"], errors="coerce"),
        "delta": pd.to_numeric(opt["delta"], errors="coerce"),
        "gamma": pd.to_numeric(opt["gamma"], errors="coerce"),
        "theta": pd.to_numeric(opt["theta"], errors="coerce"),
        "vega": pd.to_numeric(opt["vega"], errors="coerce"),
        "rho": pd.to_numeric(opt["rho"], errors="coerce"),
    })[SLICE_COLUMNS]
    und_out: pd.DataFrame | None = None
    if und is not None and not und.empty and "last" in und.columns:
        und_out = pd.DataFrame({
            "bar_ts": pd.to_datetime(und["minute_ts"]),
            "last": pd.to_numeric(und["last"], errors="coerce"),
        }).dropna()
    return out, und_out


def _cboe_frames(
    s3: Any, ticker: str, d: str
) -> tuple[pd.DataFrame, pd.DataFrame | None] | None:
    """Minute snapshots → 5-min bars: each bar uses the FIRST snapshot at or
    after the bar's start (the state at bar open — never intra-bar future
    information). Full chain filtered to the slice; the ~15-min feed delay is
    a property of the SOURCE, disclosed via fill_source, never shifted."""
    keys = sorted(r2.list_keys(s3, f"{CBOE_OPT}/ticker={ticker}/date={d}/"))
    if not keys:
        return None

    # capture ts from the key (snap_YYYYMMDDTHHMMZ, UTC) → ET wall-clock
    def _key_ts(key: str) -> datetime | None:
        stem = key.rsplit("snap_", 1)[-1].removesuffix(".parquet")
        try:
            ts = pd.Timestamp(datetime.strptime(stem, "%Y%m%dT%H%MZ"), tz="UTC")
        except ValueError:
            return None
        return ts.tz_convert("America/New_York").tz_localize(None).to_pydatetime()

    stamped = [(k, t) for k in keys if (t := _key_ts(k)) is not None]
    # first snapshot per 5-min bar
    per_bar: dict[datetime, str] = {}
    for key, ts in stamped:
        bar = ts.replace(minute=ts.minute - ts.minute % 5, second=0, microsecond=0)
        if bar not in per_bar:
            per_bar[bar] = key

    def one(item: tuple[datetime, str]) -> tuple[datetime, pd.DataFrame] | None:
        bar, key = item
        df = r2.get_parquet(s3, key)
        if df is None or df.empty:
            return None
        return bar, df

    with ThreadPoolExecutor(max_workers=CBOE_FETCH_WORKERS) as pool:
        fetched = [f for f in pool.map(one, sorted(per_bar.items())) if f is not None]
    if not fetched:
        return None

    frames: list[pd.DataFrame] = []
    und_rows: list[dict[str, Any]] = []
    for bar, df in fetched:
        spot = _num(df["spot"].dropna().iloc[0]) if df["spot"].notna().any() else None
        dte = pd.to_numeric(df["dte"], errors="coerce")
        strike = pd.to_numeric(df["strike"], errors="coerce")
        mask = dte <= CBOE_SLICE_MAX_CALENDAR_DTE
        if spot is not None:
            mask &= (strike - spot).abs() <= SLICE_ATM_BAND
            und_rows.append({"bar_ts": bar, "last": spot})
        sub = df[mask]
        if sub.empty:
            continue
        frames.append(pd.DataFrame({
            "bar_ts": bar,
            "expiration": sub["expiration"],
            "right": sub["right"],
            "strike": pd.to_numeric(sub["strike"], errors="coerce"),
            "bid": pd.to_numeric(sub["bid"], errors="coerce"),
            "ask": pd.to_numeric(sub["ask"], errors="coerce"),
            "last": pd.to_numeric(sub["last"], errors="coerce"),
            "volume": pd.to_numeric(sub["volume"], errors="coerce"),
            "open_interest": pd.to_numeric(sub["open_interest"], errors="coerce"),
            "iv": pd.to_numeric(sub["iv"], errors="coerce"),
            "delta": pd.to_numeric(sub["delta"], errors="coerce"),
            "gamma": pd.to_numeric(sub["gamma"], errors="coerce"),
            "theta": pd.to_numeric(sub["theta"], errors="coerce"),
            "vega": pd.to_numeric(sub["vega"], errors="coerce"),
            "rho": pd.to_numeric(sub["rho"], errors="coerce"),
        })[SLICE_COLUMNS])
    if not frames:
        return None
    und = pd.DataFrame(und_rows) if und_rows else None
    return pd.concat(frames, ignore_index=True), und


def _build_slice(
    session: date, opt: pd.DataFrame, und: pd.DataFrame | None, source: str
) -> SessionSlice:
    opt = opt.dropna(subset=["bar_ts", "expiration", "right", "strike"])
    quotes: dict[datetime, dict[ContractKey, Quote]] = {}
    exp = pd.to_datetime(opt["expiration"]).dt.date
    groups = opt.groupby(pd.to_datetime(opt["bar_ts"])).groups
    for bar_key, group_idx in groups.items():
        bar = pd.Timestamp(str(bar_key))  # Hashable → concrete timestamp for mypy
        group = opt.loc[group_idx]
        per: dict[ContractKey, Quote] = {}
        for rec, e in zip(group.to_dict("records"), exp.loc[group_idx], strict=True):
            key = ContractKey(expiration=e, right=str(rec["right"]),
                              strike=float(rec["strike"]))
            per[key] = Quote(
                bid=_num(rec.get("bid")),
                ask=_num(rec.get("ask")),
                delta=_num(rec.get("delta")),
                iv=_num(rec.get("iv")),
                gamma=_num(rec.get("gamma")),
                theta=_num(rec.get("theta")),
                vega=_num(rec.get("vega")),
                rho=_num(rec.get("rho")),
                volume=_num_i(rec.get("volume")),
                open_interest=_num_i(rec.get("open_interest")),
                last=_num(rec.get("last")),
                greeks_source="vendor",
            )
        quotes[bar.to_pydatetime()] = per

    underlying: dict[datetime, float] = {}
    if und is not None and not und.empty:
        for rec in und.dropna(subset=["bar_ts", "last"]).to_dict("records"):
            underlying[pd.Timestamp(rec["bar_ts"]).to_pydatetime()] = float(rec["last"])

    bars = sorted(set(quotes) | set(underlying))
    return SessionSlice(
        session=session, bars=bars, quotes=quotes,
        underlying=underlying, quote_source=source,
    )


# ------------------------------------------------------------------- caching

def _cache_paths(ticker: str, d: str) -> tuple[Path, Path, Path]:
    base = CACHE_DIR / ticker
    return (base / f"{d}_options.parquet", base / f"{d}_und.parquet",
            base / f"{d}_meta.json")


def _read_cached(ticker: str, d: str) -> tuple[pd.DataFrame, pd.DataFrame | None, str] | None:
    opt_p, und_p, meta_p = _cache_paths(ticker, d)
    if not (opt_p.exists() and meta_p.exists()):
        return None
    try:
        meta = json.loads(meta_p.read_text())
        if meta.get("v") != CACHE_SCHEMA_VERSION:
            return None
        opt = pd.read_parquet(opt_p)
        und = pd.read_parquet(und_p) if und_p.exists() else None
        return opt, und, str(meta["source"])
    except Exception:
        return None


def _write_cache(ticker: str, d: str, opt: pd.DataFrame,
                 und: pd.DataFrame | None, source: str) -> None:
    opt_p, und_p, meta_p = _cache_paths(ticker, d)
    try:
        opt_p.parent.mkdir(parents=True, exist_ok=True)
        opt.to_parquet(opt_p, index=False)
        if und is not None and not und.empty:
            und.to_parquet(und_p, index=False)
        meta_p.write_text(json.dumps({"v": CACHE_SCHEMA_VERSION, "source": source}))
    except Exception:
        pass  # cache is an optimization, never a requirement


# --------------------------------------------------------------------- store

class IntradayStore:
    """Lazy, LRU-bounded access to a ticker's 5-minute sessions.
    Satisfies app.engine.market.IntradayProvider."""

    def __init__(self, ticker: str) -> None:
        self.ticker = ticker
        self._sessions: dict[date, str] | None = None  # session -> source
        self._lru: OrderedDict[date, SessionSlice | None] = OrderedDict()

    @property
    def slice_max_trading_dte(self) -> int:
        return SLICE_MAX_TRADING_DTE

    def _session_sources(self) -> dict[date, str]:
        if self._sessions is None:
            s3 = r2.r2_client()
            cboe = r2.list_date_prefixes(s3, f"{CBOE_OPT}/ticker={self.ticker}/")
            ivol = r2.list_date_prefixes(s3, f"{IVOL_OPT}/ticker={self.ticker}/")
            src: dict[date, str] = {date.fromisoformat(d): "cboe_minute" for d in cboe}
            # ivol overwrites: real NBBO outranks delayed data (amendment 1)
            src.update({date.fromisoformat(d): "ivol_5min" for d in ivol})
            self._sessions = src
        return self._sessions

    def sessions(self) -> list[date]:
        return sorted(self._session_sources())

    def source_for(self, session: date) -> str | None:
        return self._session_sources().get(session)

    def _ensure_cached(
        self, session: date
    ) -> tuple[pd.DataFrame, pd.DataFrame | None, str] | None:
        """Frames for a session, from the disk cache or the lake (writing
        the cache). Thread-safe for concurrent prefetch."""
        source = self.source_for(session)
        if source is None:
            return None
        d = session.isoformat()
        cached = _read_cached(self.ticker, d)
        if cached is not None and cached[2] == source:
            return cached
        s3 = r2.r2_client()
        frames = (_ivol_frames(s3, self.ticker, d) if source == "ivol_5min"
                  else _cboe_frames(s3, self.ticker, d))
        if frames is None:
            return None
        opt, und = frames
        _write_cache(self.ticker, d, opt, und, source)
        return opt, und, source

    def prefetch(self, sessions: list[date], workers: int = 16) -> int:
        """Warm the on-disk cache concurrently (a full-history 5-min run
        touches thousands of session objects; fetching them inline is the
        slow path). Returns how many sessions ended up cached."""
        with ThreadPoolExecutor(max_workers=workers) as pool:
            done = list(pool.map(self._ensure_cached, sessions))
        return sum(1 for f in done if f is not None)

    def slice_for(self, session: date) -> SessionSlice | None:
        if session in self._lru:
            self._lru.move_to_end(session)
            return self._lru[session]
        frames = self._ensure_cached(session)
        if frames is None:
            if self.source_for(session) is not None:
                self._remember(session, None)
            return None
        opt, und, source = frames
        slc = _build_slice(session, opt, und, source)
        self._remember(session, slc)
        return slc

    def _remember(self, session: date, slc: SessionSlice | None) -> None:
        self._lru[session] = slc
        while len(self._lru) > LRU_SESSIONS:
            self._lru.popitem(last=False)


_STORE_CACHE: dict[str, IntradayStore] = {}


def load_intraday_store(ticker: str) -> IntradayStore:
    store = _STORE_CACHE.get(ticker)
    if store is None:
        store = IntradayStore(ticker)
        _STORE_CACHE[ticker] = store
    return store
