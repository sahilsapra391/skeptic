"""Starvation guards for the intraday recorder's Yahoo leg.

On 2026-07-13 the first-cycle Yahoo leg ran inline for ~5 minutes
(09:33→09:38 ET: cold yfinance crumb fetch plus per-ticker retries) and the
per-minute CBOE cadence lost the 13:34–13:38Z snapshots. These tests pin the
fix: the Yahoo leg runs in a single worker thread writing through the same
r2_put_parquet path, a tick that fires mid-leg is skipped (never queued,
never blocking), a wedged leg is abandoned and replaced instead of killing
yahoo capture, a failed thread start never unwinds the CBOE loop, and the
--once path still runs everything inline.
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
    def snapshot(ticker: str, deadline: float | None = None) -> pd.DataFrame:
        started.set()
        assert release.wait(JOIN_TIMEOUT), "test deadlock: release never set"
        return _chain_df()
    return snapshot


def _join(leg: intraday.YahooLeg, thread: threading.Thread | None = None) -> None:
    """Join a leg's thread and FAIL if it is still alive — a silently leaked
    worker outlives monkeypatch teardown and starts calling the real yfinance."""
    thread = thread if thread is not None else leg._thread
    if thread is None:  # a failed assert may reach the finally before any start
        return
    thread.join(JOIN_TIMEOUT)
    assert not thread.is_alive()


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
        _join(leg)
    assert leg.maybe_start(None, TS, dry_run=True) is True
    _join(leg)


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
        _join(leg)
    cboe_keys = [k for k in puts if "source=cboe_delayed" in k]
    assert cboe_keys == [intraday.snap_key("cboe_delayed", t, ts)
                         for ts in ticks for t in intraday.TICKERS]
    yahoo_keys = [k for k in puts if "source=yahoo" in k]
    assert yahoo_keys == [intraday.snap_key("yahoo", t, ticks[0]) for t in intraday.TICKERS]


def test_yahoo_leg_writes_through_r2_put_parquet(monkeypatch):
    """The worker writes via the exact same put path, client, and snap keys."""
    puts: list[tuple[object, str, int]] = []
    s3 = object()
    monkeypatch.setattr(intraday, "yahoo_snapshot",
                        lambda t, deadline=None: _chain_df())
    monkeypatch.setattr(intraday, "r2_put_parquet",
                        lambda client, key, df: puts.append((client, key, len(df))))
    leg = intraday.YahooLeg()
    assert leg.maybe_start(s3, TS, dry_run=False)
    _join(leg)
    assert [k for _, k, _ in puts] == [
        intraday.snap_key("yahoo", t, TS) for t in intraday.TICKERS]
    assert all(client is s3 for client, _, _ in puts)


def test_yahoo_leg_ticker_failure_is_logged_not_fatal(monkeypatch, caplog):
    """A per-ticker failure keeps the leg going and keeps the log line format."""
    puts: list[str] = []

    def snapshot(ticker: str, deadline: float | None = None) -> pd.DataFrame:
        if ticker == "SPY":
            raise RuntimeError("crumb fetch timed out")
        return _chain_df()

    monkeypatch.setattr(intraday, "yahoo_snapshot", snapshot)
    monkeypatch.setattr(intraday, "r2_put_parquet",
                        lambda client, key, df: puts.append(key))
    leg = intraday.YahooLeg()
    with caplog.at_level(logging.WARNING, logger="intraday"):
        assert leg.maybe_start(object(), TS, dry_run=False)
        _join(leg)
    assert puts == [intraday.snap_key("yahoo", t, TS) for t in ("QQQ", "IWM")]
    assert any("yahoo SPY failed" in r.getMessage() for r in caplog.records)


def test_wedged_leg_is_abandoned_and_replaced(monkeypatch, caplog):
    """A leg in flight past WEDGED_AFTER_SEC is abandoned (logged at ERROR) and
    a fresh leg starts on the same tick — yahoo capture never stays dead."""
    release, started = threading.Event(), threading.Event()
    monkeypatch.setattr(intraday, "yahoo_snapshot", _blocking_snapshot(release, started))
    monkeypatch.setattr(intraday.YahooLeg, "WEDGED_AFTER_SEC", 0.0)
    leg = intraday.YahooLeg()
    first = None
    try:
        with caplog.at_level(logging.WARNING, logger="intraday"):
            assert leg.maybe_start(None, TS, dry_run=True)
            assert started.wait(JOIN_TIMEOUT)
            first = leg._thread
            assert leg.maybe_start(None, TS.replace(minute=49), dry_run=True) is True
            assert leg._thread is not first  # replaced, not the old handle
    finally:
        release.set()
        _join(leg, thread=first)
        _join(leg)
    wedged = [r for r in caplog.records
              if r.levelno == logging.ERROR and "abandoning" in r.getMessage()]
    assert len(wedged) == 1
    assert "20260713T1334Z" in wedged[0].getMessage()  # names the stalled leg's stamp


def test_below_wedge_threshold_skips_do_not_replace(monkeypatch, caplog):
    """Under the wall-clock threshold an in-flight leg only produces skip
    warnings — never the abandon ERROR, never a second thread."""
    release, started = threading.Event(), threading.Event()
    monkeypatch.setattr(intraday, "yahoo_snapshot", _blocking_snapshot(release, started))
    leg = intraday.YahooLeg()  # default WEDGED_AFTER_SEC of 30 min never trips here
    try:
        with caplog.at_level(logging.WARNING, logger="intraday"):
            assert leg.maybe_start(None, TS, dry_run=True)
            assert started.wait(JOIN_TIMEOUT)
            first = leg._thread
            for _ in range(3):
                assert leg.maybe_start(None, TS, dry_run=True) is False
            assert leg._thread is first
    finally:
        release.set()
        _join(leg)
    skips = [r for r in caplog.records if "skipping this tick" in r.getMessage()]
    assert len(skips) == 3 and all(r.levelno == logging.WARNING for r in skips)
    assert not any(r.levelno == logging.ERROR for r in caplog.records)


def test_thread_start_failure_never_unwinds_the_caller(monkeypatch, caplog):
    """Thread.start() raising (resource pressure) must not propagate into
    run_loop's minute loop; the leg stays startable at the next tick."""
    class BoomThread(threading.Thread):
        def start(self) -> None:
            raise RuntimeError("can't start new thread")

    leg = intraday.YahooLeg()
    with monkeypatch.context() as m:
        m.setattr(intraday.threading, "Thread", BoomThread)
        with caplog.at_level(logging.ERROR, logger="intraday"):
            assert leg.maybe_start(None, TS, dry_run=True) is False
    assert leg._thread is None  # no half-started state blocks the retry
    assert any("failed to start" in r.getMessage() for r in caplog.records)
    monkeypatch.setattr(intraday, "yahoo_snapshot",
                        lambda t, deadline=None: _chain_df())
    assert leg.maybe_start(None, TS, dry_run=True) is True  # next tick recovers
    _join(leg)


def test_yahoo_tick_due_zero_means_never():
    """--yahoo-every 0 fires nothing, not even at minute 0 of a window (the
    old 10**9 sentinel still fired there: 0 % anything == 0)."""
    assert intraday.yahoo_tick_due(0, 15) is True
    assert intraday.yahoo_tick_due(1, 15) is False
    assert intraday.yahoo_tick_due(15, 15) is True
    assert all(not intraday.yahoo_tick_due(i, 0) for i in range(3))


def test_snapshot_cycle_uses_injected_yahoo_dispatcher(monkeypatch):
    """run_loop injects YahooLeg.maybe_start as yahoo_run; the cycle passes it
    the same (s3, ts, dry_run) it gave the CBOE leg."""
    calls: list[tuple] = []
    monkeypatch.setattr(intraday, "cboe_cycle",
                        lambda s3, ts, dry_run: calls.append(("cboe", s3, ts, dry_run)))
    s3 = object()
    intraday.snapshot_cycle(s3, do_yahoo=True, dry_run=True,
                            yahoo_run=lambda *a: calls.append(("yahoo", *a)))
    assert [c[0] for c in calls] == ["cboe", "yahoo"]
    assert calls[0][1:] == calls[1][1:]  # identical s3/ts/dry_run for both legs


def test_once_path_still_runs_yahoo_inline(monkeypatch):
    """snapshot_cycle's default yahoo_run keeps both legs on the calling thread."""
    seen: list[threading.Thread] = []

    def snapshot(ticker: str, deadline: float | None = None) -> pd.DataFrame:
        seen.append(threading.current_thread())
        return _chain_df()

    monkeypatch.setattr(intraday, "fetch_cboe_chain", snapshot)
    monkeypatch.setattr(intraday, "yahoo_snapshot", snapshot)
    intraday.snapshot_cycle(None, do_yahoo=True, dry_run=True)
    assert len(seen) == 2 * len(intraday.TICKERS)
    assert all(t is threading.current_thread() for t in seen)
