#!/usr/bin/env python3
"""
backfill_massive.py — Massive (formerly Polygon.io) Options Basic FREE tier:
per-contract EOD OHLCV for QQQ / IWM, ~2 years deep.

HONEST SCOPE: the free tier serves per-contract daily aggregates only —
open/high/low/close/volume/VWAP/transactions. NO bid/ask, NO greeks, NO IV.
So this is a COVERAGE / CROSS-VALIDATION / VOLUME source, never a fill source
(guardrail #1 forbids filling without a real bid/ask). Most useful for QQQ/IWM,
where the chain lake is otherwise nearly empty.

Auth: `Authorization: Bearer $MASSIVE_API_KEY` (collector/.env).
Base: https://api.massive.com   ·   Rate: ~5 req/min on Basic (paced + 429 backoff).

Endpoints:
  contracts   GET /v3/reference/options/contracts?underlying_ticker=QQQ&expired=… &limit=1000
              paginated via next_url — the 2-yr symbol universe (active + expired)
  aggregates  GET /v2/aggs/ticker/{O:SYMBOL}/range/1/day/{from}/{to}
              one call per contract → its full daily OHLCV history

Modes:
  probe       validate key, endpoints, rate headers, universe size. RUN FIRST.
  enumerate   build + bank the contract universe per ticker
  aggs        per-contract daily OHLCV (resumable) — the long backfill
  all         enumerate then aggs

Prefixes:  reference/massive/contracts/ticker={T}.parquet
           reference/massive/option_agg/ticker={T}/symbol={SYM}.parquet
State:     reference/state/massive_backfill.json
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import threading
import time
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests

from collect import r2_client, r2_get_json, r2_put_json, r2_put_parquet

log = logging.getLogger("massive")

BASE = "https://api.massive.com"
STATE_KEY = "reference/state/massive_backfill.json"
TICKERS = ["QQQ", "IWM"]
DEFAULT_RATE = 5          # Basic free tier ≈ 5 req/min
HISTORY_DAYS = 730        # ~2 years (the Basic retention window)
RETRIES = 6
TIMEOUT = 60

_KEY = ""


def _load_dotenv() -> None:
    env = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(env):
        return
    for line in open(env):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


class RateGate:
    """Spaces requests 60/per_min apart. Free tier is ~5/min, so this is the
    binding constraint — the backfill is slow by design, never rude."""

    def __init__(self, per_min: float) -> None:
        self.interval = 60.0 / max(per_min, 1.0)
        self.lock = threading.Lock()
        self.next = 0.0
        self.count = 0

    def wait(self) -> None:
        with self.lock:
            now = time.time()
            slot = max(now, self.next)
            self.next = slot + self.interval
            sleep = slot - now
        if sleep > 0:
            time.sleep(sleep)
        self.count += 1


_GATE = RateGate(DEFAULT_RATE)


def _get(path: str | None, params: dict | None = None,
         full_url: str | None = None) -> tuple[int, dict | None]:
    """Rate-gated Bearer GET with 429/5xx backoff. `full_url` follows a
    pagination next_url verbatim (Bearer header still applied)."""
    headers = {"Authorization": f"Bearer {_KEY}"}
    url = full_url or f"{BASE}{path}"
    for attempt in range(RETRIES):
        _GATE.wait()
        try:
            r = requests.get(url, params=None if full_url else params,
                             headers=headers, timeout=TIMEOUT)
        except Exception as exc:  # noqa: BLE001
            if attempt == RETRIES - 1:
                log.warning("request error: %s", exc)
                return -1, None
            time.sleep(2**attempt)
            continue
        if r.status_code == 200:
            try:
                return 200, r.json()
            except Exception:
                return 200, None
        if r.status_code == 429 or r.status_code >= 500:
            # free-tier throttle — wait out the minute and retry
            time.sleep(min(15 * (attempt + 1), 65))
            continue
        return r.status_code, None
    return 429, None


# ------------------------------------------------------------ state
def _state(s3) -> dict:
    return r2_get_json(s3, STATE_KEY, {"contracts": {}, "aggs_done": {}})


def _flush(s3, state: dict) -> None:
    r2_put_json(s3, STATE_KEY, state)


# ------------------------------------------------------------ enumerate
def enumerate_contracts(s3, state: dict, tickers: list[str], dry: bool) -> None:
    # the reference lists contracts back to 2011, but aggregates only cover ~2yr —
    # so restrict to contracts EXPIRING within the aggregate window (their trading
    # life then overlaps it), else we'd make thousands of zero-bar aggregate calls.
    exp_floor = (datetime.now(timezone.utc).date() - timedelta(days=HISTORY_DAYS)).isoformat()
    for t in tickers:
        symbols: dict[str, dict] = {}
        for expired in ("false", "true"):
            code, body = _get("/v3/reference/options/contracts",
                              {"underlying_ticker": t, "expired": expired, "limit": 1000,
                               "expiration_date.gte": exp_floor})
            pages = 0
            while code == 200 and body:
                for row in body.get("results", []):
                    sym = row.get("ticker")
                    if sym:
                        symbols[sym] = row
                nxt = body.get("next_url")
                pages += 1
                if not nxt:
                    break
                code, body = _get(None, full_url=nxt)
            log.info("%s expired=%s: %d pages, running total %d", t, expired, pages, len(symbols))
        if symbols and not dry:
            r2_put_parquet(s3, f"reference/massive/contracts/ticker={t}.parquet",
                           pd.DataFrame(list(symbols.values())))
        state["contracts"][t] = sorted(symbols)
        _flush(s3, state)
        log.info("%s: %d contracts enumerated", t, len(symbols))


# ------------------------------------------------------------ aggregates
def pull_aggs(s3, state: dict, tickers: list[str], dry: bool) -> None:
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=HISTORY_DAYS)
    for t in tickers:
        universe = state["contracts"].get(t, [])
        if not universe:
            log.warning("%s: no enumerated universe — run enumerate first", t)
            continue
        done = set(state["aggs_done"].get(t, []))
        todo = [s for s in universe if s not in done]
        log.info("%s: %d contracts to pull (%d done) — ~%.1f h at %d/min",
                 t, len(todo), len(done), len(todo) / max(DEFAULT_RATE, 1) / 60, DEFAULT_RATE)
        for sym in todo:
            code, body = _get(f"/v2/aggs/ticker/{sym}/range/1/day/{start}/{end}",
                              {"adjusted": "true", "sort": "asc", "limit": 50000})
            if code == 200 and body and body.get("results"):
                df = pd.DataFrame(body["results"])
                if "t" in df.columns:
                    df["date"] = pd.to_datetime(df["t"], unit="ms").dt.strftime("%Y-%m-%d")
                df["ticker"] = t
                df["occ_symbol"] = sym
                df["source"] = "massive"
                if not dry:
                    r2_put_parquet(
                        s3, f"reference/massive/option_agg/ticker={t}/symbol={sym}.parquet", df)
            state["aggs_done"].setdefault(t, []).append(sym)
            if _GATE.count % 25 == 0:
                _flush(s3, state)
        _flush(s3, state)
        log.info("%s: aggregates complete", t)


# ------------------------------------------------------------ probe
def probe(s3) -> int:
    code, body = _get("/v3/reference/options/contracts",
                      {"underlying_ticker": "QQQ", "expired": "true", "limit": 1000})
    n = len(body.get("results", [])) if body else 0
    print(f"contracts (QQQ, 1 page): HTTP {code} · {n} rows"
          + (f" · sample {body['results'][0].get('ticker')}" if n else ""))
    if code == 200 and n:
        sym = body["results"][0]["ticker"]
        end = datetime.now(timezone.utc).date()
        start = end - timedelta(days=HISTORY_DAYS)
        c2, b2 = _get(f"/v2/aggs/ticker/{sym}/range/1/day/{start}/{end}",
                      {"adjusted": "true", "sort": "asc", "limit": 50000})
        rows = b2.get("results", []) if b2 else []
        print(f"aggregates ({sym}): HTTP {c2} · {len(rows)} daily bars"
              + (f" · keys {sorted(rows[0].keys())}" if rows else ""))
    print("NOTE: free tier = OHLCV only (no bid/ask/greeks) — coverage source, not fills.")
    return 0 if code == 200 else 1


# ------------------------------------------------------------ main
def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mode", choices=("probe", "enumerate", "aggs", "all"), default="probe")
    ap.add_argument("--tickers", default=",".join(TICKERS))
    ap.add_argument("--rate", type=int, default=DEFAULT_RATE, help="req/min (free tier ~5)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    _load_dotenv()
    global _KEY, _GATE
    _KEY = os.environ.get("MASSIVE_API_KEY", "")
    if not _KEY:
        sys.exit("set MASSIVE_API_KEY in collector/.env")
    _GATE = RateGate(args.rate)

    tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    s3 = r2_client()
    state = _state(s3)

    if args.mode == "probe":
        return probe(s3)
    if args.mode in ("enumerate", "all"):
        enumerate_contracts(s3, state, tickers, args.dry_run)
    if args.mode in ("aggs", "all"):
        pull_aggs(s3, state, tickers, args.dry_run)
    _flush(s3, state)
    log.info("done (mode=%s, requests=%d)", args.mode, _GATE.count)
    return 0


if __name__ == "__main__":
    sys.exit(main())
