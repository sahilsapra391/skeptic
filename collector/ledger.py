#!/usr/bin/env python3
"""
ledger.py — the coverage ledger (ENGINE-V3 D3a, data-arrival hook).

After every collection run, append one row PER TICKER to
state/coverage_ledger.parquet recording what the lake holds right now:
EOD chain sessions, intraday 5-minute sessions, IVX observations, and
their latest dates. Deltas between any two ledger rows are what the
nightly auto-unlock scan (D3b) and the weekly priority ranking (D3d)
reason from — "N new sessions arrived since this verdict was refused"
becomes a computable fact instead of a hope.

Append-only; one row group per run keeps the file tiny (a year of nightly
runs ≈ 750 rows). Never logs chain data rows — counts and dates only.

Run:  cd collector && uv run python ledger.py
Env:  R2_* vars (same as collect.py).
"""

from __future__ import annotations

import logging
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd


def _load_dotenv(path: Path = Path(__file__).parent / ".env") -> None:
    """Local runs read collector/.env; Actions env always wins (setdefault)."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv()

from collect import (  # noqa: E402  (env loads first, like intraday.py)
    TICKERS,
    list_chain_dates,
    r2_client,
    r2_get_parquet,
    r2_put_parquet,
)

log = logging.getLogger("ledger")

LEDGER_KEY = "state/coverage_ledger.parquet"
EOD_SOURCES = ["ivolatility", "alphavantage", "yahoo", "dolthub"]
INTRADAY_PREFIXES = [
    "options_intraday/source=ivolatility",
    "options_intraday/source=cboe_delayed",
]


def _date_prefixes(s3, prefix: str) -> list[str]:
    """Sorted ISO dates under date=YYYY-MM-DD/ sub-prefixes (cheap
    Delimiter listing — mirrors backend app/data/r2.py)."""
    dates: list[str] = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=os.environ["R2_BUCKET"],
                                   Prefix=f"{prefix}/", Delimiter="/"):
        for cp in page.get("CommonPrefixes", []):
            m = re.search(r"date=(\d{4}-\d{2}-\d{2})", cp["Prefix"])
            if m:
                dates.append(m.group(1))
    return sorted(dates)


def _ivx_stats(s3, ticker: str) -> tuple[int, str | None]:
    """IVX observation count + last date from the banked year files."""
    total = 0
    last: str | None = None
    paginator = s3.get_paginator("list_objects_v2")
    keys: list[str] = []
    for page in paginator.paginate(Bucket=os.environ["R2_BUCKET"],
                                   Prefix=f"reference/ivol/ivx/ticker={ticker}/"):
        keys.extend(item["Key"] for item in page.get("Contents", []))
    for key in sorted(keys):
        df = r2_get_parquet(s3, key)
        if df is None or df.empty or "date" not in df.columns:
            continue
        total += len(df)
        year_last = str(pd.to_datetime(df["date"]).max().date())
        last = year_last if last is None or year_last > last else last
    return total, last


def snapshot_rows(s3) -> list[dict]:
    """One ledger row per ticker: the lake's coverage right now."""
    ts = datetime.now(UTC).isoformat()
    rows: list[dict] = []
    for ticker in TICKERS:
        eod: set[str] = set()
        for source in EOD_SOURCES:
            eod.update(list_chain_dates(s3, source, ticker))
        intraday: set[str] = set()
        for prefix in INTRADAY_PREFIXES:
            intraday.update(_date_prefixes(s3, f"{prefix}/ticker={ticker}"))
        ivx_obs, ivx_last = _ivx_stats(s3, ticker)
        rows.append({
            "ts": ts,
            "ticker": ticker,
            "eod_sessions": len(eod),
            "eod_last": max(eod) if eod else None,
            "intraday_sessions": len(intraday),
            "intraday_last": max(intraday) if intraday else None,
            "ivx_obs": ivx_obs,
            "ivx_last": ivx_last,
        })
    return rows


def append_snapshot() -> pd.DataFrame:
    s3 = r2_client()
    rows = snapshot_rows(s3)
    fresh = pd.DataFrame(rows)
    existing = r2_get_parquet(s3, LEDGER_KEY)
    combined = (
        pd.concat([existing, fresh], ignore_index=True)
        if existing is not None and not existing.empty
        else fresh
    )
    r2_put_parquet(s3, LEDGER_KEY, combined)
    for r in rows:
        log.info(
            "%s: eod=%s (last %s) · intraday=%s (last %s) · ivx=%s",
            r["ticker"], r["eod_sessions"], r["eod_last"],
            r["intraday_sessions"], r["intraday_last"], r["ivx_obs"],
        )
    return combined


def main() -> int:
    logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                        format="%(asctime)s %(levelname)s %(message)s")
    combined = append_snapshot()
    log.info("coverage ledger: %d rows total → r2://%s", len(combined), LEDGER_KEY)
    return 0


if __name__ == "__main__":
    sys.exit(main())
