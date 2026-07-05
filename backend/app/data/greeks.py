"""Black-Scholes greeks for chain rows that lack vendor greeks.

DATA-PIPELINE §4 promised Yahoo rows their greeks "computed via Black-Scholes
at ingest"; until D1a that never happened — Yahoo sessions carried delta=None
into the engine. This module fills the gap at LOAD time (backend ingest), so
the whole lake benefits retroactively, and tags every filled row
`greeks_source="computed"` (guardrail #6: sources are never blurred).

Unit conventions match the lake's vendor greeks (verified against DoltHub
vendor rows with an aggregate-diff probe, 2026-07-05 — see PR):
  theta per calendar DAY, vega per 1 vol POINT (1%), rho per 1% rate move,
  T in calendar days / 365.

Inputs:
  r — FRED DGS3MO series banked at reference/rates_dgs3mo.parquet by the
      collector (point-in-time: last observation ≤ trading date). When the
      series is absent the flat FALLBACK_R applies, logged once per load.
  q — static per-ticker trailing dividend yields, a documented approximation
      (the iVolatility yield endpoint is tariff-empty; see BUILD-LOG).
"""

from __future__ import annotations

import logging
import math

import numpy as np
import pandas as pd
from numpy.typing import NDArray

log = logging.getLogger("skeptic.greeks")

FALLBACK_R = 0.04  # flat risk-free fallback when the FRED series is absent

# Trailing dividend yields, approximate by design (no yield series in the
# lake). Revisit if a real series ever lands.
DIVIDEND_YIELD = {"SPY": 0.013, "QQQ": 0.006, "IWM": 0.012}

_SQRT2 = math.sqrt(2.0)
_INV_SQRT_2PI = 1.0 / math.sqrt(2.0 * math.pi)

FloatArray = NDArray[np.float64]


def _ncdf(x: FloatArray) -> FloatArray:
    erf = np.vectorize(math.erf, otypes=[np.float64])
    return np.asarray(0.5 * (1.0 + erf(x / _SQRT2)), dtype=np.float64)


def _npdf(x: FloatArray) -> FloatArray:
    return np.asarray(_INV_SQRT_2PI * np.exp(-0.5 * x * x), dtype=np.float64)


def bs_price(
    spot: FloatArray,
    strike: FloatArray,
    dte_days: FloatArray,
    iv: FloatArray,
    is_call: NDArray[np.bool_],
    r: FloatArray,
    q: float,
) -> FloatArray:
    """European Black-Scholes price with continuous dividend yield q.
    Exposed so tests can cross-check the greeks by finite differences."""
    t = np.maximum(dte_days, 1e-9) / 365.0
    st = iv * np.sqrt(t)
    d1 = (np.log(spot / strike) + (r - q + 0.5 * iv * iv) * t) / st
    d2 = d1 - st
    df_q = np.exp(-q * t)
    df_r = np.exp(-r * t)
    call = spot * df_q * _ncdf(d1) - strike * df_r * _ncdf(d2)
    put = strike * df_r * _ncdf(-d2) - spot * df_q * _ncdf(-d1)
    return np.asarray(np.where(is_call, call, put), dtype=np.float64)


def bs_greeks(
    spot: FloatArray,
    strike: FloatArray,
    dte_days: FloatArray,
    iv: FloatArray,
    is_call: NDArray[np.bool_],
    r: FloatArray,
    q: float,
) -> dict[str, FloatArray]:
    """delta, gamma, theta (per day), vega (per vol point), rho (per 1% rate)."""
    t = np.maximum(dte_days, 1e-9) / 365.0
    st = iv * np.sqrt(t)
    d1 = (np.log(spot / strike) + (r - q + 0.5 * iv * iv) * t) / st
    d2 = d1 - st
    df_q = np.exp(-q * t)
    df_r = np.exp(-r * t)
    nd1, nd2 = _ncdf(d1), _ncdf(d2)
    pdf1 = _npdf(d1)

    delta = np.where(is_call, df_q * nd1, df_q * (nd1 - 1.0))
    gamma = df_q * pdf1 / (spot * st)
    vega = spot * df_q * pdf1 * np.sqrt(t) / 100.0
    theta_call = -spot * df_q * pdf1 * iv / (2.0 * np.sqrt(t)) - (
        r * strike * df_r * nd2 - q * spot * df_q * nd1
    )
    theta_put = -spot * df_q * pdf1 * iv / (2.0 * np.sqrt(t)) + (
        r * strike * df_r * _ncdf(-d2) - q * spot * df_q * _ncdf(-d1)
    )
    theta = np.where(is_call, theta_call, theta_put) / 365.0
    rho = np.where(is_call, strike * t * df_r * nd2, -strike * t * df_r * _ncdf(-d2)) / 100.0

    return {
        "delta": np.asarray(delta, dtype=np.float64),
        "gamma": np.asarray(gamma, dtype=np.float64),
        "theta": np.asarray(theta, dtype=np.float64),
        "vega": np.asarray(vega, dtype=np.float64),
        "rho": np.asarray(rho, dtype=np.float64),
    }


def rates_asof(trading_dates: pd.Series, rates: pd.DataFrame | None) -> FloatArray:
    """Per-row risk-free rate (decimal): last DGS3MO observation ≤ trading
    date. Point-in-time by construction — never a future observation."""
    n = len(trading_dates)
    if rates is None or rates.empty or "date" not in rates or "rate_pct" not in rates:
        log.warning("no rates series in the lake — using flat fallback r=%.2f%%",
                    FALLBACK_R * 100)
        return np.full(n, FALLBACK_R, dtype=np.float64)
    r = rates.dropna(subset=["rate_pct"]).sort_values("date")
    r_dates = pd.to_datetime(r["date"]).values
    r_vals = (pd.to_numeric(r["rate_pct"], errors="coerce") / 100.0).to_numpy(dtype=np.float64)
    td = pd.to_datetime(trading_dates).values
    idx = np.searchsorted(r_dates, td, side="right") - 1
    out = np.where(idx >= 0, r_vals[np.clip(idx, 0, None)], FALLBACK_R)
    return np.asarray(out, dtype=np.float64)


def fill_missing_greeks(
    df: pd.DataFrame,
    ticker: str,
    spot_by_date: dict[object, float],
    rates: pd.DataFrame | None,
) -> pd.DataFrame:
    """Compute greeks for rows whose vendor delta is missing (the Yahoo case).

    Rules, all honest:
      - only rows with delta null AND usable iv/spot/dte are filled; partial
        vendor rows are never mixed with computed values;
      - filled rows are tagged greeks_source="computed";
      - 0-DTE rows at the close are left None (degenerate greeks are worse
        than an honest gap).
    """
    if df.empty or "iv" not in df.columns or "delta" not in df.columns:
        return df
    df = df.copy()
    if "greeks_source" not in df.columns:
        df["greeks_source"] = None
    for col in ("gamma", "theta", "vega", "rho"):
        if col not in df.columns:
            df[col] = np.nan
    # the Yahoo collector pre-labels rows greeks_source="computed" as a
    # promise (DATA-PIPELINE §4); rows that carry no delta have no greeks
    # yet, whatever the label claims — normalize, then tag what WE compute
    df.loc[df["delta"].isna(), "greeks_source"] = None

    td = pd.to_datetime(df["trading_date"])
    dte = (pd.to_datetime(df["expiration"]) - td).dt.days
    spot = pd.to_numeric(df["spot"], errors="coerce") if "spot" in df.columns else pd.Series(
        np.nan, index=df.index
    )
    spot = spot.fillna(td.dt.date.map(spot_by_date))
    iv = pd.to_numeric(df["iv"], errors="coerce")

    need = (
        df["delta"].isna()
        & iv.notna()
        & (iv > 0)
        & spot.notna()
        & (spot > 0)
        & (dte > 0)
        & df["right"].isin(["call", "put"])
    )
    if not bool(need.any()):
        return df

    r = rates_asof(td[need], rates)
    g = bs_greeks(
        spot[need].to_numpy(dtype=np.float64),
        pd.to_numeric(df.loc[need, "strike"], errors="coerce").to_numpy(dtype=np.float64),
        dte[need].to_numpy(dtype=np.float64),
        iv[need].to_numpy(dtype=np.float64),
        (df.loc[need, "right"] == "call").to_numpy(dtype=np.bool_),
        r,
        DIVIDEND_YIELD.get(ticker, 0.0),
    )
    for col in ("delta", "gamma", "theta", "vega", "rho"):
        df.loc[need, col] = g[col]
    df.loc[need, "greeks_source"] = "computed"
    log.info("%s: computed Black-Scholes greeks for %d rows lacking vendor greeks",
             ticker, int(need.sum()))
    return df
