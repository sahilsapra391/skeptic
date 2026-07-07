#!/usr/bin/env python3
"""
ledger.py — the coverage ledger (ENGINE-V3 D3a, data-arrival hook)
           + the per-session resolution map (ENGINE-V4 F0).

After every collection run, append one row PER TICKER to
state/coverage_ledger.parquet recording what the lake holds right now:
EOD chain sessions, intraday 5-minute sessions, IVX observations, and
their latest dates (F0 adds UW minute/daily, IVS and Massive counts —
additive columns, old readers unaffected). Deltas between any two ledger
rows are what the nightly auto-unlock scan (D3b) and the weekly priority
ranking (D3d) reason from — "N new sessions arrived since this verdict
was refused" becomes a computable fact instead of a hope.

F0 additionally rebuilds two artifacts each run (self-improvement thesis:
new data flows into runs automatically, no redeploy):
  state/resolution_map/ticker={T}.parquet — per-session clock_resolution /
    quote_resolution (derivation imported from backend app/data/resolution.py
    so the honesty-critical mapping has exactly ONE implementation, tested
    in the backend battery)
  state/source_coverage.json — new-source coverage windows (UW families,
    IVS, Massive, tape) for /api/data/coverage — the backend never has to
    list these prefixes in a request path.

Append-only; one row group per run keeps the file tiny (a year of nightly
runs ≈ 750 rows). Never logs chain data rows — counts and dates only.

Run:  cd collector && uv run python ledger.py [--skip-resolution]
Env:  R2_* vars (same as collect.py).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

# single-source the resolution derivation + UW family registry from the
# backend (see module doc) — these import only pandas/boto3/stdlib
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.data.resolution import (  # noqa: E402
    RESOLUTION_MAP_KEY,
    derive_resolution_rows,
)
from app.data.uw import MARKET_DATE_FAMILIES, TICKER_DATE_FAMILIES  # noqa: E402


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


def _list_keys(s3, prefix: str) -> list[str]:
    keys: list[str] = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=os.environ["R2_BUCKET"], Prefix=prefix):
        keys.extend(item["Key"] for item in page.get("Contents", []))
    return keys


def _uw_minute_by_session(s3, ticker: str) -> dict[str, int]:
    """session → distinct contracts with banked UW 1-min bars (full key
    listing — collector context only, this never runs in a request path)."""
    per_date: dict[str, set[str]] = defaultdict(set)
    for key in _list_keys(s3, f"uw/option_intraday/ticker={ticker}/"):
        md = re.search(r"date=(\d{4}-\d{2}-\d{2})", key)
        ms = re.search(r"symbol=([^/]+)", key)
        if md and ms:
            per_date[md.group(1)].add(ms.group(1))
    return {d: len(syms) for d, syms in per_date.items()}


def _uw_families_by_session(s3, ticker: str) -> dict[str, int]:
    """session → count of UW per-session signal families banked that date
    (per-ticker families + the market-wide ones, which apply to every
    ticker's session)."""
    counts: dict[str, int] = defaultdict(int)
    for family in sorted(TICKER_DATE_FAMILIES):
        for d in _date_prefixes(s3, f"uw/{family}/ticker={ticker}"):
            counts[d] += 1
    for family in sorted(MARKET_DATE_FAMILIES):
        for d in _date_prefixes(s3, f"uw/{family}"):
            counts[d] += 1
    return dict(counts)


def snapshot_rows(s3) -> list[dict]:
    """One ledger row per ticker: the lake's coverage right now.
    F0 columns are ADDITIVE — D3b/D3d readers of the original columns are
    untouched; pre-F0 rows read back with NaN in the new columns."""
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
        minute = _uw_minute_by_session(s3, ticker)
        ivs = _date_prefixes(s3, f"reference/ivol/ivs/ticker={ticker}")
        massive_aggs = len(_list_keys(
            s3, f"reference/massive/option_agg/ticker={ticker}/"))
        tape = _date_prefixes(s3, f"uw/option_tape/ticker={ticker}")
        rows.append({
            "ts": ts,
            "ticker": ticker,
            "eod_sessions": len(eod),
            "eod_last": max(eod) if eod else None,
            "intraday_sessions": len(intraday),
            "intraday_last": max(intraday) if intraday else None,
            "ivx_obs": ivx_obs,
            "ivx_last": ivx_last,
            # F0 additive columns
            "uw_minute_sessions": len(minute),
            "uw_minute_last": max(minute) if minute else None,
            "ivs_sessions": len(ivs),
            "ivs_last": ivs[-1] if ivs else None,
            "massive_agg_symbols": massive_aggs,
            "tape_sessions": len(tape),
        })
    return rows


def build_resolution_maps(s3) -> None:
    """Rebuild state/resolution_map/ticker={T}.parquet per ticker (F0).
    Derivation is the backend's single implementation; this side only
    gathers the listings. Runs after every collection — new sessions and
    finer data upgrade the map automatically (self-improvement thesis)."""
    for ticker in TICKERS:
        minute = _uw_minute_by_session(s3, ticker)
        ivol_5m = _date_prefixes(
            s3, f"options_intraday/source=ivolatility/ticker={ticker}")
        recorder = _date_prefixes(
            s3, f"options_intraday/source=cboe_delayed/ticker={ticker}")
        eod: set[str] = set()
        for source in EOD_SOURCES:
            eod.update(list_chain_dates(s3, source, ticker))
        ivs = _date_prefixes(s3, f"reference/ivol/ivs/ticker={ticker}")
        families = _uw_families_by_session(s3, ticker)
        rows = derive_resolution_rows(
            minute_contracts_by_session=minute,
            ivol_5min_sessions=ivol_5m,
            recorder_sessions=recorder,
            eod_sessions=sorted(eod),
            ivs_sessions=ivs,
            uw_families_by_session=families,
        )
        key = RESOLUTION_MAP_KEY.format(ticker=ticker)
        r2_put_parquet(s3, key, pd.DataFrame(rows))
        n_min = sum(1 for r in rows if r["clock_resolution"] == "minute")
        n_5m = sum(1 for r in rows if r["clock_resolution"] == "five_min")
        log.info("%s resolution map: %d sessions (minute=%d · five_min=%d) → r2://%s",
                 ticker, len(rows), n_min, n_5m, key)


SOURCE_COVERAGE_KEY = "state/source_coverage.json"


def build_source_coverage(s3) -> None:
    """New-source coverage windows for /api/data/coverage (F0) — computed
    here so the backend never lists these prefixes in a request path."""
    def _window(dates: list[str]) -> dict | None:
        if not dates:
            return None
        return {"sessions": len(dates), "first": dates[0], "last": dates[-1]}

    uw_daily: dict[str, dict] = {}
    for family in sorted(TICKER_DATE_FAMILIES):
        per_ticker = {
            t: w for t in TICKERS
            if (w := _window(_date_prefixes(s3, f"uw/{family}/ticker={t}")))
        }
        if per_ticker:
            uw_daily[family] = per_ticker
    for family in sorted(MARKET_DATE_FAMILIES):
        w = _window(_date_prefixes(s3, f"uw/{family}"))
        if w:
            uw_daily[family] = {"market": w}

    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "uw_daily": uw_daily,
        "uw_minute": {
            t: _window(sorted(_uw_minute_by_session(s3, t))) for t in TICKERS
        },
        "uw_tape": {
            t: _window(_date_prefixes(s3, f"uw/option_tape/ticker={t}"))
            for t in TICKERS
        },
        "ivs": {
            t: _window(_date_prefixes(s3, f"reference/ivol/ivs/ticker={t}"))
            for t in TICKERS
        },
        "massive": {
            t: {"agg_symbols": len(_list_keys(
                s3, f"reference/massive/option_agg/ticker={t}/"))}
            for t in TICKERS
        },
    }
    s3.put_object(
        Bucket=os.environ["R2_BUCKET"], Key=SOURCE_COVERAGE_KEY,
        Body=json.dumps(payload, indent=1).encode(),
        ContentType="application/json",
    )
    log.info("source coverage → r2://%s (%d uw families)",
             SOURCE_COVERAGE_KEY, len(uw_daily))


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
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--skip-resolution", action="store_true",
                    help="append the ledger row only (skip F0 artifacts)")
    args = ap.parse_args()
    combined = append_snapshot()
    log.info("coverage ledger: %d rows total → r2://%s", len(combined), LEDGER_KEY)
    if not args.skip_resolution:
        s3 = r2_client()
        build_resolution_maps(s3)
        build_source_coverage(s3)
    return 0


if __name__ == "__main__":
    sys.exit(main())
