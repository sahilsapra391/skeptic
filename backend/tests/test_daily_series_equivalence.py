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

import pandas as pd
import pytest

from app.data import indicators as ind
from app.engine.conditions import _series_pair, _tail_values, evaluate_condition
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


def _legacy_pair(closes: list[float], cond: Condition) -> list[float]:
    """`evaluate_condition`'s pre-cache arithmetic, transcribed — the same
    prefix Series → indicator → last-two-positions the engine ran per
    session."""
    s = pd.Series(closes, dtype=float)
    name = cond.indicator.value
    if name == "rsi":
        return _tail_values(ind.rsi(s, cond.period or 14))
    if name == "sma":
        return _tail_values(ind.sma(s, cond.period or 14))
    if name == "ema":
        return _tail_values(ind.ema(s, cond.period or 14))
    if name == "price_vs_sma_pct":
        return _tail_values((s / ind.sma(s, cond.period or 50) - 1.0) * 100.0)
    if name == "price_vs_ema_pct":
        return _tail_values((s / ind.ema(s, cond.period or 20) - 1.0) * 100.0)
    if name == "ema_cross_state":
        params = cond.params or {}
        fast, slow = int(params.get("fast", 9)), int(params.get("slow", 20))
        return _tail_values(ind.ema(s, fast) - ind.ema(s, slow))
    raise AssertionError(f"uncached branch in CASES: {name}")


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
def test_cached_pair_equals_legacy_pair_at_every_session(doc, store) -> None:
    cond = _cond(doc)
    cache = DailySeriesCache(store)
    for i, day in enumerate(store.sessions):
        closes = store._closes[: i + 1]  # exactly MarketView.closes_upto()
        legacy = _legacy_pair(closes, cond)
        cached = cache.tail_pair(cond, day)
        assert cached == legacy, (
            f"{cond.indicator.value} diverged at session {i} ({day}): "
            f"cached={cached} legacy={legacy}"
        )
        # NaN never survives into the pair on either path
        assert all(math.isfinite(v) for v in cached)


@pytest.mark.parametrize("doc", CASES, ids=lambda d: f"{d['indicator']}-{d.get('period', 'def')}")
def test_evaluate_condition_verdict_matches_at_every_session(doc, store) -> None:
    """The pair feeds a boolean — prove the DECISION is identical too, at
    every session, through the real evaluate_condition (cached view) vs
    the legacy arithmetic."""
    cond = _cond(doc)
    for i, day in enumerate(store.sessions):
        view = MarketView(store, day)
        got = evaluate_condition(view, cond)
        closes = store._closes[: i + 1]
        threshold = 0.0 if cond.indicator.value == "ema_cross_state" else cond.value
        want = _series_pair(_legacy_pair(closes, cond), cond.operator, threshold)
        assert got == want, f"{cond.indicator.value} verdict differs at {day}"


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
