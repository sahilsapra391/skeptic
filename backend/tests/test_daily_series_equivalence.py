"""DailySeriesCache ≡ the per-session recompute — the owner's gate.

The cache reads position i of a series computed over the store's FULL
close history where the engine used to recompute the indicator from the
prefix [0..i]. That is only sound if every cached indicator is causal;
if it is not, a run reads a value derived from FUTURE closes — a
LOOKAHEAD bug (guardrail #2), not a rounding difference.

So this file does not sample. For every cached indicator branch, at
EVERY session of the store, the cached pair must equal the legacy prefix
pair with EXACT float equality (`==`, never approx: the cache's promise
is the same number, not a near one).

`test_lookahead_canary.py` carries the other half — the permanent
point-in-time canary run WITH the cache attached.
"""

from __future__ import annotations

import math

import pytest

from app.engine.conditions import evaluate_condition
from app.engine.daily_series import CACHED_INDICATORS, DailySeriesCache, series_key
from app.engine.market import MarketView
from app.models.spec import Condition
from tests.fixtures.synthetic_market import synthetic_store

# every cached branch, with the period defaults evaluate_condition applies
# (deliberately including None → the branch default, which DIFFERS per
# indicator: 14 oscillators / 50 price-vs-SMA / 20 price-vs-EMA)
CASES: list[dict] = [
    {"indicator": "rsi", "period": 14, "operator": "<", "value": 45},
    {"indicator": "rsi", "period": 2, "operator": ">", "value": 80},
    {"indicator": "rsi", "operator": "<", "value": 30},  # default period
    {"indicator": "sma", "period": 20, "operator": ">", "value": 100},
    {"indicator": "sma", "operator": ">", "value": 100},
    {"indicator": "ema", "period": 9, "operator": "<", "value": 105},
    {"indicator": "ema", "operator": "<", "value": 105},
    {"indicator": "price_vs_sma_pct", "period": 20, "operator": "<", "value": 2.0},
    {"indicator": "price_vs_sma_pct", "operator": ">", "value": -1.0},  # default 50
    {"indicator": "price_vs_ema_pct", "period": 10, "operator": ">", "value": 0.5},
    {"indicator": "price_vs_ema_pct", "operator": "<", "value": 1.0},  # default 20
    {"indicator": "ema_cross_state", "operator": ">", "value": 0},
    {"indicator": "ema_cross_state", "params": {"fast": 5, "slow": 13},
     "operator": "crosses_above", "value": 0},
    {"indicator": "rsi", "period": 14, "operator": "crosses_below", "value": 50},
]


def _cond(doc: dict) -> Condition:
    return Condition.model_validate(doc)


class LegacyView:
    """A MarketView WITHOUT `daily_series_pair`.

    This is the whole point of the gate. `evaluate_condition` picks the
    cached path by duck-typing (`getattr(view, "daily_series_pair", None)`),
    so a plain MarketView takes it — and comparing that against a
    transcription of the legacy math in this file would compare the cache
    to a COPY, leaving the real legacy branch unexecuted and free to drift
    (a period default changed in conditions.py would keep this file green
    while every non-MarketView caller silently evaluated a different
    indicator). Delegating everything except that one attribute forces
    `evaluate_condition` down its real prefix-recompute branch, so the two
    sides of every assertion below are both production code paths.
    """

    def __init__(self, store, as_of) -> None:  # type: ignore[no-untyped-def]
        self._view = MarketView(store, as_of)

    def __getattr__(self, name: str):  # type: ignore[no-untyped-def]
        if name == "daily_series_pair":
            raise AttributeError(name)  # the cache seam is invisible here
        return getattr(self._view, name)


@pytest.fixture(scope="module")
def store():
    # 700 sessions: long enough that a non-causal indicator would drift
    # visibly, short enough to run every session × every case
    return synthetic_store(seed=11, sessions=700)


def test_cases_cover_every_cached_indicator() -> None:
    """The exhaustiveness the gate claims — if a new indicator joins
    CACHED_INDICATORS without a case here, this fails rather than
    silently leaving it unproven."""
    covered = {_cond(c).indicator for c in CASES}
    assert covered == set(CACHED_INDICATORS)


@pytest.mark.parametrize("doc", CASES, ids=lambda d: f"{d['indicator']}-{d.get('period', 'def')}")
def test_cached_verdict_equals_legacy_verdict_at_every_session(doc, store) -> None:
    """BOTH sides run the real `evaluate_condition`: one through a
    MarketView (cached branch), one through LegacyView (the prefix
    recompute the engine used to do). Every session, no sampling — the
    decision the engine acts on must be identical."""
    cond = _cond(doc)
    for day in store.sessions:
        cached = evaluate_condition(MarketView(store, day), cond)
        legacy = evaluate_condition(LegacyView(store, day), cond)
        assert cached == legacy, (
            f"{cond.indicator.value} verdict diverged at {day}: "
            f"cached={cached} legacy={legacy}"
        )


@pytest.mark.parametrize("doc", CASES, ids=lambda d: f"{d['indicator']}-{d.get('period', 'def')}")
def test_cached_pair_is_exactly_the_legacy_pair_at_every_session(doc, store, monkeypatch) -> None:
    """The verdict above is a boolean — two different numbers could agree
    on it by luck on this data. This pins the VALUES with exact float
    equality (`==`, never approx: the cache promises the same number, not
    a near one).

    The expected pair is CAPTURED from the real legacy branch — spy on
    `_series_pair` and record the list `evaluate_condition` actually hands
    it — so nothing here transcribes the production arithmetic and there
    is nothing to drift out of sync with it."""
    cond = _cond(doc)
    import app.engine.conditions as conditions_mod

    captured: list[list[float]] = []
    real_series_pair = conditions_mod._series_pair

    def spy(values: list[float], op, threshold: float) -> bool:  # type: ignore[no-untyped-def]
        captured.append(list(values))
        return real_series_pair(values, op, threshold)

    monkeypatch.setattr(conditions_mod, "_series_pair", spy)

    cache = DailySeriesCache(store)
    for day in store.sessions:
        captured.clear()
        evaluate_condition(LegacyView(store, day), cond)  # the REAL legacy path
        # an empty prefix short-circuits before _series_pair — the cache
        # must return the same nothing
        legacy_pair = captured[-1] if captured else []
        cached = cache.tail_pair(cond, day)
        assert cached == legacy_pair, (
            f"{cond.indicator.value} value diverged at {day}: "
            f"cached={cached} legacy={legacy_pair}"
        )
        assert all(math.isfinite(v) for v in cached)


def test_series_key_ignores_threshold_but_not_period(store) -> None:
    """The F8 condition sweeps move the THRESHOLD — every cell must share
    one series (that is where the sweep's speed-up comes from), while a
    different period must never be served the wrong series."""
    a = _cond({"indicator": "rsi", "period": 14, "operator": "<", "value": 30})
    b = _cond({"indicator": "rsi", "period": 14, "operator": ">", "value": 70})
    c = _cond({"indicator": "rsi", "period": 21, "operator": "<", "value": 30})
    assert series_key(a) == series_key(b)
    assert series_key(a) != series_key(c)

    cache = DailySeriesCache(store)
    cache.tail_pair(a, store.sessions[-1])
    cache.tail_pair(b, store.sessions[-1])
    assert len(cache._series) == 1  # one series served both thresholds
    cache.tail_pair(c, store.sessions[-1])
    assert len(cache._series) == 2


def test_cache_is_dropped_with_the_run(store) -> None:
    """The store outlives runs (chains._STORE_CACHE); the memo must not."""
    view = MarketView(store, store.sessions[-1])
    evaluate_condition(view, _cond(CASES[0]))
    assert store._daily_series is not None
    store.drop_daily_series_cache()
    assert store._daily_series is None


def test_empty_prefix_evaluates_false_like_the_legacy_path(store) -> None:
    """A session before the store's first is an empty prefix: the legacy
    path returned False on `if not closes`; the cache must not fabricate."""
    from datetime import timedelta

    before = store.sessions[0] - timedelta(days=1)
    cache = DailySeriesCache(store)
    assert cache.tail_pair(_cond(CASES[0]), before) == []
    assert evaluate_condition(MarketView(store, before), _cond(CASES[0])) is False


def test_full_memo_falls_back_to_the_legacy_path_not_to_a_crash(store, monkeypatch) -> None:
    """Past _MAX_SERIES the cache stops memoizing and returns None, and
    `evaluate_condition` must recompute from the prefix — the pre-cache
    behavior. The alternative shipped in review: keep computing the FULL
    series per session without storing it, which is ~2× SLOWER than the
    prefix code it replaced (and `entry.conditions` has no schema cap, so
    the ceiling is reachable). A cache that cannot help must hand back."""
    import app.engine.daily_series as ds

    monkeypatch.setattr(ds, "_MAX_SERIES", 1)
    cond_a = _cond({"indicator": "rsi", "period": 14, "operator": "<", "value": 45})
    cond_b = _cond({"indicator": "sma", "period": 20, "operator": ">", "value": 100})
    day = store.sessions[-1]

    cache = DailySeriesCache(store)
    assert cache.tail_pair(cond_a, day) is not None  # first key fits
    assert cache.tail_pair(cond_b, day) is None  # second is over the ceiling
    assert cache.tail_pair(cond_a, day) is not None  # …the stored one still serves

    # and through the real engine path the answer is still the legacy one
    store.drop_daily_series_cache()
    for cond in (cond_a, cond_b):
        assert evaluate_condition(MarketView(store, day), cond) == evaluate_condition(
            LegacyView(store, day), cond
        )
