"""Computed Black-Scholes greeks (D1a).

Two independent verifications, per the hand-computed-fixture convention:
  1. pinned constants from Hull's textbook example (S=42, K=40, r=10%,
     σ=20%, T=0.5y — book prices 4.76 / 0.81) plus a dividend-yield case;
  2. finite differences of bs_price, an implementation-independent check
     that every greek is the derivative it claims to be.

Unit conventions under test (must match the lake's vendor greeks): theta
per calendar DAY, vega per 1 vol POINT, rho per 1% rate move.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from app.data.greeks import FALLBACK_R, bs_greeks, bs_price, fill_missing_greeks, rates_asof


def _arr(*v: float) -> np.ndarray:
    return np.array(v, dtype=np.float64)


BOTH = np.array([True, False])  # call, put


class TestAnalyticFixture:
    """Pinned values generated once from the closed form and cross-checked
    against Hull's book prices; the implementation must reproduce them."""

    def test_hull_textbook_case(self) -> None:
        # S=42, K=40, r=10%, sigma=20%, T=0.5y (dte 182.5/365), q=0
        px = bs_price(_arr(42, 42), _arr(40, 40), _arr(182.5, 182.5),
                      _arr(0.2, 0.2), BOTH, _arr(0.10, 0.10), 0.0)
        assert px[0] == pytest.approx(4.759422, abs=1e-5)  # book: 4.76
        assert px[1] == pytest.approx(0.808599, abs=1e-5)  # book: 0.81

        g = bs_greeks(_arr(42, 42), _arr(40, 40), _arr(182.5, 182.5),
                      _arr(0.2, 0.2), BOTH, _arr(0.10, 0.10), 0.0)
        assert g["delta"][0] == pytest.approx(0.77913129, abs=1e-6)
        assert g["delta"][1] == pytest.approx(-0.22086871, abs=1e-6)
        assert g["gamma"][0] == pytest.approx(0.04996267, abs=1e-6)
        assert g["theta"][0] == pytest.approx(-0.01249066, abs=1e-6)  # per day
        assert g["theta"][1] == pytest.approx(-0.00206623, abs=1e-6)
        assert g["vega"][0] == pytest.approx(0.08813415, abs=1e-6)  # per vol point
        assert g["rho"][0] == pytest.approx(0.13982046, abs=1e-6)  # per 1% rate
        assert g["rho"][1] == pytest.approx(-0.05042543, abs=1e-6)

    def test_dividend_yield_case(self) -> None:
        # S=100, K=105, dte=60, iv=0.25, r=5%, q=1.3% (the SPY constant)
        px = bs_price(_arr(100, 100), _arr(105, 105), _arr(60, 60),
                      _arr(0.25, 0.25), BOTH, _arr(0.05, 0.05), 0.013)
        assert px[0] == pytest.approx(2.30448361, abs=1e-6)
        assert px[1] == pytest.approx(6.65847730, abs=1e-6)

        g = bs_greeks(_arr(100, 100), _arr(105, 105), _arr(60, 60),
                      _arr(0.25, 0.25), BOTH, _arr(0.05, 0.05), 0.013)
        assert g["delta"][0] == pytest.approx(0.35468419, abs=1e-6)
        assert g["delta"][1] == pytest.approx(-0.64318111, abs=1e-6)
        assert g["gamma"][0] == pytest.approx(0.03666723, abs=1e-6)
        assert g["theta"][0] == pytest.approx(-0.03467292, abs=1e-6)
        assert g["theta"][1] == pytest.approx(-0.02396114, abs=1e-6)
        assert g["vega"][0] == pytest.approx(0.15068725, abs=1e-6)
        assert g["rho"][0] == pytest.approx(0.05451606, abs=1e-6)
        assert g["rho"][1] == pytest.approx(-0.11667384, abs=1e-6)


class TestFiniteDifferences:
    """Each greek must equal the corresponding derivative of bs_price —
    verified numerically, independent of the closed-form derivation."""

    S = _arr(95.0, 95.0)
    K = _arr(100.0, 100.0)
    DTE = _arr(45.0, 45.0)
    IV = _arr(0.3, 0.3)
    R = _arr(0.045, 0.045)
    Q = 0.012
    H = 1e-4

    def _price(self, **over: np.ndarray) -> np.ndarray:
        args = {"spot": self.S, "strike": self.K, "dte_days": self.DTE,
                "iv": self.IV, "r": self.R}
        args.update(over)
        return bs_price(args["spot"], args["strike"], args["dte_days"],
                        args["iv"], BOTH, args["r"], self.Q)

    def test_greeks_match_price_derivatives(self) -> None:
        g = bs_greeks(self.S, self.K, self.DTE, self.IV, BOTH, self.R, self.Q)
        h = self.H
        delta_fd = (self._price(spot=self.S + h) - self._price(spot=self.S - h)) / (2 * h)
        gamma_fd = (
            self._price(spot=self.S + h) - 2 * self._price() + self._price(spot=self.S - h)
        ) / (h * h)
        vega_fd = (self._price(iv=self.IV + h) - self._price(iv=self.IV - h)) / (2 * h) / 100
        rho_fd = (self._price(r=self.R + h) - self._price(r=self.R - h)) / (2 * h) / 100
        theta_fd = (
            self._price(dte_days=self.DTE - 0.05) - self._price(dte_days=self.DTE + 0.05)
        ) / 0.1  # per calendar day

        assert np.max(np.abs(g["delta"] - delta_fd)) < 1e-6
        assert np.max(np.abs(g["gamma"] - gamma_fd)) < 1e-4
        assert np.max(np.abs(g["vega"] - vega_fd)) < 1e-6
        assert np.max(np.abs(g["rho"] - rho_fd)) < 1e-6
        assert np.max(np.abs(g["theta"] - theta_fd)) < 1e-6


def _chain_frame(rows: list[dict[str, object]]) -> pd.DataFrame:
    base: dict[str, object] = {
        "trading_date": "2024-01-02", "expiration": "2024-02-02", "right": "put",
        "strike": 95.0, "bid": 1.0, "ask": 1.2, "last": None, "volume": None,
        "open_interest": None, "iv": 0.2, "delta": None, "gamma": None,
        "theta": None, "vega": None, "rho": None, "greeks_source": None,
        "spot": None, "source": "yahoo",
    }
    return pd.DataFrame([{**base, **r} for r in rows])


class TestFillMissingGreeks:
    SPOT = {date(2024, 1, 2): 100.0}

    def test_fills_only_rows_missing_vendor_delta(self) -> None:
        df = _chain_frame([
            {"delta": -0.30, "gamma": 0.02, "theta": -0.05, "vega": 0.09,
             "rho": -0.04, "greeks_source": "vendor", "source": "dolthub", "spot": 100.0},
            {},  # yahoo-like: greeks all missing, spot joined from dailies
        ])
        out = fill_missing_greeks(df, "SPY", dict(self.SPOT), None)

        # vendor row untouched — vendor and computed values never mix
        assert out.loc[0, "delta"] == -0.30
        assert out.loc[0, "gamma"] == 0.02
        assert out.loc[0, "greeks_source"] == "vendor"

        # missing row filled and tagged
        assert out.loc[1, "greeks_source"] == "computed"
        assert -1.0 < out.loc[1, "delta"] < 0.0  # a put
        for col in ("gamma", "theta", "vega", "rho"):
            assert pd.notna(out.loc[1, col])
        assert out.loc[1, "gamma"] > 0
        assert out.loc[1, "vega"] > 0

    def test_no_iv_no_spot_or_zero_dte_stay_none(self) -> None:
        df = _chain_frame([
            # collector pre-labels Yahoo rows "computed" before anything is
            # computed — an unfillable row must lose the label, not keep it
            {"iv": None, "spot": 100.0, "greeks_source": "computed"},
            {"trading_date": "2024-01-03"},  # date absent from the spot join map
            {"expiration": "2024-01-02", "spot": 100.0},  # 0 DTE → honest None
        ])
        out = fill_missing_greeks(df, "SPY", dict(self.SPOT), None)
        assert out["delta"].isna().all()
        assert out["greeks_source"].isna().all()

    def test_empty_frame_passthrough(self) -> None:
        df = pd.DataFrame()
        assert fill_missing_greeks(df, "SPY", {}, None).empty


class TestRatesAsof:
    def test_point_in_time_pick_and_fallback(self) -> None:
        rates = pd.DataFrame({
            "date": pd.to_datetime(["2024-01-02", "2024-01-05"]),
            "rate_pct": [5.0, 4.0],
        })
        td = pd.Series(pd.to_datetime(["2024-01-01", "2024-01-03", "2024-01-06"]))
        out = rates_asof(td, rates)
        assert out[0] == pytest.approx(FALLBACK_R)  # before first observation
        assert out[1] == pytest.approx(0.05)  # last obs ≤ Jan 3 is Jan 2
        assert out[2] == pytest.approx(0.04)  # last obs ≤ Jan 6 is Jan 5

    def test_missing_series_uses_fallback(self) -> None:
        td = pd.Series(pd.to_datetime(["2024-01-03"]))
        assert rates_asof(td, None)[0] == pytest.approx(FALLBACK_R)
        assert rates_asof(td, pd.DataFrame())[0] == pytest.approx(FALLBACK_R)
