"""Starvation guards for the intraday recorder's Yahoo leg.

On 2026-07-13 the first-cycle Yahoo leg ran inline for ~5 minutes
(09:33→09:38 ET: cold yfinance crumb fetch plus per-ticker retries) and the
per-minute CBOE cadence lost the 13:34–13:38Z snapshots. These tests pin the
fix: the Yahoo leg runs in a single worker thread writing through the same
r2_put_parquet path, a tick that fires mid-leg is skipped (never queued,
never blocking), a wedged leg escalates to ERROR, and the --once path still
runs everything inline.
"""

from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime

import intraday
import pandas as pd

TS = datetime(2026, 7, 13, 13, 34, tzinfo=UTC)
JOIN_TIMEOUT = 10.0  # generous; every wait is event-driven, not a sleep


def _chain_df() -> pd.DataFrame:
    return pd.DataFrame({"strike": [400.0], "bid": [1.0], "ask": [1.1]})


def _blocking_snapshot(release: threading.Event, started: threading.Event):
    def snapshot(ticker: str) -> pd.DataFrame:
        started.set()
        assert release.wait(JOIN_TIMEOUT), "test deadlock: release never set"
        return _chain_df()
    return snapshot


def test_slow_yahoo_leg_never_blocks_the_caller(monkeypatch):
    """maybe_start returns immediately while the leg is mid-fetch; a tick that
    lands during the previous leg is skipped, and the next free tick runs."""
    release, started = threading.Event(), threading.Event()
    monkeypatch.setattr(intraday, "yahoo_snapshot", _blocking_snapshot(release, started))
    leg = intraday.YahooLeg()
    try:
        assert leg.maybe_start(None, TS, dry_run=True) is True
        assert started.wait(JOIN_TIMEOUT)
        # we are back on the calling thread while yahoo is still blocked
        assert leg._thread.is_alive()
        assert leg.maybe_start(None, TS, dry_run=True) is False
    finally:
        release.set()
        leg._thread.join(JOIN_TIMEOUT)
    assert not leg._thread.is_alive()
    assert leg.maybe_start(None, TS, dry_run=True) is True
    leg._thread.join(JOIN_TIMEOUT)


def test_cboe_snapshots_continue_while_yahoo_leg_in_flight(monkeypatch):
    """The 2026-07-13 regression: a multi-minute Yahoo leg must not cost CBOE
    minutes, and overlapping yahoo ticks are skipped, not queued."""
    release, started = threading.Event(), threading.Event()
    puts: list[str] = []
    monkeypatch.setattr(intraday, "yahoo_snapshot", _blocking_snapshot(release, started))
    monkeypatch.setattr(intraday, "fetch_cboe_chain", lambda t: _chain_df())
    monkeypatch.setattr(intraday, "r2_put_parquet",
                        lambda client, key, df: puts.append(key))
    leg = intraday.YahooLeg()
    ticks = [TS.replace(minute=34 + i) for i in range(3)]
    try:
        for ts in ticks:  # what run_loop does each minute, with a yahoo tick every time
            intraday.cboe_cycle(object(), ts, dry_run=False)
            leg.maybe_start(object(), ts, dry_run=False)
    finally:
        release.set()
        leg._thread.join(JOIN_TIMEOUT)
    cboe_keys = [k for k in puts if "source=cboe_delayed" in k]
    assert cboe_keys == [intraday.snap_key("cboe_delayed", t, ts)
                         for ts in ticks for t in intraday.TICKERS]
    yahoo_keys = [k for k in puts if "source=yahoo" in k]
    assert yahoo_keys == [intraday.snap_key("yahoo", t, ticks[0]) for t in intraday.TICKERS]


def test_yahoo_leg_writes_through_r2_put_parquet(monkeypatch):
    """The worker writes via the exact same put path, client, and snap keys."""
    puts: list[tuple[object, str, int]] = []
    s3 = object()
    monkeypatch.setattr(intraday, "yahoo_snapshot", lambda t: _chain_df())
    monkeypatch.setattr(intraday, "r2_put_parquet",
                        lambda client, key, df: puts.append((client, key, len(df))))
    leg = intraday.YahooLeg()
    assert leg.maybe_start(s3, TS, dry_run=False)
    leg._thread.join(JOIN_TIMEOUT)
    assert not leg._thread.is_alive()
    assert [k for _, k, _ in puts] == [
        intraday.snap_key("yahoo", t, TS) for t in intraday.TICKERS]
    assert all(client is s3 for client, _, _ in puts)


def test_yahoo_leg_ticker_failure_is_logged_not_fatal(monkeypatch, caplog):
    """A per-ticker failure keeps the leg going and keeps the log line format."""
    puts: list[str] = []

    def snapshot(ticker: str) -> pd.DataFrame:
        if ticker == "SPY":
            raise RuntimeError("crumb fetch timed out")
        return _chain_df()

    monkeypatch.setattr(intraday, "yahoo_snapshot", snapshot)
    monkeypatch.setattr(intraday, "r2_put_parquet",
                        lambda client, key, df: puts.append(key))
    leg = intraday.YahooLeg()
    with caplog.at_level(logging.WARNING, logger="intraday"):
        assert leg.maybe_start(object(), TS, dry_run=False)
        leg._thread.join(JOIN_TIMEOUT)
    assert not leg._thread.is_alive()
    assert puts == [intraday.snap_key("yahoo", t, TS) for t in ("QQQ", "IWM")]
    assert any("yahoo SPY failed" in r.getMessage() for r in caplog.records)


def test_wedged_leg_escalates_to_error(monkeypatch, caplog):
    """A leg still in flight after WEDGED_TICKS skipped ticks is called out at
    ERROR level — a silently stalled redundancy leg would hide a coverage gap."""
    release, started = threading.Event(), threading.Event()
    monkeypatch.setattr(intraday, "yahoo_snapshot", _blocking_snapshot(release, started))
    leg = intraday.YahooLeg()
    try:
        with caplog.at_level(logging.WARNING, logger="intraday"):
            assert leg.maybe_start(None, TS, dry_run=True)
            assert started.wait(JOIN_TIMEOUT)
            for _ in range(intraday.YahooLeg.WEDGED_TICKS):
                assert not leg.maybe_start(None, TS, dry_run=True)
    finally:
        release.set()
        leg._thread.join(JOIN_TIMEOUT)
    skips = [r for r in caplog.records
             if r.levelno == logging.WARNING and "skipping this tick" in r.getMessage()]
    wedged = [r for r in caplog.records
              if r.levelno == logging.ERROR and "wedged" in r.getMessage()]
    assert len(skips) == intraday.YahooLeg.WEDGED_TICKS - 1
    assert len(wedged) == 1


def test_once_path_still_runs_yahoo_inline(monkeypatch):
    """snapshot_cycle (the --once path) keeps both legs on the calling thread."""
    seen: list[threading.Thread] = []

    def note(df_factory):
        def wrapped(ticker: str) -> pd.DataFrame:
            seen.append(threading.current_thread())
            return df_factory()
        return wrapped

    monkeypatch.setattr(intraday, "fetch_cboe_chain", note(_chain_df))
    monkeypatch.setattr(intraday, "yahoo_snapshot", note(_chain_df))
    intraday.snapshot_cycle(None, do_yahoo=True, dry_run=True)
    assert len(seen) == 2 * len(intraday.TICKERS)
    assert all(t is threading.current_thread() for t in seen)
