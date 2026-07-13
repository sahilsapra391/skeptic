#!/usr/bin/env python3
"""derive_fill_calibration.py — UW option tape → fill-model calibration
artifact (D3d: the engine's configured slip, MEASURED from real prints).

Reduces each banked tape session to per-(side, size_bucket) slip
histograms (math single-sourced in backend app/data/fill_calibration.py,
fixture-tested):
  reference/derived/fill_calibration/ticker={T}.parquet
      date · side · size_bucket · n · b_* bins · slip_median · context

Incremental by SET DIFFERENCE over the tape date prefixes (the F4
self-healing rule): unreadable sessions leave no row and retry next run.
The tape is a frozen record (trial over, floor 2026-03-02 → 2026-07-10),
so after one full pass this derive is a permanent no-op — it stays in the
nightly chain only so a future tape source lights it back up.

Run:  cd collector && uv run python derive_fill_calibration.py [--tickers ...]
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

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.data.fill_calibration import FILL_CAL_KEY, calibrate_session  # noqa: E402

from collect import (  # noqa: E402
    r2_client,
    r2_get_parquet,
    r2_get_parquet_spooled,
    r2_put_parquet,
)

log = logging.getLogger("fill_calibration")
_DATE_RE = re.compile(r"date=(\d{4}-\d{2}-\d{2})")

# the five required columns only (per-print side lives in `tags`; the
# *_vol columns are cumulative contract-day counters — never read). A
# leaner projection also can't hard-fail on a future source's schema.
_TAPE_COLUMNS = ["price", "size", "nbbo_bid", "nbbo_ask", "tags"]


def _tape_sessions(s3, ticker: str) -> list[str]:
    dates: list[str] = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=os.environ["R2_BUCKET"],
                                   Prefix=f"uw/option_tape/ticker={ticker}/",
                                   Delimiter="/"):
        for cp in page.get("CommonPrefixes", []):
            m = _DATE_RE.search(cp["Prefix"])
            if m:
                dates.append(m.group(1))
    return sorted(dates)


def _combined(frames: list[pd.DataFrame]) -> pd.DataFrame:
    return (pd.concat(frames, ignore_index=True)
            .drop_duplicates(subset=["date", "side", "size_bucket"],
                             keep="last")
            .sort_values(["date", "side", "size_bucket"])
            .reset_index(drop=True))


def run(s3, ticker: str) -> int:
    key = FILL_CAL_KEY.format(ticker=ticker)
    existing = r2_get_parquet(s3, key)
    have = (set(existing["date"].astype(str))
            if existing is not None and not existing.empty
            and "date" in existing.columns else set())
    todo = [d for d in _tape_sessions(s3, ticker) if d not in have]
    if not todo:
        log.info("%s: up to date (%d sessions)", ticker, len(have))
        return 0
    frames = [existing] if existing is not None and not existing.empty else []
    derived = 0
    for d in todo:
        prints = r2_get_parquet_spooled(
            s3, f"uw/option_tape/ticker={ticker}/date={d}/trades.parquet",
            columns=_TAPE_COLUMNS)
        rows = calibrate_session(prints)
        if rows is None:
            log.warning("%s %s: unreadable tape — no row, retries next run",
                        ticker, d)
            continue
        frames.append(pd.DataFrame([{"date": d, **r} for r in rows]))
        derived += 1
        if derived % 10 == 0:
            # checkpoint: a session is ~130 MB of spooled tape download —
            # a network crash must not lose the whole ticker's pass
            r2_put_parquet(s3, key, _combined(frames))
            log.info("%s: %d/%d sessions derived (checkpointed)",
                     ticker, derived, len(todo))
    if derived:
        r2_put_parquet(s3, key, _combined(frames))
    log.info("%s: derived %d sessions → r2://%s", ticker, derived, key)
    return derived


def main() -> int:
    logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                        format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tickers", default="SPY,QQQ,IWM")
    args = ap.parse_args()
    s3 = r2_client()
    n = 0
    for t in [x.strip().upper() for x in args.tickers.split(",") if x.strip()]:
        n += run(s3, t)
    log.info("done: %d ticker-sessions derived", n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
