"""CBOE-recorder spot tail (chart live intraday underlying).

The chart's underlying lake is nightly; when the Alpaca IEX tail isn't
configured, today's intraday candles come from the CBOE recorder's ~2-minute
snapshots — spot for price, the diff of the cumulative session volume for
volume (~15-min delayed, disclosed). These prove the spot/volume extraction,
the > after filter, the incremental cache, and — through get_bars — the
"live while the session is open, completed-and-labeled once it closes"
behavior. r2 is mocked, so no live lake is needed.
"""

from __future__ import annotations

import pandas as pd
import pytest

from app.data import bars

_D = pd.Timestamp.now(tz=bars.ET).date().isoformat()  # today's ET session date
_DYMD = _D.replace("-", "")


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    bars._recorder_cache.clear()


def _key(hhmm: str) -> str:
    return f"{bars.CBOE_RECORDER}/ticker=SPY/date={_D}/snap_{_DYMD}T{hhmm}Z.parquet"


def _install(monkeypatch: pytest.MonkeyPatch, snaps: list[tuple[str, float, float | None]]) -> None:
    """snaps: [(HHMM, spot, cum_vol|None)] → keys + per-key parquet frames.
    cum_vol None omits the und_volume column (older snapshots)."""
    frames = {_key(hhmm): _frame(spot, cv) for hhmm, spot, cv in snaps}
    monkeypatch.setattr(bars.r2, "list_keys", lambda s3, prefix: list(frames))
    monkeypatch.setattr(bars.r2, "get_parquet", lambda s3, key: frames[key])


def _frame(spot: float, cum_vol: float | None) -> pd.DataFrame:
    cols: dict[str, list[float]] = {"spot": [spot], "bid": [1.0], "ask": [1.1]}
    if cum_vol is not None:
        cols["und_volume"] = [cum_vol]
    return pd.DataFrame(cols)


def _after_midnight() -> pd.Timestamp:
    return pd.Timestamp(f"{_D} 00:00", tz="UTC")


# ─────────────────────────── spot + volume extraction ──────────────────────
def test_builds_ohlc_and_volume_from_cumulative(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, [("1400", 745.0, 100), ("1402", 745.5, 150), ("1404", 744.8, 210)])
    out = bars._recorder_spot_tail(None, "SPY", _after_midnight())

    assert out is not None and len(out) == 3
    assert list(out["minute_ts"].dt.strftime("%H%M")) == ["1400", "1402", "1404"]
    # each snapshot is a spot tick: open == high == low == close == spot
    assert list(out["close"]) == [745.0, 745.5, 744.8]
    for col in ("open", "high", "low"):
        assert list(out[col]) == list(out["close"])
    # per-bar volume = diff of the cumulative (first bar carries its cumulative)
    assert list(out["volume"]) == [100.0, 50.0, 60.0]


def test_volume_zero_when_recorder_lacks_cumulative(monkeypatch: pytest.MonkeyPatch) -> None:
    # older snapshots (pre-collector-change) carry no und_volume → honest 0
    _install(monkeypatch, [("1400", 745.0, None), ("1402", 745.5, None)])
    out = bars._recorder_spot_tail(None, "SPY", _after_midnight())
    assert out is not None and set(out["volume"]) == {0.0}


def test_after_filters_older_snapshots(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, [("1400", 745.0, 100), ("1402", 745.5, 150), ("1404", 744.8, 210)])
    after = pd.Timestamp(f"{_D} 14:02", tz="UTC")
    out = bars._recorder_spot_tail(None, "SPY", after)
    assert out is not None
    assert list(out["minute_ts"].dt.strftime("%H%M")) == ["1404"]
    # volume diff spans the FULL sequence, so the surviving bar is 210−150=60
    assert list(out["volume"]) == [60.0]


def test_incremental_cache_reads_only_new_snapshots(monkeypatch: pytest.MonkeyPatch) -> None:
    reads: list[str] = []
    frames = {_key("1400"): _frame(745.0, 100), _key("1402"): _frame(745.5, 150)}
    monkeypatch.setattr(bars.r2, "list_keys", lambda s3, prefix: list(frames))

    def _get_parquet(s3: object, key: str) -> pd.DataFrame:
        reads.append(key)
        return frames[key]

    monkeypatch.setattr(bars.r2, "get_parquet", _get_parquet)

    bars._recorder_spot_tail(None, "SPY", _after_midnight())
    assert len(reads) == 2  # both snapshots read on first call

    # a new snapshot lands; force a re-list (bypass the 20s TTL), fetch again
    frames[_key("1404")] = _frame(744.8, 210)
    bars._recorder_cache["SPY"]["listed_at"] = 0.0
    out = bars._recorder_spot_tail(None, "SPY", _after_midnight())

    assert len(reads) == 3  # only the ONE new snapshot was read (2 cached)
    assert out is not None and len(out) == 3


def test_none_when_recorder_has_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bars.r2, "list_keys", lambda s3, prefix: [])
    assert bars._recorder_spot_tail(None, "SPY", _after_midnight()) is None


# ─────────────── get_bars: live while open, completed once closed ───────────
def _stub_nightly(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nightly lake ending yesterday (so its last bar is > 2 min stale) + no
    APCA keys, so get_bars reaches the recorder tail."""
    monkeypatch.delenv("APCA_API_KEY_ID", raising=False)
    monkeypatch.delenv("APCA_API_SECRET_KEY", raising=False)
    monkeypatch.setattr(bars.r2, "r2_client", lambda: object())
    ymd = pd.Timestamp.now(tz="UTC").normalize() - pd.Timedelta(days=1)
    ts = pd.date_range(ymd + pd.Timedelta(hours=14), periods=30, freq="1min", tz="UTC")
    night = pd.DataFrame(
        {"minute_ts": ts, "open": 744.0, "high": 744.2, "low": 743.8,
         "close": 744.1, "volume": 1000}
    )
    cur_month = pd.Timestamp.now(tz="UTC").strftime("%Y-%m")
    monkeypatch.setattr(
        bars, "_cached_month",
        lambda s3, ticker, month: night.copy() if month == cur_month else None,
    )


def _tail_at(minutes_ago: list[int]) -> pd.DataFrame:
    now = pd.Timestamp.now(tz="UTC").floor("min")
    ts = [now - pd.Timedelta(minutes=m) for m in minutes_ago]
    n = len(ts)
    return pd.DataFrame(
        {"minute_ts": ts, "open": [745.0] * n, "high": [745.0] * n,
         "low": [745.0] * n, "close": [745.0] * n, "volume": [500.0] * n}
    )


def test_get_bars_live_while_session_open(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_nightly(monkeypatch)
    # newest recorder snapshot is 2 min old → the session is open → live
    monkeypatch.setattr(bars, "_recorder_spot_tail", lambda s3, t, after: _tail_at([4, 2]))
    out = bars.get_bars("SPY", "5m", "1w", [])
    assert out["live"] is True
    assert out["live_label"] == "delayed ~15m · CBOE recorder"
    last_date = pd.Timestamp(out["bars"][-1]["t"]).tz_convert(bars.ET).date()
    assert last_date.isoformat() == _D


def test_get_bars_completed_and_not_live_once_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_nightly(monkeypatch)
    # newest recorder snapshot is 60 min old → session closed → NOT live, but
    # today's (delayed) session is still shown, labeled as completing overnight
    monkeypatch.setattr(bars, "_recorder_spot_tail", lambda s3, t, after: _tail_at([62, 60]))
    out = bars.get_bars("SPY", "5m", "1w", [])
    assert out["live"] is False
    assert out["live_label"] == "CBOE recorder (delayed) · completes overnight"
    last_date = pd.Timestamp(out["bars"][-1]["t"]).tz_convert(bars.ET).date()
    assert last_date.isoformat() == _D  # today's session is present, just completed
