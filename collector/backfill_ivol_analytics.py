#!/usr/bin/env python3
"""
backfill_ivol_analytics.py — bank every entitled iVolatility analytics
dataset into R2, in product-value order:

  1. ivx     IV index history (IV term structure per session) — powers
             iv_percentile with 20 years of depth on every ticker
  2. hv      realized-volatility series — cross-check for our computed vol
  3. ivs     daily IV surfaces (delta × tenor grid) — future skew/term triggers
  4. bars1m  1-minute underlying bars — extends the frozen Alpaca window
  5. yield   dividend yield   (best-effort: empty on some tariffs)
  6. rates   interest rates   (best-effort: empty on some tariffs)

Layout (new prefixes, nothing existing is touched):
  reference/ivol/ivx/ticker={T}/year={Y}.parquet
  reference/ivol/hv/ticker={T}/year={Y}.parquet
  reference/ivol/ivs/ticker={T}/month={YYYY-MM}.parquet
  bars_1m/source=ivolatility/ticker={T}/date={D}/bars.parquet
  reference/ivol/yield_ticker={T}.parquet · reference/ivol/interest_rates.parquet

Resumable via state/ivol_analytics.json (done + empty chunks per dataset).
Paced for the vendor's requests-per-second cap: single worker, ~1.3s
between calls, exponential backoff on 429/5xx.

Usage:  uv run python backfill_ivol_analytics.py [--tickers SPY,QQQ,IWM]
        [--datasets ivx,hv,ivs,bars1m,yield,rates] [--from-year 2005]
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import requests

from backfill_ivol import _load_dotenv
from collect import r2_client, r2_get_json, r2_put_json, r2_put_parquet

log = logging.getLogger("ivol-analytics")

BASE = "https://restapi.ivolatility.com"
STATE_KEY = "state/ivol_analytics.json"
PACE_SECONDS = 1.3
RETRIES = 5


def _get(headers: dict[str, str], path: str, **params) -> list[dict] | None:
    for attempt in range(RETRIES):
        try:
            r = requests.get(f"{BASE}{path}", params=params, headers=headers, timeout=180)
            if r.status_code in (429, 500, 502, 503, 504):
                raise requests.HTTPError(f"HTTP {r.status_code}")
            r.raise_for_status()
            time.sleep(PACE_SECONDS)
            body = r.json()
            data = body.get("data") if isinstance(body, dict) else body
            return data if isinstance(data, list) else []
        except Exception as exc:  # noqa: BLE001 — retry then surface
            if attempt == RETRIES - 1:
                log.warning("%s %s: giving up (%s)", path, params.get("symbol", ""), exc)
                return None
            time.sleep(min(2**attempt * 2, 30))
    return None


def _frame(rows: list[dict], ticker: str) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    df.insert(0, "ticker", ticker)
    df.insert(1, "source", "ivolatility")
    return df


class Job:
    def __init__(self, args: argparse.Namespace) -> None:
        key = os.environ.get("IVOL_API_KEY")
        if not key:
            sys.exit("set IVOL_API_KEY in collector/.env")
        self.h = {"apiKey": key}
        self.s3 = r2_client()
        self.state: dict = r2_get_json(self.s3, STATE_KEY, {})
        self.tickers = args.tickers
        self.from_year = args.from_year
        self.this_year = datetime.now(timezone.utc).year

    # -------------------------------------------------- state helpers
    def _chunks(self, dataset: str, ticker: str) -> dict:
        return self.state.setdefault(dataset, {}).setdefault(
            ticker, {"done": [], "empty": []}
        )

    def _flush(self) -> None:
        r2_put_json(self.s3, STATE_KEY, self.state)

    def _run_chunks(self, dataset: str, ticker: str, chunk_ids: list[str], fetch, key_for) -> None:
        st = self._chunks(dataset, ticker)
        todo = [c for c in chunk_ids if c not in st["done"] and c not in st["empty"]]
        log.info("%s %s: %s chunks to fetch", dataset, ticker, len(todo))
        for i, chunk in enumerate(todo):
            rows = fetch(chunk)
            if rows is None:
                continue  # errored — next run retries
            if not rows:
                st["empty"].append(chunk)
            else:
                r2_put_parquet(self.s3, key_for(chunk), _frame(rows, ticker))
                st["done"].append(chunk)
            if (i + 1) % 25 == 0:
                self._flush()
                log.info("%s %s: %s/%s", dataset, ticker, i + 1, len(todo))
        self._flush()

    # -------------------------------------------------------- datasets
    def ivx(self) -> None:
        years = [str(y) for y in range(self.from_year, self.this_year + 1)]
        for t in self.tickers:
            self._run_chunks(
                "ivx", t, years,
                lambda y, t=t: _get(self.h, "/equities/eod/ivx", symbol=t,
                                    **{"from": f"{y}-01-01", "to": f"{y}-12-31"}),
                lambda y, t=t: f"reference/ivol/ivx/ticker={t}/year={y}.parquet",
            )

    def hv(self) -> None:
        years = [str(y) for y in range(self.from_year, self.this_year + 1)]
        for t in self.tickers:
            self._run_chunks(
                "hv", t, years,
                lambda y, t=t: _get(self.h, "/equities/eod/hv", symbol=t,
                                    **{"from": f"{y}-01-01", "to": f"{y}-12-31"}),
                lambda y, t=t: f"reference/ivol/hv/ticker={t}/year={y}.parquet",
            )

    def ivs(self) -> None:
        months = []
        y, m = self.from_year, 1
        now = datetime.now(timezone.utc)
        while (y, m) <= (now.year, now.month):
            months.append(f"{y}-{m:02d}")
            y, m = (y + 1, 1) if m == 12 else (y, m + 1)

        def last_day(month: str) -> str:
            y0, m0 = int(month[:4]), int(month[5:])
            nxt = date(y0 + (m0 == 12), (m0 % 12) + 1, 1)
            return (nxt - timedelta(days=1)).isoformat()

        for t in self.tickers:
            self._run_chunks(
                "ivs", t, months,
                lambda mo, t=t: _get(self.h, "/equities/eod/ivs", symbol=t,
                                     **{"from": f"{mo}-01", "to": last_day(mo)}),
                lambda mo, t=t: f"reference/ivol/ivs/ticker={t}/month={mo}.parquet",
            )

    def bars1m(self) -> None:
        # per-session objects; the empty-chunk state discovers the floor
        start = date(max(self.from_year, 2010), 1, 1)
        end = datetime.now(timezone.utc).date() - timedelta(days=1)
        days, d = [], start
        while d <= end:
            if d.weekday() < 5:
                days.append(d.isoformat())
            d += timedelta(days=1)
        for t in self.tickers:
            self._run_chunks(
                "bars1m", t, days,
                lambda day, t=t: _get(self.h, "/equities/intraday/stock-prices",
                                      symbol=t, date=day, minuteType="MINUTE_1"),
                lambda day, t=t: f"bars_1m/source=ivolatility/ticker={t}/date={day}/bars.parquet",
            )

    def yield_(self) -> None:
        for t in self.tickers:
            rows = _get(self.h, "/equities/yield", symbol=t, region="USA")
            if rows:
                r2_put_parquet(self.s3, f"reference/ivol/yield_ticker={t}.parquet",
                               _frame(rows, t))
            else:
                log.info("yield %s: empty on this tariff — skipped", t)

    def rates(self) -> None:
        rows = _get(self.h, "/equities/interest-rates",
                    **{"from": f"{self.from_year}-01-01",
                       "till": datetime.now(timezone.utc).date().isoformat()})
        if rows:
            r2_put_parquet(self.s3, "reference/ivol/interest_rates.parquet",
                           _frame(rows, "USD"))
        else:
            log.info("interest-rates: empty on this tariff — skipped")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    _load_dotenv()
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tickers", default="SPY,QQQ,IWM")
    ap.add_argument("--datasets", default="ivx,hv,ivs,bars1m,yield,rates")
    ap.add_argument("--from-year", type=int, default=2005)
    args = ap.parse_args()
    args.tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]

    job = Job(args)
    order = [d.strip() for d in args.datasets.split(",") if d.strip()]
    runners = {"ivx": job.ivx, "hv": job.hv, "ivs": job.ivs,
               "bars1m": job.bars1m, "yield": job.yield_, "rates": job.rates}
    for name in order:
        log.info("=== dataset: %s ===", name)
        runners[name]()
    log.info("all requested datasets complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
