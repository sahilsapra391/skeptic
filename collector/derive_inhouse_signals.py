#!/usr/bin/env python3
"""
derive_inhouse_signals.py — cboe_eod chains + our own dailies → the
in-house forward signal series (owner decision 2026-07-08: no vendor
subscriptions; compute what the lake honestly supports).

Two artifacts per ticker (reduction MATH lives in the backend —
app/data/inhouse_signals.py, one implementation, fixture-tested):
  reference/derived/inhouse_signals/ticker={T}.parquet
      date · skew_25d · term_slope_30_90 · atm_iv_30d · atm_iv_90d ·
      net_gex · net_dex · put_call_ratio · max_pain_dist_pct
      — incremental by SET DIFFERENCE over the cboe_eod chain dates
      (self-healing: unreadable chains retry next night; a session whose
      chain derives Nones still writes a row and is not retried).
  reference/derived/hv_inhouse/ticker={T}.parquet
      date · hv_30d — FULL history from the underlying dailies, cheap
      vector math, overwritten every run (the dailies themselves are a
      full-history overwrite; convention probe-pinned vs the frozen
      vendor series, MAE 0.0002 over 5,408 overlap sessions).

Run:  cd collector && uv run python derive_inhouse_signals.py [--tickers SPY,QQQ,IWM]
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
from app.data.inhouse_signals import (  # noqa: E402
    CHAIN_SIGNALS_KEY,
    HV_KEY,
    derive_chain_signal_row,
    hv30_frame,
)

from collect import (  # noqa: E402  (env loads first, like the other derives)
    TICKERS,
    list_chain_dates,
    r2_client,
    r2_get_parquet,
    r2_put_parquet,
)

log = logging.getLogger("inhouse_signals")
_DATE_RE = re.compile(r"date=(\d{4}-\d{2}-\d{2})")


def _load_artifact(s3, key: str) -> tuple[pd.DataFrame | None, set[str]]:
    df = r2_get_parquet(s3, key)
    if df is None or df.empty or "date" not in df.columns:
        return None, set()
    return df, set(df["date"].astype(str))


def _append(s3, key: str, existing: pd.DataFrame | None, rows: list[dict]) -> None:
    fresh = pd.DataFrame(rows)
    combined = (pd.concat([existing, fresh], ignore_index=True)
                if existing is not None and not existing.empty else fresh)
    combined = (combined.drop_duplicates(subset=["date"], keep="last")
                .sort_values("date").reset_index(drop=True))
    r2_put_parquet(s3, key, combined)


def _closes_by_date(daily: pd.DataFrame | None) -> dict[str, float]:
    if daily is None or daily.empty or not {"date", "close"}.issubset(daily.columns):
        return {}
    dates = pd.to_datetime(daily["date"], errors="coerce")
    closes = pd.to_numeric(daily["close"], errors="coerce")
    return {d.date().isoformat(): float(c)
            for d, c in zip(dates, closes)
            if pd.notna(d) and pd.notna(c)}


def derive_ticker(s3, ticker: str) -> int:
    daily = r2_get_parquet(s3, f"underlying/ticker={ticker}/daily.parquet")

    # HV — full history, overwritten (deterministic vector math)
    hv = hv30_frame(daily)
    if hv.empty:
        log.warning("%s: no dailies → hv_inhouse skipped this run", ticker)
    else:
        r2_put_parquet(s3, HV_KEY.format(ticker=ticker), hv)

    # chain signals — set-difference incremental over cboe_eod sessions
    key = CHAIN_SIGNALS_KEY.format(ticker=ticker)
    existing, have = _load_artifact(s3, key)
    todo = [d for d in list_chain_dates(s3, "cboe_eod", ticker) if d not in have]
    if not todo:
        log.info("%s: chain signals up to date (%d sessions)", ticker, len(have))
        return 0
    closes = _closes_by_date(daily)
    rows: list[dict] = []
    skipped: list[str] = []
    for d in todo:
        chain = r2_get_parquet(
            s3, f"options/source=cboe_eod/ticker={ticker}/date={d}/chain.parquet")
        if chain is None or chain.empty:
            skipped.append(d)  # unreadable driving input — retry next run
            continue
        row = derive_chain_signal_row(chain, d, closes.get(d))
        row["date"] = d
        rows.append(row)
    if rows:
        _append(s3, key, existing, rows)
        log.info("%s: derived %d sessions → r2://%s", ticker, len(rows), key)
    if skipped:
        log.warning("%s: %d sessions unreadable, retry next run: %s%s",
                    ticker, len(skipped), ", ".join(skipped[:10]),
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
    for ticker in [t.strip().upper() for t in args.tickers.split(",") if t.strip()]:
        total += derive_ticker(s3, ticker)
    log.info("done: %d chain-signal sessions derived", total)
    return 0


if __name__ == "__main__":
    sys.exit(main())
