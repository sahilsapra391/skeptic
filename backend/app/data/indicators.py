"""Chart indicators — strictly trailing windows, computed server-side so the
frontend never does math beyond shaping (TECH-SPEC §8). The M2 engine's
indicator module grows from here; every function ships with a hand-computed
fixture test (tests/test_indicators.py).

All functions take/return pandas Series aligned to the input index; warmup
positions are NaN (the chart draws nothing there — no fabricated values).
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def sma(close: pd.Series, period: int) -> pd.Series:
    return close.rolling(window=period, min_periods=period).mean()


def ema(close: pd.Series, period: int) -> pd.Series:
    """EMA seeded with the SMA of the first `period` values (classic charting
    convention) — values before the seed are NaN, never extrapolated."""
    if len(close) < period:
        return pd.Series(np.nan, index=close.index)
    seed = close.iloc[:period].mean()
    out = np.full(len(close), np.nan)
    out[period - 1] = seed
    k = 2.0 / (period + 1.0)
    values = close.to_numpy(dtype=float)
    for i in range(period, len(values)):
        out[i] = values[i] * k + out[i - 1] * (1.0 - k)
    return pd.Series(out, index=close.index)


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder's RSI: seed averages = simple mean of the first `period` deltas,
    then Wilder smoothing avg = (prev*(period-1) + current) / period."""
    n = len(close)
    out = np.full(n, np.nan)
    if n <= period:
        return pd.Series(out, index=close.index)
    deltas = np.diff(close.to_numpy(dtype=float))
    gains = np.clip(deltas, 0, None)
    losses = np.clip(-deltas, 0, None)
    avg_gain = gains[:period].mean()
    avg_loss = losses[:period].mean()
    out[period] = 100.0 if avg_loss == 0 else 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)
    for i in range(period + 1, n):
        avg_gain = (avg_gain * (period - 1) + gains[i - 1]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i - 1]) / period
        out[i] = 100.0 if avg_loss == 0 else 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)
    return pd.Series(out, index=close.index)


def vwap(df: pd.DataFrame, session: pd.Series) -> pd.Series:
    """Session-anchored VWAP: cumulative typical-price*volume / cumulative
    volume, reset per `session` label (ET trading date)."""
    typical = (df["high"] + df["low"] + df["close"]) / 3.0
    pv = typical * df["volume"]
    cum_pv = pv.groupby(session).cumsum()
    cum_v = df["volume"].groupby(session).cumsum()
    return (cum_pv / cum_v.replace(0, np.nan)).astype(float)


def bollinger(close: pd.Series, period: int = 20, mult: float = 2.0) -> dict[str, pd.Series]:
    mid = sma(close, period)
    # population std (ddof=0), the charting convention
    sd = close.rolling(window=period, min_periods=period).std(ddof=0)
    return {"mid": mid, "upper": mid + mult * sd, "lower": mid - mult * sd}


def macd(
    close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9
) -> dict[str, pd.Series]:
    line = ema(close, fast) - ema(close, slow)
    sig = ema(line.dropna(), signal).reindex(close.index)
    return {"macd": line, "signal": sig, "hist": line - sig}
