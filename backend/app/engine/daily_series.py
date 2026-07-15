"""Per-store memo of daily indicator series — the O(n²) → O(n) fix.

`evaluate_condition`'s daily branch used to rebuild the WHOLE indicator
from the growing prefix at every session: `pd.Series(view.closes_upto())`
then `ind.rsi(s, period)`. That is O(n) work per session, O(n²) per
simulation — and the sensitivity sweep re-runs the simulation ~20×, so a
conditioned full-history run pays it ~20 times over. Measured on a
700-session conditioned run (2026-07-15): the gauntlet was 37× the
engine, `evaluate_condition` was 90% of it, and `indicators.rsi` alone
was 27,509 calls / 3.6s.

The fix is not new math — it is the SAME functions, called once per
distinct indicator signature over the store's full close history, with
each session reading its own position. This is sound only because every
cached indicator is CAUSAL (prefix-aligned): the value at position i
computed over the full history equals the value at i computed over the
prefix [0..i].

  sma  — `rolling(period).mean()`: position i averages [i-period+1 .. i]
  ema  — seeded with the SMA of the first `period` closes, then a strictly
         forward recursion out[i] = f(close[i], out[i-1])
  rsi  — Wilder: seed from the first `period` deltas, forward recursion
  price_vs_{sma,ema}_pct / ema_cross_state — elementwise combinations of
         the above

Both short-prefix guards agree too: a prefix shorter than the warmup
yields NaN at exactly the positions the full series has NaN, so the
evaluated pair is identical either way.

That claim is a LOOKAHEAD claim, not a performance claim: if it were
wrong for any indicator, a run would read a value computed with future
closes — the exact failure guardrail #2 exists to prevent. It is
therefore held down by two tests, not by this docstring:
  * tests/test_daily_series_equivalence.py — cached vs legacy, exact float
    equality, EVERY cached branch × EVERY session of a fixture store.
  * tests/test_lookahead_canary.py — the permanent point-in-time canary,
    run WITH this cache attached as well as without.

Bounded per the OOM directive: one float list per distinct (indicator,
period, params) in the spec and its sweep cells — condition sweeps move
the THRESHOLD, never the period, so the sweep adds no keys. The cache
hangs off the MarketStore and is dropped when the run ends
(`MarketStore.drop_daily_series_cache`), because the store itself
outlives runs in `_STORE_CACHE`.
"""

from __future__ import annotations

import math
from bisect import bisect_right
from datetime import date
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

from app.data import indicators as ind
from app.models.spec import Condition, Indicator

if TYPE_CHECKING:  # pragma: no cover
    from app.engine.market import MarketStore

# The indicator families this cache serves: every one is a pure, causal
# function of the close series (see the module docstring's proof).
#
# What is NOT here, honestly split (review finding 2026-07-15 — the
# boundary is not one principle):
#   * VIX/IVX/GEX/flow LEVELS — per-day accessors, no series to memo.
#   * the *_rank_1y families — still O(n²), but the fix there is NOT a
#     cache: market.py's history accessors rebuild the whole prefix list
#     per call and `_trailing_rank` then discards all but the last 252.
#     Bounding the accessor is O(1) and needs no causality argument.
#   * realized_vol_20d — a fixed 20-window std, but over
#     `pct_change().dropna()`, whose prefix-dependent NaN dropping makes
#     the positional argument genuinely messier. Deferred on merit.
#   * drawdown_from_high_pct with a period — a rolling max, as causal as
#     sma. DEFERRED, not disqualified: it simply wasn't what the profile
#     blamed. The door is open; the proof would be the same shape.
# Everything above is correct today, just not sped up.
CACHED_INDICATORS = frozenset({
    Indicator.RSI,
    Indicator.SMA,
    Indicator.EMA,
    Indicator.PRICE_VS_SMA_PCT,
    Indicator.PRICE_VS_EMA_PCT,
    Indicator.EMA_CROSS_STATE,
})

# A spec's conditions plus its sweep cells cannot realistically approach
# this many distinct signatures (threshold sweeps share a series), but the
# schema puts no ceiling on entry.conditions, so the memo needs one (OOM
# directive). Past it, `tail_pair` returns None and the caller falls back
# to the legacy prefix recompute: an unbounded-but-uncached series would
# be computed over the FULL history at every session — strictly WORSE
# than the O(i) prefix it replaced (review finding 2026-07-15). A cache
# that cannot help must hand back, never quietly cost more.
_MAX_SERIES = 64

_SeriesKey = tuple[str, int, int]


def series_key(cond: Condition) -> _SeriesKey:
    """The cache identity of a condition's SERIES — the threshold
    (`cond.value`) and the operator are deliberately absent: the F8
    condition sweeps move the threshold, so every cell of a threshold
    sweep shares one series.

    The per-branch period defaults are replicated from
    `evaluate_condition` (they differ: 14 for the oscillator family, 50
    for price-vs-SMA, 20 for price-vs-EMA). Replication is safe only
    because it is TESTED, not because it is careful:
    tests/test_daily_series_equivalence.py drives both this path and the
    real legacy branch and fails on any drift between them."""
    name = cond.indicator
    if name is Indicator.PRICE_VS_SMA_PCT:
        return ("price_vs_sma_pct", cond.period or 50, 0)
    if name is Indicator.PRICE_VS_EMA_PCT:
        return ("price_vs_ema_pct", cond.period or 20, 0)
    if name is Indicator.EMA_CROSS_STATE:
        params = cond.params or {}
        return ("ema_cross_state", int(params.get("fast", 9)), int(params.get("slow", 20)))
    return (str(name.value), cond.period or 14, 0)


class DailySeriesCache:
    """Full-history daily indicator series, memoized per store."""

    def __init__(self, store: MarketStore) -> None:
        self._store = store
        # ndarrays, not float lists: the pair boxes 2 values per call, so
        # boxing the whole series would cost ~5× the memory for nothing
        self._series: dict[_SeriesKey, np.ndarray] = {}
        self._closes: pd.Series | None = None

    def _close_series(self) -> pd.Series:
        if self._closes is None:
            self._closes = pd.Series(self._store._closes, dtype=float)
        return self._closes

    def _full(self, key: _SeriesKey) -> np.ndarray | None:
        """The whole series for `key`, or None when the memo is full (the
        caller must then use the legacy prefix path — see _MAX_SERIES)."""
        hit = self._series.get(key)
        if hit is not None:
            return hit
        if len(self._series) >= _MAX_SERIES:
            return None
        name, a, b = key
        s = self._close_series()
        if name == "rsi":
            out = ind.rsi(s, a)
        elif name == "sma":
            out = ind.sma(s, a)
        elif name == "ema":
            out = ind.ema(s, a)
        elif name == "price_vs_sma_pct":
            out = (s / ind.sma(s, a) - 1.0) * 100.0
        elif name == "price_vs_ema_pct":
            out = (s / ind.ema(s, a) - 1.0) * 100.0
        elif name == "ema_cross_state":
            out = ind.ema(s, a) - ind.ema(s, b)
        else:  # pragma: no cover — CACHED_INDICATORS gates the callers
            raise ValueError(f"uncached series {name}")
        values: np.ndarray = out.to_numpy(dtype=float)
        self._series[key] = values
        return values

    def prefix_len(self, as_of: date) -> int:
        """Sessions ≤ as_of — the length of `MarketView.closes_upto()`, so
        position idx-1 is the latest observable value."""
        return bisect_right(self._store.sessions, as_of)

    def tail_pair(self, cond: Condition, as_of: date) -> list[float] | None:
        """The evaluation pair for `_series_pair`, identical to
        `_tail_values(indicator(pd.Series(closes_upto()), period))`: the
        last TWO POSITIONS of the prefix, non-finite ones dropped (never
        "the last two finite values" — a NaN at either position is
        removed, and `_series_pair` then refuses the pair).

        None means "not cached, use the legacy path" — never a value."""
        values = self._full(series_key(cond))
        if values is None:
            return None
        idx = self.prefix_len(as_of)
        if idx <= 0:
            return []
        # float() on np.float64 is exact — the pair is bit-identical to the
        # legacy path's `.tolist()` floats (tests/test_daily_series_equivalence)
        return [float(v) for v in values[max(0, idx - 2):idx] if math.isfinite(v)]
