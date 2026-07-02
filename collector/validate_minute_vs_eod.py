#!/usr/bin/env python3
"""
validate_minute_vs_eod.py — one-off cross-source validation over the
SPY overlap window (2024-02 → 2026-06): DoltHub EOD quote chains vs
Alpaca 1-minute trade bars.

The two sources share exactly one observable — price level — so the check
is: for every contract present in both on the same session, does the day's
LAST TRADE (Alpaca, last bar ≤ options close) sit inside/near the day's
CLOSING BID/ASK (DoltHub)? Plus structural alignment: joined contracts
prove that expiration parsing, strike scaling, and date attribution agree
across two independently written ingest pipelines. Tick-exact equality is
not expected (trades lag quotes on illiquid strikes; the archive chain is
filtered to ~3 expirations, ±30% strikes) — systematic disagreement is
what would indicate a bug.

Run once after both backfills complete; record results in BUILD-LOG.
Env: R2_*. Read-only against the lake.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from collections import defaultdict
from datetime import date

import pandas as pd

from collect import nyse, r2_client, r2_get_parquet

log = logging.getLogger("validate")

OVERLAP_START = date(2024, 2, 5)   # Alpaca history start
OVERLAP_END = date(2026, 6, 30)    # DoltHub window end
TICKER = "SPY"
ABS_TOL = 0.05                     # $ tolerance beyond the quoted spread
REL_TOL = 0.02                     # or 2% of mid, whichever is larger


def underlying_minute_closes(s3, cache: dict, d: str) -> pd.Series | None:
    month = d[:7]
    if month not in cache:
        df = r2_get_parquet(s3, f"underlying_minute/ticker={TICKER}/month={month}/bars.parquet")
        if df is None or df.empty:
            cache[month] = None
        else:
            df = df.assign(et=pd.to_datetime(df["minute_ts"]).dt.tz_convert("America/New_York"))
            cache[month] = df
    df = cache[month]
    if df is None:
        return None
    day = df[df["et"].dt.date.astype(str) == d]
    return day.set_index("et")["close"].sort_index() if len(day) else None


def session_report(s3, und_cache: dict, d: str) -> dict | None:
    eod = r2_get_parquet(s3, f"options/source=dolthub/ticker={TICKER}/date={d}/chain.parquet")
    bars = r2_get_parquet(s3, f"options_minute/source=alpaca/ticker={TICKER}/date={d}/bars.parquet")
    if eod is None or eod.empty or bars is None or bars.empty:
        return None
    spots = underlying_minute_closes(s3, und_cache, d)
    if spots is None:
        return None  # underlying minute bars not landed yet; session recounted next run
    # last trade of the session per contract — but a stale print from hours
    # before the close legitimately disagrees with the closing quote (delta
    # times the intraday move), so price comparison uses only contracts whose
    # last trade happened near the close. Diagnosed on 2024-05-31: all large
    # "violations" were deep-ITM strikes last traded before 13:30.
    bars = bars.assign(_et=pd.to_datetime(bars["minute_ts"]).dt.tz_convert("America/New_York"))
    last = (bars.sort_values("minute_ts").groupby(["expiration", "right", "strike"])
            .agg(last_trade=("close", "last"), last_et=("_et", "last"),
                 day_volume=("volume", "sum")).reset_index())
    last = last[last["last_et"] >= (last["last_et"].dt.normalize()
                                    + pd.Timedelta(hours=15, minutes=45))]
    last["expiration"] = last["expiration"].astype(str)
    eod = eod.copy()
    eod["expiration"] = eod["expiration"].astype(str)
    j = eod.merge(last, on=["expiration", "right", "strike"], how="left")

    two_sided = j[(j["bid"].notna()) & (j["ask"].notna()) & (j["bid"] > 0)]
    traded = two_sided[two_sided["last_trade"].notna()].copy()
    if len(two_sided) == 0 or len(traded) == 0:
        return None
    # delta-adjust each print to the close: even a 15:50 trade on a high-delta
    # contract drifts by delta x (close - spot@print); vendor delta + our own
    # underlying minute bars remove that first-order timing effect
    spot_close = float(eod["spot"].iloc[0])
    spot_at_print = traded["last_et"].map(
        lambda ts: float(spots.asof(ts)) if not pd.isna(spots.asof(ts)) else spot_close)
    traded["cmp_price"] = traded["last_trade"] + traded["delta"] * (spot_close - spot_at_print)
    mid = (traded["bid"] + traded["ask"]) / 2
    # The vendor's capture moment is not exactly the close (documented since
    # the eval); on fast closes that skews every high-delta contract by
    # delta x (a dollar or two). Self-calibrate the session's effective
    # capture spot from its own high-|delta| contracts and re-reference.
    hd = traded[traded["delta"].abs() >= 0.5]
    offset = 0.0
    if len(hd) >= 5:
        offset = float(((hd["bid"] + hd["ask"]) / 2 - hd["cmp_price"]).div(hd["delta"]).median())
        offset = max(-3.0, min(3.0, offset))
    traded["cmp_price"] = traded["cmp_price"] + traded["delta"] * offset
    tol = pd.concat([pd.Series(ABS_TOL, index=mid.index), mid * REL_TOL], axis=1).max(axis=1)
    inside = ((traded["cmp_price"] >= traded["bid"] - tol) &
              (traded["cmp_price"] <= traded["ask"] + tol))
    viol = traded[~inside]
    return {
        "date": d,
        "eod_contracts": len(eod),
        "two_sided": len(two_sided),
        "joined_traded": len(traded),
        "join_rate": len(traded) / len(two_sided),
        "capture_offset": round(offset, 3),
        "violations": len(viol),
        "worst_dev": float(((viol["cmp_price"] - (viol["bid"] + viol["ask"]) / 2).abs()
                            / ((viol["bid"] + viol["ask"]) / 2)).max()) if len(viol) else 0.0,
    }


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default=OVERLAP_START.isoformat())
    ap.add_argument("--end", default=OVERLAP_END.isoformat())
    args = ap.parse_args()

    s3 = r2_client()
    from collect import r2_get_json
    quarantined = set(r2_get_json(s3, "state/dolthub_backfill.json", {})
                      .get("quarantined_stale", {}))
    und_cache: dict = {}
    sessions = [s.date().isoformat() for s in nyse().sessions_in_range(args.start, args.end)
                if s.date().isoformat() not in quarantined]
    reports, skipped = [], 0
    for i, d in enumerate(sessions):
        r = session_report(s3, und_cache, d)
        if r is None:
            skipped += 1
            continue
        reports.append(r)
        if i % 100 == 0:
            log.info("… %d/%d sessions, %d compared", i, len(sessions), len(reports))

    if not reports:
        log.error("nothing to compare — are both backfills complete?")
        return 1

    df = pd.DataFrame(reports)
    df["viol_rate"] = df["violations"] / df["joined_traded"].clip(lower=1)
    out_csv = os.environ.get("VALIDATE_SESSIONS_CSV")
    if out_csv:
        df.to_csv(out_csv, index=False)
    total_joined = int(df["joined_traded"].sum())
    total_viol = int(df["violations"].sum())
    print("\n================ cross-source validation: DoltHub EOD vs Alpaca minute ================")
    print(f"window {args.start} → {args.end}: {len(df)} sessions compared, "
          f"{skipped} skipped (missing on one side — archive gaps expected pre-2024-09)")
    print(f"contracts joined (two-sided quote + near-close trade): {total_joined:,}")
    print(f"mean join rate: {df['join_rate'].mean():.1%}")
    print(f"last-trade outside [bid-tol, ask+tol]: {total_viol:,} "
          f"({total_viol / max(total_joined, 1):.2%})   tol = max(${ABS_TOL}, {REL_TOL:.0%} of mid)")
    df["ym"] = df["date"].str[:7]
    yearly = df.groupby(df["date"].str[:4]).agg(
        sessions=("date", "count"), joined=("joined_traded", "sum"),
        violations=("violations", "sum"), join_rate=("join_rate", "mean"))
    yearly["viol_rate"] = yearly["violations"] / yearly["joined"]
    print("\nper year:")
    print(yearly.to_string(float_format=lambda x: f"{x:.2%}" if x < 1 else f"{x:,.0f}"))
    worst = df.nlargest(5, "violations")[["date", "joined_traded", "violations", "worst_dev"]]
    print("\nworst sessions:")
    print(worst.to_string(index=False))
    verdict = "PASS" if total_viol / max(total_joined, 1) < 0.02 else "INVESTIGATE"
    print(f"\nVERDICT: {verdict} (threshold: <2% of joined contracts outside the widened spread)")
    return 0 if verdict == "PASS" else 2


if __name__ == "__main__":
    sys.exit(main())
