"""Chart bars for the underlying — the data behind /api/data/bars/{ticker}.

Sources, in honesty order:
- Intraday (1m → 4h): the lake's underlying minute bars
  (underlying_minute/ticker=T/month=YYYY-MM/bars.parquet, 2024-02 →,
  refreshed nightly), resampled server-side with 9:30-ET-anchored bins.
- Daily / weekly: the lake's daily history (1993 →), weekly = W-FRI.
- LIVE tail: when APCA_* keys are configured, today's missing minutes come
  from Alpaca's free IEX feed at request time (stock data — not OPRA-gated,
  unaffected by the options-entitlement freeze). Without keys the payload
  says exactly how fresh it is; nothing is extrapolated.

Tick-level intervals do not exist here on purpose: the lake has no tick
data and no $0 source provides it (docs/INTRADAY-OPTIONS-DATA-EVAL.md).
"""

from __future__ import annotations

import os
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd
import requests

from app.data import indicators as ind
from app.data import r2

TICKERS = ["SPY", "QQQ", "IWM"]
MAX_BARS = 2500  # initial buffer size
PAGE_MAX = 5000  # hard cap on any single response
# indicator warmup rows computed BEFORE the returned range so paged-in older
# bars stitch onto the buffer with correct SMA200/MACD/RSI values at the seam
INDICATOR_LOOKBACK = 300
MINUTE_LAKE_START = "2024-02"
ET = "America/New_York"

# interval -> (pandas rule, minutes per bar); None rule = raw minutes
INTRADAY_INTERVALS: dict[str, tuple[str | None, int]] = {
    "1m": (None, 1),
    "2m": ("2min", 2),
    "3m": ("3min", 3),
    "5m": ("5min", 5),
    "15m": ("15min", 15),
    "30m": ("30min", 30),
    "1h": ("60min", 60),
    "4h": ("240min", 240),
}
DAILY_INTERVALS = {"1d", "1w"}
INTERVALS = list(INTRADAY_INTERVALS) + sorted(DAILY_INTERVALS)
WINDOWS = ["1d", "1w", "1mo", "3mo", "ytd", "1y", "5y", "all"]

_month_cache: dict[tuple[str, str], tuple[float, pd.DataFrame | None]] = {}
_daily_cache: dict[str, tuple[float, pd.DataFrame | None]] = {}


def _cached_month(s3: Any, ticker: str, month: str) -> pd.DataFrame | None:
    now = time.time()
    current_month = datetime.now(UTC).strftime("%Y-%m")
    ttl = 600 if month == current_month else 86_400
    hit = _month_cache.get((ticker, month))
    if hit and now - hit[0] < ttl:
        return hit[1]
    df = r2.get_parquet(s3, f"underlying_minute/ticker={ticker}/month={month}/bars.parquet")
    if df is not None and not df.empty:
        df = df[["minute_ts", "open", "high", "low", "close", "volume"]].copy()
        df["minute_ts"] = pd.to_datetime(df["minute_ts"], utc=True)
        df = df.sort_values("minute_ts")
    _month_cache[(ticker, month)] = (now, df)
    return df


def _cached_daily(s3: Any, ticker: str) -> pd.DataFrame | None:
    now = time.time()
    hit = _daily_cache.get(ticker)
    if hit and now - hit[0] < 600:
        return hit[1]
    df = r2.get_parquet(s3, f"underlying/ticker={ticker}/daily.parquet")
    if df is not None and not df.empty:
        df = df[["date", "open", "high", "low", "close", "volume"]].copy()
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date")
    _daily_cache[ticker] = (now, df)
    return df


def _months_between(start: datetime, end: datetime) -> list[str]:
    end = end.replace(tzinfo=None)
    out, cur = [], datetime(start.year, start.month, 1)
    while cur <= end:
        out.append(cur.strftime("%Y-%m"))
        cur = (cur + timedelta(days=32)).replace(day=1)
    return [m for m in out if m >= MINUTE_LAKE_START]


def _alpaca_keys() -> dict[str, str] | None:
    kid = os.environ.get("APCA_API_KEY_ID")
    sec = os.environ.get("APCA_API_SECRET_KEY")
    if not kid or not sec:
        return None
    return {"APCA-API-KEY-ID": kid, "APCA-API-SECRET-KEY": sec}


def _live_tail_minutes(ticker: str, after: pd.Timestamp) -> pd.DataFrame | None:
    """Today's missing 1-minute bars from Alpaca's IEX feed (free tier,
    real-time). Best-effort: any failure returns None and the chart simply
    discloses its freshness."""
    headers = _alpaca_keys()
    if headers is None:
        return None
    start = max(after + pd.Timedelta(minutes=1), pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=7))
    rows: list[dict[str, Any]] = []
    token = None
    try:
        while True:
            params: dict[str, Any] = {
                "symbols": ticker,
                "timeframe": "1Min",
                "feed": "iex",
                "start": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "limit": 10000,
            }
            if token:
                params["page_token"] = token
            resp = requests.get(
                "https://data.alpaca.markets/v2/stocks/bars",
                params=params,
                headers=headers,
                timeout=10,
            )
            if resp.status_code != 200:
                return None
            payload = resp.json()
            for b in (payload.get("bars") or {}).get(ticker) or []:
                rows.append(b)
            token = payload.get("next_page_token")
            if not token:
                break
    except requests.RequestException:
        return None
    if not rows:
        return None
    df = pd.DataFrame(rows).rename(
        columns={
            "t": "minute_ts", "o": "open", "h": "high", "l": "low", "c": "close", "v": "volume",
        }
    )
    df["minute_ts"] = pd.to_datetime(df["minute_ts"], utc=True)
    return df[["minute_ts", "open", "high", "low", "close", "volume"]].sort_values("minute_ts")


def _window_start(window: str, last_ts: pd.Timestamp) -> pd.Timestamp | None:
    if window == "all":
        return None
    if window == "ytd":
        return pd.Timestamp(year=last_ts.year, month=1, day=1, tz=last_ts.tz)
    days = {"1d": 1, "1w": 7, "1mo": 31, "3mo": 92, "1y": 365, "5y": 365 * 5}[window]
    return last_ts - pd.Timedelta(days=days)


def _resample_minutes(df: pd.DataFrame, rule: str | None) -> pd.DataFrame:
    et = df.set_index(df["minute_ts"].dt.tz_convert(ET)).sort_index()
    if rule is None:
        out = et[["open", "high", "low", "close", "volume"]].copy()
    else:
        # 9:30-ET-anchored bins so 30m/1h/4h candles align to the session open
        out = (
            et.resample(rule, origin="start_day", offset="9h30min", label="left")
            .agg(
                open=("open", "first"),
                high=("high", "max"),
                low=("low", "min"),
                close=("close", "last"),
                volume=("volume", "sum"),
            )
            .dropna(subset=["open"])
        )
    out.index.name = "ts"
    return out.reset_index()


def _intraday_frame(
    s3: Any, ticker: str, interval: str, window: str, before: pd.Timestamp | None, target: int
) -> tuple[pd.DataFrame, bool, bool, int]:
    """Returns (frame incl. indicator-lookback rows, live, has_more, keep)."""
    rule, per_bar = INTRADAY_INTERVALS[interval]
    reference_end = before.to_pydatetime() if before is not None else datetime.now(UTC)
    bars_per_month = (390 // per_bar + 1) * 21
    months_needed = max(2, (target + INDICATOR_LOOKBACK) // max(bars_per_month, 1) + 2)
    all_months = _months_between(datetime(2024, 2, 1), reference_end)
    months = all_months[-months_needed:]
    truncated_months = len(all_months) > len(months)

    frames = [m for month in months if (m := _cached_month(s3, ticker, month)) is not None]
    minutes = (
        pd.concat(frames, ignore_index=True).drop_duplicates(subset="minute_ts")
        if frames
        else pd.DataFrame(columns=["minute_ts", "open", "high", "low", "close", "volume"])
    )

    live = False
    if before is None:
        last = minutes["minute_ts"].max() if len(minutes) else pd.Timestamp("2024-02-01", tz="UTC")
        if datetime.now(UTC) - last.to_pydatetime() > timedelta(minutes=2):
            tail = _live_tail_minutes(ticker, last)
            if tail is not None and len(tail):
                minutes = pd.concat([minutes, tail], ignore_index=True).drop_duplicates(
                    subset="minute_ts"
                )
                live = True
        else:
            live = True

    if minutes.empty:
        empty = pd.DataFrame(columns=["ts", "open", "high", "low", "close", "volume"])
        return empty, False, False, 0

    bars = _resample_minutes(minutes.sort_values("minute_ts"), rule)
    if before is not None:
        bars = bars[bars["ts"] < before]
        count = target
    elif window == "1d":
        # the latest session only (weekends show Friday, honestly)
        last_session = bars["ts"].dt.date.max()
        count = int((bars["ts"].dt.date == last_session).sum())
    else:
        start = _window_start(window, bars["ts"].max())
        count = int((bars["ts"] >= start).sum()) if start is not None else target
    count = min(max(count, 1), target)
    pre_count = len(bars)
    # keep lookback rows BEFORE the window so indicators are warm at its edge
    bars = bars.tail(count + INDICATOR_LOOKBACK).reset_index(drop=True)
    keep = min(count, len(bars))
    has_more = truncated_months or pre_count > len(bars)
    return bars, live, has_more, keep


def _daily_frame(
    s3: Any, ticker: str, interval: str, window: str, before: pd.Timestamp | None, target: int
) -> tuple[pd.DataFrame, bool, int]:
    daily = _cached_daily(s3, ticker)
    if daily is None or daily.empty:
        return pd.DataFrame(columns=["ts", "open", "high", "low", "close", "volume"]), False, 0
    df = daily.rename(columns={"date": "ts"}).copy()
    df["ts"] = df["ts"].dt.tz_localize(ET)
    if interval == "1w":
        df = (
            df.set_index("ts")
            .resample("W-FRI", label="left")
            .agg(
                open=("open", "first"),
                high=("high", "max"),
                low=("low", "min"),
                close=("close", "last"),
                volume=("volume", "sum"),
            )
            .dropna(subset=["open"])
            .reset_index()
        )
    if before is not None:
        df = df[df["ts"] < before]
        count = target
    else:
        start = _window_start(window, df["ts"].max())
        count = int((df["ts"] >= start).sum()) if start is not None else target
    count = min(max(count, 1), target)
    pre_count = len(df)
    df = df.tail(count + INDICATOR_LOOKBACK).reset_index(drop=True)
    keep = min(count, len(df))
    return df, pre_count > len(df), keep


def _parse_indicator(spec: str) -> tuple[str, list[float]]:
    parts = spec.strip().lower().split(":")
    return parts[0], [float(p) for p in parts[1:] if p]


def _attach_indicators(bars: pd.DataFrame, specs: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if bars.empty:
        return out
    close = bars["close"]
    session = bars["ts"].dt.tz_convert(ET).dt.date.astype(str)

    def listify(series: pd.Series) -> list[float | None]:
        return [None if pd.isna(v) else round(float(v), 4) for v in series]

    for spec in specs[:10]:
        name, params = _parse_indicator(spec)
        key = spec.strip().lower()
        if name == "sma":
            out[key] = listify(ind.sma(close, int(params[0]) if params else 20))
        elif name == "ema":
            out[key] = listify(ind.ema(close, int(params[0]) if params else 9))
        elif name == "rsi":
            out[key] = listify(ind.rsi(close, int(params[0]) if params else 14))
        elif name == "vwap":
            out[key] = listify(ind.vwap(bars, session))
        elif name == "bb":
            period = int(params[0]) if params else 20
            mult = params[1] if len(params) > 1 else 2.0
            bands = ind.bollinger(close, period, mult)
            out[key] = {k: listify(v) for k, v in bands.items()}
        elif name == "macd":
            if len(params) >= 3:
                fast, slow, sig = int(params[0]), int(params[1]), int(params[2])
            else:
                fast, slow, sig = 12, 26, 9
            lines = ind.macd(close, fast, slow, sig)
            out[key] = {k: listify(v) for k, v in lines.items()}
        # unknown names are ignored (the UI only offers known ones)
    return out


def _slice_indicators(indicators: dict[str, Any], cut: int) -> dict[str, Any]:
    if cut <= 0:
        return indicators
    out: dict[str, Any] = {}
    for key, series in indicators.items():
        if isinstance(series, dict):
            out[key] = {k: v[cut:] for k, v in series.items()}
        else:
            out[key] = series[cut:]
    return out


def get_bars(
    ticker: str,
    interval: str,
    window: str,
    indicator_specs: list[str],
    before: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    s3 = r2.r2_client()
    before_ts = pd.Timestamp(before) if before else None
    if before_ts is not None and before_ts.tzinfo is None:
        before_ts = before_ts.tz_localize("UTC")
    target = min(limit or MAX_BARS, PAGE_MAX)

    if interval in DAILY_INTERVALS:
        frame, has_more, keep = _daily_frame(s3, ticker, interval, window, before_ts, target)
        live = False
        source = "lake dailies 1993→ (refreshed nightly)"
    else:
        frame, live, has_more, keep = _intraday_frame(
            s3, ticker, interval, window, before_ts, target
        )
        source = (
            "lake minutes 2024-02→ + live IEX tail"
            if live
            else "lake minutes 2024-02→ (nightly; add APCA_* keys for a live tail)"
        )

    # indicators computed over the FULL frame (with lookback rows), then the
    # lookback is sliced off so paged responses stitch seamlessly
    indicators = _slice_indicators(
        _attach_indicators(frame, indicator_specs), max(0, len(frame) - keep)
    )
    bars = frame.tail(keep).reset_index(drop=True) if keep else frame.iloc[0:0]

    volume = bars["volume"].fillna(0) if not bars.empty else pd.Series(dtype=float)
    payload_bars = [
        {
            "t": ts.isoformat(),
            "o": round(float(o), 4),
            "h": round(float(h), 4),
            "l": round(float(low), 4),
            "c": round(float(c), 4),
            "v": int(v) if not np.isnan(v) else 0,
        }
        for ts, o, h, low, c, v in zip(
            bars["ts"], bars["open"], bars["high"], bars["low"], bars["close"], volume, strict=False
        )
    ]
    return {
        "ticker": ticker,
        "interval": interval,
        "window": window,
        "live": live,
        "source": source,
        "as_of": bars["ts"].max().isoformat() if not bars.empty else None,
        "has_more": bool(has_more),
        "bars": payload_bars,
        "indicators": indicators,
    }
