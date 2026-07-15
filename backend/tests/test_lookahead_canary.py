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


# ── The daily indicator cache reads the store's FULL close history ──────────
# (app/engine/daily_series.py) and hands each session position idx-1 of it.
# That is a POINT-IN-TIME claim, so the canary — not only the equivalence
# test — must hold it down: the cache is duck-typed off MarketViewLike, so
# a Protocol-satisfying fake would silently keep the legacy path and leave
# the cached path unguarded (owner requirement 2026-07-15).

_CACHED_CONDS = [
    {"indicator": "rsi", "period": 14, "operator": "<", "value": 50},
    {"indicator": "sma", "period": 5, "operator": ">", "value": 100},
    {"indicator": "ema", "period": 5, "operator": "<", "value": 100},
    {"indicator": "price_vs_sma_pct", "period": 5, "operator": "<", "value": 1.0},
    {"indicator": "price_vs_ema_pct", "period": 5, "operator": ">", "value": -1.0},
    {"indicator": "ema_cross_state", "params": {"fast": 3, "slow": 6},
     "operator": ">", "value": 0},
]


def _synthetic():
    from tests.fixtures.synthetic_market import synthetic_store

    return synthetic_store(seed=11, sessions=120)


@pytest.mark.parametrize("doc", _CACHED_CONDS, ids=lambda d: str(d["indicator"]))
def test_cached_series_cannot_see_the_future(doc) -> None:
    """The direct proof: rewrite every close AFTER as_of and the value the
    cache serves at as_of must not move by a single bit. A cache that
    leaked one future close into a seed, a mean, or a recursion would
    change here."""
    from app.engine.daily_series import DailySeriesCache
    from app.models.spec import Condition

    cond = Condition.model_validate(doc)
    store = _synthetic()
    cutoff_idx = 80
    as_of = store.sessions[cutoff_idx]
    honest = DailySeriesCache(store).tail_pair(cond, as_of)

    # a second store, identical up to as_of, wildly different after it
    poisoned = _synthetic()
    for d in poisoned.sessions[cutoff_idx + 1:]:
        poisoned.underlying_close[d] = 9_999.0
    poisoned.__post_init__()  # rebuild _closes from the poisoned map
    tainted = DailySeriesCache(poisoned).tail_pair(cond, as_of)

    assert honest == tainted, (
        f"{doc['indicator']}: future closes changed the value at {as_of} — "
        "the daily series cache is reading past as_of (guardrail #2)"
    )
    # and the poison IS visible later, or the test proves nothing
    later = DailySeriesCache(poisoned).tail_pair(cond, poisoned.sessions[-1])
    assert later != DailySeriesCache(store).tail_pair(cond, store.sessions[-1])


def test_engine_condition_path_is_bounded_with_the_cache_attached() -> None:
    """The same proof through the REAL read path (MarketView →
    evaluate_condition → cache), not the cache in isolation."""
    from app.engine.conditions import evaluate_condition
    from app.models.spec import Condition

    store, poisoned = _synthetic(), _synthetic()
    cutoff_idx = 80
    for d in poisoned.sessions[cutoff_idx + 1:]:
        poisoned.underlying_close[d] = 9_999.0
    poisoned.__post_init__()
    as_of = store.sessions[cutoff_idx]
    for doc in _CACHED_CONDS:
        cond = Condition.model_validate(doc)
        assert evaluate_condition(MarketView(store, as_of), cond) == evaluate_condition(
            MarketView(poisoned, as_of), cond
        ), f"{doc['indicator']}: the engine's decision at {as_of} moved with FUTURE data"


def test_barview_daily_series_is_bounded_at_the_previous_session() -> None:
    """A daily condition evaluated at an INTRADAY bar must read the
    previous session's history: today's daily close does not exist yet.
    BarView.closes_upto() delegates to _prev — daily_series_pair must
    delegate identically, or the cache (keyed on BarView.as_of, which IS
    today) would read today's close at 09:30."""
    from app.engine.engine import BarView
    from app.engine.market import IntradayView, build_fixture_slice
    from app.models.spec import Condition

    store = _synthetic()
    today = store.sessions[80]
    prev_day = store.sessions[79]
    slc = build_fixture_slice(
        today.isoformat(), quotes={"09:30": []}, underlying={"09:30": 123.45},
    )
    bar = sorted(slc.bars)[0]
    prev_view = MarketView(store, prev_day)
    bview = BarView(IntradayView(slc, bar), prev_view)

    assert bview.as_of == today  # the bar IS today…
    for doc in _CACHED_CONDS:
        cond = Condition.model_validate(doc)
        # …yet its daily history is the previous session's, exactly like
        # closes_upto(), and NOT a view bound at today
        assert bview.daily_series_pair(cond) == prev_view.daily_series_pair(cond)
        assert bview.daily_series_pair(cond) != MarketView(store, today).daily_series_pair(cond)
    assert bview.closes_upto() == prev_view.closes_upto()
