"""F0 new-source PIT readers — hand-computed truncation fixtures.

Every reader established in the F0 data spine proves, per source:
  (a) row-level truncation at an intra-session as_of (files carry intraday
      timestamps — file-date filtering alone is not point-in-time),
  (b) LookaheadError on any request beyond as_of,
  (c) honest `unavailable` (None) when the lake has nothing — never a guess,
  (d) collector metadata (captured_at) is never used as observation time.

r2 is monkeypatched; no live lake. The deliberately-lookahead "evil reader"
test proves the truncation assertions have teeth (would go red if the PIT
bound were dropped).
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import pandas as pd
import pytest

from app.data import ivol_analytics, massive, r2, uw
from app.engine.market import LookaheadError

D = date(2026, 6, 5)
D_PREV = date(2026, 6, 4)
D_NEXT = date(2026, 6, 8)


@pytest.fixture(autouse=True)
def _clear_caches() -> None:
    uw._FRAME_CACHE.clear()
    massive._FRAME_CACHE.clear()
    ivol_analytics._IVS_CACHE.clear()


def _install(monkeypatch: pytest.MonkeyPatch, frames: dict[str, pd.DataFrame]) -> None:
    monkeypatch.setattr(r2, "get_parquet", lambda s3, key: frames.get(key))


def _ts(hhmm: str, day: date = D) -> str:
    return f"{day}T{hhmm[:2]}:{hhmm[2:]}:00Z"


# ── UW per-session rows: row-level truncation ───────────────────────────────
def _tide_frame() -> pd.DataFrame:
    # market_tide idiom: ET-offset timestamps, one row per 5-min bucket
    return pd.DataFrame({
        "timestamp": [f"{D}T09:30:00-04:00", f"{D}T09:35:00-04:00", f"{D}T09:40:00-04:00"],
        "net_call_premium": [-16259243.0, -54176718.0, 1000000.0],
        "date": [str(D)] * 3,
        "captured_at": ["2026-07-07T02:14:04+00:00"] * 3,  # metadata, must be ignored
    })


def test_uw_daily_row_level_truncation(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, {f"uw/market_tide/date={D}/rows.parquet": _tide_frame()})
    # as_of 13:36 UTC == 09:36 ET → exactly the 09:30 and 09:35 rows survive
    as_of = datetime(2026, 6, 5, 13, 36, tzinfo=UTC)
    out = uw.daily_rows(None, "market_tide", None, D, as_of)
    assert out is not None and len(out) == 2
    assert list(out["net_call_premium"]) == [-16259243.0, -54176718.0]


def test_uw_daily_full_session_visible_at_date_as_of(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, {f"uw/market_tide/date={D}/rows.parquet": _tide_frame()})
    out = uw.daily_rows(None, "market_tide", None, D, D)  # end-of-day view
    assert out is not None and len(out) == 3


def test_uw_daily_earlier_session_fully_visible(monkeypatch: pytest.MonkeyPatch) -> None:
    # intra-session moment on the 8th → the 5th's file is history, all rows
    _install(monkeypatch, {f"uw/market_tide/date={D}/rows.parquet": _tide_frame()})
    as_of = datetime(2026, 6, 8, 13, 31, tzinfo=UTC)
    out = uw.daily_rows(None, "market_tide", None, D, as_of)
    assert out is not None and len(out) == 3


def test_uw_daily_beyond_as_of_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, {f"uw/market_tide/date={D_NEXT}/rows.parquet": _tide_frame()})
    with pytest.raises(LookaheadError):
        uw.daily_rows(None, "market_tide", None, D_NEXT, D)


def test_uw_daily_absent_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, {})
    assert uw.daily_rows(None, "spot_exposures", "SPY", D, D) is None


def test_uw_daily_unknown_family_refused() -> None:
    with pytest.raises(ValueError):
        uw.daily_rows(None, "made_up_family", "SPY", D, D)


def test_uw_spot_exposures_z_stamps(monkeypatch: pytest.MonkeyPatch) -> None:
    frame = pd.DataFrame({
        "time": [_ts("1430"), _ts("1449")],  # 10:30 / 10:49 ET as UTC Z-stamps
        "gamma_per_one_percent_move_oi": [101.14, -3211191427.18],
        "ticker": ["SPY", "SPY"],
    })
    _install(monkeypatch, {f"uw/spot_exposures/ticker=SPY/date={D}/rows.parquet": frame})
    as_of = datetime(2026, 6, 5, 14, 45, tzinfo=UTC)
    out = uw.daily_rows(None, "spot_exposures", "SPY", D, as_of)
    assert out is not None and len(out) == 1
    assert float(out["gamma_per_one_percent_move_oi"].iloc[0]) == 101.14


def test_uw_rows_without_timestamps_hidden_intrasession(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # a family whose rows carry no intraday stamps is a same-day observation:
    # visible at the date-level view, honestly EMPTY at an intra-session moment
    frame = pd.DataFrame({"max_pain": [745.0], "ticker": ["SPY"]})
    _install(monkeypatch, {f"uw/max_pain/ticker=SPY/date={D}/rows.parquet": frame})
    assert uw.daily_rows(None, "max_pain", "SPY", D, D) is not None
    midday = datetime(2026, 6, 5, 15, 0, tzinfo=UTC)
    assert uw.daily_rows(None, "max_pain", "SPY", D, midday) is None


def test_uw_naive_stamps_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    # review finding: a tz-naive stamp could mean ET or UTC — localizing by
    # assumption can hide a session of lookahead, so rows whose wall-clock
    # reference is unknowable are UNOBSERVABLE at an intra-session moment
    frame = pd.DataFrame({
        "timestamp": [f"{D} 09:30:00", f"{D} 15:30:00"],  # naive — ET? UTC?
        "net_call_premium": [1.0, 2.0],
    })
    _install(monkeypatch, {f"uw/market_tide/date={D}/rows.parquet": frame})
    midday = datetime(2026, 6, 5, 20, 0, tzinfo=UTC)
    assert uw.daily_rows(None, "market_tide", None, D, midday) is None  # fail closed
    # ...but a date-level (end-of-day) view still sees the session's file
    assert uw.daily_rows(None, "market_tide", None, D, D) is not None


def test_uw_mixed_naive_rows_drop_out(monkeypatch: pytest.MonkeyPatch) -> None:
    # one naive stamp among aware ones: the naive ROW drops (fail closed),
    # the aware rows keep honest ≤-moment behavior
    frame = pd.DataFrame({
        "timestamp": [f"{D}T13:30:00Z", f"{D} 13:31:00", f"{D}T13:40:00Z"],
        "net_call_premium": [1.0, 2.0, 3.0],
    })
    _install(monkeypatch, {f"uw/market_tide/date={D}/rows.parquet": frame})
    as_of = datetime(2026, 6, 5, 13, 35, tzinfo=UTC)
    out = uw.daily_rows(None, "market_tide", None, D, as_of)
    assert out is not None and list(out["net_call_premium"]) == [1.0]


def test_uw_exotic_offset_as_of_bound_is_utc_normalized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # review finding: as_of 09:00+14:00 on the 6th IS 19:00 UTC on the 5th
    # (15:00 ET, mid-session). The bound must derive from the UTC-normalized
    # moment: D stays the LIVE session and its 15:30 ET row stays invisible.
    frame = pd.DataFrame({
        "timestamp": [f"{D}T09:30:00-04:00", f"{D}T15:30:00-04:00"],
        "net_call_premium": [1.0, 2.0],
    })
    _install(monkeypatch, {f"uw/market_tide/date={D}/rows.parquet": frame})
    from datetime import timedelta, timezone

    as_of = datetime(2026, 6, 6, 9, 0, tzinfo=timezone(timedelta(hours=14)))
    out = uw.daily_rows(None, "market_tide", None, D, as_of)
    assert out is not None and list(out["net_call_premium"]) == [1.0]


# ── UW series: observation-date truncation ──────────────────────────────────
def test_uw_series_truncates_by_observation_date(monkeypatch: pytest.MonkeyPatch) -> None:
    frame = pd.DataFrame({
        "date": [str(D_PREV), str(D), str(D_NEXT)],
        "iv_rank": [10.0, 20.0, 30.0],
    })
    _install(monkeypatch, {"reference/uw/iv_rank/ticker=SPY.parquet": frame})
    out = uw.series(None, "iv_rank", "SPY", D)
    assert out is not None and list(out["iv_rank"]) == [10.0, 20.0]


def test_uw_series_same_day_hidden_intrasession(monkeypatch: pytest.MonkeyPatch) -> None:
    # review finding: series rows are END-OF-DAY observations — at 09:35 ET
    # on D, D's own observation must NOT exist yet (only strictly-earlier
    # sessions), matching the daily-close rule in docs/HONESTY.md
    frame = pd.DataFrame({
        "date": [str(D_PREV), str(D)],
        "iv_rank": [10.0, 20.0],
    })
    _install(monkeypatch, {"reference/uw/iv_rank/ticker=SPY.parquet": frame})
    midday = datetime(2026, 6, 5, 13, 35, tzinfo=UTC)  # 09:35 ET on D
    out = uw.series(None, "iv_rank", "SPY", midday)
    assert out is not None and list(out["iv_rank"]) == [10.0]  # D excluded


def test_uw_series_unboundable_rows_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    # no recognizable observation-date column → unavailable, never unbounded
    frame = pd.DataFrame({"iv_rank": [10.0], "captured_at": ["2026-07-07T00:00:00Z"]})
    _install(monkeypatch, {"reference/uw/iv_rank/ticker=SPY.parquet": frame})
    assert uw.series(None, "iv_rank", "SPY", D) is None


# ── UW minute bars: trade candles, never a fill source ──────────────────────
def _minute_frame() -> pd.DataFrame:
    return pd.DataFrame({
        "start_time": [_ts("1930"), _ts("1931"), _ts("1955")],
        "close": [7.0, 7.1, 7.35],
        "volume_ask_side": [1, 2, 1],
        "occ_symbol": ["SPY260706C00739000"] * 3,
    })


def test_uw_minute_bars_truncate_and_sort(monkeypatch: pytest.MonkeyPatch) -> None:
    key = f"uw/option_intraday/ticker=SPY/symbol=SPY260706C00739000/date={D}/bars.parquet"
    _install(monkeypatch, {key: _minute_frame()})
    as_of = datetime(2026, 6, 5, 19, 32, tzinfo=UTC)
    out = uw.minute_bars(None, "SPY", "SPY260706C00739000", D, as_of)
    assert out is not None and list(out["close"]) == [7.0, 7.1]


def test_uw_minute_bars_have_no_nbbo_columns(monkeypatch: pytest.MonkeyPatch) -> None:
    """Documents the clock-vs-quote split (owner decision 2026-07-07): UW
    minute bars carry NO bid/ask. If UW ever adds NBBO columns this goes red
    — the signal to revisit the fill-source ban, not to silently fill."""
    key = f"uw/option_intraday/ticker=SPY/symbol=SPY260706C00739000/date={D}/bars.parquet"
    _install(monkeypatch, {key: _minute_frame()})
    out = uw.minute_bars(None, "SPY", "SPY260706C00739000", D, D)
    assert out is not None
    assert "bid" not in out.columns and "ask" not in out.columns


def test_uw_minute_bars_beyond_as_of_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, {})
    with pytest.raises(LookaheadError):
        uw.minute_bars(None, "SPY", "X", D_NEXT, D)


# ── Massive aggregates ───────────────────────────────────────────────────────
def _agg_frame() -> pd.DataFrame:
    return pd.DataFrame({
        "date": [str(D_PREV), str(D), str(D_NEXT)],
        "c": [88.0, 89.1, 90.0],
        "v": [50, 100, 70],
    })


def test_massive_agg_truncates(monkeypatch: pytest.MonkeyPatch) -> None:
    key = "reference/massive/option_agg/ticker=QQQ/symbol=O:QQQTEST.parquet"
    _install(monkeypatch, {key: _agg_frame()})
    out = massive.option_agg(None, "QQQ", "O:QQQTEST", D)
    assert out is not None and list(out["c"]) == [88.0, 89.1]


def test_massive_agg_session_beyond_as_of_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, {})
    with pytest.raises(LookaheadError):
        massive.option_agg(None, "QQQ", "O:QQQTEST", D, session=D_NEXT)


def test_massive_agg_nothing_visible_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # a contract with rows only after as_of has nothing visible → None
    key = "reference/massive/option_agg/ticker=QQQ/symbol=O:QQQTEST.parquet"
    _install(monkeypatch, {key: _agg_frame()})
    assert massive.option_agg(None, "QQQ", "O:QQQTEST", date(2026, 6, 1)) is None


def test_massive_same_day_hidden_intrasession(monkeypatch: pytest.MonkeyPatch) -> None:
    # review finding: daily OHLCV aggregates are END-OF-DAY observations —
    # at 09:35 ET on D, D's own row must not exist yet
    key = "reference/massive/option_agg/ticker=QQQ/symbol=O:QQQTEST.parquet"
    _install(monkeypatch, {key: _agg_frame()})
    midday = datetime(2026, 6, 5, 13, 35, tzinfo=UTC)
    out = massive.option_agg(None, "QQQ", "O:QQQTEST", midday)
    assert out is not None and list(out["c"]) == [88.0]  # D_PREV only


def test_massive_contracts_reference_returns_a_copy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # review finding: never hand out the cached frame — caller mutation must
    # not poison the process-wide cache
    key = "reference/massive/contracts/ticker=QQQ.parquet"
    _install(monkeypatch, {key: pd.DataFrame({"ticker": ["O:QQQTEST"]})})
    first = massive.contracts_reference(None, "QQQ")
    assert first is not None
    first["ticker"] = "MUTATED"
    second = massive.contracts_reference(None, "QQQ")
    assert second is not None and list(second["ticker"]) == ["O:QQQTEST"]


# ── iVol IVS surfaces ────────────────────────────────────────────────────────
def _surface_frame() -> pd.DataFrame:
    return pd.DataFrame({
        "period": [7, 7], "strike": [737.42, 737.42],
        "Call/Put": ["C", "P"], "IV": [0.182558, 0.177445],
        "delta": [0.516079, -0.486669],
    })


def test_ivs_surface_reads_and_normalizes(monkeypatch: pytest.MonkeyPatch) -> None:
    key = f"reference/ivol/ivs/ticker=SPY/date={D}/surface.parquet"
    _install(monkeypatch, {key: _surface_frame()})
    out = ivol_analytics.load_ivs_surface(None, "SPY", D, D)
    assert out is not None and len(out) == 2
    assert float(out["IV"].iloc[0]) == pytest.approx(0.182558)


def test_ivs_surface_beyond_as_of_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, {})
    with pytest.raises(LookaheadError):
        ivol_analytics.load_ivs_surface(None, "SPY", D_NEXT, D)


def test_ivs_surface_absent_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, {})
    assert ivol_analytics.load_ivs_surface(None, "SPY", D, D) is None


def test_ivs_surface_same_day_hidden_intrasession(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # review finding: a surface is an END-OF-DAY fit — at 09:35 ET on D,
    # D's own surface does not exist yet (honest None, not a raise: the
    # session itself is not beyond as_of)
    key = f"reference/ivol/ivs/ticker=SPY/date={D}/surface.parquet"
    _install(monkeypatch, {key: _surface_frame()})
    midday = datetime(2026, 6, 5, 13, 35, tzinfo=UTC)
    assert ivol_analytics.load_ivs_surface(None, "SPY", D, midday) is None
    # and it appears at the end-of-day view
    assert ivol_analytics.load_ivs_surface(None, "SPY", D, D) is not None


# ── the evil reader: proof the assertions have teeth ─────────────────────────
def _evil_daily_rows(
    s3: Any, family: str, ticker: str | None, session: date, as_of: date | datetime
) -> pd.DataFrame | None:
    """Deliberately-lookahead variant: same signature, NO truncation. The
    PIT property test must fail against it — proving the canary bites."""
    key = (f"uw/{family}/date={session}/rows.parquet" if ticker is None
           else f"uw/{family}/ticker={ticker}/date={session}/rows.parquet")
    return r2.get_parquet(None, key)


def test_evil_reader_is_caught_by_the_pit_property(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(monkeypatch, {f"uw/market_tide/date={D}/rows.parquet": _tide_frame()})
    as_of = datetime(2026, 6, 5, 13, 36, tzinfo=UTC)

    def pit_property(reader: Any) -> None:
        out = reader(None, "market_tide", None, D, as_of)
        assert out is not None
        stamps = pd.to_datetime(out["timestamp"], utc=True, format="mixed")
        assert bool((stamps <= pd.Timestamp(as_of)).all()), "rows beyond as_of leaked"

    pit_property(uw.daily_rows)  # the real reader passes
    with pytest.raises(AssertionError, match="rows beyond as_of leaked"):
        pit_property(_evil_daily_rows)  # the lookahead reader is caught
