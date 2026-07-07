"""FX.1 — the 1-min underlying frame builder (app/data/intraday.py).

bars_1m rows carry the MOST RECENT print, so a session's open repeats the
prior session's close until a fresh print lands (probe-verified 2026-07-07:
Monday 09:30 rows carried Friday's lastDateTime). Those stale rows must be
dropped — a Friday print is not a Monday price — and the grid bounded to
regular hours. Volume is cumulative → per-bar diff, first fresh bar carries
its cumulative (the 5-min underlying convention)."""

from __future__ import annotations

import pandas as pd
import pytest

from app.data import intraday, r2

D = "2025-01-06"


def _frame(rows: list[tuple[str, float, str, float]]) -> pd.DataFrame:
    # (timestamp, lastPrice, lastDateTime, cumulative volume)
    return pd.DataFrame({
        "timestamp": [r[0] for r in rows],
        "lastPrice": [r[1] for r in rows],
        "lastDateTime": [r[2] for r in rows],
        "volume": [r[3] for r in rows],
        "bidPrice": 99.9, "askPrice": 100.1,  # present but unused here
    })


def _install(monkeypatch: pytest.MonkeyPatch, df: pd.DataFrame | None) -> None:
    monkeypatch.setattr(r2, "get_parquet", lambda s3, key: df)


def test_stale_prior_session_prints_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    df = _frame([
        (f"{D} 09:30:00", 99.50, "2025-01-03 16:00:04", 95_000_000),  # Friday print
        (f"{D} 09:31:00", 100.10, f"{D} 09:30:59", 12_000),
        (f"{D} 09:32:00", 100.20, f"{D} 09:31:58", 30_000),
    ])
    _install(monkeypatch, df)
    out = intraday._minute_und_frame(None, "SPY", D)
    assert out is not None and len(out) == 2
    assert list(out["last"]) == [100.10, 100.20]
    # cumulative → per-bar: first FRESH bar carries its cumulative
    assert list(out["volume"]) == [12_000.0, 18_000.0]


def test_grid_bounded_to_regular_hours(monkeypatch: pytest.MonkeyPatch) -> None:
    df = _frame([
        (f"{D} 09:29:00", 100.0, f"{D} 09:28:59", 1_000),   # pre-open
        (f"{D} 09:30:00", 100.1, f"{D} 09:29:59", 2_000),
        (f"{D} 15:59:00", 101.0, f"{D} 15:58:59", 50_000),
        (f"{D} 16:05:00", 101.5, f"{D} 16:04:59", 51_000),  # post-close
    ])
    _install(monkeypatch, df)
    out = intraday._minute_und_frame(None, "SPY", D)
    assert out is not None
    assert [t.strftime("%H:%M") for t in out["bar_ts"]] == ["09:30", "15:59"]


def test_all_stale_session_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    df = _frame([(f"{D} 09:30:00", 99.5, "2025-01-03 16:00:04", 95_000_000)])
    _install(monkeypatch, df)
    assert intraday._minute_und_frame(None, "SPY", D) is None


def test_absent_or_short_columns_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, None)
    assert intraday._minute_und_frame(None, "SPY", D) is None
    _install(monkeypatch, pd.DataFrame({"timestamp": [f"{D} 09:30:00"]}))
    assert intraday._minute_und_frame(None, "SPY", D) is None
