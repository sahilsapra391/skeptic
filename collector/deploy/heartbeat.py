#!/usr/bin/env python3
"""heartbeat.py — page when the intraday recorder stops banking snapshots during
a session. A systemd timer runs this every few minutes on the recorder host.

This is the guard that turns a SILENT stall (the 2026-07-09 failure, where the
recorder was alive but recorded nothing and no one knew until a chart looked
wrong) into an alert. It checks: if we are inside the options session window
(XNYS-aware, +15 min close lag) and the newest cboe_delayed SPY snapshot for
today is older than --stale-min, emit an alert. The yahoo redundancy lane is
watched too (--yahoo-stale-min, much looser: it ticks ~every 15 min and its
worker thread self-heals within 30 min), since it can now die without
touching the CBOE cadence this check would otherwise notice.

Alert sink: POST to $ALERT_WEBHOOK if set (a plain-text body, which ntfy.sh,
Slack, and Discord webhooks all accept in some form), otherwise a loud stderr
line that lands in the systemd journal. Exit 1 on alert, 0 otherwise, so the
timer's own failure tracking also flags it.

Run from the collector/ directory (imports the production collector's R2 and
calendar helpers): `uv run --env-file .env python deploy/heartbeat.py`.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import timedelta

import pandas as pd
import requests

# heartbeat.py lives in collector/deploy/ but reuses the production collector's
# R2 + calendar helpers in collector/. Running a script puts its own dir on
# sys.path, not the parent, so add collector/ explicitly before importing.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from collect import nyse, r2_client  # noqa: E402  (path set above)

PREFIX = "options_intraday"
CLOSE_LAG_MIN = 15
_ET = "America/New_York"
_SNAP_RE = re.compile(r"snap_(\d{8}T\d{4})Z\.parquet$")


def session_window(now: pd.Timestamp, cal):
    """(open, close+lag) if `now` is inside a session window, else None."""
    lag = timedelta(minutes=CLOSE_LAG_MIN)
    day = now.tz_convert(_ET).date()
    sessions = cal.sessions_in_range(
        pd.Timestamp(day) - pd.Timedelta(days=1), pd.Timestamp(day) + pd.Timedelta(days=1))
    for s in sessions:
        open_ts = cal.session_open(s)
        close_ts = cal.session_close(s) + lag
        if open_ts <= now < close_ts:
            return open_ts, close_ts
    return None


def newest_snapshot(s3, bucket: str, ticker: str, day,
                    source: str = "cboe_delayed") -> pd.Timestamp | None:
    """Newest snapshot timestamp (UTC) for (source, ticker, day), or None."""
    prefix = f"{PREFIX}/source={source}/ticker={ticker}/date={day}/"
    newest = None
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            m = _SNAP_RE.search(obj["Key"])
            if m:
                ts = pd.Timestamp(m.group(1), tz="UTC")
                if newest is None or ts > newest:
                    newest = ts
    return newest


def alert(msg: str) -> None:
    print(f"ALERT: {msg}", file=sys.stderr)
    url = os.environ.get("ALERT_WEBHOOK")
    if url:
        try:
            requests.post(url, data=msg.encode(), timeout=10)
        except Exception as exc:  # noqa: BLE001 — a failed page must not crash the check
            print(f"alert webhook failed: {exc}", file=sys.stderr)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stale-min", type=int, default=6,
                    help="alert if the newest snapshot is older than this (minutes)")
    ap.add_argument("--grace-min", type=int, default=8,
                    help="don't alert in the first N minutes after the open")
    ap.add_argument("--ticker", default="SPY", help="which lane to watch")
    ap.add_argument("--yahoo-stale-min", type=int, default=120,
                    help="alert if the newest yahoo snapshot is older than this "
                         "(minutes); 0 disables. Generous because yahoo runs at a "
                         "~15-min cadence, slow legs skip ticks, and a wedged leg "
                         "is only abandoned after 30 min (intraday.YahooLeg)")
    args = ap.parse_args()

    now = pd.Timestamp.now(tz="UTC")
    win = session_window(now, nyse())
    if win is None:
        return 0  # market closed — nothing to guard
    open_ts, _ = win
    if now - open_ts < pd.Timedelta(minutes=args.grace_min):
        return 0  # give the recorder a moment after the open

    s3 = r2_client()
    bucket = os.environ["R2_BUCKET"]
    day = now.tz_convert(_ET).date()
    rc = 0
    newest = newest_snapshot(s3, bucket, args.ticker, day)
    if newest is None:
        mins = int((now - open_ts).total_seconds() // 60)
        alert(f"skeptic recorder: NO {args.ticker} snapshots today ({day}), {mins} min into the session")
        rc = 1
    else:
        age = (now - newest).total_seconds() / 60
        if age > args.stale_min:
            alert(f"skeptic recorder: last {args.ticker} snapshot {age:.0f} min ago "
                  f"(> {args.stale_min}) during session {day}")
            rc = 1

    # The yahoo lane runs off-thread in the recorder, so its death no longer
    # freezes the CBOE cadence (which is what used to trip this pager
    # indirectly). Watch its snapshot freshness explicitly; before the first
    # snapshot of the day the session open counts as the last-seen time.
    if args.yahoo_stale_min > 0:
        newest_y = newest_snapshot(s3, bucket, args.ticker, day, source="yahoo")
        last_y = newest_y if newest_y is not None else open_ts
        age_y = (now - last_y).total_seconds() / 60
        if age_y > args.yahoo_stale_min:
            alert(f"skeptic recorder: yahoo lane stale — last {args.ticker} yahoo "
                  f"snapshot {age_y:.0f} min ago (> {args.yahoo_stale_min}) during "
                  f"session {day}"
                  + ("" if newest_y is not None else " (none today)"))
            rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
