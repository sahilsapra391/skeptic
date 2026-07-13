#!/usr/bin/env python3
"""
intraday.py — forward minute-by-minute options quote recorder (DATA-PIPELINE
job 5). Runs as a long-lived launchd agent on the owner's Mac (GitHub Actions
free minutes cannot host a 6.75h/day loop — see docs/INTRADAY-OPTIONS-DATA-EVAL.md).

Every minute of the options session (NYSE open → close + 15 min, XNYS
calendar-aware including early closes):
  - CBOE delayed-quote JSON per ticker: full chain with bid/ask, displayed
    NBBO sizes, IV, greeks, OI, volume in ONE request per underlying
    (~3 req/min total), plus the underlying's cumulative session volume and
    the feed's 30d IV index (iv30) per snapshot. Quotes are ~15-min delayed;
    snapshot_ts records capture time, source_ts the feed's own stamp. These
    snapshots are the forward fill-quote record the Alpaca bar history lacks
    (M1.5 step-0 finding C) AND the raw material for the nightly cboe_eod
    close chain (collector/derive_cboe_eod.py).
  - Every --yahoo-every minutes (default 15): the yfinance chain snapshot
    (reusing collect.yahoo_snapshot) as cross-source redundancy, dispatched
    to a single worker thread (YahooLeg) so a slow Yahoo session can never
    starve the per-minute CBOE cadence — a cold first cycle at the open
    (crumb fetch + per-ticker retries) ran 09:33→09:38 ET on 2026-07-13 and
    cost the 13:34–13:38Z CBOE snapshots. Yahoo at 1-min cadence would need
    ~120 req/min and risks throttling the same source the nightly EOD record
    depends on, so it stays at a gentle cadence; a tick that fires while the
    previous leg is still in flight is skipped, never queued.

Layout: options_intraday/source={cboe_delayed,yahoo}/ticker=T/date=D/
snap_YYYYMMDDTHHMMZ.parquet — same snap_* shape as the EOD Yahoo record.

Missing R2 credentials → clear log line + exit 78; launchd's ThrottleInterval
retries later, so filling collector/.env is all it takes to go live.

Data goes to R2 only. Never commit data to git.
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests

log = logging.getLogger("intraday")
_ET = ZoneInfo("America/New_York")

CBOE_URL = "https://cdn.cboe.com/api/global/delayed_quotes/options/{ticker}.json"
CBOE_OCC_RE = re.compile(r"^([A-Z]+)\s*(\d{6})([CP])(\d{8})$")
PREFIX = "options_intraday"
OPTIONS_CLOSE_LAG_MIN = 15  # ETF options trade 15 min past the equity close
USER_AGENT = "skeptic-collector/0.1 (personal research)"
PRE_OPEN_POLL_SEC = 30  # re-check the wall clock this often while waiting for the open


def load_dotenv(path: Path) -> None:
    """Minimal KEY=VALUE loader so the launchd agent needs no plist secrets."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_dotenv(Path(__file__).parent / ".env")

from collect import (  # noqa: E402  (env must load before collect's clients)
    CANONICAL_COLUMNS,
    TICKERS,
    nyse,
    r2_client,
    r2_put_parquet,
    yahoo_snapshot,
)


# ----------------------------- CBOE leg ------------------------------------

def fetch_cboe_chain(ticker: str) -> pd.DataFrame:
    resp = requests.get(CBOE_URL.format(ticker=ticker),
                        headers={"User-Agent": USER_AGENT}, timeout=20)
    resp.raise_for_status()
    payload = resp.json()
    data = payload.get("data") or {}
    options = data.get("options") or []
    now = datetime.now(UTC)
    spot = data.get("current_price") or data.get("close")
    # the underlying's CUMULATIVE session share volume (the CBOE quote carries
    # it alongside spot); banked per snapshot so the chart's intraday tail can
    # show real volume (diffed to per-bar at read time). None if the feed omits it.
    und_volume = data.get("volume")
    # the feed's own 30d IV index for the underlying, banked per snapshot —
    # a free forward cross-check for the in-house ATM-IV derivation now that
    # the iVolatility analytics series is frozen (no subscription)
    iv30 = data.get("iv30")
    rows, dropped = [], 0
    for o in options:
        m = CBOE_OCC_RE.match((o.get("option") or "").strip())
        if not m or m.group(1) != ticker:
            dropped += 1
            continue
        exp = datetime.strptime(m.group(2), "%y%m%d").date()
        trading_date = now.astimezone(_ET).date()
        rows.append({
            "ticker": ticker,
            "trading_date": trading_date,
            "snapshot_ts": now,
            "expiration": exp,
            "dte": (exp - trading_date).days,
            "right": "call" if m.group(3) == "C" else "put",
            "strike": int(m.group(4)) / 1000.0,
            "bid": o.get("bid"),
            "ask": o.get("ask"),
            "last": o.get("last_trade_price"),
            "volume": o.get("volume"),
            "open_interest": o.get("open_interest"),
            "iv": o.get("iv"),
            "delta": o.get("delta"),
            "gamma": o.get("gamma"),
            "theta": o.get("theta"),
            "vega": o.get("vega"),
            "rho": o.get("rho"),
            "greeks_source": "vendor",
            "spot": spot,
            "source": "cboe_delayed",
            "source_ts": payload.get("timestamp") or data.get("last_trade_time"),
            "und_volume": und_volume,
            # displayed NBBO depth in contracts — the F5 disclosure input the
            # feed always carried and the recorder used to drop
            "bid_size": o.get("bid_size"),
            "ask_size": o.get("ask_size"),
            "iv30": iv30,
        })
    if dropped:
        log.debug("%s: dropped %d non-standard symbols", ticker, dropped)
    return pd.DataFrame(rows, columns=CANONICAL_COLUMNS
                        + ["source_ts", "und_volume", "bid_size", "ask_size", "iv30"])


def snap_key(source: str, ticker: str, ts: datetime) -> str:
    d = ts.astimezone(_ET).date()
    stamp = ts.strftime("%Y%m%dT%H%MZ")
    return f"{PREFIX}/source={source}/ticker={ticker}/date={d}/snap_{stamp}.parquet"


def cboe_cycle(s3, ts: datetime, dry_run: bool) -> None:
    for ticker in TICKERS:
        try:
            df = fetch_cboe_chain(ticker)
            if df.empty:
                log.warning("cboe %s: empty chain", ticker)
            elif not dry_run:
                r2_put_parquet(s3, snap_key("cboe_delayed", ticker, ts), df)
            log.info("cboe %s: %d rows%s", ticker, len(df), " (dry-run)" if dry_run else "")
        except Exception as exc:
            log.warning("cboe %s failed: %s", ticker, exc)


def yahoo_cycle(s3, ts: datetime, dry_run: bool) -> None:
    for ticker in TICKERS:
        try:
            df = yahoo_snapshot(ticker)
            if df is None or df.empty:
                log.warning("yahoo %s: empty snapshot", ticker)
            elif not dry_run:
                r2_put_parquet(s3, snap_key("yahoo", ticker, ts), df)
            log.info("yahoo %s: %d rows%s", ticker,
                     0 if df is None else len(df), " (dry-run)" if dry_run else "")
        except Exception as exc:
            log.warning("yahoo %s failed: %s", ticker, exc)


def snapshot_cycle(s3, do_yahoo: bool, dry_run: bool) -> None:
    """One fully inline snapshot cycle — the --once path. The session loop
    instead dispatches the Yahoo leg through YahooLeg so it runs off-thread."""
    ts = datetime.now(UTC)
    cboe_cycle(s3, ts, dry_run)
    if do_yahoo:
        yahoo_cycle(s3, ts, dry_run)


class YahooLeg:
    """Off-thread dispatcher for the Yahoo snapshot leg.

    A cold yfinance session (crumb fetch, per-ticker retry sleeps of up to
    ~2 min) can run for minutes; inline it blocks the per-minute CBOE
    cadence. The leg therefore runs in a single daemon worker thread,
    writing through the same r2_put_parquet path (boto3 clients are
    thread-safe; the client is created once on the main thread). At most
    one leg is ever in flight: a tick that fires while the previous leg is
    still running is skipped, never queued — Yahoo is cross-source
    redundancy and the next tick recovers it. A leg that stays in flight
    past WEDGED_TICKS skipped ticks is called out at ERROR level, because
    Python threads cannot be killed and yahoo capture is stalled until the
    recorder restarts.
    """

    WEDGED_TICKS = 3

    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._started = ""
        self._skips = 0

    def maybe_start(self, s3, ts: datetime, dry_run: bool) -> bool:
        if self._thread is not None and self._thread.is_alive():
            self._skips += 1
            if self._skips >= self.WEDGED_TICKS:
                log.error("yahoo leg started %s looks wedged (%d ticks skipped); "
                          "yahoo capture is stalled until the recorder restarts",
                          self._started, self._skips)
            else:
                log.warning("yahoo leg started %s still running; skipping this tick",
                            self._started)
            return False
        self._skips = 0
        self._started = ts.strftime("%Y%m%dT%H%MZ")
        self._thread = threading.Thread(target=yahoo_cycle, args=(s3, ts, dry_run),
                                        name="yahoo-leg", daemon=True)
        self._thread.start()
        return True


# ----------------------------- session loop ---------------------------------

def current_or_next_window(now: pd.Timestamp) -> tuple[pd.Timestamp, pd.Timestamp]:
    """(start, end) of the current/next options session window, UTC."""
    cal = nyse()
    lag = timedelta(minutes=OPTIONS_CLOSE_LAG_MIN)
    day = now.tz_convert(_ET).date()
    sessions = cal.sessions_in_range(
        pd.Timestamp(day) - pd.Timedelta(days=1), pd.Timestamp(day) + pd.Timedelta(days=10))
    for s in sessions:
        open_ts = cal.session_open(s)
        end_ts = cal.session_close(s) + lag  # early-close aware
        if now < end_ts:
            return max(open_ts, now), end_ts
    raise RuntimeError("no upcoming session found in 10-day lookahead")


def prefix_gb(s3, prefix: str) -> float:
    total = 0
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=os.environ["R2_BUCKET"], Prefix=prefix):
        for obj in page.get("Contents", []):
            total += obj["Size"]
    return total / 1e9


def run_loop(yahoo_every: int, dry_run: bool, max_lake_gb: float) -> int:
    s3 = None if dry_run else r2_client()
    yahoo_leg = YahooLeg()
    log.info("intraday recorder up: CBOE every minute, Yahoo every %d min, "
             "lake cap %.1f GB", yahoo_every, max_lake_gb)
    while True:
        now = pd.Timestamp.now(tz="UTC")
        start, end = current_or_next_window(now)
        if now < start:
            wait = (start - now).total_seconds()
            log.info("market closed; sleeping %.0f min until %s", wait / 60, start)
            # Re-check the wall clock in short polls rather than sleeping the whole
            # gap in one call. macOS suspends time.sleep() while the host sleeps, so
            # a single long sleep begun before a laptop sleep has not elapsed on
            # wake and we would sail straight past the open (observed 2026-07-09,
            # when the recorder missed 164 min of the session). Short polls cost
            # nothing and re-evaluate within one interval of any wake, so we enter
            # the session on time.
            while True:
                remaining = (start - pd.Timestamp.now(tz="UTC")).total_seconds()
                if remaining <= 0:
                    break
                time.sleep(min(PRE_OPEN_POLL_SEC, remaining))
            continue
        if not dry_run:
            used = prefix_gb(s3, PREFIX)
            if used > max_lake_gb:
                # protect the shared bucket (and the nightly EOD record) from
                # filling the free tier; owner raises the cap after enabling
                # R2 billing, or asks for a filtered/downsampled lake
                log.error("intraday lake %.1f GB exceeds cap %.1f GB — pausing "
                          "recording; raise --max-lake-gb or thin the lake", used, max_lake_gb)
                time.sleep(3600)
                continue
        log.info("in session window until %s", end)
        minute_idx = 0
        while pd.Timestamp.now(tz="UTC") < end:
            cycle_start = time.monotonic()
            ts = datetime.now(UTC)
            cboe_cycle(s3, ts, dry_run)
            if minute_idx % yahoo_every == 0:
                yahoo_leg.maybe_start(s3, ts, dry_run)
            minute_idx += 1
            elapsed = time.monotonic() - cycle_start
            time.sleep(max(0.0, 60.0 - elapsed))
        log.info("session window closed")


def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--yahoo-every", type=int, default=15,
                    help="Yahoo snapshot cadence in minutes (0 = never)")
    ap.add_argument("--once", action="store_true", help="one snapshot cycle, then exit")
    ap.add_argument("--dry-run", action="store_true", help="fetch+normalize only, no R2 writes")
    ap.add_argument("--max-lake-gb", type=float,
                    default=float(os.environ.get("INTRADAY_MAX_GB", "6")),
                    help="pause recording when options_intraday/ exceeds this")
    args = ap.parse_args()

    required = ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET")
    if not args.dry_run and any(k not in os.environ for k in required):
        log.error("R2 credentials missing — fill collector/.env (see .env.example); "
                  "agent will retry via launchd ThrottleInterval")
        return 78  # EX_CONFIG

    if args.once:
        s3 = None if args.dry_run else r2_client()
        snapshot_cycle(s3, do_yahoo=(args.yahoo_every > 0), dry_run=args.dry_run)
        return 0
    if args.yahoo_every <= 0:
        args.yahoo_every = 10 ** 9  # effectively never
    return run_loop(args.yahoo_every, args.dry_run, args.max_lake_gb)


if __name__ == "__main__":
    sys.exit(main())
