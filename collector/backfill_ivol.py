#!/usr/bin/env python3
"""
backfill_ivol.py — one-shot iVolatility EOD options backfill into the R2 lake.

Built for the Data Cloud API trial: run --probe the moment the key works,
then the full pull. Every (ticker, session) becomes one parquet object at

    options/source=ivolatility/ticker={T}/date={D}/chain.parquet

in the collector's canonical column layout, so the backend engine reads it
with zero changes (source precedence lives in backend/app/data/chains.py).

Endpoint: GET https://restapi.ivolatility.com/equities/eod/options-rawiv
          ?symbol=SPY&date=YYYY-MM-DD&region=USA  (+ apiKey or token)
Response: {"status": ..., "data": [{symbol, date, expiration, strike,
          "call/put", bid, ask, iv, delta, gamma, theta, vega, rho, volume,
          open_interest, stock_price_for_iv, option_symbol, ...}]}
(Schema from iVolatility's published OpenAPI spec, github.com/IVolatility-com.)

Usage:
  uv run python backfill_ivol.py --probe                # 1 day, validate key+schema
  uv run python backfill_ivol.py                        # full pull, all tickers
  uv run python backfill_ivol.py --tickers SPY --from 2005-01-03 --to 2019-12-31
  uv run python backfill_ivol.py --dry-run              # fetch+validate, no writes

Env (collector/.env): IVOL_API_KEY  — or IVOL_USERNAME + IVOL_PASSWORD —
plus the usual R2_* vars. Resumable: already-written dates are skipped via
an R2 listing; sessions the vendor has no rows for are remembered in
state/ivol_backfill.json so re-runs don't re-ask.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import requests

from collect import (  # the production collector owns the R2 conventions
    CANONICAL_COLUMNS,
    r2_client,
    r2_get_json,
    r2_put_json,
    r2_put_parquet,
)

log = logging.getLogger("ivol")

BASE = "https://restapi.ivolatility.com"
ENDPOINT = "/equities/eod/options-rawiv"
STATE_KEY = "state/ivol_backfill.json"
DEFAULT_TICKERS = ["SPY", "QQQ", "IWM"]
DEFAULT_FROM = "2005-01-03"  # the "20+ years" claim, probed honestly
PROBE_DATE = "2024-06-14"  # a boring, definitely-existing SPY session
REQUEST_TIMEOUT = 120
RETRIES = 4
STATE_FLUSH_EVERY = 50

# per-day sanity gates — a day failing these is logged and NOT written
MIN_ROWS = 50
MAX_CROSSED_FRACTION = 0.05


def _auth_params() -> dict[str, str]:
    key = os.environ.get("IVOL_API_KEY")
    if key:
        return {"apiKey": key}
    user, pw = os.environ.get("IVOL_USERNAME"), os.environ.get("IVOL_PASSWORD")
    if user and pw:
        resp = requests.get(
            f"{BASE}/token/get", params={"username": user, "password": pw}, timeout=30
        )
        resp.raise_for_status()
        token = resp.text.strip().strip('"')
        return {"token": token}
    sys.exit("set IVOL_API_KEY (or IVOL_USERNAME + IVOL_PASSWORD) in collector/.env")


def _fetch_day(auth: dict[str, str], ticker: str, day: str) -> list[dict] | None:
    """One session's raw rows, or None after retries (caller logs + skips)."""
    params = {"symbol": ticker, "date": day, "region": "USA", **auth}
    for attempt in range(RETRIES):
        try:
            resp = requests.get(f"{BASE}{ENDPOINT}", params=params, timeout=REQUEST_TIMEOUT)
            if resp.status_code in (429, 500, 502, 503, 504):
                raise requests.HTTPError(f"HTTP {resp.status_code}")
            resp.raise_for_status()
            body = resp.json()
            data = body.get("data") if isinstance(body, dict) else body
            return data if isinstance(data, list) else []
        except Exception as exc:  # noqa: BLE001 — retry then surface
            if attempt == RETRIES - 1:
                log.warning("%s %s: giving up after %s attempts (%s)", ticker, day, RETRIES, exc)
                return None
            time.sleep(2**attempt)
    return None


def _normalize(ticker: str, day: str, rows: list[dict]) -> pd.DataFrame:
    """Vendor rows → the collector's canonical chain layout."""
    df = pd.DataFrame(rows)
    right_raw = df.get("call/put", pd.Series(dtype=str)).astype(str).str.upper()
    out = pd.DataFrame(
        {
            "ticker": ticker,
            "trading_date": day,
            "snapshot_ts": f"{day}T21:00:00+00:00",  # nominal EOD mark
            "expiration": pd.to_datetime(df["expiration"]).dt.strftime("%Y-%m-%d"),
            "right": right_raw.map({"C": "call", "P": "put"}),
            "strike": pd.to_numeric(df["strike"], errors="coerce"),
            "bid": pd.to_numeric(df["bid"], errors="coerce"),
            "ask": pd.to_numeric(df["ask"], errors="coerce"),
            "last": pd.NA,
            "volume": pd.to_numeric(df.get("volume"), errors="coerce"),
            "open_interest": pd.to_numeric(df.get("open_interest"), errors="coerce"),
            "iv": pd.to_numeric(df.get("iv"), errors="coerce"),
            "delta": pd.to_numeric(df.get("delta"), errors="coerce"),
            "gamma": pd.to_numeric(df.get("gamma"), errors="coerce"),
            "theta": pd.to_numeric(df.get("theta"), errors="coerce"),
            "vega": pd.to_numeric(df.get("vega"), errors="coerce"),
            "rho": pd.to_numeric(df.get("rho"), errors="coerce"),
            "greeks_source": "vendor:ivolatility",
            "spot": pd.to_numeric(df.get("stock_price_for_iv"), errors="coerce"),
            "source": "ivolatility",
        }
    )
    out["dte"] = (
        pd.to_datetime(out["expiration"]) - pd.to_datetime(out["trading_date"])
    ).dt.days
    out = out.dropna(subset=["strike", "expiration", "right"])
    return out[CANONICAL_COLUMNS]


def _validate(ticker: str, day: str, df: pd.DataFrame) -> str | None:
    """Reason the day is unusable, or None when it passes."""
    if len(df) < MIN_ROWS:
        return f"only {len(df)} rows"
    quoted = df.dropna(subset=["bid", "ask"])
    if len(quoted):
        crossed = float((quoted["bid"] > quoted["ask"]).mean())
        if crossed > MAX_CROSSED_FRACTION:
            return f"{crossed:.1%} crossed quotes"
    bad_delta = df["delta"].dropna().abs().gt(1.0).mean()
    if bad_delta > 0:
        return f"{bad_delta:.1%} deltas outside [-1, 1]"
    if (df["dte"] < 0).any():
        return "expirations before the trading date"
    return None


def _existing_dates(s3, ticker: str) -> set[str]:
    import re as _re

    dates: set[str] = set()
    paginator = s3.get_paginator("list_objects_v2")
    prefix = f"options/source=ivolatility/ticker={ticker}/"
    for page in paginator.paginate(Bucket=os.environ["R2_BUCKET"], Prefix=prefix):
        for obj in page.get("Contents", []):
            if m := _re.search(r"date=(\d{4}-\d{2}-\d{2})", obj["Key"]):
                dates.add(m.group(1))
    return dates


def _weekdays(start: str, end: str) -> list[str]:
    d0, d1 = date.fromisoformat(start), date.fromisoformat(end)
    out = []
    d = d0
    while d <= d1:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def probe(auth: dict[str, str]) -> int:
    rows = _fetch_day(auth, "SPY", PROBE_DATE)
    if rows is None:
        print("PROBE FAILED — request errored (check the key / plan access)")
        return 1
    if not rows:
        print("PROBE FAILED — authenticated but zero rows for a known session")
        return 1
    df = _normalize("SPY", PROBE_DATE, rows)
    problem = _validate("SPY", PROBE_DATE, df)
    print(f"probe SPY {PROBE_DATE}: {len(df):,} contracts")
    with pd.option_context("display.width", 200):
        print(df[["expiration", "right", "strike", "bid", "ask", "delta", "iv"]].head(4))
    print("greeks coverage:", f"{df['delta'].notna().mean():.1%}")
    print("validation:", problem or "PASS")
    return 0 if problem is None else 1


def run(args: argparse.Namespace) -> int:
    auth = _auth_params()
    if args.probe:
        return probe(auth)

    s3 = r2_client()
    state = r2_get_json(s3, STATE_KEY, {"empty": {}})
    state_lock = threading.Lock()
    end = args.to or (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()

    for ticker in args.tickers:
        have = _existing_dates(s3, ticker)
        known_empty = set(state["empty"].get(ticker, []))
        todo = [d for d in _weekdays(args.start, end) if d not in have and d not in known_empty]
        log.info("%s: %s sessions to fetch (%s already in R2, %s known-empty)",
                 ticker, len(todo), len(have), len(known_empty))
        done_count = 0

        def one(day: str, _t: str = ticker) -> None:
            nonlocal done_count
            rows = _fetch_day(auth, _t, day)
            if rows is None:
                return  # errored after retries — a re-run picks it up
            if not rows:
                with state_lock:
                    state["empty"].setdefault(_t, []).append(day)
                return
            df = _normalize(_t, day, rows)
            problem = _validate(_t, day, df)
            if problem:
                log.warning("%s %s REJECTED: %s", _t, day, problem)
                return
            if not args.dry_run:
                key = f"options/source=ivolatility/ticker={_t}/date={day}/chain.parquet"
                r2_put_parquet(s3, key, df)
            with state_lock:
                done_count += 1
                if done_count % STATE_FLUSH_EVERY == 0:
                    r2_put_json(s3, STATE_KEY, state)
                    log.info("%s: %s/%s done", _t, done_count, len(todo))

        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            list(pool.map(one, todo))
        r2_put_json(s3, STATE_KEY, state)
        log.info("%s: finished — %s sessions written", ticker, done_count)
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tickers", default=",".join(DEFAULT_TICKERS))
    ap.add_argument("--from", dest="start", default=DEFAULT_FROM)
    ap.add_argument("--to", dest="to", default=None)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--probe", action="store_true", help="one known day, validate key+schema")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    args.tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
