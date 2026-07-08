#!/usr/bin/env python3
"""
derive_flow_signals.py — UW families → EOD flow/pin signals (ENGINE-V4 F2/F3).

Reduces each banked session of uw/net_prem_ticks + uw/nope + uw/max_pain
(per ticker) and uw/market_tide (market-wide) to one EOD row:
  reference/derived/flow_signals/ticker={T}.parquet
      date · net_premium · put_call_ratio · nope_eod · max_pain_dist_pct
  reference/derived/market_tide_signals.parquet
      date · market_tide

Incremental by SET DIFFERENCE (the F4 self-healing rule): each run derives
exactly the listed sessions absent from the artifact — transient read
failures retry next night, late-landing sessions are picked up when they
appear, no state file. A session missing one family derives None for that
family's signals (a ROW is written, so it is not retried); only sessions
where the DRIVING family listing exists but nothing could be read stay
pending. The reduction MATH lives in the backend
(app/data/flow_signals.py) — one implementation, fixture-tested.

Run:  cd collector && uv run python derive_flow_signals.py [--tickers SPY,QQQ,IWM]
Env:  R2_* vars (same as collect.py).
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
from pathlib import Path

import pandas as pd


def _load_dotenv(path: Path = Path(__file__).parent / ".env") -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv()

# single-source the reduction from the backend (F0 pattern — pandas-only)
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.data.flow_signals import (  # noqa: E402
    FLOW_KEY,
    TIDE_KEY,
    derive_flow_row,
    derive_tide_row,
)

from collect import (  # noqa: E402
    TICKERS,
    r2_client,
    r2_get_parquet,
    r2_put_parquet,
)

log = logging.getLogger("flow_signals")
_DATE_RE = re.compile(r"date=(\d{4}-\d{2}-\d{2})")


def _sessions(s3, prefix: str) -> list[str]:
    dates: list[str] = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=os.environ["R2_BUCKET"],
                                   Prefix=prefix, Delimiter="/"):
        for cp in page.get("CommonPrefixes", []):
            m = _DATE_RE.search(cp["Prefix"])
            if m:
                dates.append(m.group(1))
    return sorted(dates)


def _have(s3, key: str) -> set[str]:
    df = r2_get_parquet(s3, key)
    if df is None or df.empty or "date" not in df.columns:
        return set()
    return set(df["date"].astype(str))


def _append(s3, key: str, existing_have: set[str], rows: list[dict]) -> None:
    fresh = pd.DataFrame(rows)
    existing = r2_get_parquet(s3, key) if existing_have else None
    combined = (pd.concat([existing, fresh], ignore_index=True)
                if existing is not None and not existing.empty else fresh)
    combined = (combined.drop_duplicates(subset=["date"], keep="last")
                .sort_values("date").reset_index(drop=True))
    r2_put_parquet(s3, key, combined)


def derive_ticker(s3, ticker: str) -> int:
    """Derive every net_prem_ticks-listed session the artifact lacks.
    net_prem_ticks is the DRIVING family (densest of the three); nope /
    max_pain gaps derive None for their columns that day."""
    key = FLOW_KEY.format(ticker=ticker)
    have = _have(s3, key)
    todo = [d for d in _sessions(s3, f"uw/net_prem_ticks/ticker={ticker}/")
            if d not in have]
    if not todo:
        log.info("%s: up to date (%d sessions)", ticker, len(have))
        return 0
    rows: list[dict] = []
    skipped: list[str] = []
    for d in todo:
        np_df = r2_get_parquet(s3, f"uw/net_prem_ticks/ticker={ticker}/date={d}/rows.parquet")
        if np_df is None or np_df.empty:
            skipped.append(d)  # unreadable driving family — retry next run
            continue
        row = derive_flow_row(
            np_df,
            r2_get_parquet(s3, f"uw/nope/ticker={ticker}/date={d}/rows.parquet"),
            r2_get_parquet(s3, f"uw/max_pain/ticker={ticker}/date={d}/rows.parquet"),
            d,
        )
        row["date"] = d
        rows.append(row)
    if rows:
        _append(s3, key, have, rows)
        log.info("%s: derived %d sessions → r2://%s", ticker, len(rows), key)
    if skipped:
        log.warning("%s: %d sessions unreadable, retry next run: %s%s",
                    ticker, len(skipped), ", ".join(skipped[:10]),
                    " …" if len(skipped) > 10 else "")
    return len(rows)


def derive_market(s3) -> int:
    have = _have(s3, TIDE_KEY)
    todo = [d for d in _sessions(s3, "uw/market_tide/") if d not in have]
    if not todo:
        log.info("market_tide: up to date (%d sessions)", len(have))
        return 0
    rows: list[dict] = []
    skipped: list[str] = []
    for d in todo:
        df = r2_get_parquet(s3, f"uw/market_tide/date={d}/rows.parquet")
        if df is None or df.empty:
            skipped.append(d)
            continue
        row = derive_tide_row(df)
        row["date"] = d
        rows.append(row)
    if rows:
        _append(s3, TIDE_KEY, have, rows)
        log.info("market_tide: derived %d sessions → r2://%s", len(rows), TIDE_KEY)
    if skipped:
        log.warning("market_tide: %d sessions unreadable, retry next run", len(skipped))
    return len(rows)


def main() -> int:
    logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                        format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tickers", default=",".join(TICKERS))
    args = ap.parse_args()
    s3 = r2_client()
    total = derive_market(s3)
    for t in [x.strip().upper() for x in args.tickers.split(",") if x.strip()]:
        total += derive_ticker(s3, t)
    log.info("done: %d session-rows derived", total)
    return 0


if __name__ == "__main__":
    sys.exit(main())
