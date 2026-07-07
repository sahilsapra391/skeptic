"""Entry/exit condition evaluation over strictly trailing windows.

Every indicator reads only what MarketView exposes (≤ as_of); the math
delegates to app.data.indicators, which is fixture-tested.
"""

from __future__ import annotations

import math

import pandas as pd

from app.data import indicators as ind
from app.engine.market import MarketViewLike
from app.models.spec import Condition, Indicator, Operator, Timeframe


def _compare(value: float, op: Operator, threshold: float) -> bool:
    if op in (Operator.LT,):
        return value < threshold
    if op in (Operator.LTE,):
        return value <= threshold
    if op in (Operator.GT,):
        return value > threshold
    if op in (Operator.GTE,):
        return value >= threshold
    # above/below on scalar comparisons behave as >/<
    if op is Operator.ABOVE:
        return value > threshold
    if op is Operator.BELOW:
        return value < threshold
    raise ValueError(f"operator {op} needs series context")


def _series_pair(values: list[float], op: Operator, threshold: float) -> bool:
    """crosses_above/below need yesterday's value too."""
    if len(values) < 2:
        return False
    prev, cur = values[-2], values[-1]
    if op is Operator.CROSSES_ABOVE:
        return prev <= threshold < cur
    if op is Operator.CROSSES_BELOW:
        return prev >= threshold > cur
    return _compare(cur, op, threshold)


def _tail_values(series: pd.Series, n: int = 2) -> list[float]:
    vals = [float(v) for v in series.tail(n).tolist() if not math.isnan(float(v))]
    return vals


# Fixed indicator lookback at the 5-min timeframe: the schema caps period at
# 400 bars; 1,200 bars (~3 weeks of sessions) triple-covers it. A bounded
# window is the standard charting convention AND keeps a full-history run
# O(n) — recomputing Wilder smoothing over years of bars per decision was
# measured at 7.5× the engine's cost. Deterministic: same spec + data =
# same values, always.
INTRADAY_LOOKBACK_BARS = 1_200


def _intraday_condition(view: MarketViewLike, cond: Condition) -> bool:
    """5-minute timeframe (D2c): price-series indicators over the run's
    rolling 5-min lasts (bounded INTRADAY_LOOKBACK_BARS); VWAP is
    session-anchored. Warmup or an empty bar history evaluates False —
    never a thin-window guess.

    FX.3 (owner decision 2026-07-07, entries AND exits — one semantic):
    the PRICE side of price-vs-indicator conditions reads the CURRENT
    bar's real underlying print, so a minute-grid bar can observe a touch
    between 5-min stamps. The indicator SERIES stays stamp-sampled (FX.1
    parity — the live price refines WHEN a touch is observable, never the
    indicator's defined cadence, so minute jitter cannot manufacture
    RSI-type signals that don't exist at the indicator's resolution). On
    a 5-min grid the current print IS the sampled last — bit-identical by
    construction (print-less bars fall back to the sampled last)."""
    closes = view.intraday_closes_upto()[-INTRADAY_LOOKBACK_BARS:]
    if not closes:
        return False
    live = view.close()
    px = live if live is not None else closes[-1]
    if cond.indicator is Indicator.PRICE_VS_VWAP_PCT:
        vwap = view.intraday_vwap()
        if vwap is None or vwap <= 0:
            return False
        pct = (px / vwap - 1.0) * 100.0
        return _compare(pct, cond.operator, cond.value)

    s = pd.Series(closes, dtype=float)
    period = cond.period or 14
    ind_name = cond.indicator
    if ind_name is Indicator.RSI:
        return _series_pair(_tail_values(ind.rsi(s, period)), cond.operator, cond.value)
    if ind_name is Indicator.SMA:
        return _series_pair(_tail_values(ind.sma(s, period)), cond.operator, cond.value)
    if ind_name is Indicator.EMA:
        return _series_pair(_tail_values(ind.ema(s, period)), cond.operator, cond.value)
    if ind_name is Indicator.PRICE_VS_SMA_PCT:
        sma = ind.sma(s, cond.period or 50)
        pct_series = (s / sma - 1.0) * 100.0
        return _series_pair(
            _live_price_tail(pct_series, px, sma), cond.operator, cond.value)
    if ind_name is Indicator.PRICE_VS_EMA_PCT:
        ema = ind.ema(s, cond.period or 20)
        pct_series = (s / ema - 1.0) * 100.0
        return _series_pair(
            _live_price_tail(pct_series, px, ema), cond.operator, cond.value)
    if ind_name is Indicator.EMA_CROSS_STATE:
        params = cond.params or {}
        fast = int(params.get("fast", 9))
        slow = int(params.get("slow", 20))
        diff = ind.ema(s, fast) - ind.ema(s, slow)
        return _series_pair(_tail_values(diff), cond.operator, 0.0)
    return False  # validation keeps daily-only indicators off this path


def _live_price_tail(pct_series: pd.Series, px: float, ma: pd.Series) -> list[float]:
    """The evaluation pair for price-vs-MA conditions with the FX.3 live
    price side: prev stays the last fully-sampled pct (stamp cadence);
    cur becomes the CURRENT print against the latest sampled MA. At a
    stamp bar the print equals the sampled last, so cur is numerically
    unchanged — the 5-min grid is bit-identical. An unevaluable tail
    (warmup NaN) keeps the old NaN-dropping behavior untouched."""
    values = _tail_values(pct_series)
    if not values:
        return values
    ma_last = float(ma.iloc[-1])
    if math.isnan(ma_last) or ma_last == 0.0 or math.isnan(float(pct_series.iloc[-1])):
        return values
    values[-1] = (px / ma_last - 1.0) * 100.0
    return values


def evaluate_condition(view: MarketViewLike, cond: Condition) -> bool:
    if cond.timeframe is Timeframe.FIVE_MIN:
        return _intraday_condition(view, cond)
    closes = view.closes_upto()
    if not closes:
        return False
    s = pd.Series(closes, dtype=float)
    period = cond.period or 14
    ind_name = cond.indicator

    if ind_name is Indicator.RSI:
        return _series_pair(_tail_values(ind.rsi(s, period)), cond.operator, cond.value)
    if ind_name is Indicator.SMA:
        return _series_pair(_tail_values(ind.sma(s, period)), cond.operator, cond.value)
    if ind_name is Indicator.EMA:
        return _series_pair(_tail_values(ind.ema(s, period)), cond.operator, cond.value)
    if ind_name is Indicator.PRICE_VS_SMA_PCT:
        sma = ind.sma(s, cond.period or 50)
        pct = (s / sma - 1.0) * 100.0
        return _series_pair(_tail_values(pct), cond.operator, cond.value)
    if ind_name is Indicator.PRICE_VS_EMA_PCT:
        ema = ind.ema(s, cond.period or 20)
        pct = (s / ema - 1.0) * 100.0
        return _series_pair(_tail_values(pct), cond.operator, cond.value)
    if ind_name is Indicator.EMA_CROSS_STATE:
        params = cond.params or {}
        fast = int(params.get("fast", 9))
        slow = int(params.get("slow", 20))
        diff = ind.ema(s, fast) - ind.ema(s, slow)
        vals = _tail_values(diff)
        # above/below compare fast vs slow (threshold 0); crosses supported
        return _series_pair(vals, cond.operator, 0.0)
    if ind_name is Indicator.REALIZED_VOL_20D:
        if len(closes) < 22:
            return False
        rets = s.pct_change().dropna().tail(20)
        vol = float(rets.std(ddof=1)) * math.sqrt(252) * 100.0
        return _compare(vol, cond.operator, cond.value)
    if ind_name is Indicator.DRAWDOWN_FROM_HIGH_PCT:
        # period bounds the reference high: "2% below its 20-day high" is a
        # rolling 20-session peak, not the all-time high since data start.
        # No period keeps the historical since-inception behavior.
        def _dd(series: pd.Series) -> float:
            window = series.tail(cond.period) if cond.period else series
            return (1.0 - float(series.iloc[-1]) / float(window.max())) * 100.0

        dd_now = _dd(s)
        dd_prev = _dd(s.iloc[:-1]) if len(closes) >= 2 else dd_now
        return _series_pair([dd_prev, dd_now], cond.operator, cond.value)
    if ind_name is Indicator.VIX_LEVEL:
        vix = view.vix()
        if vix is None:
            return False
        return _compare(vix, cond.operator, cond.value)
    if ind_name is Indicator.IV_PERCENTILE_1Y:
        history = view.atm_iv_history()
        if len(history) < 20:
            return False
        window = history[-252:]
        current = window[-1]
        rank = sum(1 for v in window if v <= current) / len(window) * 100.0
        return _compare(rank, cond.operator, cond.value)
    if ind_name is Indicator.IVX_RANK_1Y:
        # vendor IVX (30d IV Mean) percentile within the trailing 252
        # observations. Owner amendment 3: below 126 trailing observations
        # the rank is unevaluable that day — False, never a thin-window guess.
        history = view.ivx_30d_history()
        if len(history) < 126:
            return False
        window = history[-252:]
        current = window[-1]
        rank = sum(1 for v in window if v <= current) / len(window) * 100.0
        return _compare(rank, cond.operator, cond.value)
    if ind_name is Indicator.IVX_LEVEL_30D:
        # stored as a decimal; compared in percentage points (vix_level
        # ergonomics: "IVX above 25" → value 25)
        ivx = view.ivx_30d()
        if ivx is None:
            return False
        return _compare(ivx * 100.0, cond.operator, cond.value)
    if ind_name is Indicator.HV_IV_SPREAD_30D:
        ivx = view.ivx_30d()
        hv = view.hv_30d()
        if ivx is None or hv is None:
            return False
        return _compare((ivx - hv) * 100.0, cond.operator, cond.value)
    raise ValueError(f"unhandled indicator {ind_name}")  # pragma: no cover


def all_conditions_pass(view: MarketViewLike, conds: list[Condition]) -> bool:
    return all(evaluate_condition(view, c) for c in conds)
