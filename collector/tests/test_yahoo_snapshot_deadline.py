"""yahoo_snapshot's wall-clock deadline (the intraday leg budget).

The 2026-07-13 starvation fix bounds each intraday yahoo leg's duration by
passing deadline=time.monotonic()+budget; the crawl truncates once past it,
disclosed in the log. The nightly EOD path passes nothing and must crawl
everything exactly as before.
"""

from __future__ import annotations

import logging
import sys
import types
from datetime import date, timedelta

import collect
import pandas as pd
import pytest


class _FakeChain:
    def __init__(self):
        self.calls = pd.DataFrame({
            "strike": [400.0], "bid": [1.0], "ask": [1.2], "lastPrice": [1.1],
            "volume": [10], "openInterest": [100], "impliedVolatility": [0.2]})
        self.puts = self.calls.copy()


class _FakeTicker:
    """Three expirations inside the DTE window; each chain fetch advances the
    fake clock by 100 so deadlines trip deterministically."""

    def __init__(self, symbol, clock):
        self.fast_info = {"last_price": 400.0}
        today = date.today()
        self.options = tuple((today + timedelta(days=d)).isoformat() for d in (7, 14, 21))
        self.chain_calls = 0
        self._clock = clock

    def option_chain(self, exp):
        self.chain_calls += 1
        self._clock["t"] += 100.0
        return _FakeChain()


@pytest.fixture
def fake_yf(monkeypatch):
    clock = {"t": 0.0}
    tickers: list[_FakeTicker] = []

    def make(symbol):
        tk = _FakeTicker(symbol, clock)
        tickers.append(tk)
        return tk

    monkeypatch.setitem(sys.modules, "yfinance", types.SimpleNamespace(Ticker=make))
    monkeypatch.setattr(collect.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(collect.time, "sleep", lambda s: None)
    return tickers


def test_no_deadline_crawls_everything(fake_yf):
    """The nightly EOD path (no deadline) is unchanged: every expiration."""
    df = collect.yahoo_snapshot("SPY")
    assert fake_yf[0].chain_calls == 3
    assert len(df) == 6  # 3 expirations x calls+puts x 1 strike


def test_deadline_truncates_mid_crawl(fake_yf, caplog):
    """Past the deadline the crawl stops, keeps what it has, and says so."""
    with caplog.at_level(logging.WARNING, logger="collector"):
        df = collect.yahoo_snapshot("SPY", deadline=150.0)
    assert fake_yf[0].chain_calls == 2  # t=0 ok, t=100 ok, t=200 over
    assert len(df) == 4
    assert any("truncating with 1 of 3 expirations unfetched" in r.getMessage()
               for r in caplog.records)


def test_deadline_already_past_fetches_nothing(fake_yf, caplog):
    with caplog.at_level(logging.WARNING, logger="collector"):
        df = collect.yahoo_snapshot("SPY", deadline=-1.0)
    assert fake_yf[0].chain_calls == 0
    assert df.empty
    assert any("truncating with 3 of 3 expirations unfetched" in r.getMessage()
               for r in caplog.records)
