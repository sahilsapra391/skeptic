"""Indicator tests against hand-computed fixtures (repo convention: the math
in comments is the spec; the code must match it)."""

import numpy as np
import pandas as pd
import pytest

from app.data import indicators as ind


def s(values: list[float]) -> pd.Series:
    return pd.Series(values, dtype=float)


def test_sma_hand_computed() -> None:
    # SMA(2) of [1, 2, 3]:
    #   idx0: warmup -> NaN
    #   idx1: (1+2)/2 = 1.5
    #   idx2: (2+3)/2 = 2.5
    out = ind.sma(s([1, 2, 3]), 2)
    assert np.isnan(out.iloc[0])
    assert out.iloc[1] == pytest.approx(1.5)
    assert out.iloc[2] == pytest.approx(2.5)


def test_ema_hand_computed() -> None:
    # EMA(2) of [1, 2, 3], k = 2/(2+1) = 2/3, seeded with SMA(2):
    #   idx1 (seed): (1+2)/2 = 1.5
    #   idx2: 3*(2/3) + 1.5*(1/3) = 2 + 0.5 = 2.5
    out = ind.ema(s([1, 2, 3]), 2)
    assert np.isnan(out.iloc[0])
    assert out.iloc[1] == pytest.approx(1.5)
    assert out.iloc[2] == pytest.approx(2.5)


def test_rsi_hand_computed_wilder() -> None:
    # RSI(2) of [1, 2, 3, 2, 3]; deltas = +1, +1, -1, +1
    #   seed (idx2): avg_gain = (1+1)/2 = 1, avg_loss = 0        -> RSI 100
    #   idx3: avg_gain = (1*1+0)/2 = 0.5, avg_loss = (0*1+1)/2 = 0.5
    #         RS = 1                                             -> RSI 50
    #   idx4: avg_gain = (0.5*1+1)/2 = 0.75, avg_loss = (0.5*1+0)/2 = 0.25
    #         RS = 3 -> 100 - 100/4                              -> RSI 75
    out = ind.rsi(s([1, 2, 3, 2, 3]), 2)
    assert np.isnan(out.iloc[0]) and np.isnan(out.iloc[1])
    assert out.iloc[2] == pytest.approx(100.0)
    assert out.iloc[3] == pytest.approx(50.0)
    assert out.iloc[4] == pytest.approx(75.0)


def test_vwap_hand_computed_with_session_reset() -> None:
    # bars: typical price = (H+L+C)/3
    #   bar0: (12+8+10)/3 = 10, vol 100 -> cum 10*100/100        = 10
    #   bar1: (16+10+13)/3 = 13, vol 300
    #         -> (1000 + 3900) / 400                              = 12.25
    #   bar2 (NEW session): (22+18+20)/3 = 20, vol 50 -> resets   = 20
    df = pd.DataFrame(
        {
            "high": [12, 16, 22],
            "low": [8, 10, 18],
            "close": [10, 13, 20],
            "volume": [100, 300, 50],
        }
    )
    session = pd.Series(["d1", "d1", "d2"])
    out = ind.vwap(df, session)
    assert out.iloc[0] == pytest.approx(10.0)
    assert out.iloc[1] == pytest.approx(12.25)
    assert out.iloc[2] == pytest.approx(20.0)


def test_bollinger_hand_computed() -> None:
    # BB(2, mult=2) of [1, 3]: mid = 2, population std = 1
    #   upper = 2 + 2*1 = 4; lower = 2 - 2*1 = 0
    out = ind.bollinger(s([1, 3]), 2, 2.0)
    assert out["mid"].iloc[1] == pytest.approx(2.0)
    assert out["upper"].iloc[1] == pytest.approx(4.0)
    assert out["lower"].iloc[1] == pytest.approx(0.0)


def test_macd_shape_and_warmup() -> None:
    # structural: macd = ema(fast) - ema(slow); hist = macd - signal where
    # both defined; all NaN inside the slow warmup window
    close = s(list(np.linspace(100, 120, 60)))
    out = ind.macd(close, 12, 26, 9)
    assert np.isnan(out["macd"].iloc[24])
    assert not np.isnan(out["macd"].iloc[25])
    valid = out["hist"].dropna().index
    assert len(valid) > 0
    for i in valid[:3]:
        assert out["hist"].loc[i] == pytest.approx(
            out["macd"].loc[i] - out["signal"].loc[i]
        )


def test_monotonic_rise_rsi_is_100() -> None:
    out = ind.rsi(s(list(range(1, 30))), 14)
    assert out.iloc[-1] == pytest.approx(100.0)
