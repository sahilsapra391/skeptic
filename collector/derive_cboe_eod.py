#!/usr/bin/env python3
"""
derive_cboe_eod.py — the day's LAST CBOE recorder snapshot → a canonical
EOD chain (the forward chain record, owner decision 2026-07-08).

Why: the Alpha Vantage leg is premium-gated (dormant) and the Yahoo nightly
snapshot is capped at 60 DTE with no vendor greeks — the recorder already
banks the FULL chain (all expirations, vendor greeks/IV/OI, displayed NBBO
sizes) every minute through the close. The final snapshot of a session,
captured ~16:14 ET, shows the ~15-min-delayed feed at ~16:00 ET — the
closest thing to a true close chain the lake gets for $0. The delay is a
property of the source, disclosed by source="cboe_eod" exactly the way
cboe_minute disclosures work intraday; it is never shifted or hidden.

Writes options/source=cboe_eod/ticker={T}/date={D}/chain.parquet in the
canonical schema (+ source_ts / bid_size / ask_size / iv30 when the
snapshot carries them). Engine precedence (backend app/data/chains.py):
ivolatility > alphavantage > cboe_eod > yahoo > dolthub.

Incremental by SET DIFFERENCE (the F4 self-healing rule): each run derives
exactly the recorder dates absent from the cboe_eod prefix. Per-day gates
(mirroring the iVol backfill gates — a rejected day is logged and retried
next run, never written):
  * the session must be OVER (now ≥ close + 30 min: options close lag +
    feed delay) — a mid-session run must not mint a fake "EOD" chain;
  * the last snapshot must be captured AT/AFTER the equity close — a
    recorder that died mid-day left no honest close record for that date;
  * ≥ 50 rows, ≤ 5% crossed quotes, no expirations before the trading
    date, every |delta| ≤ 1.

Run:  cd collector && uv run python derive_cboe_eod.py [--tickers SPY,QQQ,IWM]
Env:  R2_* vars (same as collect.py).
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
from datetime import datetime, timedelta, timezone
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

from collect import (  # noqa: E402  (env loads first, like the other derives)
    CANONICAL_COLUMNS,
    TICKERS,
    list_chain_dates,
    list_date_prefixes,
    nyse,
    r2_client,
    r2_get_parquet,
    r2_list_keys,
    r2_put_parquet,
)

log = logging.getLogger("cboe_eod")

SNAP_PREFIX = "options_intraday/source=cboe_delayed"
CHAIN_KEY = "options/source=cboe_eod/ticker={ticker}/date={d}/chain.parquet"
# extra columns banked when the snapshot carries them (older snapshots
# predate the recorder's size/iv30 capture — absence is honest, not padded)
EXTRA_COLUMNS = ["source_ts", "bid_size", "ask_size", "iv30"]
# session completeness: options close 15 min after the equity close, the
# feed runs ~15 min delayed — before close+30min the "last" snapshot cannot
# show the close state yet
SESSION_SETTLE_MINUTES = 30
# The delayed feed at capture time T shows ~T−15min, so a snapshot only
# reflects the CLOSE when captured ≥ close+15min. The recorder's 60s cycle
# guarantees a healthy day a snapshot in [close+14:00, close+15:00) (its
# loop runs to close+15 and key stamps truncate to the minute), so ≥ +14min
# is the tightest gate that never rejects a healthy day — a recorder that
# died at 16:02 ET, whose last snapshot shows ~15:47 state, is refused
# rather than minted as a "close" chain (review finding).
CLOSE_CAPTURE_MIN_LAG_MINUTES = 14
MIN_ROWS = 50
MAX_CROSSED_SHARE = 0.05
DELTA_EPS = 1e-6

_SNAP_RE = re.compile(r"snap_(\d{8}T\d{4})Z\.parquet$")


def _session_bounds(d: str) -> tuple[pd.Timestamp, pd.Timestamp] | None:
    """(equity close, derivable-after) for a session date, UTC; None when
    the calendar says d never traded (a recorder date partition that isn't
    a session is a defect worth a loud log, not a chain)."""
    cal = nyse()
    ts = pd.Timestamp(d)
    if not cal.is_session(ts):
        return None
    close = cal.session_close(ts)
    return close, close + timedelta(minutes=SESSION_SETTLE_MINUTES)


def _last_snap(s3, ticker: str, d: str) -> tuple[str, pd.Timestamp] | None:
    """(key, capture ts UTC) of the day's last snapshot. Stamps are
    zero-padded UTC so the lexicographic max is the chronological last."""
    keys = r2_list_keys(s3, f"{SNAP_PREFIX}/ticker={ticker}/date={d}/")
    stamped = []
    for k in keys:
        m = _SNAP_RE.search(k)
        if m:
            stamped.append((m.group(1), k))
    if not stamped:
        return None
    stamp, key = max(stamped)
    ts = pd.Timestamp(datetime.strptime(stamp, "%Y%m%dT%H%M"), tz="UTC")
    return key, ts


def _gate(df: pd.DataFrame, d: str) -> str | None:
    """Reason the day fails a quality gate, else None. Counts only in logs."""
    if len(df) < MIN_ROWS:
        return f"only {len(df)} rows (< {MIN_ROWS})"
    bid = pd.to_numeric(df["bid"], errors="coerce")
    ask = pd.to_numeric(df["ask"], errors="coerce")
    both = bid.notna() & ask.notna() & (bid > 0) & (ask > 0)
    if both.any():
        crossed = float(((bid > ask) & both).sum() / both.sum())
        if crossed > MAX_CROSSED_SHARE:
            return f"crossed share {crossed:.1%} (> {MAX_CROSSED_SHARE:.0%})"
    exp = pd.to_datetime(df["expiration"], errors="coerce")
    if exp.isna().any():
        return f"{int(exp.isna().sum())} unparseable expirations"
    if (exp.dt.date < pd.Timestamp(d).date()).any():
        return "expirations before the trading date"
    delta = pd.to_numeric(df["delta"], errors="coerce")
    if (delta.abs() > 1.0 + DELTA_EPS).any():
        return "|delta| > 1 rows"
    if not pd.to_numeric(df["spot"], errors="coerce").notna().any():
        return "no spot on any row"
    return None


def _normalize(df: pd.DataFrame, ticker: str, d: str) -> pd.DataFrame:
    """Snapshot rows → canonical chain rows for trading date d. The rows
    already ARE canonical-shaped (the recorder writes the same schema);
    this re-stamps identity fields and recomputes dte against d so a
    snapshot captured just past midnight UTC can never mislabel the row."""
    out = df.copy()
    out["ticker"] = ticker
    out["trading_date"] = d
    out["source"] = "cboe_eod"
    out["greeks_source"] = "vendor"
    exp = pd.to_datetime(out["expiration"], errors="coerce")
    out["dte"] = (exp - pd.Timestamp(d)).dt.days
    cols = CANONICAL_COLUMNS + [c for c in EXTRA_COLUMNS if c in out.columns]
    return out[cols]


def derive_ticker(s3, ticker: str, now: pd.Timestamp) -> int:
    have = set(list_chain_dates(s3, "cboe_eod", ticker))
    snap_dates = list_date_prefixes(s3, f"{SNAP_PREFIX}/ticker={ticker}/")
    todo = [d for d in snap_dates if d not in have]
    if not todo:
        log.info("%s: up to date (%d sessions)", ticker, len(have))
        return 0
    written = 0
    for d in todo:
        bounds = _session_bounds(d)
        if bounds is None:
            log.warning("%s %s: recorder date is not an XNYS session — skipped", ticker, d)
            continue
        close, ready_at = bounds
        if now < ready_at:
            log.info("%s %s: session not settled yet (ready %s) — skipped", ticker, d, ready_at)
            continue
        last = _last_snap(s3, ticker, d)
        if last is None:
            log.warning("%s %s: no parseable snapshots — retry next run", ticker, d)
            continue
        key, captured = last
        if captured < close + timedelta(minutes=CLOSE_CAPTURE_MIN_LAG_MINUTES):
            # recorder died before the close was VISIBLE on the delayed
            # feed: nothing on this date can honestly claim to be the EOD
            # chain (retried nightly — cheap, and a later manual backfill
            # of snaps would self-heal it)
            log.warning("%s %s: last snapshot %s cannot show the %s close on a "
                        "~15-min-delayed feed — no honest EOD chain for this "
                        "session", ticker, d, captured, close)
            continue
        df = r2_get_parquet(s3, key)
        if df is None or df.empty:
            log.warning("%s %s: last snapshot unreadable — retry next run", ticker, d)
            continue
        reason = _gate(df, d)
        if reason is not None:
            log.warning("%s %s: rejected — %s", ticker, d, reason)
            continue
        out = _normalize(df, ticker, d)
        r2_put_parquet(s3, CHAIN_KEY.format(ticker=ticker, d=d), out)
        written += 1
    log.info("%s: %d close chains written (%d already banked)", ticker, written, len(have))
    return written


def main() -> int:
    logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                        format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tickers", default=",".join(TICKERS))
    args = ap.parse_args()
    s3 = r2_client()
    now = pd.Timestamp(datetime.now(timezone.utc))
    total = 0
    for ticker in [t.strip().upper() for t in args.tickers.split(",") if t.strip()]:
        total += derive_ticker(s3, ticker, now)
    log.info("done: %d chains written", total)
    return 0


if __name__ == "__main__":
    sys.exit(main())
