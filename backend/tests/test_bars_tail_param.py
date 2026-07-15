"""tail=0 — the fast first paint (2026-07-15).

The chart's first paint used to block on the live-tail fetch (an R2
listing + snapshot pulls when no IEX keys exist) even when the cached lake
could answer instantly. `include_tail=False` must serve the lake WITHOUT
touching either tail path, labeled honestly; the default must keep
fetching the tail. r2 and the lake are mocked — no live bucket needed."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from app.data import bars


def _stale_lake_minutes() -> pd.DataFrame:
    # a lake > 2 minutes old — exactly the state that arms the tail fetch
    end = datetime.now(UTC) - timedelta(hours=3)
    ts = pd.date_range(end=end, periods=390, freq="1min", tz="UTC")
    return pd.DataFrame({
        "minute_ts": ts,
        "open": 100.0,
        "high": 100.5,
        "low": 99.5,
        "close": 100.2,
        "volume": 1000.0,
    })


@pytest.fixture()
def tail_calls(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    frame = _stale_lake_minutes()
    monkeypatch.setattr(bars.r2, "r2_client", lambda: object())
    monkeypatch.setattr(bars, "_cached_months", lambda s3, ticker, months: [frame])
    calls = {"iex": 0, "recorder": 0}

    def iex_tripwire(ticker: str, last: pd.Timestamp) -> None:
        calls["iex"] += 1
        return None

    def recorder_tripwire(s3: object, ticker: str, last: pd.Timestamp) -> None:
        calls["recorder"] += 1
        return None

    monkeypatch.setattr(bars, "_live_tail_minutes", iex_tripwire)
    monkeypatch.setattr(bars, "_recorder_spot_tail", recorder_tripwire)
    return calls


def test_tail_0_never_touches_the_live_tail(tail_calls: dict[str, int]) -> None:
    out = bars.get_bars("SPY", "5m", "1w", [], include_tail=False)
    assert tail_calls == {"iex": 0, "recorder": 0}
    assert out["live"] is False
    assert "live tail loads separately" in out["source"]
    assert out["bars"], "the cached lake is still served"


def test_default_still_fetches_the_tail(tail_calls: dict[str, int]) -> None:
    out = bars.get_bars("SPY", "5m", "1w", [])
    assert tail_calls["iex"] == 1  # tried IEX first…
    assert tail_calls["recorder"] == 1  # …then fell to the recorder path
    assert "loads separately" not in out["source"]


def test_paged_requests_fetch_no_tail_regardless(tail_calls: dict[str, int]) -> None:
    before = datetime.now(UTC).isoformat()
    bars.get_bars("SPY", "5m", "1w", [], before=before, include_tail=True)
    assert tail_calls == {"iex": 0, "recorder": 0}


def test_daily_interval_ignores_tail_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    # dailies never had a tail; tail=0 must not change their path
    monkeypatch.setattr(bars.r2, "r2_client", lambda: object())
    # _cached_daily's contract: a tz-naive "date" column (localized inside)
    dates = pd.date_range(end=datetime.now(UTC).date(), periods=300, freq="1D")
    daily = pd.DataFrame({
        "date": dates, "open": 100.0, "high": 101.0, "low": 99.0,
        "close": 100.5, "volume": 1_000_000.0,
    })
    monkeypatch.setattr(bars, "_cached_daily", lambda s3, ticker: daily)
    out = bars.get_bars("SPY", "1d", "1y", [], include_tail=False)
    assert out["bars"]
    assert "loads separately" not in out["source"]
