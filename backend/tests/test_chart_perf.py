"""Chart/Observatory load-time plumbing: the concurrent month fetch keeps
frames in month order and answers repeats from cache, the month cache is
bounded (OOM guard), and coverage serves stale-while-revalidating without
ever inventing a payload."""

import threading
import time

import pandas as pd
import pytest

from app.data import bars
from app.data import coverage as cov


def _month_frame(month: str) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "minute_ts": [pd.Timestamp(f"{month}-01T14:30:00Z")],
            "open": [1.0],
            "high": [1.0],
            "low": [1.0],
            "close": [1.0],
            "volume": [1],
        }
    )


def test_cached_months_preserves_order_and_caches(monkeypatch: pytest.MonkeyPatch) -> None:
    bars._month_cache.clear()
    calls: list[str] = []

    def fake_get_parquet(s3: object, key: str) -> pd.DataFrame:
        calls.append(key)
        return _month_frame(key.split("month=")[1].split("/")[0])

    monkeypatch.setattr(bars.r2, "get_parquet", fake_get_parquet)
    months = ["2025-01", "2025-02", "2025-03", "2025-04"]
    frames = bars._cached_months(None, "SPY", months)
    # concurrent fetch, but the assembled frames stay in requested order
    assert [f["minute_ts"].iloc[0].strftime("%Y-%m") for f in frames] == months
    n = len(calls)
    bars._cached_months(None, "SPY", months)
    assert len(calls) == n  # a warm repeat never re-reads the lake
    bars._month_cache.clear()


def test_month_cache_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    bars._month_cache.clear()
    monkeypatch.setattr(bars, "_MONTH_CACHE_MAX", 5)
    monkeypatch.setattr(bars.r2, "get_parquet", lambda s3, key: None)
    for i in range(12):
        bars._cached_month(None, "SPY", f"2024-{i + 1:02d}")
    assert len(bars._month_cache) <= 5
    bars._month_cache.clear()


def test_coverage_fresh_cache_never_rebuilds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cov, "build_coverage", lambda: pytest.fail("must not rebuild"))
    monkeypatch.setitem(cov._CACHE, "snap", (time.time(), {"generated_at": "x"}))
    assert cov.coverage_cached()["generated_at"] == "x"


def test_coverage_serves_stale_while_revalidating(monkeypatch: pytest.MonkeyPatch) -> None:
    stale = {"generated_at": "old"}
    fresh = {"generated_at": "new"}
    built = threading.Event()

    def fake_build() -> dict[str, str]:
        built.set()
        return fresh

    monkeypatch.setattr(cov, "build_coverage", fake_build)
    monkeypatch.setitem(cov._CACHE, "snap", (time.time() - cov.CACHE_SECONDS - 1, stale))
    # served instantly from the (real, age-disclosed) cache — never blocks
    assert cov.coverage_cached() is stale
    assert built.wait(5), "background rebuild never ran"
    deadline = time.time() + 5
    while cov._CACHE["snap"][1] is not fresh and time.time() < deadline:
        time.sleep(0.02)
    assert cov._CACHE["snap"][1] is fresh


def test_coverage_too_stale_blocks_and_rebuilds(monkeypatch: pytest.MonkeyPatch) -> None:
    fresh = {"generated_at": "new"}
    monkeypatch.setattr(cov, "build_coverage", lambda: fresh)
    too_old = time.time() - cov.CACHE_SECONDS - cov.STALE_SERVE_SECONDS - 1
    monkeypatch.setitem(cov._CACHE, "snap", (too_old, {"generated_at": "ancient"}))
    # past the serve-stale ceiling the old heartbeat is NOT presented as
    # current — the caller waits for a real rebuild
    assert cov.coverage_cached() is fresh
