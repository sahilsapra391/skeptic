#!/usr/bin/env python3
"""
collect.py — Skeptic data pipeline collector (production).

Productionized from reference/collector_v2.py per docs/DATA-PIPELINE.md.

Modes:
  --mode eod        Capture the Alpha Vantage EOD record for the last few
                    completed NYSE sessions (explicit dates, self-healing
                    lookback) + Yahoo redundancy snapshot + underlying/VIX
                    refresh, for all tickers.
  --mode backfill   Spend remaining Alpha Vantage budget walking backward
                    through history from the persisted frontier.
  --mode underlying Refresh underlying OHLCV + VIX only. Downloads full
                    history every time, so this doubles as the one-time
                    deep backfill.
  --mode quality    Data-quality scan of the lake (DATA-PIPELINE.md §6);
                    writes state/quality_flags.json.
  --mode all        eod then backfill (the nightly GitHub Actions entrypoint).

Env vars required: ALPHAVANTAGE_API_KEY, R2_ACCOUNT_ID, R2_ACCESS_KEY_ID,
R2_SECRET_ACCESS_KEY, R2_BUCKET. Optional: HEALTHCHECK_URL (pinged on
success, <url>/fail on failure, for eod/backfill/all runs).

Data goes to R2 only. Never commit data to git.
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import os
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone

import boto3
import pandas as pd
import requests

log = logging.getLogger("collector")

TICKERS = ["SPY", "QQQ", "IWM"]
BACKFILL_PRIORITY = ["SPY", "QQQ", "IWM"]  # SPY to exhaustion first
AV_DAILY_BUDGET = 25
AV_PACING_SECONDS = 13  # ~5 req/min, stay polite
AV_HISTORY_FLOOR = date(2008, 1, 2)
EOD_LOOKBACK_SESSIONS = 3  # self-healing window for the forward record
YAHOO_MAX_DTE = 60
QUALITY_SCAN_SESSIONS = 10
NULL_QUOTE_FLAG = 0.20  # DATA-PIPELINE §6
CROSSED_FLAG = 0.01
SPOT_DRIFT_FLAG = 0.005

CANONICAL_COLUMNS = [
    "ticker", "trading_date", "snapshot_ts", "expiration", "dte", "right",
    "strike", "bid", "ask", "last", "volume", "open_interest", "iv",
    "delta", "gamma", "theta", "vega", "rho", "greeks_source", "spot", "source",
]

STOOQ_SYMBOLS = {"SPY": "spy.us", "QQQ": "qqq.us", "IWM": "iwm.us", "^VIX": "^vix"}

FRONTIER_KEY = "state/backfill_frontier.json"
BUDGET_KEY = "state/av_budget.json"
QUALITY_KEY = "state/quality_flags.json"


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
    log.info("wrote r2://%s (%s rows)", key, f"{len(df):,}")


def r2_get_parquet(s3, key: str,
                   columns: list[str] | None = None) -> pd.DataFrame | None:
    """`columns` projects at decode time — the bytes still download whole,
    so for GB-scale objects prefer r2_get_parquet_spooled."""
    try:
        obj = s3.get_object(Bucket=os.environ["R2_BUCKET"], Key=key)
        return pd.read_parquet(io.BytesIO(obj["Body"].read()), columns=columns)
    except Exception:
        return None


def r2_get_parquet_spooled(s3, key: str,
                           columns: list[str] | None = None
                           ) -> pd.DataFrame | None:
    """Stream the object to a temp file, then column-project the read —
    the full multi-hundred-MB byte buffer never resides in memory next to
    the decoded frame (the OOM rule for tape-scale objects)."""
    import tempfile

    try:
        with tempfile.NamedTemporaryFile(suffix=".parquet") as tmp:
            s3.download_fileobj(os.environ["R2_BUCKET"], key, tmp)
            tmp.flush()
            return pd.read_parquet(tmp.name, columns=columns)
    except Exception:
        return None


def r2_get_json(s3, key: str, default):
    try:
        obj = s3.get_object(Bucket=os.environ["R2_BUCKET"], Key=key)
        return json.loads(obj["Body"].read())
    except s3.exceptions.NoSuchKey:
        return default


def r2_put_json(s3, key: str, payload) -> None:
    s3.put_object(Bucket=os.environ["R2_BUCKET"], Key=key,
                  Body=json.dumps(payload, indent=2).encode())


def r2_exists(s3, key: str) -> bool:
    try:
        s3.head_object(Bucket=os.environ["R2_BUCKET"], Key=key)
        return True
    except Exception:
        return False


def r2_list_keys(s3, prefix: str) -> list[str]:
    keys: list[str] = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=os.environ["R2_BUCKET"], Prefix=prefix):
        keys.extend(item["Key"] for item in page.get("Contents", []))
    return keys


def av_chain_key(ticker: str, d: date | str) -> str:
    d = d.isoformat() if isinstance(d, date) else d
    return f"options/source=alphavantage/ticker={ticker}/date={d}/chain.parquet"


def list_chain_dates(s3, source: str, ticker: str) -> list[str]:
    """Sorted ISO dates that have at least one chain object for (source, ticker)."""
    prefix = f"options/source={source}/ticker={ticker}/"
    dates = set()
    for key in r2_list_keys(s3, prefix):
        m = re.search(r"date=(\d{4}-\d{2}-\d{2})", key)
        if m:
            dates.add(m.group(1))
    return sorted(dates)


def list_date_prefixes(s3, prefix: str) -> list[str]:
    """Sorted ISO dates under date=YYYY-MM-DD/ sub-prefixes (cheap Delimiter
    listing — mirrors backend app/data/r2.py; the shared home so derive
    scripts stop growing private copies)."""
    dates: list[str] = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=os.environ["R2_BUCKET"],
                                   Prefix=prefix, Delimiter="/"):
        for cp in page.get("CommonPrefixes", []):
            m = re.search(r"date=(\d{4}-\d{2}-\d{2})", cp["Prefix"])
            if m:
                dates.append(m.group(1))
    return sorted(dates)


# ----------------------------- NYSE calendar -------------------------------

_CAL = None


def nyse():
    global _CAL
    if _CAL is None:
        import exchange_calendars as xcals
        _CAL = xcals.get_calendar("XNYS")
    return _CAL


def completed_sessions(now_utc: datetime, k: int) -> list[date]:
    """The last k NYSE sessions whose close is at or before now."""
    cal = nyse()
    start = pd.Timestamp(now_utc.date() - timedelta(days=30))
    end = pd.Timestamp(now_utc.date())
    out = []
    for s in cal.sessions_in_range(start, end):
        if cal.session_close(s) <= pd.Timestamp(now_utc):
            out.append(pd.Timestamp(s).date())
    return out[-k:]


def previous_session_before(d: date) -> date | None:
    """The NYSE session immediately before d (which need not be a session)."""
    cal = nyse()
    window = cal.sessions_in_range(pd.Timestamp(d - timedelta(days=20)), pd.Timestamp(d))
    prior = [pd.Timestamp(s).date() for s in window if pd.Timestamp(s).date() < d]
    return prior[-1] if prior else None


# ------------------------- Alpha Vantage source -----------------------------

class AvBudget:
    """Hard wall on the 25/day free quota, persisted in R2 (stateless runners)."""

    def __init__(self, s3):
        self.s3 = s3
        today = date.today().isoformat()
        state = r2_get_json(s3, BUDGET_KEY, {"date": today, "used": 0})
        if state.get("date") != today:
            state = {"date": today, "used": 0}
        self.state = state

    @property
    def remaining(self) -> int:
        return AV_DAILY_BUDGET - self.state["used"]

    def spend(self, n: int = 1) -> None:
        self.state["used"] += n
        r2_put_json(self.s3, BUDGET_KEY, self.state)


def av_fetch_chain(ticker: str, trading_date: date) -> pd.DataFrame:
    """One AV request = full EOD chain (all expirations) for one ticker-date."""
    params = {
        "function": "HISTORICAL_OPTIONS",
        "symbol": ticker,
        "date": trading_date.isoformat(),
        "apikey": os.environ["ALPHAVANTAGE_API_KEY"],
    }
    resp = requests.get("https://www.alphavantage.co/query", params=params, timeout=60)
    resp.raise_for_status()
    payload = resp.json()
    rows = payload.get("data") or []
    if not rows:
        # Empty is legit (pre-finalization, pre-listing); a Note/Information
        # body is how AV signals throttling — surface it.
        note = payload.get("Note") or payload.get("Information") or ""
        if note:
            raise RuntimeError(f"AV non-data response: {note[:160]}")
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
        "spot": None,  # joined from underlying dailies at query time (TECH-SPEC §4)
        "source": "alphavantage",
    })
    out["dte"] = (pd.to_datetime(out["expiration"]) - pd.Timestamp(eff_date)).dt.days
    return out[CANONICAL_COLUMNS]


# ----------------------------- Yahoo source --------------------------------

def yahoo_snapshot(ticker: str, max_dte: int = YAHOO_MAX_DTE,
                   deadline: float | None = None) -> pd.DataFrame:
    """Live chain snapshot. Non-fatal by design; AV is the record.

    `deadline` is a time.monotonic() value: once past it the expiration crawl
    truncates (partial snapshot, disclosed in the log). The intraday recorder
    uses it to bound a leg's wall time; the nightly EOD path passes nothing
    and crawls everything, unchanged."""
    import yfinance as yf  # local import: optional leg

    tk = yf.Ticker(ticker)
    now = datetime.now(timezone.utc)
    try:
        spot = float(tk.fast_info.get("last_price") or tk.fast_info.get("previous_close"))
    except Exception:
        spot = None
    try:
        expirations = list(tk.options or [])
    except Exception as exc:
        log.warning("[yahoo:%s] could not list expirations: %s", ticker, exc)
        return pd.DataFrame()

    frames = []
    for n_seen, exp in enumerate(expirations):
        if deadline is not None and time.monotonic() >= deadline:
            log.warning("[yahoo:%s] leg budget exhausted; truncating with %d of %d "
                        "expirations unfetched", ticker, len(expirations) - n_seen,
                        len(expirations))
            break
        exp_d = datetime.strptime(exp, "%Y-%m-%d").date()
        dte = (exp_d - now.date()).days
        if not (0 <= dte <= max_dte):
            continue
        chain = None
        for attempt in range(3):  # yfinance hardening per DATA-PIPELINE §5
            try:
                chain = tk.option_chain(exp)
                break
            except Exception as exc:
                log.warning("[yahoo:%s] %s attempt %d failed: %s", ticker, exp, attempt + 1, exc)
                time.sleep(2 * (attempt + 1))
        if chain is None:
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
                "greeks_source": "computed",  # greeks computed at ingest by the backend
                "spot": spot,
                "source": "yahoo",
            }))
        time.sleep(1)
    return pd.concat(frames, ignore_index=True)[CANONICAL_COLUMNS] if frames else pd.DataFrame()


# ------------------------ Underlying + VIX dailies --------------------------

def fetch_underlying(symbol: str) -> pd.DataFrame:
    """Full-history daily OHLCV via yfinance, Stooq as fallback."""
    try:
        import yfinance as yf
        df = yf.download(symbol, period="max", interval="1d",
                         auto_adjust=False, progress=False)
        if df is not None and not df.empty:
            df = df.reset_index()
            df.columns = [str(c[0]) if isinstance(c, tuple) else str(c) for c in df.columns]
            df = df.rename(columns={
                "Date": "date", "Open": "open", "High": "high", "Low": "low",
                "Close": "close", "Adj Close": "adj_close", "Volume": "volume",
            })
            if "adj_close" not in df.columns:
                df["adj_close"] = df["close"]
            df["date"] = pd.to_datetime(df["date"])
            df["symbol"] = symbol
            return df[["date", "open", "high", "low", "close", "adj_close", "volume", "symbol"]]
    except Exception as exc:
        log.warning("[underlying:%s] yfinance failed: %s", symbol, exc)

    stooq = STOOQ_SYMBOLS.get(symbol)
    if stooq:
        try:
            resp = requests.get("https://stooq.com/q/d/l/",
                                params={"s": stooq, "i": "d"}, timeout=60)
            resp.raise_for_status()
            df = pd.read_csv(io.StringIO(resp.text))
            if not df.empty and "Date" in df.columns:
                df = df.rename(columns={
                    "Date": "date", "Open": "open", "High": "high",
                    "Low": "low", "Close": "close", "Volume": "volume",
                })
                df["adj_close"] = df["close"]  # Stooq serves unadjusted only
                df["date"] = pd.to_datetime(df["date"])
                df["symbol"] = symbol
                if "volume" not in df.columns:
                    df["volume"] = None
                log.info("[underlying:%s] served by Stooq fallback", symbol)
                return df[["date", "open", "high", "low", "close", "adj_close", "volume", "symbol"]]
        except Exception as exc:
            log.warning("[underlying:%s] stooq fallback failed: %s", symbol, exc)
    return pd.DataFrame()


def fetch_fred_dgs3mo() -> pd.DataFrame:
    """3-month T-bill yield from FRED (free CSV, no key) — the risk-free rate
    for the backend's computed Black-Scholes greeks (DATA-PIPELINE §4)."""
    resp = requests.get("https://fred.stlouisfed.org/graph/fredgraph.csv",
                        params={"id": "DGS3MO"}, timeout=60)
    resp.raise_for_status()
    df = pd.read_csv(io.StringIO(resp.text))
    if df.empty or len(df.columns) != 2:
        return pd.DataFrame()
    df.columns = ["date", "rate_pct"]  # header name varies (DATE/observation_date)
    df["rate_pct"] = pd.to_numeric(df["rate_pct"], errors="coerce")  # '.' = missing
    df = df.dropna(subset=["rate_pct"])
    df["date"] = pd.to_datetime(df["date"])
    return df[["date", "rate_pct"]]


def refresh_underlying_and_vix(s3) -> None:
    """Daily OHLCV for tickers + ^VIX + risk-free rate. Full-history
    overwrite: cheap, idempotent."""
    targets = [(t, f"underlying/ticker={t}/daily.parquet") for t in TICKERS]
    targets.append(("^VIX", "reference/vix_daily.parquet"))
    for symbol, key in targets:
        df = fetch_underlying(symbol)
        if df.empty:
            log.warning("[underlying:%s] no data from any source, skipped (non-fatal)", symbol)
            continue
        r2_put_parquet(s3, key, df)
    try:
        rates = fetch_fred_dgs3mo()
        if rates.empty:
            log.warning("[rates] FRED DGS3MO returned no data, skipped (non-fatal)")
        else:
            r2_put_parquet(s3, "reference/rates_dgs3mo.parquet", rates)
    except Exception as exc:
        log.warning("[rates] FRED DGS3MO fetch failed, non-fatal: %s", exc)


# ------------------------------- modes -------------------------------------

def run_eod(s3, budget: AvBudget) -> bool:
    """Capture the AV record for recent sessions + Yahoo redundancy + dailies.

    Returns True if the AV record is complete, treating the very latest
    session as optional (vendors finalize EOD data with a lag; the catch-up
    cron and tomorrow's lookback will pick it up).
    """
    now = datetime.now(timezone.utc)
    sessions = completed_sessions(now, EOD_LOOKBACK_SESSIONS)
    latest = sessions[-1]
    log.info("== EOD record (Alpha Vantage), target sessions: %s ==",
             [d.isoformat() for d in sessions])

    targets = [(t, d) for t in TICKERS for d in sessions
               if not r2_exists(s3, av_chain_key(t, d))]
    if not targets:
        log.info("AV record already complete (catch-up no-op)")
    av_ok = True
    av_unavailable = False
    for ticker, d in targets:
        if budget.remaining <= 0:
            log.warning("AV budget exhausted; record incomplete today")
            av_ok = False
            break
        try:
            df = av_fetch_chain(ticker, d)
            budget.spend()
        except (RuntimeError, requests.RequestException) as exc:
            if "premium" in str(exc).lower():
                log.error("[av:%s] HISTORICAL_OPTIONS is premium-gated on this "
                          "key; AV leg skipped. Data-strategy decision pending "
                          "(docs/BUILD-LOG.md, M1 addendum).", ticker)
                av_unavailable = True
            else:
                log.warning("[av:%s] %s: %s — stopping AV leg", ticker, d, exc)
            av_ok = False
            break
        if df.empty:
            if d == latest:
                log.info("[av:%s] %s not published yet (normal before vendor finalization)",
                         ticker, d)
            else:
                log.warning("[av:%s] %s returned no data", ticker, d)
                av_ok = False
        else:
            r2_put_parquet(s3, av_chain_key(ticker, df["trading_date"].iloc[0]), df)
        time.sleep(AV_PACING_SECONDS)

    log.info("== Yahoo redundancy snapshot ==")
    yahoo_written = 0
    for ticker in TICKERS:
        try:
            df = yahoo_snapshot(ticker)
            if df.empty:
                log.info("[yahoo:%s] empty snapshot, non-fatal", ticker)
                continue
            ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%MZ")
            d = df["trading_date"].iloc[0]
            key = f"options/source=yahoo/ticker={ticker}/date={d}/snap_{ts}.parquet"
            r2_put_parquet(s3, key, df)
            yahoo_written += 1
        except Exception as exc:
            log.warning("[yahoo:%s] failed, non-fatal: %s", ticker, exc)

    log.info("== Underlying + VIX refresh ==")
    refresh_underlying_and_vix(s3)
    if av_unavailable:
        # Interim posture while the data-strategy decision is pending: a
        # premium-gated AV key is a known condition, not a nightly incident.
        # The run is healthy if the Yahoo leg fully covered the day.
        return yahoo_written == len(TICKERS)
    return av_ok


def run_backfill(s3, budget: AvBudget, limit: int | None = None) -> None:
    """Walk backward through history with whatever AV budget remains."""
    now = datetime.now(timezone.utc)
    frontier = r2_get_json(s3, FRONTIER_KEY,
                           {t: date.today().isoformat() for t in TICKERS})
    # Protect the forward record: keep budget in reserve for any EOD targets
    # still missing (matters when backfill runs before/without eod).
    sessions = completed_sessions(now, EOD_LOOKBACK_SESSIONS)
    reserve = sum(1 for t in TICKERS for d in sessions
                  if not r2_exists(s3, av_chain_key(t, d)))
    fetched = 0
    log.info("== Backfill drip (budget=%d, eod reserve=%d, limit=%s) ==",
             budget.remaining, reserve, limit)

    for ticker in BACKFILL_PRIORITY:
        while True:
            if budget.remaining <= reserve:
                log.info("backfill stopping: budget down to eod reserve (%d)", reserve)
                return
            if limit is not None and fetched >= limit:
                log.info("backfill stopping: --backfill-limit %d reached", limit)
                return
            current = date.fromisoformat(frontier[ticker])
            prev = previous_session_before(current)
            if prev is None or prev < AV_HISTORY_FLOOR:
                log.info("[%s] backfill COMPLETE to %s", ticker, AV_HISTORY_FLOOR)
                break  # next priority ticker
            key = av_chain_key(ticker, prev)
            if not r2_exists(s3, key):
                try:
                    df = av_fetch_chain(ticker, prev)
                    budget.spend()
                    fetched += 1
                except (RuntimeError, requests.RequestException) as exc:
                    if "premium" in str(exc).lower():
                        log.error("[%s] backfill unavailable: HISTORICAL_OPTIONS "
                                  "is premium-gated on this key", ticker)
                    else:
                        log.warning("[%s] %s: %s — stopping backfill for today",
                                    ticker, prev, exc)
                    return
                if df.empty:
                    log.warning("[%s] %s returned no data; advancing frontier", ticker, prev)
                else:
                    r2_put_parquet(s3, key, df)
                time.sleep(AV_PACING_SECONDS)
            frontier[ticker] = prev.isoformat()
            r2_put_json(s3, FRONTIER_KEY, frontier)  # crash-safe: state per date


def run_quality(s3) -> dict:
    """Cross-source and internal sanity flags per DATA-PIPELINE §6."""
    now = datetime.now(timezone.utc)
    cal = nyse()
    report: dict = {"generated_at": now.isoformat(), "tickers": {}}
    frontier = r2_get_json(s3, FRONTIER_KEY, {})

    for ticker in TICKERS:
        entry: dict = {}
        av_dates = list_chain_dates(s3, "alphavantage", ticker)
        entry["av_sessions"] = len(av_dates)
        if av_dates:
            lo, hi = av_dates[0], av_dates[-1]
            entry["av_range"] = [lo, hi]
            expected = {pd.Timestamp(s).date().isoformat()
                        for s in cal.sessions_in_range(pd.Timestamp(lo), pd.Timestamp(hi))}
            missing = sorted(expected - set(av_dates))
            entry["missing_sessions_count"] = len(missing)
            entry["missing_sessions_recent"] = missing[-10:]
            entry["flag_missing_sessions"] = len(missing) > 0

            nulls = crossed = rows = 0
            for d in av_dates[-QUALITY_SCAN_SESSIONS:]:
                df = r2_get_parquet(s3, av_chain_key(ticker, d))
                if df is None or df.empty:
                    continue
                rows += len(df)
                nulls += int(((df["bid"].isna() | (df["bid"] == 0))
                              & (df["ask"].isna() | (df["ask"] == 0))).sum())
                both = df.dropna(subset=["bid", "ask"])
                crossed += int((both["bid"] > both["ask"]).sum())
            if rows:
                entry["scanned_sessions"] = min(len(av_dates), QUALITY_SCAN_SESSIONS)
                entry["null_quote_rate"] = round(nulls / rows, 4)
                entry["flag_null_quotes"] = (nulls / rows) > NULL_QUOTE_FLAG
                entry["crossed_rate"] = round(crossed / rows, 4)
                entry["flag_crossed"] = (crossed / rows) > CROSSED_FLAG

        # Spot drift: Yahoo snapshot spot vs underlying daily close for the
        # same session (AV chains carry no spot by design; see TECH-SPEC §4).
        try:
            ydates = list_chain_dates(s3, "yahoo", ticker)
            if ydates:
                yd = ydates[-1]
                snaps = sorted(r2_list_keys(
                    s3, f"options/source=yahoo/ticker={ticker}/date={yd}/"))
                ydf = r2_get_parquet(s3, snaps[-1]) if snaps else None
                udf = r2_get_parquet(s3, f"underlying/ticker={ticker}/daily.parquet")
                if ydf is not None and udf is not None and ydf["spot"].notna().any():
                    yspot = float(ydf["spot"].dropna().iloc[0])
                    row = udf[udf["date"] == pd.Timestamp(yd)]
                    if not row.empty:
                        close = float(row["close"].iloc[0])
                        drift = abs(yspot - close) / close
                        entry["spot_drift_vs_underlying"] = round(drift, 5)
                        entry["flag_spot_drift"] = drift > SPOT_DRIFT_FLAG
        except Exception as exc:
            log.warning("[quality:%s] spot drift check failed: %s", ticker, exc)

        # Backfill progress + ETA at the ~22 requests/day drip rate.
        if ticker in frontier:
            f = frontier[ticker]
            remaining = len(cal.sessions_in_range(pd.Timestamp(AV_HISTORY_FLOOR),
                                                  pd.Timestamp(f))) - 1
            remaining = max(remaining, 0)
            entry["backfill_frontier"] = f
            entry["backfill_sessions_remaining"] = remaining
            eta_days = int(remaining / 22 * 7 / 5) + 1
            entry["backfill_eta"] = (now.date() + timedelta(days=eta_days)).isoformat()
        report["tickers"][ticker] = entry

    r2_put_json(s3, QUALITY_KEY, report)
    log.info("quality flags written to r2://%s", QUALITY_KEY)
    print(json.dumps(report, indent=2))
    return report


def _ping_healthcheck(ok: bool) -> None:
    hc = os.environ.get("HEALTHCHECK_URL")
    if not hc:
        return
    url = hc if ok else hc.rstrip("/") + "/fail"
    try:
        requests.get(url, timeout=10)
        log.info("healthcheck ping sent (%s)", "success" if ok else "fail")
    except Exception as exc:
        log.warning("healthcheck ping failed: %s", exc)


def main() -> None:
    logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                        format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mode", default="all",
                    choices=["eod", "backfill", "underlying", "quality", "all"])
    ap.add_argument("--backfill-limit", type=int, default=None,
                    help="cap AV requests spent on backfill this run")
    args = ap.parse_args()

    s3 = r2_client()
    ok = True
    try:
        if args.mode in ("eod", "all"):
            ok = run_eod(s3, AvBudget(s3)) and ok
        if args.mode in ("backfill", "all"):
            run_backfill(s3, AvBudget(s3), limit=args.backfill_limit)
        if args.mode == "underlying":
            refresh_underlying_and_vix(s3)
        if args.mode == "quality":
            run_quality(s3)
    except Exception:
        ok = False
        log.exception("FATAL")
    finally:
        if args.mode in ("eod", "backfill", "all"):
            _ping_healthcheck(ok)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
