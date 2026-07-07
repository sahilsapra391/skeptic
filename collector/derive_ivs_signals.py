#!/usr/bin/env python3
"""
derive_ivs_signals.py — surface → signal derivation (ENGINE-V4 F4).

Reads each banked IVS session surface ONCE and appends one row per session
to reference/derived/ivs_signals/ticker={T}.parquet:
  date · skew_25d · term_slope_30_90 · atm_iv_30d · atm_iv_90d  (vol points)

Incremental by state (state/ivs_signals_derive.json records the last
derived session per ticker); nightly runs derive only new sessions, so the
signal series grows with the lake automatically (self-improvement thesis).
The derivation MATH is imported from the backend (app/data/ivs_signals.py)
— one implementation, fixture-tested in the backend battery; this side
only walks the lake.

Run:  cd collector && uv run python derive_ivs_signals.py [--tickers SPY,QQQ,IWM]
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

# single-source the derivation from the backend (F0 pattern — pandas-only)
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.data.ivs_signals import SIGNALS_KEY, derive_signal_row  # noqa: E402

from collect import (  # noqa: E402
    TICKERS,
    r2_client,
    r2_get_json,
    r2_get_parquet,
    r2_put_json,
    r2_put_parquet,
)

log = logging.getLogger("ivs_signals")
STATE_KEY = "state/ivs_signals_derive.json"


def _surface_dates(s3, ticker: str) -> list[str]:
    dates: list[str] = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=os.environ["R2_BUCKET"],
                                   Prefix=f"reference/ivol/ivs/ticker={ticker}/",
                                   Delimiter="/"):
        for cp in page.get("CommonPrefixes", []):
            m = re.search(r"date=(\d{4}-\d{2}-\d{2})", cp["Prefix"])
            if m:
                dates.append(m.group(1))
    return sorted(dates)


def derive_ticker(s3, ticker: str, state: dict) -> int:
    """Append rows for sessions newer than the state watermark. Returns
    how many sessions were derived."""
    last = (state.get(ticker) or {}).get("last")
    todo = [d for d in _surface_dates(s3, ticker) if last is None or d > last]
    if not todo:
        log.info("%s: up to date (last %s)", ticker, last)
        return 0
    rows: list[dict] = []
    for d in todo:
        surf = r2_get_parquet(
            s3, f"reference/ivol/ivs/ticker={ticker}/date={d}/surface.parquet")
        if surf is None or surf.empty:
            continue  # honest gap — no surface, no row
        row = derive_signal_row(surf)
        row["date"] = d
        rows.append(row)
    key = SIGNALS_KEY.format(ticker=ticker)
    fresh = pd.DataFrame(rows)
    existing = r2_get_parquet(s3, key)
    combined = (pd.concat([existing, fresh], ignore_index=True)
                if existing is not None and not existing.empty else fresh)
    if not combined.empty:
        combined = (combined.drop_duplicates(subset=["date"], keep="last")
                    .sort_values("date").reset_index(drop=True))
        r2_put_parquet(s3, key, combined)
    state[ticker] = {"last": todo[-1]}
    r2_put_json(s3, STATE_KEY, state)
    n_skew = int(fresh["skew_25d"].notna().sum()) if not fresh.empty else 0
    log.info("%s: derived %d sessions (skew present %d) → r2://%s",
             ticker, len(rows), n_skew, key)
    return len(rows)


def main() -> int:
    logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                        format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tickers", default=",".join(TICKERS))
    args = ap.parse_args()
    s3 = r2_client()
    state = r2_get_json(s3, STATE_KEY, {})
    total = 0
    for t in [x.strip().upper() for x in args.tickers.split(",") if x.strip()]:
        total += derive_ticker(s3, t, state)
    log.info("done: %d sessions derived", total)
    return 0


if __name__ == "__main__":
    sys.exit(main())
