#!/usr/bin/env python3
"""
alpaca.py — Alpaca minute-bar options lake (DATA-PIPELINE §4b, BUILD-PLAN M1.5).

Modes:
  --mode backfill   Walk month × ticker from 2024-02 (Alpaca history start)
                    to the current month, pulling 1-minute option bars for the
                    full chain plus underlying minute bars. Resumable via
                    state/alpaca_backfill.json; honors --max-minutes so CI
                    runs stop cleanly and the next dispatch continues.
  --mode eod        Nightly top-up: re-pull any of the last few completed
                    sessions missing from the lake (self-healing lookback,
                    same idea as the AV leg).

Step-0 findings this design encodes (BUILD-LOG 2026-07-02): expired
contracts list back to 2024-02 via the trading API; historical option
QUOTES are not served on the Basic plan (404 — latest-only), so this module
never requests quotes; account rate limit is 200 req/min.

Env vars required: APCA_API_KEY_ID, APCA_API_SECRET_KEY, R2_ACCOUNT_ID,
R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET.

Data goes to R2 only. Never commit data to git.
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import requests

from collect import (
    TICKERS,
    completed_sessions,
    r2_client,
    r2_get_json,
    r2_put_json,
    r2_put_parquet,
    r2_list_keys,
)

log = logging.getLogger("alpaca")

TRADING_BASE = "https://paper-api.alpaca.markets"
DATA_BASE = "https://data.alpaca.markets"
HISTORY_START_MONTH = "2024-02"  # Alpaca options history begins 2024-02
STATE_KEY = "state/alpaca_backfill.json"
MINUTE_PREFIX = "options_minute/source=alpaca"
UNDERLYING_MINUTE_PREFIX = "underlying_minute"
MAX_EXP_DAYS = 400  # bars for expirations further out are ~all-empty; cap batches
BATCH_SYMBOLS = 100  # data API maximum
PAGE_LIMIT = 10000  # data API maximum
REQS_PER_MIN = 185  # stay under the 200/min account limit
EOD_LOOKBACK_SESSIONS = 5
OCC_RE = re.compile(r"^([A-Z]+)(\d{6})([CP])(\d{8})$")

MINUTE_COLUMNS = [
    "ticker", "trading_date", "minute_ts", "occ_symbol", "expiration",
    "right", "strike", "open", "high", "low", "close", "volume",
    "trade_count", "vwap", "source",
]


class Pacer:
    """Token-spacing for the shared 200 req/min account budget."""

    def __init__(self, per_minute: int = REQS_PER_MIN):
        self.interval = 60.0 / per_minute
        self.next_at = 0.0
        self.used = 0

    def wait(self) -> None:
        now = time.monotonic()
        if now < self.next_at:
            time.sleep(self.next_at - now)
        self.next_at = max(now, self.next_at) + self.interval
        self.used += 1


PACER = Pacer()


def _headers() -> dict:
    return {
        "APCA-API-KEY-ID": os.environ["APCA_API_KEY_ID"],
        "APCA-API-SECRET-KEY": os.environ["APCA_API_SECRET_KEY"],
    }


def _get(url: str, params: dict) -> dict:
    for attempt in range(5):
        PACER.wait()
        resp = requests.get(url, params=params, headers=_headers(), timeout=30)
        if resp.status_code == 200:
            return resp.json()
        if resp.status_code == 429:
            log.warning("429 rate-limited; backing off")
            time.sleep(15 * (attempt + 1))
            continue
        if resp.status_code >= 500:
            time.sleep(5 * (attempt + 1))
            continue
        raise RuntimeError(f"GET {url} -> {resp.status_code}: {resp.text[:200]}")
    raise RuntimeError(f"GET {url}: retries exhausted")


# ----------------------------- universe ------------------------------------

def contract_symbols(ticker: str, exp_gte: date, exp_lte: date) -> list[str]:
    """All contract symbols (active + expired) with expiration in the window."""
    symbols: set[str] = set()
    for status in ("inactive", "active"):
        token = None
        while True:
            params = {
                "underlying_symbols": ticker,
                "status": status,
                "expiration_date_gte": exp_gte.isoformat(),
                "expiration_date_lte": exp_lte.isoformat(),
                "limit": 10000,
            }
            if token:
                params["page_token"] = token
            payload = _get(f"{TRADING_BASE}/v2/options/contracts", params)
            for c in payload.get("option_contracts") or payload.get("contracts") or []:
                symbols.add(c["symbol"])
            token = payload.get("next_page_token")
            if not token:
                break
    # sort by (expiration, strike) so a batch's rows cluster in time
    return sorted(symbols, key=lambda s: (s[len(ticker):len(ticker) + 6], s))


# ----------------------------- bars ----------------------------------------

def fetch_option_bars(symbols: list[str], start_iso: str, end_iso: str) -> list[dict]:
    rows: list[dict] = []
    for i in range(0, len(symbols), BATCH_SYMBOLS):
        batch = symbols[i:i + BATCH_SYMBOLS]
        token = None
        while True:
            params = {
                "symbols": ",".join(batch),
                "timeframe": "1Min",
                "start": start_iso,
                "end": end_iso,
                "limit": PAGE_LIMIT,
            }
            if token:
                params["page_token"] = token
            payload = _get(f"{DATA_BASE}/v1beta1/options/bars", params)
            for sym, bars in (payload.get("bars") or {}).items():
                for b in bars or []:
                    rows.append({"symbol": sym, **b})
            token = payload.get("next_page_token")
            if not token:
                break
    return rows


def bars_to_frame(ticker: str, raw: list[dict]) -> pd.DataFrame:
    """Normalize raw Alpaca bars to the §4b minute-bar schema."""
    if not raw:
        return pd.DataFrame(columns=MINUTE_COLUMNS)
    df = pd.DataFrame(raw)
    parsed = df["symbol"].str.extract(OCC_RE)
    df["expiration"] = pd.to_datetime(parsed[1], format="%y%m%d").dt.date
    df["right"] = parsed[2].map({"C": "call", "P": "put"})
    df["strike"] = parsed[3].astype(float) / 1000.0
    ts = pd.to_datetime(df["t"], utc=True)
    df["minute_ts"] = ts
    df["trading_date"] = ts.dt.tz_convert("America/New_York").dt.date
    df = df.rename(columns={
        "symbol": "occ_symbol", "o": "open", "h": "high", "l": "low",
        "c": "close", "v": "volume", "n": "trade_count", "vw": "vwap",
    })
    df["ticker"] = ticker
    df["source"] = "alpaca"
    return df[MINUTE_COLUMNS]


def write_sessions(s3, ticker: str, frame: pd.DataFrame) -> dict[str, int]:
    """One parquet per (ticker, trading_date); idempotent overwrite."""
    written = {}
    for day, day_df in frame.groupby("trading_date"):
        key = f"{MINUTE_PREFIX}/ticker={ticker}/date={day}/bars.parquet"
        r2_put_parquet(s3, key, day_df.sort_values(["occ_symbol", "minute_ts"]))
        written[str(day)] = len(day_df)
    return written


# ----------------------------- underlying ----------------------------------

def fetch_underlying_minute(month_start: date, month_end: date) -> pd.DataFrame:
    rows: list[dict] = []
    token = None
    while True:
        params = {
            "symbols": ",".join(TICKERS),
            "timeframe": "1Min",
            "start": f"{month_start}T00:00:00Z",
            "end": f"{month_end}T23:59:59Z",
            "limit": PAGE_LIMIT,
        }
        if token:
            params["page_token"] = token
        payload = _get(f"{DATA_BASE}/v2/stocks/bars", params)
        for sym, bars in (payload.get("bars") or {}).items():
            for b in bars or []:
                rows.append({"symbol": sym, **b})
        token = payload.get("next_page_token")
        if not token:
            break
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows).rename(columns={
        "symbol": "ticker", "o": "open", "h": "high", "l": "low",
        "c": "close", "v": "volume", "n": "trade_count", "vw": "vwap",
    })
    df["minute_ts"] = pd.to_datetime(df["t"], utc=True)
    return df[["ticker", "minute_ts", "open", "high", "low", "close",
               "volume", "trade_count", "vwap"]]


def write_underlying_month(s3, month: str, frame: pd.DataFrame) -> None:
    for ticker, tdf in frame.groupby("ticker"):
        key = f"{UNDERLYING_MINUTE_PREFIX}/ticker={ticker}/month={month}/bars.parquet"
        r2_put_parquet(s3, key, tdf.sort_values("minute_ts"))


# ----------------------------- months --------------------------------------

def month_range(start_month: str) -> list[str]:
    out, cur = [], datetime.strptime(start_month, "%Y-%m").date().replace(day=1)
    today = datetime.now(timezone.utc).date()
    while cur <= today:
        out.append(cur.strftime("%Y-%m"))
        cur = (cur + timedelta(days=32)).replace(day=1)
    return out


def month_bounds(month: str) -> tuple[date, date]:
    start = datetime.strptime(month, "%Y-%m").date()
    end = (start + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    return start, end


def backfill_month(s3, ticker: str, month: str) -> dict:
    start, end = month_bounds(month)
    t0 = time.monotonic()
    symbols = contract_symbols(ticker, start, end + timedelta(days=MAX_EXP_DAYS))
    raw = fetch_option_bars(symbols, f"{start}T00:00:00Z", f"{end}T23:59:59Z")
    frame = bars_to_frame(ticker, raw)
    written = write_sessions(s3, ticker, frame)
    stats = {
        "status": "done",
        "contracts": len(symbols),
        "rows": int(len(frame)),
        "sessions": len(written),
        "seconds": int(time.monotonic() - t0),
    }
    log.info("%s %s: %s contracts, %s rows, %s sessions, %ss",
             ticker, month, len(symbols), len(frame), len(written), stats["seconds"])
    return stats


def run_backfill(start_month: str, tickers: list[str], max_minutes: float) -> int:
    s3 = r2_client()
    state = r2_get_json(s3, STATE_KEY, {})
    deadline = time.monotonic() + max_minutes * 60
    months = month_range(start_month)
    pending = [(t, m) for m in months for t in tickers
               if state.get(t, {}).get(m, {}).get("status") != "done"]
    log.info("backfill: %d ticker-months pending of %d total",
             len(pending), len(months) * len(tickers))

    for ticker, month in pending:
        if time.monotonic() > deadline:
            log.warning("INCOMPLETE: wall-clock budget reached; %d ticker-months "
                        "remain — re-dispatch to continue", len(
                            [(t, m) for (t, m) in pending
                             if state.get(t, {}).get(m, {}).get("status") != "done"]))
            return 0
        stats = backfill_month(s3, ticker, month)
        state.setdefault(ticker, {})[month] = stats
        r2_put_json(s3, STATE_KEY, state)

    # underlying minute bars, once per month (cheap; after options so a
    # wall-clock stop never leaves options behind underlying)
    for month in months:
        if state.get("_underlying", {}).get(month) == "done":
            continue
        if time.monotonic() > deadline:
            log.warning("INCOMPLETE: underlying minute months remain")
            return 0
        start, end = month_bounds(month)
        frame = fetch_underlying_minute(start, end)
        if not frame.empty:
            write_underlying_month(s3, month, frame)
        state.setdefault("_underlying", {})[month] = "done"
        r2_put_json(s3, STATE_KEY, state)

    log.info("backfill COMPLETE: %d requests used this run", PACER.used)
    return 0


# ----------------------------- eod top-up -----------------------------------

def lake_has_session(s3, ticker: str, day: date) -> bool:
    key = f"{MINUTE_PREFIX}/ticker={ticker}/date={day}/bars.parquet"
    return bool(r2_list_keys(s3, key))


def run_eod(tickers: list[str]) -> int:
    s3 = r2_client()
    sessions = completed_sessions(datetime.now(timezone.utc), EOD_LOOKBACK_SESSIONS)
    failures = 0
    for ticker in tickers:
        for day in sessions:
            if day < date(2024, 2, 1) or lake_has_session(s3, ticker, day):
                continue
            try:
                symbols = contract_symbols(ticker, day, day + timedelta(days=MAX_EXP_DAYS))
                raw = fetch_option_bars(symbols, f"{day}T00:00:00Z", f"{day}T23:59:59Z")
                frame = bars_to_frame(ticker, raw)
                written = write_sessions(s3, ticker, frame)
                log.info("eod %s %s: %s rows", ticker, day, written.get(str(day), 0))
            except Exception:
                log.exception("eod top-up failed for %s %s", ticker, day)
                failures += 1
    # refresh current month's underlying minute bars
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    start, end = month_bounds(month)
    frame = fetch_underlying_minute(start, min(end, datetime.now(timezone.utc).date()))
    if not frame.empty:
        write_underlying_month(s3, month, frame)
    return 1 if failures else 0


def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["backfill", "eod"], required=True)
    ap.add_argument("--tickers", default=",".join(TICKERS))
    ap.add_argument("--start-month", default=HISTORY_START_MONTH)
    ap.add_argument("--max-minutes", type=float, default=330.0)
    args = ap.parse_args()
    tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]

    if args.mode == "backfill":
        return run_backfill(args.start_month, tickers, args.max_minutes)
    return run_eod(tickers)


if __name__ == "__main__":
    sys.exit(main())
