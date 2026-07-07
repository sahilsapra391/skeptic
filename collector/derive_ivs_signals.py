#!/usr/bin/env python3
"""
derive_ivs_signals.py — surface → signal derivation (ENGINE-V4 F4).

Reads each banked IVS session surface ONCE and appends one row per session
to reference/derived/ivs_signals/ticker={T}.parquet:
  date · skew_25d · term_slope_30_90 · atm_iv_30d · atm_iv_90d  (vol points)

Incremental by SET DIFFERENCE, not a watermark: each run derives exactly
the listed surface sessions that have no row in the artifact yet. That
makes every hole self-healing (review finding, F4): a transient R2 read
failure retries next night, and a surface that the iVol backfill drip
lands at an OLD date is picked up the night it appears — no state file to
advance past it, nothing to recover manually. A derived-but-signal-less
session writes an all-None row (so it is not retried); only sessions whose
surface could not be READ stay pending, and the skip count is logged
loudly. Self-improvement thesis: the signal series grows with the lake
automatically.

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
    r2_get_parquet,
    r2_put_parquet,
)

log = logging.getLogger("ivs_signals")


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


def derive_ticker(s3, ticker: str) -> int:
    """Derive every listed surface session the artifact doesn't cover yet.
    Returns how many sessions were derived."""
    key = SIGNALS_KEY.format(ticker=ticker)
    existing = r2_get_parquet(s3, key)
    have: set[str] = set()
    if existing is not None and not existing.empty and "date" in existing.columns:
        have = set(existing["date"].astype(str))
    todo = [d for d in _surface_dates(s3, ticker) if d not in have]
    if not todo:
        log.info("%s: up to date (%d sessions in artifact)", ticker, len(have))
        return 0
    rows: list[dict] = []
    skipped: list[str] = []
    for d in todo:
        surf = r2_get_parquet(
            s3, f"reference/ivol/ivs/ticker={ticker}/date={d}/surface.parquet")
        if surf is None or surf.empty:
            # unreadable ≠ derived: NO row is written, so this session is
            # retried on the next pass — a hole heals, never sticks
            skipped.append(d)
            continue
        row = derive_signal_row(surf)
        row["date"] = d
        rows.append(row)
    if rows:
        fresh = pd.DataFrame(rows)
        combined = (pd.concat([existing, fresh], ignore_index=True)
                    if existing is not None and not existing.empty else fresh)
        combined = (combined.drop_duplicates(subset=["date"], keep="last")
                    .sort_values("date").reset_index(drop=True))
        r2_put_parquet(s3, key, combined)
        n_skew = int(fresh["skew_25d"].notna().sum())
        log.info("%s: derived %d sessions (skew present %d) → r2://%s",
                 ticker, len(rows), n_skew, key)
    if skipped:
        log.warning("%s: %d sessions listed but unreadable, will retry next "
                    "run: %s%s", ticker, len(skipped), ", ".join(skipped[:10]),
                    " …" if len(skipped) > 10 else "")
    return len(rows)


def main() -> int:
    logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                        format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tickers", default=",".join(TICKERS))
    args = ap.parse_args()
    s3 = r2_client()
    total = 0
    for t in [x.strip().upper() for x in args.tickers.split(",") if x.strip()]:
        total += derive_ticker(s3, t)
    log.info("done: %d sessions derived", total)
    return 0


if __name__ == "__main__":
    sys.exit(main())
