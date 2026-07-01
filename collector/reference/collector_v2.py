#!/usr/bin/env python3
"""
collector_v2.py — Skeptic data pipeline, reference implementation.

Reference for Claude Code to productionize per docs/DATA-PIPELINE.md.
Three modes:
  --mode eod       Capture today's EOD record (Alpha Vantage) + Yahoo
                   redundancy snapshot + underlying/VIX append, for all tickers.
  --mode backfill  Spend remaining Alpha Vantage budget walking backward
                   through history from the persisted frontier.
  --mode all       eod then backfill (the nightly GitHub Actions entrypoint).

Design points (see DATA-PIPELINE.md):
  * Alpha Vantage HISTORICAL_OPTIONS = EOD source of record (full chain, all
    expirations, IV + greeks included, history to ~2008). 25 req/day free.
  * yfinance = redundancy today, intraday later. Failures are non-fatal.
  * Everything lands in Cloudflare R2 (S3-compatible) as Parquet under the
    canonical layout. State (backfill frontier, AV budget) lives in R2 too,
    so GitHub Actions runners stay stateless.
  * Ends with a Healthchecks.io ping so silent death gets noticed.

Env vars required:
  ALPHAVANTAGE_API_KEY, R2_ACCOUNT_ID, R2_ACCESS_KEY_ID,
  R2_SECRET_ACCESS_KEY, R2_BUCKET, HEALTHCHECK_URL (optional)

Deps: pip install pandas pyarrow boto3 requests yfinance exchange_calendars
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
import time
from datetime import date, datetime, timezone

import boto3
import pandas as pd
import requests

TICKERS = ["SPY", "QQQ", "IWM"]
BACKFILL_PRIORITY = ["SPY", "QQQ", "IWM"]  # SPY to exhaustion first
AV_DAILY_BUDGET = 25
AV_PACING_SECONDS = 13          # ~5 req/min, stay polite
AV_HISTORY_FLOOR = date(2008, 1, 2)
CANONICAL_COLUMNS = [
    "ticker", "trading_date", "snapshot_ts", "expiration", "dte", "right",
    "strike", "bid", "ask", "last", "volume", "open_interest", "iv",
    "delta", "gamma", "theta", "vega", "rho", "greeks_source", "spot", "source",
]


# ----------------------------- R2 helpers ---------------------------------

def r2_client():
    account = os.environ["R2_ACCOUNT_ID"]
    return boto3.client(
        "s3",
        endpoint_url=f"https://{account}.r2.cloudflarestorage.com",
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
        region_name="auto",
    )


def r2_put_parquet(s3, key: str, df: pd.DataFrame) -> None:
    buf = io.BytesIO()
    df.to_parquet(buf, index=False)
    s3.put_object(Bucket=os.environ["R2_BUCKET"], Key=key, Body=buf.getvalue())
    print(f"  wrote r2://{key}  ({len(df):,} rows)")


def r2_get_json(s3, key: str, default):
    try:
        obj = s3.get_object(Bucket=os.environ["R2_BUCKET"], Key=key)
        return json.loads(obj["Body"].read())
    except s3.exceptions.NoSuchKey:
        return default


def r2_put_json(s3, key: str, payload) -> None:
    s3.put_object(Bucket=os.environ["R2_BUCKET"], Key=key,
                  Body=json.dumps(payload).encode())


def r2_exists(s3, key: str) -> bool:
    try:
        s3.head_object(Bucket=os.environ["R2_BUCKET"], Key=key)
        return True
    except Exception:
        return False


# ------------------------- Alpha Vantage source -----------------------------

class AvBudget:
    """Hard wall on the 25/day free quota, persisted in R2 (stateless runners)."""

    def __init__(self, s3):
        self.s3 = s3
        self.key = "state/av_budget.json"
        today = date.today().isoformat()
        state = r2_get_json(s3, self.key, {"date": today, "used": 0})
        if state.get("date") != today:
            state = {"date": today, "used": 0}
        self.state = state

    @property
    def remaining(self) -> int:
        return AV_DAILY_BUDGET - self.state["used"]

    def spend(self, n: int = 1) -> None:
        self.state["used"] += n
        r2_put_json(self.s3, self.key, self.state)


def av_fetch_chain(ticker: str, trading_date: date | None) -> pd.DataFrame:
    """One AV request = full EOD chain (all expirations) for one ticker-date."""
    params = {
        "function": "HISTORICAL_OPTIONS",
        "symbol": ticker,
        "apikey": os.environ["ALPHAVANTAGE_API_KEY"],
    }
    if trading_date is not None:
        params["date"] = trading_date.isoformat()
    resp = requests.get("https://www.alphavantage.co/query", params=params, timeout=60)
    resp.raise_for_status()
    payload = resp.json()
    rows = payload.get("data") or []
    if not rows:
        # Legit for holidays / pre-listing dates; also how AV signals throttling.
        note = payload.get("Note") or payload.get("Information") or ""
        if note:
            raise RuntimeError(f"AV non-data response: {note[:120]}")
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    eff_date = pd.to_datetime(df["date"].iloc[0]).date()
    out = pd.DataFrame({
        "ticker": ticker,
        "trading_date": eff_date.isoformat(),
        "snapshot_ts": f"{eff_date.isoformat()}T21:00:00Z",  # session close, UTC approx
        "expiration": df["expiration"],
        "right": df["type"].str.lower(),
        "strike": pd.to_numeric(df["strike"], errors="coerce"),
        "bid": pd.to_numeric(df["bid"], errors="coerce"),
        "ask": pd.to_numeric(df["ask"], errors="coerce"),
        "last": pd.to_numeric(df["last"], errors="coerce"),
        "volume": pd.to_numeric(df["volume"], errors="coerce"),
        "open_interest": pd.to_numeric(df["open_interest"], errors="coerce"),
        "iv": pd.to_numeric(df["implied_volatility"], errors="coerce"),
        "delta": pd.to_numeric(df["delta"], errors="coerce"),
        "gamma": pd.to_numeric(df["gamma"], errors="coerce"),
        "theta": pd.to_numeric(df["theta"], errors="coerce"),
        "vega": pd.to_numeric(df["vega"], errors="coerce"),
        "rho": pd.to_numeric(df["rho"], errors="coerce"),
        "greeks_source": "vendor",
        "spot": None,   # backfilled from underlying dailies at query time
        "source": "alphavantage",
    })
    out["dte"] = (pd.to_datetime(out["expiration"]) - pd.Timestamp(eff_date)).dt.days
    return out[CANONICAL_COLUMNS]


# ----------------------------- Yahoo source --------------------------------

def yahoo_snapshot(ticker: str, max_dte: int = 60) -> pd.DataFrame:
    """Live chain snapshot. Non-fatal by design; AV is the record."""
    import yfinance as yf  # local import: optional leg

    tk = yf.Ticker(ticker)
    now = datetime.now(timezone.utc)
    try:
        spot = float(tk.fast_info.get("last_price") or tk.fast_info.get("previous_close"))
    except Exception:
        spot = None
    frames = []
    for exp in list(tk.options or []):
        exp_d = datetime.strptime(exp, "%Y-%m-%d").date()
        dte = (exp_d - now.date()).days
        if not (0 <= dte <= max_dte):
            continue
        try:
            chain = tk.option_chain(exp)
        except Exception as exc:
            print(f"  [yahoo:{ticker}] {exp} failed: {exc}")
            time.sleep(1)
            continue
        for right, raw in (("call", chain.calls), ("put", chain.puts)):
            if raw is None or raw.empty:
                continue
            frames.append(pd.DataFrame({
                "ticker": ticker,
                "trading_date": now.date().isoformat(),
                "snapshot_ts": now.isoformat(),
                "expiration": exp,
                "dte": dte,
                "right": right,
                "strike": raw["strike"],
                "bid": raw.get("bid"),
                "ask": raw.get("ask"),
                "last": raw.get("lastPrice"),
                "volume": raw.get("volume"),
                "open_interest": raw.get("openInterest"),
                "iv": raw.get("impliedVolatility"),
                "delta": None, "gamma": None, "theta": None, "vega": None, "rho": None,
                "greeks_source": "computed",  # computed at ingest by backend
                "spot": spot,
                "source": "yahoo",
            }))
        time.sleep(1)
    return pd.concat(frames, ignore_index=True)[CANONICAL_COLUMNS] if frames else pd.DataFrame()


def refresh_underlying_and_vix(s3) -> None:
    """Daily OHLCV for tickers + ^VIX, full-history download (cheap, idempotent)."""
    import yfinance as yf

    for symbol, key in [*[(t, f"underlying/ticker={t}/daily.parquet") for t in TICKERS],
                        ("^VIX", "reference/vix_daily.parquet")]:
        try:
            df = yf.download(symbol, period="max", interval="1d",
                             auto_adjust=False, progress=False)
            if df.empty:
                print(f"  [underlying:{symbol}] empty download, skipped")
                continue
            df = df.reset_index()
            df.columns = [str(c[0]) if isinstance(c, tuple) else str(c) for c in df.columns]
            df["symbol"] = symbol
            r2_put_parquet(s3, key, df)
        except Exception as exc:
            print(f"  [underlying:{symbol}] failed (non-fatal): {exc}")


# ------------------------------- modes -------------------------------------

def run_eod(s3, budget: AvBudget) -> None:
    print("== EOD record (Alpha Vantage) ==")
    for ticker in TICKERS:
        if budget.remaining <= 0:
            print("  AV budget exhausted, record incomplete today")
            break
        df = av_fetch_chain(ticker, trading_date=None)  # None = latest session
        budget.spend()
        if df.empty:
            print(f"  [av:{ticker}] no data (holiday?)")
        else:
            d = df["trading_date"].iloc[0]
            key = f"options/source=alphavantage/ticker={ticker}/date={d}/chain.parquet"
            if r2_exists(s3, key):
                print(f"  [av:{ticker}] {d} already recorded (catch-up run), skipped")
            else:
                r2_put_parquet(s3, key, df)
        time.sleep(AV_PACING_SECONDS)

    print("== Yahoo redundancy snapshot ==")
    for ticker in TICKERS:
        try:
            df = yahoo_snapshot(ticker)
            if df.empty:
                print(f"  [yahoo:{ticker}] empty, non-fatal")
                continue
            ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%MZ")
            d = df["trading_date"].iloc[0]
            key = f"options/source=yahoo/ticker={ticker}/date={d}/snap_{ts}.parquet"
            r2_put_parquet(s3, key, df)
        except Exception as exc:
            print(f"  [yahoo:{ticker}] failed, non-fatal: {exc}")

    print("== Underlying + VIX refresh ==")
    refresh_underlying_and_vix(s3)


def run_backfill(s3, budget: AvBudget) -> None:
    """Walk backward through history with whatever AV budget remains."""
    import exchange_calendars as xcals

    cal = xcals.get_calendar("XNYS")
    frontier = r2_get_json(s3, "state/backfill_frontier.json",
                           {t: date.today().isoformat() for t in TICKERS})
    print(f"== Backfill drip (budget remaining: {budget.remaining}) ==")

    for ticker in BACKFILL_PRIORITY:
        while budget.remaining > 0:
            current = date.fromisoformat(frontier[ticker])
            prev = cal.previous_session(pd.Timestamp(current)).date()
            if prev < AV_HISTORY_FLOOR:
                print(f"  [{ticker}] backfill COMPLETE to {AV_HISTORY_FLOOR}")
                break
            key = f"options/source=alphavantage/ticker={ticker}/date={prev}/chain.parquet"
            if not r2_exists(s3, key):
                try:
                    df = av_fetch_chain(ticker, trading_date=prev)
                    budget.spend()
                    if not df.empty:
                        r2_put_parquet(s3, key, df)
                    time.sleep(AV_PACING_SECONDS)
                except RuntimeError as exc:
                    print(f"  [{ticker}] {prev}: {exc} — stopping for today")
                    return
            frontier[ticker] = prev.isoformat()
            r2_put_json(s3, "state/backfill_frontier.json", frontier)
        else:
            return  # budget gone mid-ticker
        # ticker complete → fall through to next priority ticker


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["eod", "backfill", "all"], default="all")
    args = ap.parse_args()

    s3 = r2_client()
    budget = AvBudget(s3)
    ok = True
    try:
        if args.mode in ("eod", "all"):
            run_eod(s3, budget)
        if args.mode in ("backfill", "all"):
            run_backfill(s3, budget)
    except Exception as exc:
        ok = False
        print(f"FATAL: {exc}", file=sys.stderr)
    finally:
        hc = os.environ.get("HEALTHCHECK_URL")
        if hc:
            try:
                requests.get(hc if ok else hc.rstrip("/") + "/fail", timeout=10)
            except Exception:
                pass
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
