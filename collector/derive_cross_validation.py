#!/usr/bin/env python3
"""
derive_cross_validation.py — nightly cross-source validation (ENGINE-V4 F7).

Reduces every session (or Massive symbol) where two INDEPENDENT sources
overlap to uniform agreement records:
  reference/derived/cross_validation/pair={pair}/ticker={T}.parquet
      date · joined · checked · within_band · agreement_rate (+extras)

Pairs (comparator MATH lives in app/data/cross_validation.py — one
implementation, fixture-tested):
  dolthub_vs_alpaca   SPY: EOD closing quotes vs minute trades (near-close
                      + delta-adjusted, the proven one-off methodology)
  dolthub_vs_uw       SPY: chain volume/OI per expiry vs UW volume_oi_expiry
  yahoo_vs_ivol5m     EOD quote chain vs the last 5-min NBBO (0-2 DTE)
  massive_vs_ivol5m   QQQ/IWM: daily close vs the 5-min NBBO day range —
                      unit of work is the SYMBOL (each aggs file holds the
                      contract's whole history); per-session COUNTS
                      accumulate as the crawl lands (F5-deferred check)

Incremental by SET DIFFERENCE per pair (the F4 self-healing rule): session
pairs derive exactly the overlapping sessions absent from the artifact;
the Massive pair tracks processed symbols in a companion parquet.
Unreadable inputs leave no row and retry next run.

Run:  cd collector && uv run python derive_cross_validation.py [--pairs a,b]
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
from app.data.cross_validation import (  # noqa: E402
    PAIR_KEY,
    compare_dolthub_alpaca,
    compare_dolthub_uw,
    compare_massive_ivol5m,
    compare_quote_close,
)

from collect import r2_client, r2_get_parquet, r2_put_parquet  # noqa: E402

log = logging.getLogger("cross_validation")
_DATE_RE = re.compile(r"date=(\d{4}-\d{2}-\d{2})")
MASSIVE_DONE_KEY = ("reference/derived/cross_validation/"
                    "massive_symbols_done/ticker={ticker}.parquet")


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


def _artifact(s3, key: str) -> tuple[pd.DataFrame | None, set[str]]:
    df = r2_get_parquet(s3, key)
    if df is None or df.empty or "date" not in df.columns:
        return None, set()
    return df, set(df["date"].astype(str))


def _write(s3, key: str, existing: pd.DataFrame | None,
           rows: list[dict]) -> None:
    fresh = pd.DataFrame(rows)
    combined = (pd.concat([existing, fresh], ignore_index=True)
                if existing is not None and not existing.empty else fresh)
    combined = (combined.drop_duplicates(subset=["date"], keep="last")
                .sort_values("date").reset_index(drop=True))
    r2_put_parquet(s3, key, combined)


class _UndMinutes:
    """Month-cached SPY underlying minute closes (the one-off's cache)."""

    def __init__(self, s3) -> None:
        self.s3 = s3
        self.cache: dict[str, pd.DataFrame | None] = {}

    def day(self, d: str) -> pd.Series | None:
        month = d[:7]
        if month not in self.cache:
            df = r2_get_parquet(
                self.s3, f"underlying_minute/ticker=SPY/month={month}/bars.parquet")
            if df is None or df.empty:
                self.cache[month] = None
            else:
                self.cache[month] = df.assign(
                    et=pd.to_datetime(df["minute_ts"]).dt.tz_convert(
                        "America/New_York"))
            if len(self.cache) > 4:  # bound the month cache (OOM guard)
                self.cache.pop(next(iter(self.cache)))
        df = self.cache[month]
        if df is None:
            return None
        day = df[df["et"].dt.date.astype(str) == d]
        return day.set_index("et")["close"].sort_index() if len(day) else None


def run_dolthub_vs_alpaca(s3) -> int:
    key = PAIR_KEY.format(pair="dolthub_vs_alpaca", ticker="SPY")
    existing, have = _artifact(s3, key)
    dolt = set(_sessions(s3, "options/source=dolthub/ticker=SPY/"))
    alp = set(_sessions(s3, "options_minute/source=alpaca/ticker=SPY/"))
    todo = sorted((dolt & alp) - have)
    if not todo:
        log.info("dolthub_vs_alpaca: up to date (%d sessions)", len(have))
        return 0
    und = _UndMinutes(s3)
    rows: list[dict] = []
    skipped = 0
    for d in todo:
        eod = r2_get_parquet(s3, f"options/source=dolthub/ticker=SPY/date={d}/chain.parquet")
        bars = r2_get_parquet(s3, f"options_minute/source=alpaca/ticker=SPY/date={d}/bars.parquet")
        spots = und.day(d)
        rec = (compare_dolthub_alpaca(eod, bars, spots)
               if spots is not None else None)
        if rec is None:
            skipped += 1
            continue
        rec["date"] = d
        rows.append(rec)
    if rows:
        _write(s3, key, existing, rows)
    log.info("dolthub_vs_alpaca: derived %d sessions (%d skipped) → r2://%s",
             len(rows), skipped, key)
    return len(rows)


def run_dolthub_vs_uw(s3) -> int:
    key = PAIR_KEY.format(pair="dolthub_vs_uw", ticker="SPY")
    existing, have = _artifact(s3, key)
    dolt = set(_sessions(s3, "options/source=dolthub/ticker=SPY/"))
    uw = set(_sessions(s3, "uw/volume_oi_expiry/ticker=SPY/"))
    todo = sorted((dolt & uw) - have)
    if not todo:
        log.info("dolthub_vs_uw: up to date (%d sessions)", len(have))
        return 0
    rows: list[dict] = []
    for d in todo:
        rec = compare_dolthub_uw(
            r2_get_parquet(s3, f"options/source=dolthub/ticker=SPY/date={d}/chain.parquet"),
            r2_get_parquet(s3, f"uw/volume_oi_expiry/ticker=SPY/date={d}/rows.parquet"),
        )
        if rec is None:
            continue
        rec["date"] = d
        rows.append(rec)
    if rows:
        _write(s3, key, existing, rows)
    log.info("dolthub_vs_uw: derived %d sessions → r2://%s", len(rows), key)
    return len(rows)


def _ivol_last_quotes(day: pd.DataFrame) -> pd.DataFrame | None:
    """Last 5-min NBBO per contract from an iVol intraday day frame."""
    if day is None or day.empty:
        return None
    day = day.sort_values("minute_ts")
    return (day.groupby(["expiration", "right", "strike"])
            .agg(bid=("bid", "last"), ask=("ask", "last")).reset_index())


def run_yahoo_vs_ivol5m(s3, ticker: str) -> int:
    key = PAIR_KEY.format(pair="yahoo_vs_ivol5m", ticker=ticker)
    existing, have = _artifact(s3, key)
    yah = set(_sessions(s3, f"options/source=yahoo/ticker={ticker}/"))
    iv = set(_sessions(s3, f"options_intraday/source=ivolatility/ticker={ticker}/"))
    todo = sorted((yah & iv) - have)
    if not todo:
        log.info("yahoo_vs_ivol5m %s: up to date (%d sessions)", ticker, len(have))
        return 0
    rows: list[dict] = []
    for d in todo:
        ref = r2_get_parquet(s3, f"options/source=yahoo/ticker={ticker}/date={d}/chain.parquet")
        day = r2_get_parquet(
            s3, f"options_intraday/source=ivolatility/ticker={ticker}/date={d}/bars.parquet")
        rec = compare_quote_close(ref, _ivol_last_quotes(day))
        if rec is None:
            continue
        rec["date"] = d
        rows.append(rec)
    if rows:
        _write(s3, key, existing, rows)
    log.info("yahoo_vs_ivol5m %s: derived %d sessions → r2://%s",
             ticker, len(rows), key)
    return len(rows)


def run_massive_vs_ivol5m(s3, ticker: str) -> int:
    """Unit of work = the SYMBOL (each Massive aggs file holds one
    contract's whole daily history). Per-session COUNTS accumulate into
    the artifact as the crawl lands; agreement_rate recomputed on write."""
    key = PAIR_KEY.format(pair="massive_vs_ivol5m", ticker=ticker)
    done_key = MASSIVE_DONE_KEY.format(ticker=ticker)
    done_df = r2_get_parquet(s3, done_key)
    done: set[str] = (set(done_df["symbol"].astype(str))
                      if done_df is not None and not done_df.empty else set())
    paginator = s3.get_paginator("list_objects_v2")
    symbols: list[str] = []
    prefix = f"reference/massive/option_agg/ticker={ticker}/"
    for page in paginator.paginate(Bucket=os.environ["R2_BUCKET"], Prefix=prefix):
        for obj in page.get("Contents", []):
            m = re.search(r"symbol=([^/]+)\.parquet$", obj["Key"])
            if m and m.group(1) not in done:
                symbols.append(m.group(1))
    if not symbols:
        log.info("massive_vs_ivol5m %s: up to date (%d symbols)", ticker, len(done))
        return 0
    existing, _ = _artifact(s3, key)
    counts: dict[str, dict[str, int]] = {}
    if existing is not None:
        for row in existing.to_dict("records"):
            counts[str(row["date"])] = {
                "joined": int(row["joined"]), "checked": int(row["checked"]),
                "within_band": int(row["within_band"]),
            }
    day_cache: dict[str, pd.DataFrame | None] = {}
    processed: list[str] = []
    for sym in symbols:
        m_df = r2_get_parquet(s3, f"{prefix}symbol={sym}.parquet")
        if m_df is None or m_df.empty or "date" not in m_df.columns:
            processed.append(sym)  # empty aggs file — nothing to compare, done
            continue
        for d, day_rows in m_df.groupby(m_df["date"].astype(str)):
            if d not in day_cache:
                if len(day_cache) > 30:  # bound (OOM guard)
                    day_cache.pop(next(iter(day_cache)))
                day_cache[d] = r2_get_parquet(
                    s3, f"options_intraday/source=ivolatility/ticker={ticker}"
                        f"/date={d}/bars.parquet")
            rec = compare_massive_ivol5m(day_rows, day_cache[d])
            if rec is None:
                continue
            c = counts.setdefault(d, {"joined": 0, "checked": 0, "within_band": 0})
            c["joined"] += rec["joined"]
            c["checked"] += rec["checked"]
            c["within_band"] += rec["within_band"]
        processed.append(sym)
    rows = [{"date": d, **c,
             "agreement_rate": (round(c["within_band"] / c["checked"], 4)
                                if c["checked"] else None)}
            for d, c in counts.items()]
    if rows:
        _write(s3, key, None, rows)  # counts already include existing
    all_done = pd.DataFrame({"symbol": sorted(done | set(processed))})
    r2_put_parquet(s3, done_key, all_done)
    log.info("massive_vs_ivol5m %s: +%d symbols (%d total) → r2://%s",
             ticker, len(processed), len(done) + len(processed), key)
    return len(processed)


def main() -> int:
    logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                        format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pairs", default="all")
    args = ap.parse_args()
    want = (set(args.pairs.split(",")) if args.pairs != "all"
            else {"dolthub_vs_alpaca", "dolthub_vs_uw", "yahoo_vs_ivol5m",
                  "massive_vs_ivol5m"})
    s3 = r2_client()
    n = 0
    if "dolthub_vs_alpaca" in want:
        n += run_dolthub_vs_alpaca(s3)
    if "dolthub_vs_uw" in want:
        n += run_dolthub_vs_uw(s3)
    if "yahoo_vs_ivol5m" in want:
        for t in ("SPY", "QQQ", "IWM"):
            n += run_yahoo_vs_ivol5m(s3, t)
    if "massive_vs_ivol5m" in want:
        for t in ("QQQ", "IWM"):
            n += run_massive_vs_ivol5m(s3, t)
    log.info("done: %d units derived", n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
