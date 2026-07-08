"""Intraday (5-minute) data layer — D2a. Lake → SessionSlice for the engine.

Sources, ranked per owner amendment 1 (real NBBO outranks delayed data):
  1. ivol_5min   options_intraday/source=ivolatility/ticker=T/date=D/bars.parquet
                 true 5-min NBBO + vendor greeks on the short-DTE ATM slice
                 (0–2 trading-DTE, ATM±$8), 2018-01 → now and growing.
  2. cboe_minute options_intraday/source=cboe_delayed/.../snap_*.parquet
                 ~15-min DELAYED minute snapshots; forward coverage only,
                 used for sessions ivol does not carry. A session is served
                 by ONE source — provenance is per-session, never blended.
  3. alpaca_modeled options_minute/source=alpaca/... (D2d): trade-print
                 OHLCV with NO quotes — the bid/ask is MODELED (mid = the
                 last trade print within the stale-print window, half-spread
                 = the ticker's own median EOD spread fraction). Flagged per
                 fill, ALWAYS filled at full adverse slippage, and only used
                 where no quote record exists (QQQ/IWM 2024-02→2026-06).
No synthetic quotes from EOD chains: a bar without a quote or a recent
trade print is a gap (owner decision — eod_interpolated is excluded).

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
import logging
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from app.data import r2
from app.data.bars import per_bar_volume
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

CACHE_SCHEMA_VERSION = 5  # v5 (F5): + displayed NBBO sizes (bid_size/ask_size)
# v4: vendor-shape hardening — duplicate stamps keep
#     the last-written row, head-truncated payloads no longer seed bar 0 with
#     the cumulative-so-far (v3: NaN cum-volume no longer injects the session
#     cumulative as one bar's volume; v2: per-bar volume, D2c)
# Spread stats version independently: its schema is untouched by session-cache
# bumps, and a needless recompute is a full serial Yahoo-EOD rescan in the
# request path (worse: a transient R2 outage during it persists None and
# silently drops every alpaca_modeled session from coverage).
SPREAD_STATS_VERSION = 2
CACHE_DIR = Path(__file__).resolve().parents[2] / ".cache" / "intraday"
LRU_SESSIONS = 32
CBOE_FETCH_WORKERS = 16

IVOL_OPT = "options_intraday/source=ivolatility"
IVOL_UND = "underlying_intraday/source=ivolatility"
CBOE_OPT = "options_intraday/source=cboe_delayed"
ALPACA_OPT = "options_minute/source=alpaca"
ALPACA_UND = "underlying_minute"
# FX.1: 1-min underlying NBBO bars (iVolatility, 2011+) — the MINUTE bar
# grid for resolution="finest" sessions. Options quotes stay the 5-min NBBO
# stamps; minute bars between stamps carry no chain and fill nothing.
BARS_1M = "bars_1m/source=ivolatility"
LRU_MINUTE_SESSIONS = 16  # separate, bounded (post-OOM rule)
# a trade print older than this is NOT a usable price for the bar — stale
# prints on sparse contracts would otherwise masquerade as quotes
ALPACA_STALE_PRINT_MIN = 5
ALPACA_SLICE_MAX_CALENDAR_DTE = 4  # same weekend-covering approximation as CBOE
MIN_HALF_SPREAD = 0.01

SLICE_COLUMNS = [
    "bar_ts", "expiration", "right", "strike", "bid", "ask", "last",
    "volume", "open_interest", "iv", "delta", "gamma", "theta", "vega", "rho",
    "bid_size", "ask_size",
]


def _size_i(v: Any) -> int | None:
    """Displayed size: a negative vendor value is garbage, not depth — it
    would count as depth-known with an automatic exceedance (v4's
    vendor-shape-hardening rule: clamp to unknown, never trust)."""
    n = _num_i(v)
    return None if n is not None and n < 0 else n


def _num(v: Any) -> float | None:
    return None if v is None or pd.isna(v) else float(v)


def _num_i(v: Any) -> int | None:
    return None if v is None or pd.isna(v) else int(v)


SESSION_OPEN = time(9, 30)  # ET wall-clock; bars are stamped at bar START

# counts only, never chain-data rows (repo rule) — the point is telling
# "one bad row dropped" apart from "the whole session evaporated"
log = logging.getLogger("skeptic.intraday")


# ------------------------------------------------------------ transformations

def _ivol_und_rows(und: pd.DataFrame | None, ctx: str) -> pd.DataFrame | None:
    """Vendor underlying rows → clean, time-ordered bars (`bar_ts` parsed).
    Defensive against vendor shape defects, each one row's problem and never
    the session's: a malformed stamp is coerced + dropped (the volume column
    already gets that tolerance), and a duplicated stamp keeps only the
    last-written row — a duplicate's cumulative diff is 0 and its dict insert
    would erase the bar's real volume in _build_slice. Row losses are logged
    (counts only) so systemic evaporation is never silent."""
    if und is None or und.empty or "last" not in und.columns:
        return None
    parsed = und.assign(bar_ts=pd.to_datetime(und["minute_ts"], errors="coerce"))
    und = parsed.dropna(subset=["bar_ts"])
    if len(und) < len(parsed):
        log.warning("ivol underlying %s: %d/%d rows dropped (unparseable stamp)",
                    ctx, len(parsed) - len(und), len(parsed))
    # the cumulative diff downstream is order-sensitive and the lake
    # preserves vendor row order unguarded — sort by stamp, never trust it
    und = und.sort_values("bar_ts", kind="stable")
    und = und.drop_duplicates(subset=["bar_ts"], keep="last")
    return None if und.empty else und


def _ivol_frames(
    s3: Any, ticker: str, d: str
) -> tuple[pd.DataFrame, pd.DataFrame | None] | None:
    opt = r2.get_parquet(s3, f"{IVOL_OPT}/ticker={ticker}/date={d}/bars.parquet")
    if opt is None or opt.empty:
        return None
    und = r2.get_parquet(s3, f"{IVOL_UND}/ticker={ticker}/date={d}/bars.parquet")
    out = pd.DataFrame({
        # coerce: one malformed stamp drops its row (_build_slice dropna),
        # never the session — raising here killed the whole load
        "bar_ts": pd.to_datetime(opt["minute_ts"], errors="coerce"),
        "expiration": opt["expiration"],
        "right": opt["right"],
        "strike": pd.to_numeric(opt["strike"], errors="coerce"),
        "bid": pd.to_numeric(opt["bid"], errors="coerce"),
        "ask": pd.to_numeric(opt["ask"], errors="coerce"),
        "last": None,
        "volume": pd.to_numeric(opt["volume"], errors="coerce"),
        "open_interest": None,
        # F5: displayed NBBO depth (contracts) — disclosure input, never
        # pricing; absent columns stay honestly None
        "bid_size": pd.to_numeric(opt["bid_size"], errors="coerce")
        if "bid_size" in opt.columns else None,
        "ask_size": pd.to_numeric(opt["ask_size"], errors="coerce")
        if "ask_size" in opt.columns else None,
        "iv": pd.to_numeric(opt["iv"], errors="coerce"),
        "delta": pd.to_numeric(opt["delta"], errors="coerce"),
        "gamma": pd.to_numeric(opt["gamma"], errors="coerce"),
        "theta": pd.to_numeric(opt["theta"], errors="coerce"),
        "vega": pd.to_numeric(opt["vega"], errors="coerce"),
        "rho": pd.to_numeric(opt["rho"], errors="coerce"),
    })[SLICE_COLUMNS]
    n_bad = int(out["bar_ts"].isna().sum())
    if n_bad == len(out):
        # every stamp evaporated — never serve (or cache) a hollow session;
        # left uncached, the next run rereads the possibly-repaired object
        log.warning("ivol options %s %s: all %d stamps unparseable — skipped",
                    ticker, d, n_bad)
        return None
    if n_bad:
        log.warning("ivol options %s %s: %d/%d rows carry unparseable stamps",
                    ticker, d, n_bad, len(out))
    und_out: pd.DataFrame | None = None
    und_rows = _ivol_und_rows(und, f"{ticker} {d}")
    if und_rows is not None:
        if "volume" in und_rows.columns:
            cum_vol = pd.to_numeric(und_rows["volume"], errors="coerce")
            # the vendor volume column is CUMULATIVE within the session
            # (probed: monotonic 0 → ~52M) — per-bar volume is the diff.
            # An unparseable cell mid-session leaves ITS bar and the next
            # NaN (volume unknown → the bar sits out of session VWAP)
            # rather than injecting the whole session cumulative as one
            # bar's volume.
            bar_vol = per_bar_volume(
                cum_vol,
                # per-bar ≡ cumulative ONLY at the session open: a
                # head-truncated payload's first cumulative is hours of
                # volume, not a bar's — unknown, like an unparseable cell
                seed_first=und_rows["bar_ts"].iloc[0].time() == SESSION_OPEN,
            )
        else:
            bar_vol = pd.Series(0.0, index=und_rows.index)  # VWAP unevaluable
        und_out = pd.DataFrame({
            "bar_ts": und_rows["bar_ts"],
            "last": pd.to_numeric(und_rows["last"], errors="coerce"),
            "volume": bar_vol,
        }).dropna(subset=["bar_ts", "last"])
    return out, und_out


def _minute_und_frame(s3: Any, ticker: str, d: str) -> pd.DataFrame | None:
    """The 1-min underlying PRICE series for one session (FX.1 minute grid).

    PRICE ONLY, deliberately: on a minute grid the 5-min underlying frame
    remains the single source of indicator samples AND session-VWAP volume
    (review finding: bars_1m is a different vendor artifact with different
    session bounds — mixing its volumes would double-count and its values
    would silently shift timeframe-"5min" signal meaning). These rows only
    refine the price between 5-min stamps.

    bars_1m rows carry the MOST RECENT print (lastPrice/lastDateTime), so
    the session open repeats the prior session's close until a fresh print
    lands — those stale rows are dropped (a Friday print is not a Monday
    price; fail closed, same spirit as app/data/pit.py). Grid bounded to
    regular hours [09:30, 16:00); stamps not aligned to whole minutes are
    dropped (alignment is what the bar loop's time math assumes)."""
    df = r2.get_parquet(s3, f"{BARS_1M}/ticker={ticker}/date={d}/bars.parquet")
    needed = {"timestamp", "lastPrice", "lastDateTime"}
    if df is None or df.empty or not needed.issubset(df.columns):
        return None
    ts = pd.to_datetime(df["timestamp"], errors="coerce")
    last = pd.to_numeric(df["lastPrice"], errors="coerce")
    last_dt = pd.to_datetime(df["lastDateTime"], errors="coerce")
    session = pd.Timestamp(d).date()
    fresh = last_dt.notna() & (last_dt.dt.date == session)
    in_session = (
        ts.notna()
        & (ts.dt.time >= pd.Timestamp("09:30").time())
        & (ts.dt.time < pd.Timestamp("16:00").time())
    )
    aligned = ts.notna() & (ts.dt.second == 0) & (ts.dt.microsecond == 0)
    keep = fresh & in_session & aligned & last.notna()
    if not bool(keep.any()):
        return None
    return pd.DataFrame({
        "bar_ts": ts[keep],
        "last": last[keep],
    }).reset_index(drop=True)


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
            "bid_size": pd.to_numeric(sub["bid_size"], errors="coerce")
            if "bid_size" in sub.columns else None,
            "ask_size": pd.to_numeric(sub["ask_size"], errors="coerce")
            if "ask_size" in sub.columns else None,
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


def _merge_minute_underlying(
    und5: pd.DataFrame, und1: pd.DataFrame
) -> tuple[pd.DataFrame, set[datetime]]:
    """The minute grid's underlying frame: the 5-min frame's rows WIN at
    their stamps (price + volume — the indicator/VWAP record, identical to
    what the 5-min grid sees, including its 16:00+ tail), and bars_1m rows
    fill the minutes between as PRICE-ONLY (volume absent → they never move
    session VWAP). Returns the merged frame and the 5-min stamp set — the
    indicator sampling stamps."""
    stamps = {pd.Timestamp(r).to_pydatetime() for r in und5["bar_ts"]}
    extra = und1.loc[~pd.to_datetime(und1["bar_ts"]).isin(list(stamps))].copy()
    extra["volume"] = float("nan")  # price-only rows never move session VWAP
    merged = pd.concat(
        [und5[["bar_ts", "last", "volume"]], extra[["bar_ts", "last", "volume"]]],
        ignore_index=True,
    ).sort_values("bar_ts")
    return merged.reset_index(drop=True), stamps


def _build_slice(
    session: date, opt: pd.DataFrame, und: pd.DataFrame | None, source: str,
    bar_resolution: str = "5min",
    indicator_stamps: set[datetime] | None = None,
) -> SessionSlice:
    opt = opt.dropna(subset=["bar_ts", "right", "strike"])
    # a malformed expiration is that row's defect, never the session's —
    # coerce + drop (the NaN-only dropna can't catch it). This is the ONE
    # spot covering all sources AND cache-served frames: a raise here would
    # recur from the version-valid cache on every retry.
    exp_ts = pd.to_datetime(opt["expiration"], errors="coerce")
    opt = opt[exp_ts.notna()]
    quotes: dict[datetime, dict[ContractKey, Quote]] = {}
    exp = exp_ts[exp_ts.notna()].dt.date
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
                bid_size=_size_i(rec.get("bid_size")),
                ask_size=_size_i(rec.get("ask_size")),
            )
        quotes[bar.to_pydatetime()] = per

    underlying: dict[datetime, float] = {}
    underlying_volume: dict[datetime, float] = {}
    if und is not None and not und.empty:
        for rec in und.dropna(subset=["bar_ts", "last"]).to_dict("records"):
            ts = pd.Timestamp(rec["bar_ts"]).to_pydatetime()
            underlying[ts] = float(rec["last"])
            vol = rec.get("volume")
            if vol is not None and not pd.isna(vol):
                underlying_volume[ts] = float(vol)

    bars = sorted(set(quotes) | set(underlying))
    return SessionSlice(
        session=session, bars=bars, quotes=quotes,
        underlying=underlying, underlying_volume=underlying_volume,
        quote_source=source, bar_resolution=bar_resolution,
        indicator_stamps=indicator_stamps,
    )


def _spread_stats(s3: Any, ticker: str) -> float | None:
    """The ticker's OWN median EOD spread fraction over the slice band —
    the modeled half-spread's only input (brief: 'spread modeled from our
    own EOD per-contract spread stats'). Computed from the Yahoo EOD chains
    (QQQ/IWM carry only a few sessions — thin, real, disclosed) and
    disk-cached. None when the ticker has no EOD chains yet: modeled quotes
    are then impossible and the alpaca sessions stay OUT of coverage."""
    cache = CACHE_DIR / ticker / "spread_stats.json"
    if cache.exists():
        try:
            meta = json.loads(cache.read_text())
            if meta.get("v") == SPREAD_STATS_VERSION:
                value = meta.get("median_spread_frac")
                return None if value is None else float(value)
        except Exception:
            pass

    fracs: list[float] = []
    for d in r2.list_chain_dates(s3, "yahoo", ticker):
        keys = sorted(r2.list_keys(s3, f"options/source=yahoo/ticker={ticker}/date={d}/"))
        if not keys:
            continue
        df = r2.get_parquet(s3, keys[-1])
        if df is None or df.empty:
            continue
        bid = pd.to_numeric(df["bid"], errors="coerce")
        ask = pd.to_numeric(df["ask"], errors="coerce")
        dte = pd.to_numeric(df["dte"], errors="coerce")
        strike = pd.to_numeric(df["strike"], errors="coerce")
        spot = pd.to_numeric(df["spot"], errors="coerce")
        mid = (bid + ask) / 2.0
        ok = (
            bid.notna() & ask.notna() & (mid > 0) & (bid <= ask)
            & (dte <= ALPACA_SLICE_MAX_CALENDAR_DTE)
            & spot.notna() & ((strike - spot).abs() <= SLICE_ATM_BAND)
        )
        fracs.extend(((ask - bid) / mid)[ok].tolist())

    value = float(pd.Series(fracs).median()) if fracs else None
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({"v": SPREAD_STATS_VERSION,
                                     "median_spread_frac": value}))
    except Exception:
        pass
    return value


_MONTH_FRAMES: dict[tuple[str, str], pd.DataFrame | None] = {}


def _alpaca_underlying_month(s3: Any, ticker: str, month: str) -> pd.DataFrame | None:
    key = (ticker, month)
    if key not in _MONTH_FRAMES:
        df = r2.get_parquet(s3, f"{ALPACA_UND}/ticker={ticker}/month={month}/bars.parquet")
        if df is not None and not df.empty:
            df = df[["minute_ts", "close", "volume"]].copy()
            df["minute_ts"] = (
                pd.to_datetime(df["minute_ts"], utc=True)
                .dt.tz_convert("America/New_York").dt.tz_localize(None)
            )
            df = df.sort_values("minute_ts")
        _MONTH_FRAMES[key] = df
        while len(_MONTH_FRAMES) > 6:
            _MONTH_FRAMES.pop(next(iter(_MONTH_FRAMES)))
    return _MONTH_FRAMES[key]


def _alpaca_frames(
    s3: Any, ticker: str, d: str, spread_frac: float
) -> tuple[pd.DataFrame, pd.DataFrame | None] | None:
    """Trade-print minute bars → MODELED 5-min quotes. Per bar b, a
    contract is quotable only if its latest print is younger than
    ALPACA_STALE_PRINT_MIN minutes (strictly BEFORE b — no intra-bar
    future); mid = that print's close, half-spread = mid × spread_frac / 2
    (floor $0.01). No greeks, no IV — delta selection honestly skips."""
    opt = r2.get_parquet(s3, f"{ALPACA_OPT}/ticker={ticker}/date={d}/bars.parquet")
    if opt is None or opt.empty:
        return None
    und_month = _alpaca_underlying_month(s3, ticker, d[:7])
    if und_month is None or und_month.empty:
        return None  # no underlying record → no ATM band → no honest slice

    day_start = pd.Timestamp(d)
    und_day = und_month[(und_month["minute_ts"] >= day_start)
                        & (und_month["minute_ts"] < day_start + pd.Timedelta(days=1))]
    if und_day.empty:
        return None

    # 5-min grid 09:30 → 16:10; underlying last = close of the minute bar
    # strictly BEFORE the grid point; per-bar volume = the window before it
    grid = pd.date_range(f"{d} 09:30", f"{d} 16:10", freq="5min")
    und_ts = und_day["minute_ts"].to_numpy()
    und_close = pd.to_numeric(und_day["close"], errors="coerce").to_numpy()
    und_vol = pd.to_numeric(und_day["volume"], errors="coerce").fillna(0).to_numpy()

    und_rows: list[dict[str, Any]] = []
    for b in grid:
        i = int(np.searchsorted(und_ts, b.to_datetime64(), side="left")) - 1
        if i < 0:
            continue
        j0 = int(np.searchsorted(und_ts, (b - pd.Timedelta(minutes=5)).to_datetime64(),
                                 side="left"))
        und_rows.append({"bar_ts": b.to_pydatetime(), "last": float(und_close[i]),
                         "volume": float(und_vol[j0:i + 1].sum())})
    if not und_rows:
        return None
    und_out = pd.DataFrame(und_rows)
    spot_by_bar = {r["bar_ts"]: r["last"] for r in und_rows}

    o = opt[["minute_ts", "occ_symbol", "expiration", "right", "strike",
             "close", "volume"]].copy()
    o["minute_ts"] = (
        pd.to_datetime(o["minute_ts"], utc=True)
        .dt.tz_convert("America/New_York").dt.tz_localize(None)
    )
    o["strike"] = pd.to_numeric(o["strike"], errors="coerce")
    o["close"] = pd.to_numeric(o["close"], errors="coerce")
    # coerce: a malformed expiration gives NaN dte_cal → filtered right below
    o["dte_cal"] = (pd.to_datetime(o["expiration"], errors="coerce")
                    - day_start).dt.days
    o = o[(o["dte_cal"] >= 0) & (o["dte_cal"] <= ALPACA_SLICE_MAX_CALENDAR_DTE)]
    o = o.dropna(subset=["minute_ts", "strike", "close"]).sort_values("minute_ts")
    if o.empty:
        return None

    frames: list[pd.DataFrame] = []
    stale = pd.Timedelta(minutes=ALPACA_STALE_PRINT_MIN)
    for b in grid:
        spot = spot_by_bar.get(b.to_pydatetime())
        if spot is None:
            continue
        window = o[(o["minute_ts"] >= b - stale) & (o["minute_ts"] < b)]
        if window.empty:
            continue
        latest = window.groupby("occ_symbol").tail(1)
        latest = latest[(latest["strike"] - spot).abs() <= SLICE_ATM_BAND]
        if latest.empty:
            continue
        mid = latest["close"]
        half = (mid * spread_frac / 2.0).clip(lower=MIN_HALF_SPREAD)
        vol = o[(o["minute_ts"] >= b - stale) & (o["minute_ts"] < b)].groupby(
            "occ_symbol")["volume"].sum().reindex(latest["occ_symbol"]).to_numpy()
        frames.append(pd.DataFrame({
            "bar_ts": b.to_pydatetime(),
            "expiration": latest["expiration"].to_numpy(),
            "right": latest["right"].to_numpy(),
            "strike": latest["strike"].to_numpy(),
            "bid": (mid - half).round(4).to_numpy(),
            "ask": (mid + half).round(4).to_numpy(),
            "last": mid.to_numpy(),
            "volume": vol,
            "open_interest": None, "iv": None, "delta": None, "gamma": None,
            "theta": None, "vega": None, "rho": None,
            "bid_size": None, "ask_size": None,  # modeled quotes have no book
        })[SLICE_COLUMNS])
    if not frames:
        return None
    return pd.concat(frames, ignore_index=True), und_out


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
        else:
            # a prior version's und file must never survive under the new
            # manifest — _read_cached serves whatever und parquet exists
            und_p.unlink(missing_ok=True)
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
        # FX.1: minute-grid slice LRU, fully separate from the 5-min path so
        # the default clock's cache behavior is untouched. Built slices only
        # — a failed build is NOT cached (bars_1m may arrive later; a stale
        # negative would defeat the nightly upgrade).
        self._lru_1m: OrderedDict[date, SessionSlice] = OrderedDict()

    @property
    def slice_max_trading_dte(self) -> int:
        return SLICE_MAX_TRADING_DTE

    def _session_sources(self) -> dict[date, str]:
        if self._sessions is None:
            s3 = r2.r2_client()
            cboe = r2.list_date_prefixes(s3, f"{CBOE_OPT}/ticker={self.ticker}/")
            ivol = r2.list_date_prefixes(s3, f"{IVOL_OPT}/ticker={self.ticker}/")
            src: dict[date, str] = {}
            # modeled sessions exist ONLY when the ticker has spread stats,
            # and only where no quote record does (lowest rank)
            if _spread_stats(s3, self.ticker) is not None:
                alpaca = r2.list_date_prefixes(s3, f"{ALPACA_OPT}/ticker={self.ticker}/")
                src.update({date.fromisoformat(d): "alpaca_modeled" for d in alpaca})
            src.update({date.fromisoformat(d): "cboe_minute" for d in cboe})
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
        if source == "ivol_5min":
            frames = _ivol_frames(s3, self.ticker, d)
        elif source == "cboe_minute":
            frames = _cboe_frames(s3, self.ticker, d)
        else:  # alpaca_modeled
            spread_frac = _spread_stats(s3, self.ticker)
            if spread_frac is None:
                return None
            frames = _alpaca_frames(s3, self.ticker, d, spread_frac)
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

    # ------------------------------------------------ FX.1: the minute grid

    def minute_sessions(self) -> set[date]:
        """Sessions eligible for the 1-min grid, from the F0 resolution map
        (an artifact lookup, never a live listing — post-OOM rule). NOT
        memoized on the store: the map's own 300s TTL governs freshness, so
        a long-lived process picks up the nightly ledger rebuild without a
        restart (self-improvement thesis); the engine snapshots the set once
        per run for determinism. Empty when the map is pending or R2 is
        unconfigured — honest degrade, recorded five_min."""
        from app.data import resolution  # local: keeps import graph flat

        try:
            s3 = r2.r2_client()
            iso = resolution.minute_clock_sessions(s3, self.ticker)
        except r2.R2NotConfigured:
            iso = set()
        return {date.fromisoformat(s) for s in iso}

    def _minute_und_cached(self, session: date) -> pd.DataFrame | None:
        """The bars_1m price frame, disk-cached beside the 5-min frames —
        a finest gauntlet re-runs the engine ~25×, and re-fetching every
        minute session from R2 per sweep cell is 1,000+ round-trips."""
        d = session.isoformat()
        base = CACHE_DIR / self.ticker
        frame_p, meta_p = base / f"{d}_und_1m.parquet", base / f"{d}_meta_1m.json"
        if frame_p.exists() and meta_p.exists():
            try:
                if json.loads(meta_p.read_text()).get("v") == CACHE_SCHEMA_VERSION:
                    return pd.read_parquet(frame_p)
            except Exception:
                pass
        und1 = _minute_und_frame(r2.r2_client(), self.ticker, d)
        if und1 is None or und1.empty:
            return None  # never disk-cache a negative — bars_1m may arrive
        try:
            frame_p.parent.mkdir(parents=True, exist_ok=True)
            und1.to_parquet(frame_p, index=False)
            meta_p.write_text(json.dumps({"v": CACHE_SCHEMA_VERSION}))
        except Exception:
            pass  # cache is an optimization, never a requirement
        return und1

    def minute_slice_for(self, session: date) -> SessionSlice | None:
        """The session at the 1-min grid. The underlying record is the
        5-MIN frame's rows at their stamps (price + volume — indicator and
        VWAP inputs identical to the 5-min grid, 16:00+ tail included) with
        bars_1m PRICE-ONLY rows between; quotes are the same 5-min NBBO
        stamps. None when the grid can't be built — the engine falls back
        to the 5-min slice and records the session as five_min."""
        if session in self._lru_1m:
            self._lru_1m.move_to_end(session)
            return self._lru_1m[session]
        if session not in self.minute_sessions():
            return None
        frames = self._ensure_cached(session)  # options + und5, disk-cached
        if frames is None:
            return None
        opt, und5, source = frames
        if und5 is None or und5.empty:
            return None  # no 5-min underlying record → no honest minute grid
        und1 = self._minute_und_cached(session)
        if und1 is None or und1.empty:
            return None
        merged, stamps = _merge_minute_underlying(und5, und1)
        slc = _build_slice(session, opt, merged, source,
                           bar_resolution="1min", indicator_stamps=stamps)
        self._lru_1m[session] = slc
        while len(self._lru_1m) > LRU_MINUTE_SESSIONS:
            self._lru_1m.popitem(last=False)
        return slc


_STORE_CACHE: dict[str, IntradayStore] = {}


def load_intraday_store(ticker: str) -> IntradayStore:
    store = _STORE_CACHE.get(ticker)
    if store is None:
        store = IntradayStore(ticker)
        _STORE_CACHE[ticker] = store
    return store
