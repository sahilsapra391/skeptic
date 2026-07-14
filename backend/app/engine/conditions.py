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


def _trailing_rank(history: list[float], min_obs: int) -> float | None:
    """Percentile (0-100) of the latest observation within its trailing
    252-observation window — ONE implementation for every *_rank/percentile
    indicator (review finding F1 #3: four inline copies WILL drift). None
    below min_obs trailing observations — unevaluable, never a thin-window
    guess (the D1 floor; each indicator declares its own min_obs), and None
    when any window observation is non-finite: a NaN CURRENT observation
    counts nothing ("v <= nan" is False for every v) and would read as rank
    0.0 — every "rank < X" passes on the poisoned day, a fabricated signal —
    while a mid-window NaN silently deflates every rank (out of the count,
    still in the denominator). Unevaluable beats a fabricated signal."""
    if len(history) < min_obs:
        return None
    window = history[-252:]
    if not all(math.isfinite(v) for v in window):
        return None
    current = window[-1]
    return sum(1 for v in window if v <= current) / len(window) * 100.0


def _trailing_zscore(history: list[float], min_obs: int) -> float | None:
    """Standardized latest observation within its trailing 252-observation
    window: (x − mean) / population σ — the σ-unit sibling of
    _trailing_rank, ONE implementation for the same reason. None below
    min_obs trailing observations (the D1 floor), None on a flat window
    (a dead feed forward-filling one value has no σ to standardize by),
    and None when any observation is non-finite — unevaluable beats a
    fabricated signal."""
    if len(history) < min_obs:
        return None
    window = history[-252:]
    if min(window) == max(window):
        # exactly flat — checked on the values, NOT via var <= 0: float
        # residue in sum(window)/n leaves var > 0 for most flat decimals
        # and the residual z comes out as exactly ±1.0 (review finding,
        # reproduced: [0.229]*144 → 1.0). NaNs make this compare False
        # and fall through to the finite guard below.
        return None
    n = len(window)
    mean = sum(window) / n
    var = sum((v - mean) ** 2 for v in window) / n
    if not (var > 0.0 and math.isfinite(var)):
        return None
    return (window[-1] - mean) / math.sqrt(var)


def _finite(value: float | None) -> float | None:
    """Gate for scalar vendor reads (the *_level/spread/ratio branches):
    a non-finite read becomes None. NaN would fall through _compare as a
    silent False on the scalar operators, and ±inf FABRICATES a threshold
    cross ("IVX above 25" is True at +inf). A poisoned row is unevaluable,
    never a signal — the same refusal _trailing_rank applies window-wide."""
    if value is None or not math.isfinite(value):
        return None
    return value


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
    # non-finite, not just NaN: an inf indicator value surviving into the
    # pair fabricates every GT/crosses_above signal (warmup NaNs and
    # poisoned infs drop the same way — the pair is the last real values)
    vals = [float(v) for v in series.tail(n).tolist() if math.isfinite(float(v))]
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
    # the live print drives evaluation ONLY at off-stamp bars; at a stamp
    # (or a print-less bar) the sampled last is the price — exactly the
    # pre-FX.3 semantics, bit-identical off minute grids
    live = view.close()
    if not view.is_indicator_stamp and live is not None:
        off_stamp_live = True
        px = live
    else:
        off_stamp_live = False
        px = closes[-1]
    if cond.indicator is Indicator.PRICE_VS_VWAP_PCT:
        # _finite, not just <= 0: an inf vwap launders into a FINITE
        # pct of exactly -100.0 ("below VWAP" fabricated); an inf px
        # makes pct +inf — the second gate refuses that side
        vwap = _finite(view.intraday_vwap())
        if vwap is None or vwap <= 0:
            return False
        pct = (px / vwap - 1.0) * 100.0
        if not math.isfinite(pct):
            return False
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
            _live_price_tail(pct_series, px, sma, off_stamp_live),
            cond.operator, cond.value)
    if ind_name is Indicator.PRICE_VS_EMA_PCT:
        ema = ind.ema(s, cond.period or 20)
        pct_series = (s / ema - 1.0) * 100.0
        return _series_pair(
            _live_price_tail(pct_series, px, ema, off_stamp_live),
            cond.operator, cond.value)
    if ind_name is Indicator.EMA_CROSS_STATE:
        params = cond.params or {}
        fast = int(params.get("fast", 9))
        slow = int(params.get("slow", 20))
        diff = ind.ema(s, fast) - ind.ema(s, slow)
        return _series_pair(_tail_values(diff), cond.operator, 0.0)
    return False  # validation keeps daily-only indicators off this path


def _live_price_tail(
    pct_series: pd.Series, px: float, ma: pd.Series, off_stamp_live: bool
) -> list[float]:
    """The evaluation pair for price-vs-MA conditions with the FX.3 live
    price side. At a STAMP bar (or a print-less bar) the pair is exactly
    the pre-FX.3 sampled tail — bit-identical everywhere off minute grids.
    At an off-stamp bar with a real print the pair is (latest SAMPLED pct,
    live pct): prev stays the last fully-sampled value so crosses are
    stamp-anchored — a genuine inter-stamp cross fires, a cross already
    resolved AT the stamp does not re-fire (review finding). An
    unevaluable tail (warmup NaN) keeps the old NaN-dropping behavior."""
    if not off_stamp_live:
        return _tail_values(pct_series)
    last_pct = float(pct_series.iloc[-1])
    ma_last = float(ma.iloc[-1])
    if not (math.isfinite(last_pct) and math.isfinite(ma_last)) or ma_last == 0.0:
        return _tail_values(pct_series)
    return [last_pct, (px / ma_last - 1.0) * 100.0]


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
        # an inf close doesn't just make vol inf — the NEXT return
        # launders into a finite -1.0 and fabricates a vol spike, so the
        # contributing closes are gated, not the output alone
        if any(math.isinf(float(v)) for v in s.tail(22).tolist()):
            return False
        rets = s.pct_change().dropna().tail(20)
        vol = float(rets.std(ddof=1)) * math.sqrt(252) * 100.0
        if not math.isfinite(vol):
            return False
        return _compare(vol, cond.operator, cond.value)
    if ind_name is Indicator.DRAWDOWN_FROM_HIGH_PCT:
        # period bounds the reference high: "2% below its 20-day high" is a
        # rolling 20-session peak, not the all-time high since data start.
        # No period keeps the historical since-inception behavior.
        def _dd(series: pd.Series) -> float:
            window = series.tail(cond.period) if cond.period else series
            hi = float(window.max())
            if not math.isfinite(hi):
                # an inf high launders into a FINITE 100% drawdown lie —
                # unevaluable must surface as NaN, caught below
                return math.nan
            return (1.0 - float(series.iloc[-1]) / hi) * 100.0

        dd_now = _dd(s)
        dd_prev = _dd(s.iloc[:-1]) if len(closes) >= 2 else dd_now
        if not (math.isfinite(dd_now) and math.isfinite(dd_prev)):
            return False
        return _series_pair([dd_prev, dd_now], cond.operator, cond.value)
    if ind_name is Indicator.VIX_LEVEL:
        vix = _finite(view.vix())
        if vix is None:
            return False
        return _compare(vix, cond.operator, cond.value)
    if ind_name is Indicator.IV_PERCENTILE_1Y:
        rank = _trailing_rank(view.atm_iv_history(), min_obs=20)
        if rank is None:
            return False
        return _compare(rank, cond.operator, cond.value)
    if ind_name is Indicator.IVX_RANK_1Y:
        # vendor IVX (30d IV Mean) percentile within the trailing 252
        # observations. Owner amendment 3: below 126 trailing observations
        # the rank is unevaluable that day — False, never a thin-window guess.
        rank = _trailing_rank(view.ivx_30d_history(), min_obs=126)
        if rank is None:
            return False
        return _compare(rank, cond.operator, cond.value)
    if ind_name is Indicator.IVX_ZSCORE_1Y:
        # v8 (parity Tier 3): the SAME 30d IVX series as ivx_rank_1y,
        # standardized instead of ranked — σ units, raw thresholds legal
        # ("IV two sigma rich" → > 2). Same 126-observation floor.
        z = _trailing_zscore(view.ivx_30d_history(), min_obs=126)
        if z is None:
            return False
        return _compare(z, cond.operator, cond.value)
    if ind_name is Indicator.IVX_LEVEL_30D:
        # stored as a decimal; compared in percentage points (vix_level
        # ergonomics: "IVX above 25" → value 25)
        ivx = _finite(view.ivx_30d())
        if ivx is None:
            return False
        return _compare(ivx * 100.0, cond.operator, cond.value)
    if ind_name is Indicator.HV_IV_SPREAD_30D:
        ivx = _finite(view.ivx_30d())
        hv = _finite(view.hv_30d())
        if ivx is None or hv is None:
            return False
        return _compare((ivx - hv) * 100.0, cond.operator, cond.value)
    if ind_name is Indicator.SKEW_25D:
        # F4: 25Δ risk reversal @30d, VOL POINTS ("skew above 5" → value 5).
        # Derived from the previous session's EOD surface at intraday bars.
        skew = _finite(view.skew_25d())
        if skew is None:
            return False
        return _compare(skew, cond.operator, cond.value)
    if ind_name is Indicator.GEX_LEVEL:
        # F1: net dealer gamma, VENDOR UNITS — vocabulary is sign-only
        # ("dealers long gamma" → > 0); the compare itself is unit-free at
        # threshold 0, and the parser refuses raw-unit thresholds
        gex = _finite(view.gex_level())
        if gex is None:
            return False
        return _compare(gex, cond.operator, cond.value)
    if ind_name is Indicator.GEX_RANK_1Y:
        # percentile within the trailing 252 observations; below 126
        # trailing observations the rank is unevaluable that day — False,
        # never a thin-window guess (owner amendment, inherits the D1
        # ivx_rank floor; unlocks as the UW window crosses it)
        rank = _trailing_rank(view.gex_history(), min_obs=126)
        if rank is None:
            return False
        return _compare(rank, cond.operator, cond.value)
    if ind_name is Indicator.DEX_LEVEL:
        # F1: net dealer delta, vendor units — sign vocabulary only
        dex = _finite(view.dex_level())
        if dex is None:
            return False
        return _compare(dex, cond.operator, cond.value)
    if ind_name is Indicator.DEX_RANK_1Y:
        rank = _trailing_rank(view.dex_history(), min_obs=126)
        if rank is None:
            return False
        return _compare(rank, cond.operator, cond.value)
    if ind_name is Indicator.NET_PREMIUM_LEVEL:
        # F2: session net options premium (call − put), VENDOR DOLLARS —
        # sign vocabulary ("bullish flow" → > 0); parser refuses raw sums
        v = _finite(view.net_premium_level())
        if v is None:
            return False
        return _compare(v, cond.operator, cond.value)
    if ind_name is Indicator.NET_PREMIUM_RANK_1Y:
        rank = _trailing_rank(view.net_premium_history(), min_obs=126)
        if rank is None:
            return False
        return _compare(rank, cond.operator, cond.value)
    if ind_name is Indicator.MARKET_TIDE_LEVEL:
        # F2: MARKET-WIDE cumulative tide's session total — one series for
        # all tickers; sign vocabulary ("market risk-on" → > 0)
        v = _finite(view.market_tide_level())
        if v is None:
            return False
        return _compare(v, cond.operator, cond.value)
    if ind_name is Indicator.MARKET_TIDE_RANK_1Y:
        rank = _trailing_rank(view.market_tide_history(), min_obs=126)
        if rank is None:
            return False
        return _compare(rank, cond.operator, cond.value)
    if ind_name is Indicator.NOPE_LEVEL:
        # F2: vendor-computed NOPE at the last session stamp — sign/rank
        # only (owner decision: the vendor's IMPLEMENTATION, not the
        # published concept; sign+rank survive monotone rescaling)
        v = _finite(view.nope_level())
        if v is None:
            return False
        return _compare(v, cond.operator, cond.value)
    if ind_name is Indicator.NOPE_RANK_1Y:
        rank = _trailing_rank(view.nope_history(), min_obs=126)
        if rank is None:
            return False
        return _compare(rank, cond.operator, cond.value)
    if ind_name is Indicator.PUT_CALL_FLOW_RATIO:
        # F2: Σput/Σcall session volume — dimensionless classic, raw
        # thresholds legal ("ratio above 1" → value 1)
        v = _finite(view.put_call_ratio())
        if v is None:
            return False
        return _compare(v, cond.operator, cond.value)
    if ind_name is Indicator.MAX_PAIN_DISTANCE_PCT:
        # F3: (front-expiry max pain − close)/close × 100 — unit-free %,
        # raw thresholds legal; signed (positive = max pain above spot).
        # "within 1% of max pain" = two ANDed conditions (< 1 and > −1)
        v = _finite(view.max_pain_distance_pct())
        if v is None:
            return False
        return _compare(v, cond.operator, cond.value)
    if ind_name is Indicator.TERM_STRUCTURE_SLOPE:
        # F4: ATM IV(90d) − ATM IV(30d), VOL POINTS; "< 0" = inverted
        slope = _finite(view.term_structure_slope())
        if slope is None:
            return False
        return _compare(slope, cond.operator, cond.value)
    raise ValueError(f"unhandled indicator {ind_name}")  # pragma: no cover


def all_conditions_pass(view: MarketViewLike, conds: list[Condition]) -> bool:
    return all(evaluate_condition(view, c) for c in conds)
