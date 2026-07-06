"""CBOE-recorder spot tail (chart live intraday underlying).

The chart's underlying lake is nightly; when the Alpaca IEX tail isn't
configured, today's intraday candles come from the CBOE recorder's ~2-minute
`spot` snapshots (~15-min delayed, disclosed as such). These prove the spot
extraction, the > after filter, and the incremental cache — with r2 mocked, so
no live lake is needed.
"""

from __future__ import annotations

import pandas as pd
import pytest

from app.data import bars


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    bars._recorder_cache.clear()


def _today() -> str:
    return pd.Timestamp.now(tz=bars.ET).date().isoformat()


def _key(hhmm: str) -> str:
    d = _today().replace("-", "")
    return (
        f"{bars.CBOE_RECORDER}/ticker=SPY/date={_today()}/snap_{d}T{hhmm}Z.parquet"
    )


def _install(monkeypatch: pytest.MonkeyPatch, spots: dict[str, float]) -> None:
    """spots: {'HHMM': spot} → keys + per-key parquet frames."""
    keys = [_key(hhmm) for hhmm in spots]
    monkeypatch.setattr(bars.r2, "list_keys", lambda s3, prefix: list(keys))

    def _get_parquet(s3: object, key: str) -> pd.DataFrame:
        hhmm = key.rsplit("T", 1)[-1].removesuffix("Z.parquet")
        return pd.DataFrame({"spot": [spots[hhmm]], "bid": [1.0], "ask": [1.1]})

    monkeypatch.setattr(bars.r2, "get_parquet", _get_parquet)


def test_builds_ohlc_minute_rows_from_spot(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, {"1400": 745.0, "1402": 745.5, "1404": 744.8})
    after = pd.Timestamp(f"{_today()} 00:00", tz="UTC")
    out = bars._recorder_spot_tail(None, "SPY", after)

    assert out is not None and len(out) == 3
    # sorted ascending by capture time
    assert list(out["minute_ts"].dt.strftime("%H%M")) == ["1400", "1402", "1404"]
    # each snapshot is a spot tick: open == high == low == close == spot, no volume
    assert list(out["close"]) == [745.0, 745.5, 744.8]
    for col in ("open", "high", "low"):
        assert list(out[col]) == list(out["close"])
    assert set(out["volume"]) == {0.0}


def test_after_filters_older_snapshots(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, {"1400": 745.0, "1402": 745.5, "1404": 744.8})
    after = pd.Timestamp(f"{_today()} 14:02", tz="UTC")  # strictly greater
    out = bars._recorder_spot_tail(None, "SPY", after)
    assert out is not None
    assert list(out["minute_ts"].dt.strftime("%H%M")) == ["1404"]


def test_incremental_cache_reads_only_new_snapshots(monkeypatch: pytest.MonkeyPatch) -> None:
    reads: list[str] = []
    spots = {"1400": 745.0, "1402": 745.5}
    keys = {"val": [_key(k) for k in spots]}
    monkeypatch.setattr(bars.r2, "list_keys", lambda s3, prefix: list(keys["val"]))

    def _get_parquet(s3: object, key: str) -> pd.DataFrame:
        reads.append(key)
        hhmm = key.rsplit("T", 1)[-1].removesuffix("Z.parquet")
        return pd.DataFrame({"spot": [spots[hhmm]]})

    monkeypatch.setattr(bars.r2, "get_parquet", _get_parquet)
    after = pd.Timestamp(f"{_today()} 00:00", tz="UTC")

    bars._recorder_spot_tail(None, "SPY", after)
    assert len(reads) == 2  # both snapshots read on first call

    # a new snapshot lands; force a re-list (bypass the 20s TTL), fetch again
    spots["1404"] = 744.8
    keys["val"].append(_key("1404"))
    bars._recorder_cache["SPY"]["listed_at"] = 0.0
    out = bars._recorder_spot_tail(None, "SPY", after)

    assert len(reads) == 3  # only the ONE new snapshot was read (2 cached)
    assert out is not None and len(out) == 3


def test_none_when_recorder_has_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bars.r2, "list_keys", lambda s3, prefix: [])
    after = pd.Timestamp(f"{_today()} 00:00", tz="UTC")
    assert bars._recorder_spot_tail(None, "SPY", after) is None


def test_get_bars_uses_recorder_tail_when_no_iex(monkeypatch: pytest.MonkeyPatch) -> None:
    """End to end: a nightly lake that ends yesterday + today's recorder spots
    (no APCA keys) → get_bars returns live with the DELAYED label and today's
    intraday candles appended."""
    monkeypatch.delenv("APCA_API_KEY_ID", raising=False)
    monkeypatch.delenv("APCA_API_SECRET_KEY", raising=False)
    monkeypatch.setattr(bars.r2, "r2_client", lambda: object())

    # nightly lake: yesterday 14:00–14:29 UTC (so its last bar is > 2 min stale)
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
    _install(monkeypatch, {"1400": 745.0, "1402": 745.6, "1404": 745.2})

    out = bars.get_bars("SPY", "5m", "1w", [])
    assert out["live"] is True
    assert out["live_label"] == "delayed ~15m · CBOE recorder"
    assert "CBOE recorder" in out["source"]
    # today's spot candles made it into the buffer
    last_date = pd.Timestamp(out["bars"][-1]["t"]).tz_convert(bars.ET).date()
    assert last_date.isoformat() == _today()
