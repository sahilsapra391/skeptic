"""FX.1 — minute-grid data plumbing (app/data/intraday.py).

The frame builder: bars_1m rows carry the MOST RECENT print, so a session's
open repeats the prior session's close until a fresh print lands
(probe-verified 2026-07-07: Monday 09:30 rows carried Friday's
lastDateTime). Those stale rows must be dropped — a Friday print is not a
Monday price — the grid bounded to regular hours, non-minute-aligned stamps
dropped, and the frame is PRICE-ONLY (the 5-min frame stays the single
source of indicator samples and VWAP volume — review finding 1).

The store glue: minute_slice_for merges the 5-MIN underlying frame (wins at
its stamps, price + volume, 16:00+ tail included) with bars_1m price-only
rows between stamps, and marks the 5-min stamps as the indicator sampling
set — the minute grid reads the SAME indicator/VWAP record the 5-min grid
does."""

from __future__ import annotations

from datetime import date, datetime

import pandas as pd
import pytest

from app.data import intraday, r2

D = "2025-01-06"


def _frame(rows: list[tuple[str, float, str]]) -> pd.DataFrame:
    # (timestamp, lastPrice, lastDateTime)
    return pd.DataFrame({
        "timestamp": [r[0] for r in rows],
        "lastPrice": [r[1] for r in rows],
        "lastDateTime": [r[2] for r in rows],
        "bidPrice": 99.9, "askPrice": 100.1,  # present but unused here
    })


def _install(monkeypatch: pytest.MonkeyPatch, df: pd.DataFrame | None) -> None:
    monkeypatch.setattr(r2, "get_parquet", lambda s3, key: df)


def test_stale_prior_session_prints_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    df = _frame([
        (f"{D} 09:30:00", 99.50, "2025-01-03 16:00:04"),  # Friday print
        (f"{D} 09:31:00", 100.10, f"{D} 09:30:59"),
        (f"{D} 09:32:00", 100.20, f"{D} 09:31:58"),
    ])
    _install(monkeypatch, df)
    out = intraday._minute_und_frame(None, "SPY", D)
    assert out is not None and len(out) == 2
    assert list(out["last"]) == [100.10, 100.20]
    assert "volume" not in out.columns  # price-only, by design


def test_grid_bounded_and_alignment_guarded(monkeypatch: pytest.MonkeyPatch) -> None:
    df = _frame([
        (f"{D} 09:29:00", 100.0, f"{D} 09:28:59"),   # pre-open
        (f"{D} 09:30:00", 100.1, f"{D} 09:29:59"),
        (f"{D} 09:31:17", 100.2, f"{D} 09:31:05"),   # off-minute stamp
        (f"{D} 15:59:00", 101.0, f"{D} 15:58:59"),
        (f"{D} 16:05:00", 101.5, f"{D} 16:04:59"),   # post-close
    ])
    _install(monkeypatch, df)
    out = intraday._minute_und_frame(None, "SPY", D)
    assert out is not None
    assert [t.strftime("%H:%M") for t in out["bar_ts"]] == ["09:30", "15:59"]


def test_all_stale_session_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    df = _frame([(f"{D} 09:30:00", 99.5, "2025-01-03 16:00:04")])
    _install(monkeypatch, df)
    assert intraday._minute_und_frame(None, "SPY", D) is None


def test_absent_or_short_columns_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, None)
    assert intraday._minute_und_frame(None, "SPY", D) is None
    _install(monkeypatch, pd.DataFrame({"timestamp": [f"{D} 09:30:00"]}))
    assert intraday._minute_und_frame(None, "SPY", D) is None


# ── the store glue: minute_slice_for merge semantics ─────────────────────────

_SESSION = date(2025, 1, 6)


def _opt_frame() -> pd.DataFrame:
    return pd.DataFrame({
        "bar_ts": [pd.Timestamp(f"{D} 09:30:00"), pd.Timestamp(f"{D} 09:35:00")],
        "expiration": [D, D], "right": ["put", "put"], "strike": [100.0, 100.0],
        "bid": [2.0, 1.9], "ask": [2.1, 2.0], "last": [None, None],
        "volume": [10, 12], "open_interest": [None, None], "iv": [0.2, 0.2],
        "delta": [-0.5, -0.5], "gamma": [None, None], "theta": [None, None],
        "vega": [None, None], "rho": [None, None],
    })


def _und5_frame() -> pd.DataFrame:
    # includes the 16:00 tail row the 5-min record really carries
    return pd.DataFrame({
        "bar_ts": [pd.Timestamp(f"{D} 09:30:00"), pd.Timestamp(f"{D} 09:35:00"),
                   pd.Timestamp(f"{D} 16:00:00")],
        "last": [100.0, 100.5, 101.0],
        "volume": [1_000.0, 2_000.0, 500.0],
    })


def _und1_frame() -> pd.DataFrame:
    # bars_1m disagrees at 09:30 (100.02) — the 5-min frame must WIN there
    return pd.DataFrame({
        "bar_ts": [pd.Timestamp(f"{D} 09:30:00"), pd.Timestamp(f"{D} 09:31:00"),
                   pd.Timestamp(f"{D} 09:32:00")],
        "last": [100.02, 100.10, 100.20],
    })


@pytest.fixture()
def store(monkeypatch: pytest.MonkeyPatch, tmp_path) -> intraday.IntradayStore:  # type: ignore[no-untyped-def]
    st = intraday.IntradayStore("SPY")
    monkeypatch.setattr(intraday, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(st, "minute_sessions", lambda: {_SESSION})
    monkeypatch.setattr(
        st, "_ensure_cached", lambda session: (_opt_frame(), _und5_frame(), "ivol_5min"))
    monkeypatch.setattr(intraday, "_minute_und_frame",
                        lambda s3, ticker, d: _und1_frame())
    monkeypatch.setattr(r2, "r2_client", lambda: object())
    return st


def test_minute_slice_merges_und5_wins_at_stamps(store: intraday.IntradayStore) -> None:
    slc = store.minute_slice_for(_SESSION)
    assert slc is not None and slc.bar_resolution == "1min"
    t = lambda hhmm: datetime(2025, 1, 6, int(hhmm[:2]), int(hhmm[3:]))  # noqa: E731
    # 5-min frame WINS at its stamps; bars_1m fills between; tail included
    assert slc.underlying[t("09:30")] == 100.0  # not bars_1m's 100.02
    assert slc.underlying[t("09:31")] == 100.10
    assert slc.underlying[t("09:32")] == 100.20
    assert slc.underlying[t("16:00")] == 101.0
    # VWAP volume comes ONLY from the 5-min stamps — minute rows carry none
    assert set(slc.underlying_volume) == {t("09:30"), t("09:35"), t("16:00")}
    # indicator sampling set == the 5-min stamps (incl. the tail)
    assert slc.indicator_stamps == {t("09:30"), t("09:35"), t("16:00")}
    # bar grid = quotes ∪ merged underlying
    assert t("09:31") in slc.bars and t("16:00") in slc.bars


def test_minute_slice_requires_und5(store: intraday.IntradayStore,
                                    monkeypatch: pytest.MonkeyPatch) -> None:
    # no 5-min underlying record → no honest minute grid (fall back, not fake)
    monkeypatch.setattr(
        store, "_ensure_cached", lambda session: (_opt_frame(), None, "ivol_5min"))
    assert store.minute_slice_for(_SESSION) is None


def test_ineligible_session_is_none_and_not_cached(
    store: intraday.IntradayStore, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(store, "minute_sessions", lambda: set())
    assert store.minute_slice_for(_SESSION) is None
    assert _SESSION not in store._lru_1m  # negatives never cached — data may arrive


def test_minute_und_disk_cache_round_trip(store: intraday.IntradayStore,
                                          monkeypatch: pytest.MonkeyPatch) -> None:
    first = store._minute_und_cached(_SESSION)
    assert first is not None
    # second read must come from disk: kill the fetch path to prove it
    monkeypatch.setattr(intraday, "_minute_und_frame",
                        lambda s3, ticker, d: pytest.fail("should hit disk cache"))
    second = store._minute_und_cached(_SESSION)
    assert second is not None and list(second["last"]) == list(first["last"])
