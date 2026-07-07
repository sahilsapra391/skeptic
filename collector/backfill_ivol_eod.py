#!/usr/bin/env python3
"""
backfill_ivol_eod.py — capture iVolatility EOD per-contract options (full greeks +
NBBO + IV + OI + volume) into the R2 chain lake, 2005→ for SPY/QQQ/IWM.

Fills the biggest EOD gap: `options/source=ivolatility/` is empty (bulk chain is
403 institutional-only), QQQ/IWM have ~3 EOD dates, SPY dolthub is 2020→ w/ no
<11 DTE. Entitled path (probe-verified 2026-07-07):
  - `/equities/eod/nearest-option-tickers` (symbol, startingDate, delta, callPut,
    dte)  → option_id + OCC option_symbol  (DISCOVERY, delta must be a DECIMAL,
    puts negative / calls positive)
  - `/equities/eod/single-stock-option-raw-iv` (symbol=OCC_or_optionId, from, to)
    → the contract's WHOLE EOD history in ONE request (date, bid, ask, iv, delta,
    gamma, theta, vega, rho, open interest, volume, strike, expiration, ...)

Design (engine-aligned — the engine SELECTS strikes by delta): discover contracts
on a delta×DTE ladder at a coarse cadence, pull each unique contract's full life
once (staged raw), then REGROUP into per-date canonical chains. Newest-first,
resumable, rate-gated concurrent like backfill_ivol_intraday.py.

Modes:
  --mode fetch     discover + pull → ivol_eod_staging/ticker={T}/id={id}.parquet
  --mode regroup   staged raw → options/source=ivolatility/ticker={T}/date={D}/chain.parquet
                   (canonical schema; LOCAL, no API — safe to re-run anytime)

Run:
  uv run --env-file .env python backfill_ivol_eod.py --mode fetch \
      --tickers SPY,QQQ,IWM --key-env IVOL_API_KEY,IVOL_API_KEY_2,IVOL_API_KEY_3
  uv run --env-file .env python backfill_ivol_eod.py --mode regroup --tickers SPY,QQQ,IWM
"""
from __future__ import annotations

import argparse
import itertools
import logging
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

import exchange_calendars as xcals
import pandas as pd
import requests

from collect import (
    CANONICAL_COLUMNS,
    r2_client,
    r2_get_json,
    r2_get_parquet,
    r2_list_keys,
    r2_put_json,
    r2_put_parquet,
)

log = logging.getLogger("ivol_eod")

BASE = "https://restapi.ivolatility.com"
EP_NEAR = "/equities/eod/nearest-option-tickers"
EP_RAWIV = "/equities/eod/single-stock-option-raw-iv"
REGION = "USA"
TIMEOUT = 120
RETRIES = 6
BACKOFF_BASE = 2.0
STATE_FLUSH_EVERY = 20

EOD_START = "2005-01-03"
DEFAULT_RATE = 80
DEFAULT_CONCURRENCY = 4
DEFAULT_CADENCE = 21          # discover every N trading days (newest-first)
# delta ladder (decimals; puts negative, calls positive) — the premium-strategy range
DEFAULT_DELTAS = [0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.40, 0.50]
DEFAULT_DTES = [14, 30, 45]  # entry tenors; whole-life pulls fill 0..exp between them
PULL_LOOKBACK_DAYS = 250     # from = expiration - this; one request = whole life

STAGE_PREFIX = "ivol_eod_staging"       # raw per-contract (NOT read by the engine)
CHAIN_PREFIX = "options/source=ivolatility"  # canonical per-date chains (engine reads)

_AUTHS: list[dict] = []
_SECRETS: list[str] = []
_key_cycle = itertools.count()
_req_count = [0]
_req_lock = threading.Lock()


class RateGate:
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
    auth = _AUTHS[next(_key_cycle) % len(_AUTHS)]
    for attempt in range(RETRIES):
        _GATE.wait()
        try:
            r = requests.get(f"{BASE}{path}", params=params, headers=auth, timeout=TIMEOUT)
        except Exception as exc:  # noqa: BLE001
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
        return r.status_code, None
    return 429, None


def rows_of(body: object) -> list[dict]:
    if isinstance(body, dict):
        d = body.get("data")
        return [x for x in d if isinstance(x, dict)] if isinstance(d, list) else []
    return [x for x in body if isinstance(x, dict)] if isinstance(body, list) else []


# ------------------------------------------------------------ discovery + pull
def discover(ticker: str, day: str, deltas: list[float], dtes: list[int]) -> dict[int, str]:
    """nearest-option-tickers across the delta×DTE ladder (puts negative, calls
    positive). Returns {option_id: option_symbol} for this discovery date."""
    found: dict[int, str] = {}

    def one(args):
        d, cp, dte = args
        signed = -d if cp == "P" else d
        st, body = _get(EP_NEAR, {"symbol": ticker, "startingDate": day, "region": REGION,
                                  "delta": round(signed, 4), "callPut": cp, "dte": dte})
        out = []
        for r in rows_of(body):
            oid, osym = r.get("option_id"), r.get("option_symbol")
            if oid and osym:
                out.append((int(oid), str(osym)))
        return out

    targets = [(d, cp, dte) for d in deltas for cp in ("P", "C") for dte in dtes]
    with ThreadPoolExecutor(max_workers=_POOL_WORKERS[0]) as pool:
        for res in pool.map(one, targets):
            for oid, osym in res:
                found[oid] = osym
    return found


def _occ_parse(option_symbol: str) -> tuple[str, str, float] | None:
    """OCC 'SPY   240731P00615000' → (expiration 'YYYY-MM-DD', right, strike).
    The last 15 chars are always yymmdd(6)+C/P(1)+strike×1000(8)."""
    m = re.search(r"(\d{6})([CP])(\d{8})", option_symbol.replace(" ", ""))
    if not m:
        return None
    yymmdd, cp, strike8 = m.group(1), m.group(2), m.group(3)
    try:
        exp = datetime.strptime(yymmdd, "%y%m%d").date().isoformat()
    except ValueError:
        return None
    return exp, ("put" if cp == "P" else "call"), int(strike8) / 1000.0


def pull_contract(ticker: str, oid: int, osym: str) -> pd.DataFrame | None:
    parsed = _occ_parse(osym)
    if parsed is None:
        return None
    exp, _right, _strike = parsed
    exp_d = date.fromisoformat(exp)
    frm = (exp_d - timedelta(days=PULL_LOOKBACK_DAYS)).isoformat()
    to = (exp_d + timedelta(days=1)).isoformat()
    st, body = _get(EP_RAWIV, {"symbol": osym, "from": frm, "to": to})
    rows = rows_of(body)
    if st != 200 or not rows:
        return None
    df = pd.DataFrame(rows)
    df["_option_id"] = oid
    df["_option_symbol"] = osym
    return df


def stage_key(ticker: str, oid: int) -> str:
    return f"{STAGE_PREFIX}/ticker={ticker}/id={oid}.parquet"


def _staged_ids(s3, ticker: str) -> set[int]:
    ids: set[int] = set()
    for k in r2_list_keys(s3, f"{STAGE_PREFIX}/ticker={ticker}/"):
        if m := re.search(r"id=(\d+)\.parquet", k):
            ids.add(int(m.group(1)))
    return ids


# ------------------------------------------------------------ calendar
def discovery_dates(start: str, end: str, cadence: int) -> list[str]:
    # default XNYS calendar only spans ~20y back; extend it to reach 2005
    cal = xcals.get_calendar("XNYS", start=pd.Timestamp(start) - pd.Timedelta(days=7))
    sess = [s.date().isoformat() for s in cal.sessions_in_range(pd.Timestamp(start), pd.Timestamp(end))]
    picked = sess[::-1][::cadence]  # newest-first, every `cadence` sessions
    return picked


# ------------------------------------------------------------ fetch mode
_POOL_WORKERS = [DEFAULT_CONCURRENCY]


def run_fetch(args, s3) -> int:
    end = args.to or (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()
    start = args.start or EOD_START
    state_key = "state/ivol_eod_state.json"
    state = r2_get_json(s3, state_key, {})

    ddates = discovery_dates(start, end, args.cadence)
    if args.limit_dates:
        ddates = ddates[: args.limit_dates]

    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        for ticker in args.tickers:
            tstate = state.setdefault(ticker, {})
            done_months = set(tstate.get("done_dates", []))
            staged = _staged_ids(s3, ticker)
            log.info("%s [eod]: %d discovery dates (%s..%s), %d already staged, %d discovery-dates done",
                     ticker, len(ddates), ddates[-1] if ddates else "-",
                     ddates[0] if ddates else "-", len(staged), len(done_months))

            for day in ddates:
                if day in done_months:
                    continue
                try:
                    contracts = discover(ticker, day, args.deltas, args.dtes)
                except Exception as exc:  # noqa: BLE001
                    log.warning("%s discover %s failed: %s", ticker, day, _scrub(repr(exc)))
                    continue
                new = {oid: osym for oid, osym in contracts.items() if oid not in staged}
                # pull the new contracts (concurrent, rate-gated)
                def _do(item):
                    oid, osym = item
                    df = pull_contract(ticker, oid, osym)
                    return oid, osym, df
                pulled = 0
                for oid, osym, df in pool.map(_do, list(new.items())):
                    if df is not None and len(df):
                        if not args.dry_run:
                            r2_put_parquet(s3, stage_key(ticker, oid), df)
                        staged.add(oid)
                        pulled += 1
                    else:
                        staged.add(oid)  # empty/bad contract — don't retry
                done_months.add(day)
                if pulled:
                    log.info("%s %s: +%d contracts staged (total %d) [req=%d]",
                             ticker, day, pulled, len(staged), _req_count[0])
                if len(done_months) % STATE_FLUSH_EVERY == 0:
                    tstate["done_dates"] = sorted(done_months)
                    r2_put_json(s3, state_key, state)
                if args.max_requests and _req_count[0] >= args.max_requests:
                    tstate["done_dates"] = sorted(done_months)
                    r2_put_json(s3, state_key, state)
                    log.info("max-requests %d reached — stopping cleanly", args.max_requests)
                    return 0

            tstate["done_dates"] = sorted(done_months)
            r2_put_json(s3, state_key, state)
            log.info("%s [eod] fetch done: %d contracts staged, req=%d", ticker, len(staged), _req_count[0])
    return 0


# ------------------------------------------------------------ regroup mode
def _num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce")


def _to_canonical(raw: pd.DataFrame, ticker: str) -> pd.DataFrame:
    right = raw.get("Call/Put", pd.Series(dtype=str)).astype(str).str.upper().map({"C": "call", "P": "put"})
    exp = pd.to_datetime(raw.get("expiration"), errors="coerce")
    td = pd.to_datetime(raw.get("date"), errors="coerce")
    out = pd.DataFrame({
        "ticker": ticker,
        "trading_date": td.dt.strftime("%Y-%m-%d"),
        "snapshot_ts": td.dt.strftime("%Y-%m-%d") + "T21:00:00+00:00",
        "expiration": exp.dt.strftime("%Y-%m-%d"),
        "dte": (exp - td).dt.days,
        "right": right,
        "strike": _num(raw.get("strike")),
        "bid": _num(raw.get("bid")),
        "ask": _num(raw.get("ask")),
        "last": _num(raw.get("price")),
        "volume": _num(raw.get("volume")),
        "open_interest": _num(raw.get("open interest")),
        "iv": _num(raw.get("iv")),
        "delta": _num(raw.get("delta")),
        "gamma": _num(raw.get("gamma")),
        "theta": _num(raw.get("theta")),
        "vega": _num(raw.get("vega")),
        "rho": _num(raw.get("rho")),
        "greeks_source": "vendor:ivolatility",
        "spot": _num(raw.get("Unadjusted close")),
        "source": "ivolatility",
    })
    return out.dropna(subset=["trading_date", "strike", "right", "expiration"])[CANONICAL_COLUMNS]


def run_regroup(args, s3) -> int:
    for ticker in args.tickers:
        keys = r2_list_keys(s3, f"{STAGE_PREFIX}/ticker={ticker}/")
        log.info("%s [regroup]: %d staged contracts", ticker, len(keys))
        if not keys:
            continue
        frames = []
        for i, k in enumerate(keys):
            df = r2_get_parquet(s3, k)
            if df is not None and len(df):
                frames.append(_to_canonical(df, ticker))
            if (i + 1) % 500 == 0:
                log.info("  %s: read %d/%d staged", ticker, i + 1, len(keys))
        if not frames:
            continue
        allrows = pd.concat(frames, ignore_index=True)
        allrows = allrows.dropna(subset=["dte"])
        allrows = allrows[allrows["dte"] >= 0]
        # one chain parquet per trading_date; de-dup (contract, date)
        allrows = allrows.drop_duplicates(subset=["trading_date", "expiration", "right", "strike"])
        n_dates = 0
        for d, g in allrows.groupby("trading_date"):
            key = f"{CHAIN_PREFIX}/ticker={ticker}/date={d}/chain.parquet"
            if not args.dry_run:
                r2_put_parquet(s3, key, g.reset_index(drop=True))
            n_dates += 1
            if n_dates % 500 == 0:
                log.info("  %s: wrote %d date-chains", ticker, n_dates)
        log.info("%s [regroup] done: %d date-chains from %d rows", ticker, n_dates, len(allrows))
    return 0


# ------------------------------------------------------------ main
def run(args: argparse.Namespace) -> int:
    global _AUTHS, _SECRETS, _GATE
    _AUTHS, _SECRETS = [], []
    for v in [x.strip() for x in args.key_env.split(",") if x.strip()]:
        val = os.environ.get(v)
        if val and val not in _SECRETS:
            _AUTHS.append({"apiKey": val})
            _SECRETS.append(val)
    if args.mode == "fetch" and not _AUTHS:
        sys.exit(f"no keys set in env for: {args.key_env}")
    _GATE = RateGate(args.rate)
    _POOL_WORKERS[0] = args.concurrency
    s3 = r2_client()
    if args.mode == "fetch":
        log.info("keys=%d concurrency=%d rate=%d/min cadence=%d deltas=%s dtes=%s",
                 len(_AUTHS), args.concurrency, args.rate, args.cadence, args.deltas, args.dtes)
        return run_fetch(args, s3)
    return run_regroup(args, s3)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mode", choices=("fetch", "regroup"), required=True)
    ap.add_argument("--tickers", default="SPY,QQQ,IWM")
    ap.add_argument("--key-env", dest="key_env", default="IVOL_API_KEY")
    ap.add_argument("--from", dest="start", default=None)
    ap.add_argument("--to", dest="to", default=None)
    ap.add_argument("--cadence", type=int, default=DEFAULT_CADENCE)
    ap.add_argument("--deltas", default=",".join(str(d) for d in DEFAULT_DELTAS))
    ap.add_argument("--dtes", default=",".join(str(d) for d in DEFAULT_DTES))
    ap.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)
    ap.add_argument("--rate", type=int, default=DEFAULT_RATE)
    ap.add_argument("--max-requests", type=int, default=0)
    ap.add_argument("--limit-dates", type=int, default=0, help="only newest N discovery dates (verify)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    args.tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    args.deltas = [float(x) for x in str(args.deltas).split(",") if x.strip()]
    args.dtes = [int(x) for x in str(args.dtes).split(",") if x.strip()]
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
