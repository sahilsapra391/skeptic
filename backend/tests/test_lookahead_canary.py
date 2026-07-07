"""Guardrail #2 canary — permanent required check (BUILD-PLAN cross-
milestone rules). A simulation at date T attempting to read past T must
raise, always. If this test goes red, everything stops."""

from datetime import date

import pytest

from app.engine.market import LookaheadError, MarketView, build_fixture_store
from tests.fixtures.engine import fx_short_put_assigned as fx


def _store():
    return build_fixture_store("SPY", fx.CHAINS, fx.UNDERLYING)


def test_close_beyond_as_of_raises() -> None:
    view = MarketView(_store(), date(2025, 1, 6))
    with pytest.raises(LookaheadError):
        view.close(date(2025, 1, 7))


def test_open_beyond_as_of_raises() -> None:
    view = MarketView(_store(), date(2025, 1, 13))
    with pytest.raises(LookaheadError):
        view.open_price(date(2025, 1, 17))


def test_trailing_series_is_bounded() -> None:
    store = _store()
    early = MarketView(store, date(2025, 1, 6))
    late = MarketView(store, date(2025, 1, 17))
    assert len(early.closes_upto()) == 1  # only the 6th
    assert len(late.closes_upto()) == 3  # 6th, 13th, 17th
    # the early view must not see the crash that happens later
    assert 94.0 not in early.closes_upto()


def test_chain_is_as_of_only() -> None:
    store = _store()
    view = MarketView(store, date(2025, 1, 6))
    keys = list(view.chain())
    assert keys, "entry-day chain should be visible"
    # the 2025-01-13 snapshot has a different quote; it must be invisible now
    q = view.quote(keys[0])
    assert q is not None and q.bid == 2.00  # entry-day quote, not the later 3.00


# ── F0 (ENGINE-V4): every new lake source honors the same contract ──────────
# UW daily/series/minute, Massive aggregates and iVol IVS surfaces all raise
# LookaheadError beyond as_of and truncate rows AT it. The hand-computed
# fixtures (incl. row-level intra-session truncation and the deliberately-
# lookahead "evil reader" red test) live in test_new_source_readers.py; this
# section keeps the beyond-as_of raise visible in the permanent canary.

def test_new_sources_raise_beyond_as_of(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from app.data import ivol_analytics, massive, r2, uw
    from app.engine.market import LookaheadError as LE

    monkeypatch.setattr(r2, "get_parquet", lambda s3, key: None)
    t, t_next = date(2026, 6, 5), date(2026, 6, 8)
    with pytest.raises(LE):
        uw.daily_rows(None, "market_tide", None, t_next, t)
    with pytest.raises(LE):
        uw.minute_bars(None, "SPY", "SPY260706C00739000", t_next, t)
    with pytest.raises(LE):
        massive.option_agg(None, "QQQ", "O:QQQTEST", t, session=t_next)
    with pytest.raises(LE):
        ivol_analytics.load_ivs_surface(None, "SPY", t_next, t)
