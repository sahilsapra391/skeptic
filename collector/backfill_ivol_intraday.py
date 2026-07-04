#!/usr/bin/env python3
"""
backfill_ivol_intraday.py — capture iVolatility INTRADAY 5-minute history into R2
during the Data Cloud trial. Companion to backfill_ivol.py (EOD, currently 403).

Verified by the probes (2026-07-04):
  * intraday endpoints are entitled on the trial key (EOD chain is 403);
  * option rawiv gives 5-min NBBO bid/ask + vendor greeks/IV, depth to 2013;
    underlying gives 5-min bid/ask/last, depth to 2011;
  * the option endpoint is STRICTLY per (symbol, date, expDate, strike) — one
    request = one contract-day (~82 bars); only optType=ALL batches call+put;
  * the ~90/min rate ceiling is SHARED per account/IP (proven: 3 keys concurrent
    still ≈ 92/min), so extra keys do NOT multiply throughput.

Throughput therefore comes from CONCURRENCY, not keys: a shared RateGate paces all
threads to ~80/min (under the ceiling) while a small thread pool fetches a session's
~50 contracts in parallel — filling the headroom a serial loop (~52/min) leaves.
Keys are rotated only to spread load / dodge any per-key penalty. R2 writes and
state stay serial (one parquet per (ticker, session) in the main thread → no races).
Newest-first + resumable (R2 object = done; empty dates remembered in state).

Modes:
  --mode underlying   5-min underlying bars for --tickers, --from..--to.
  --mode options      Short-DTE ATM option slice (0..MAX_DTE, ATM±BAND$) for
                      --tickers. Also banks each session's underlying bars.

R2 layout (source-tagged, per (ticker, session)):
  underlying_intraday/source=ivolatility/ticker={T}/date={D}/bars.parquet
  options_intraday/source=ivolatility/ticker={T}/date={D}/bars.parquet
State: state/ivol_intraday_{mode}.json  {ticker: {"empty": [dates]}}

Run:
  uv run --env-file .env python backfill_ivol_intraday.py --mode options \
      --tickers SPY,IWM,QQQ --key-env IVOL_API_KEY,IVOL_API_KEY_2,IVOL_API_KEY_3 \
      --concurrency 4 --rate 80
Env (collector/.env): IVOL_API_KEY[/ _2 / _3] + R2_* vars.
"""
from __future__ import annotations

import argparse
import itertools
import logging
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

import exchange_calendars as xcals
import pandas as pd
import requests

from collect import (
    r2_client,
    r2_get_json,
    r2_get_parquet,
    r2_list_keys,
    r2_put_json,
    r2_put_parquet,
)

log = logging.getLogger("ivol_intraday")

BASE = "https://restapi.ivolatility.com"
EP_STOCK = "/equities/intraday/stock-prices"
EP_OPT = "/equities/intraday/single-equity-option-rawiv"
REGION = "USA"
MINUTE = "MINUTE_5"
TIMEOUT = 150

RETRIES = 6
BACKOFF_BASE = 2.0
STATE_FLUSH_EVERY = 25

UNDERLYING_START = "2011-08-22"  # iVol intraday DB start (underlying)
OPTIONS_START = "2013-01-03"     # proven option-rawiv depth
DEFAULT_BAND = 8                 # ATM ± $BAND strikes (dollars, $1 grid)
DEFAULT_MAX_DTE = 2              # 0..2 trading-DTE short-dated slice
DEFAULT_RATE = 80                # aggregate req/min (under the ~90/min shared ceiling)
DEFAULT_CONCURRENCY = 4

UNDERLYING_COLUMNS = [
    "ticker", "trading_date", "minute_ts", "bid", "ask", "last",
    "bid_size", "ask_size", "volume", "source", "minute_type",
]
OPTION_COLUMNS = [
    "ticker", "trading_date", "minute_ts", "occ_symbol", "expiration", "dte",
    "right", "strike", "bid", "ask", "bid_size", "ask_size", "iv",
    "delta", "gamma", "theta", "vega", "rho", "volume", "underlying_price",
    "source", "minute_type",
]

# ---- shared, thread-safe request machinery (set up in run()) ----------------
_AUTHS: list[dict] = []          # [{"apiKey": ...}, ...] rotated across requests
_SECRETS: list[str] = []         # key values, scrubbed from any logged text
_key_cycle = itertools.count()
_req_count = [0]
_req_lock = threading.Lock()


class RateGate:
    """Hands out request start-slots spaced `60/per_min` apart across ALL threads,
    so aggregate arrival rate stays under the shared account/IP ceiling."""

    def __init__(self, per_min: float) -> None:
        self.interval = 60.0 / max(per_min, 1.0)
        self.lock = threading.Lock()
        self.next = 0.0

    def wait(self) -> None:
        with self.lock:
            now = time.time()
            slot = max(now, self.next)
            self.next = slot + self.interval
            sleep = slot - now
        if sleep > 0:
            time.sleep(sleep)


_GATE = RateGate(DEFAULT_RATE)


def _scrub(text: str) -> str:
    for v in _SECRETS:
        if v:
            text = text.replace(v, "***")
    return text


def _get(path: str, params: dict) -> tuple[int, object]:
    """Rate-gated, key-rotated GET with 429/5xx backoff. Returns (status, json).
    Auth (apiKey) goes in the HTTP HEADER. Never logs the response body."""
    auth = _AUTHS[next(_key_cycle) % len(_AUTHS)]
    for attempt in range(RETRIES):
        _GATE.wait()
        try:
            r = requests.get(f"{BASE}{path}", params=params, headers=auth, timeout=TIMEOUT)
        except Exception as exc:  # noqa: BLE001 — retry then give up
            if attempt == RETRIES - 1:
                log.warning("request error %s: %s", path, _scrub(repr(exc)))
                return -1, None
            time.sleep(BACKOFF_BASE * 2 ** attempt)
            continue
        with _req_lock:
            _req_count[0] += 1
        if r.status_code == 200:
            try:
                return 200, r.json()
            except Exception:
                return 200, None
        if r.status_code in (429, 500, 502, 503, 504):
            time.sleep(BACKOFF_BASE * 2 ** attempt)
            continue
        return r.status_code, None  # 400/403 etc — no retry
    return 429, None


def rows_of(body: object) -> list[dict]:
    if isinstance(body, list):
        return [x for x in body if isinstance(x, dict)]
    if isinstance(body, dict):
        for k in ("data", "rows", "result", "records"):
            v = body.get(k)
            if isinstance(v, list):
                return [x for x in v if isinstance(x, dict)]
    return []


# ------------------------------------------------------------ normalize
def _num(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def underlying_df(rows: list[dict], ticker: str, day: str) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    out = pd.DataFrame({
        "ticker": ticker,
        "trading_date": day,
        "minute_ts": pd.to_datetime(df.get("timestamp"), errors="coerce"),
        "bid": _num(df.get("bidPrice")),
        "ask": _num(df.get("askPrice")),
        "last": _num(df.get("lastPrice")),
        "bid_size": _num(df.get("bidSize")),
        "ask_size": _num(df.get("askSize")),
        "volume": _num(df.get("volume")),
        "source": "ivolatility",
        "minute_type": MINUTE,
    })
    return out.dropna(subset=["minute_ts"]).reset_index(drop=True)[UNDERLYING_COLUMNS]


def option_df(rows: list[dict], ticker: str, day: str) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    right = df.get("optionType", pd.Series(dtype=str)).astype(str).str.upper().map(
        {"C": "call", "P": "put"}
    )
    exp = pd.to_datetime(df.get("optionExpirationDate"), errors="coerce")
    out = pd.DataFrame({
        "ticker": ticker,
        "trading_date": day,
        "minute_ts": pd.to_datetime(df.get("timestamp"), errors="coerce"),
        "occ_symbol": df.get("optionSymbol"),
        "expiration": exp.dt.strftime("%Y-%m-%d"),
        "dte": (exp - pd.Timestamp(day)).dt.days,
        "right": right,
        "strike": _num(df.get("optionStrike")),
        "bid": _num(df.get("optionBidPrice")),
        "ask": _num(df.get("optionAskPrice")),
        "bid_size": _num(df.get("optionBidSize")),
        "ask_size": _num(df.get("optionAskSize")),
        "iv": _num(df.get("optionIv")),
        "delta": _num(df.get("optionDelta")),
        "gamma": _num(df.get("optionGamma")),
        "theta": _num(df.get("optionTheta")),
        "vega": _num(df.get("optionVega")),
        "rho": _num(df.get("optionRho")),
        "volume": _num(df.get("optionVolume")),
        "underlying_price": _num(df.get("underlyingPrice")),
        "source": "ivolatility",
        "minute_type": MINUTE,
    })
    return out.dropna(subset=["minute_ts", "strike", "right"]).reset_index(drop=True)[OPTION_COLUMNS]


# ------------------------------------------------------------ R2 keys
def underlying_key(ticker: str, day: str) -> str:
    return f"underlying_intraday/source=ivolatility/ticker={ticker}/date={day}/bars.parquet"


def option_key(ticker: str, day: str) -> str:
    return f"options_intraday/source=ivolatility/ticker={ticker}/date={day}/bars.parquet"


def _have_dates(s3, prefix: str) -> set[str]:
    import re
    have: set[str] = set()
    for k in r2_list_keys(s3, prefix):
        if m := re.search(r"date=(\d{4}-\d{2}-\d{2})", k):
            have.add(m.group(1))
    return have


# ------------------------------------------------------------ capture
def get_underlying(s3, ticker, day, have, empty, dry):
    """Return the day's underlying DataFrame, fetching+banking it if new.
    None when the vendor has no data. (Serial: called once per session.)"""
    if day in have:
        return r2_get_parquet(s3, underlying_key(ticker, day))
    if day in empty:
        return None
    status, body = _get(EP_STOCK, {"symbol": ticker, "date": day,
                                   "region": REGION, "minuteType": MINUTE})
    rows = rows_of(body)
    if status == 200 and rows:
        df = underlying_df(rows, ticker, day)
        if not dry and len(df):
            r2_put_parquet(s3, underlying_key(ticker, day), df)
        have.add(day)
        return df
    if status == 200:
        empty.add(day)
    else:
        log.warning("%s underlying %s: status=%s", ticker, day, status)
    return None


def spot_from(df: pd.DataFrame | None) -> float | None:
    if df is None or df.empty:
        return None
    last = df["last"].dropna()
    last = last[last > 0]
    if len(last) <= 1:
        return float(last.median()) if len(last) else None
    return float(last.iloc[1:].median())  # skip the stale 09:30 open bar


def fetch_option(ticker, day, exp, strike) -> list[dict]:
    status, body = _get(EP_OPT, {
        "symbol": ticker, "date": day, "expDate": exp, "strike": float(strike),
        "optType": "ALL", "minuteType": MINUTE, "region": REGION,
    })
    return rows_of(body) if status == 200 else []


def capture_option_session(s3, pool, ticker, day, exp_cal, band, max_dte, have_u, empty_u, dry) -> int:
    """Fetch the short-DTE ATM slice for one session (contracts in PARALLEL via
    `pool`, paced by the shared gate); write one parquet. Returns rows written,
    or -1 when the session couldn't be processed (no spot → retry, not empty).
    Each candidate expiry is gated by an ATM probe so absent expiries cost 1 req."""
    udf = get_underlying(s3, ticker, day, have_u, empty_u, dry)
    spot = spot_from(udf)
    if spot is None:
        return -1
    atm = round(spot)
    strikes = [atm + k for k in range(-band, band + 1)]
    i = exp_cal.get(day)
    candidate_exps = [exp_cal["_asc"][i + k] for k in range(max_dte + 1)
                      if i is not None and i + k < len(exp_cal["_asc"])]

    collected: list[dict] = []
    # phase 1 — ATM probe per candidate expiry (parallel); keep only listed expiries
    live_exps: list[str] = []
    for exp, atm_rows in pool.map(lambda e: (e, fetch_option(ticker, day, e, atm)), candidate_exps):
        if atm_rows:
            collected.extend(atm_rows)
            live_exps.append(exp)
    # phase 2 — the rest of the ladder for listed expiries (parallel)
    tasks = [(e, k) for e in live_exps for k in strikes if k != atm]
    if tasks:
        for rows in pool.map(lambda ek: fetch_option(ticker, day, ek[0], ek[1]), tasks):
            collected.extend(rows)

    if not collected:
        return 0
    df = option_df(collected, ticker, day)
    if not dry and len(df):
        r2_put_parquet(s3, option_key(ticker, day), df)
    return len(df)


# ------------------------------------------------------------ calendar
def build_exp_calendar(start: str, end: str) -> dict:
    """Ascending session-date list (extended past `end` so DTE+1/+2 expiries
    resolve near the recent edge) + a date→index map."""
    cal = xcals.get_calendar("XNYS")
    ext_end = (date.fromisoformat(end) + timedelta(days=20)).isoformat()
    sess = cal.sessions_in_range(pd.Timestamp(start), pd.Timestamp(ext_end))
    asc = [s.date().isoformat() for s in sess]
    idx: dict = {d: i for i, d in enumerate(asc)}
    idx["_asc"] = asc
    return idx


def sessions_desc(start: str, end: str) -> list[str]:
    cal = xcals.get_calendar("XNYS")
    sess = cal.sessions_in_range(pd.Timestamp(start), pd.Timestamp(end))
    return [s.date().isoformat() for s in reversed(sess)]


# ------------------------------------------------------------ run
def run(args: argparse.Namespace) -> int:
    key_vars = [v.strip() for v in args.key_env.split(",") if v.strip()]
    global _AUTHS, _SECRETS, _GATE
    _AUTHS, _SECRETS = [], []
    for v in key_vars:
        val = os.environ.get(v)
        if val and val not in _SECRETS:
            _AUTHS.append({"apiKey": val})
            _SECRETS.append(val)
    if not _AUTHS:
        sys.exit(f"no keys set in env for: {args.key_env}")
    _GATE = RateGate(args.rate)
    log.info("keys=%d concurrency=%d rate=%d/min", len(_AUTHS), args.concurrency, args.rate)

    s3 = r2_client()
    end = args.to or (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()
    default_start = UNDERLYING_START if args.mode == "underlying" else OPTIONS_START
    start = args.start or default_start
    state_key = f"state/ivol_intraday_{args.mode}.json"
    state = r2_get_json(s3, state_key, {})

    exp_cal = build_exp_calendar(start, end) if args.mode == "options" else None
    days_all = sessions_desc(start, end)  # newest-first
    if args.limit_sessions:
        days_all = days_all[: args.limit_sessions]

    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        for ticker in args.tickers:
            tstate = state.setdefault(ticker, {})
            empty = set(tstate.get("empty", []))
            u_prefix = f"underlying_intraday/source=ivolatility/ticker={ticker}/"
            o_prefix = f"options_intraday/source=ivolatility/ticker={ticker}/"
            have_u = _have_dates(s3, u_prefix)
            have_o = _have_dates(s3, o_prefix) if args.mode == "options" else set()

            todo = [d for d in days_all
                    if (d not in have_o if args.mode == "options" else d not in have_u)
                    and d not in empty]
            log.info("%s [%s]: %d sessions todo (%s..%s), %d in R2, %d known-empty",
                     ticker, args.mode, len(todo), todo[-1] if todo else "-",
                     todo[0] if todo else "-",
                     len(have_o if args.mode == "options" else have_u), len(empty))

            done = 0
            for day in todo:
                try:
                    if args.mode == "underlying":
                        df = get_underlying(s3, ticker, day, have_u, empty, args.dry_run)
                        n = 0 if df is None else len(df)
                    else:
                        n = capture_option_session(s3, pool, ticker, day, exp_cal,
                                                   args.band, args.max_dte, have_u, empty, args.dry_run)
                        if n == 0:
                            empty.add(day)
                except Exception as exc:  # noqa: BLE001 — one bad day never kills the run
                    log.warning("%s %s failed: %s", ticker, day, _scrub(repr(exc)))
                    continue
                done += 1
                if n > 0:
                    log.info("%s %s: %d rows  [req=%d, %d/%d]",
                             ticker, day, n, _req_count[0], done, len(todo))
                if done % STATE_FLUSH_EVERY == 0:
                    tstate["empty"] = sorted(empty)
                    r2_put_json(s3, state_key, state)
                    log.info("… flushed state (%s %s, %d done, req=%d)",
                             ticker, args.mode, done, _req_count[0])
                if args.max_requests and _req_count[0] >= args.max_requests:
                    log.info("max-requests %d reached — stopping cleanly", args.max_requests)
                    tstate["empty"] = sorted(empty)
                    r2_put_json(s3, state_key, state)
                    return 0

            tstate["empty"] = sorted(empty)
            r2_put_json(s3, state_key, state)
            log.info("%s [%s] finished: %d sessions processed, req=%d",
                     ticker, args.mode, done, _req_count[0])
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mode", choices=("underlying", "options"), required=True)
    ap.add_argument("--key-env", dest="key_env", default="IVOL_API_KEY",
                    help="CSV of env vars holding apiKeys, rotated across requests")
    ap.add_argument("--tickers", default=None,
                    help="CSV; default SPY,IWM,QQQ (underlying) or SPY (options)")
    ap.add_argument("--from", dest="start", default=None)
    ap.add_argument("--to", dest="to", default=None)
    ap.add_argument("--band", type=int, default=DEFAULT_BAND, help="ATM ± N dollars")
    ap.add_argument("--max-dte", type=int, default=DEFAULT_MAX_DTE)
    ap.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)
    ap.add_argument("--rate", type=int, default=DEFAULT_RATE, help="aggregate req/min")
    ap.add_argument("--max-requests", type=int, default=0, help="safety cap; 0=unlimited")
    ap.add_argument("--limit-sessions", type=int, default=0, help="only newest N (verify)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if args.tickers:
        args.tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    else:
        args.tickers = ["SPY", "IWM", "QQQ"] if args.mode == "underlying" else ["SPY"]
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
