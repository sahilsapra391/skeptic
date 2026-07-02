#!/usr/bin/env python3
"""
coverage.py — report the lake's coverage from R2 alone (DATA-PIPELINE §8.4).

Temporary stand-in for /api/data/coverage until M2. Prints, per ticker and
source: date ranges and session counts; underlying/VIX history ranges; the
backfill frontier; AV budget state; and the latest quality flags.

Env vars required: R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET.
"""

from __future__ import annotations

import json

from collect import (
    BUDGET_KEY,
    FRONTIER_KEY,
    QUALITY_KEY,
    TICKERS,
    list_chain_dates,
    r2_client,
    r2_get_json,
    r2_get_parquet,
    r2_list_keys,
)


def main() -> None:
    s3 = r2_client()
    print("=" * 62)
    print("Skeptic data lake coverage")
    print("=" * 62)

    print("\nOptions chains")
    for source in ("alphavantage", "yahoo", "dolthub"):
        for ticker in TICKERS:
            try:
                dates = list_chain_dates(s3, source, ticker)
            except Exception as exc:
                print(f"  {source:>13} {ticker}: listing failed: {exc}")
                continue
            if dates:
                print(f"  {source:>13} {ticker}: {len(dates):>5} sessions   "
                      f"{dates[0]} -> {dates[-1]}")
            else:
                print(f"  {source:>13} {ticker}:     0 sessions")

    print("\nMinute-bar options lake (alpaca)")
    for ticker in TICKERS:
        prefix = f"options_minute/source=alpaca/ticker={ticker}/date="
        try:
            keys = r2_list_keys(s3, prefix)
        except Exception as exc:
            print(f"  {ticker}: listing failed: {exc}")
            continue
        dates = sorted(k.split("date=")[1].split("/")[0] for k in keys)
        if dates:
            print(f"  {ticker}: {len(dates):>5} sessions   {dates[0]} -> {dates[-1]}")
        else:
            print(f"  {ticker}:     0 sessions")
    bf_state = r2_get_json(s3, "state/alpaca_backfill.json", {})
    done = sum(1 for t, months in bf_state.items() if t != "_underlying"
               for st in months.values() if st.get("status") == "done")
    if bf_state:
        print(f"  backfill frontier: {done} ticker-months done")

    print("\nIntraday quote snapshots (forward record, job 5)")
    import os as _os
    for source in ("cboe_delayed", "yahoo"):
        for ticker in TICKERS:
            prefix = f"options_intraday/source={source}/ticker={ticker}/date="
            dates = []
            try:
                paginator = s3.get_paginator("list_objects_v2")
                for page in paginator.paginate(Bucket=_os.environ["R2_BUCKET"],
                                               Prefix=prefix, Delimiter="/"):
                    for cp in page.get("CommonPrefixes", []):
                        dates.append(cp["Prefix"].split("date=")[1].rstrip("/"))
            except Exception as exc:
                print(f"  {source:>13} {ticker}: listing failed: {exc}")
                continue
            dates.sort()
            if dates:
                print(f"  {source:>13} {ticker}: {len(dates):>5} sessions   "
                      f"{dates[0]} -> {dates[-1]}")
            else:
                print(f"  {source:>13} {ticker}:     0 sessions")

    print("\nUnderlying dailies + VIX")
    targets = [(t, f"underlying/ticker={t}/daily.parquet") for t in TICKERS]
    targets.append(("^VIX", "reference/vix_daily.parquet"))
    for symbol, key in targets:
        df = r2_get_parquet(s3, key)
        if df is None or df.empty:
            print(f"  {symbol:>5}: MISSING ({key})")
        else:
            lo = df["date"].min().date()
            hi = df["date"].max().date()
            print(f"  {symbol:>5}: {len(df):>6} rows   {lo} -> {hi}")

    print("\nBackfill frontier (walking backward toward 2008-01-02)")
    frontier = r2_get_json(s3, FRONTIER_KEY, {})
    if frontier:
        for ticker, d in frontier.items():
            print(f"  {ticker}: {d}")
    else:
        print("  (no frontier state yet)")

    budget = r2_get_json(s3, BUDGET_KEY, {})
    if budget:
        print(f"\nAV budget: {budget.get('used', '?')}/25 used on {budget.get('date', '?')}")

    quality = r2_get_json(s3, QUALITY_KEY, {})
    if quality:
        print(f"\nQuality flags (generated {quality.get('generated_at', '?')})")
        print(json.dumps(quality.get("tickers", {}), indent=2))
    else:
        print("\nQuality flags: none yet (run --mode quality)")


if __name__ == "__main__":
    main()
