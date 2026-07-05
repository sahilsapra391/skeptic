"""Computed-vs-vendor greek parity against the LIVE lake (D1a).

Samples DoltHub vendor rows from a pinned SPY session and asserts our
Black-Scholes computation lands within tolerance of the vendor's numbers —
median-based, because vendors compute at their own spot/iv timestamps.

Deliberately NOT a committed fixture: checking real chain rows into git
would violate the repo's data rails ("never commit data to git"). The test
auto-skips when R2 credentials are absent (CI), and runs on the owner's
machine where collector/.env exists. Marked `lake`.

Tolerances were calibrated with the 2026-07-05 aggregate-diff probe
(r=5.3%, q=1.3%: median |Δdelta| 0.006, |Δvega| 0.007, |Δtheta| 0.006).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.config import load_local_env
from app.data import r2
from app.data.greeks import DIVIDEND_YIELD, bs_greeks, rates_asof

pytestmark = pytest.mark.lake

PINNED_KEY = "options/source=dolthub/ticker=SPY/date=2024-06-03/chain.parquet"
SAMPLE = 20


def test_computed_greeks_match_dolthub_vendor() -> None:
    load_local_env()
    if not r2.r2_configured():
        pytest.skip("no R2 credentials — lake parity runs on the owner's machine")

    s3 = r2.r2_client()
    df = r2.get_parquet(s3, PINNED_KEY)
    if df is None or df.empty:
        pytest.skip(f"pinned chain object missing from the lake: {PINNED_KEY}")

    df = df.dropna(subset=["iv", "delta", "gamma", "theta", "vega", "strike",
                           "spot", "expiration", "trading_date"])
    df = df[(df["iv"] > 0.01) & (df["iv"] < 3)]
    dte = (pd.to_datetime(df["expiration"]) - pd.to_datetime(df["trading_date"])).dt.days
    df = df[(dte >= 3) & (dte <= 90) & df["delta"].abs().between(0.05, 0.95)]
    assert len(df) >= SAMPLE, "pinned session has too few quality vendor rows"

    # deterministic sample: first N in contract order
    df = df.sort_values(["expiration", "right", "strike"]).head(SAMPLE)
    dte = (pd.to_datetime(df["expiration"]) - pd.to_datetime(df["trading_date"])).dt.days

    rates = r2.get_parquet(s3, "reference/rates_dgs3mo.parquet")
    r = rates_asof(pd.to_datetime(df["trading_date"]), rates)

    g = bs_greeks(
        df["spot"].to_numpy(dtype=np.float64),
        df["strike"].to_numpy(dtype=np.float64),
        dte.to_numpy(dtype=np.float64),
        df["iv"].to_numpy(dtype=np.float64),
        (df["right"] == "call").to_numpy(dtype=np.bool_),
        r,
        DIVIDEND_YIELD["SPY"],
    )

    def median_diff(name: str) -> float:
        return float(np.median(np.abs(g[name] - df[name].to_numpy(dtype=np.float64))))

    assert median_diff("delta") <= 0.03
    assert median_diff("gamma") <= 0.005
    assert median_diff("theta") <= 0.03  # per day
    assert median_diff("vega") <= 0.05  # per vol point
