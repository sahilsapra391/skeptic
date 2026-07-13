"""In-house signal derivations (forward record) — every statistic against a
hand-computed fixture (repo rule: honesty-layer math never ships untested).

Hand computations are spelled out inline so a reviewer can re-derive every
expected value with a pencil.
"""

from __future__ import annotations

import math
from datetime import date

import pandas as pd
import pytest

from app.data import inhouse_signals as ih
from app.engine.engine import data_provenance
from app.engine.market import build_fixture_store
from app.models.spec import StrategySpec

SESSION = "2026-07-07"


def _chain_rows() -> pd.DataFrame:
    """Three expirations off 2026-07-07: dte 20 (07-27), dte 40 (08-16),
    dte 90 (10-05), spot 100. Values chosen for pencil arithmetic."""
    rows = []

    def add(exp: str, right: str, strike: float, iv: float | None,
            delta: float | None, gamma: float | None = None,
            volume: float | None = None, oi: float | None = None) -> None:
        rows.append({
            "expiration": exp, "right": right, "strike": strike, "iv": iv,
            "delta": delta, "gamma": gamma, "volume": volume,
            "open_interest": oi, "spot": 100.0,
        })

    # dte 20 — ATM 100: call .19 / put .21 (mean .20); 25Δ wings bracket:
    # puts 95 (−.30, .22) & 90 (−.20, .24) → .23 at 25Δ;
    # calls 105 (.30, .19) & 110 (.20, .18) → .185 at 25Δ
    add("2026-07-27", "call", 100.0, 0.19, 0.50, gamma=0.05, volume=60, oi=100)
    add("2026-07-27", "put", 100.0, 0.21, -0.50, gamma=0.04, volume=90, oi=50)
    add("2026-07-27", "put", 95.0, 0.22, -0.30)
    add("2026-07-27", "put", 90.0, 0.24, -0.20)
    add("2026-07-27", "call", 105.0, 0.19, 0.30)
    add("2026-07-27", "call", 110.0, 0.18, 0.20)
    # dte 40 — ATM mean .30; wings identical to dte 20 (so the 30d wing
    # interpolation is exact: variance-linear between equal IVs is flat)
    add("2026-08-16", "call", 100.0, 0.29, 0.50, volume=40, oi=10)
    add("2026-08-16", "put", 100.0, 0.31, -0.50, volume=60, oi=10)
    add("2026-08-16", "put", 95.0, 0.22, -0.30)
    add("2026-08-16", "put", 90.0, 0.24, -0.20)
    add("2026-08-16", "call", 105.0, 0.185, 0.30)
    add("2026-08-16", "call", 110.0, 0.185, 0.20)
    # dte 90 — exact ATM tenor hit: mean .32
    add("2026-10-05", "call", 100.0, 0.31, 0.50)
    add("2026-10-05", "put", 100.0, 0.33, -0.50)
    return pd.DataFrame(rows)


class TestAtmAndTermStructure:
    def test_atm_iv_30d_variance_interpolation(self) -> None:
        # ATM(20d) = mean(.19, .21) = .20 → V = .04·20 = 0.8
        # ATM(40d) = mean(.29, .31) = .30 → V = .09·40 = 3.6
        # V(30) = 0.8 + ((30−20)/(40−20))·(3.6−0.8) = 2.2
        # iv(30) = √(2.2/30) = √0.073333… = 0.2708013…
        row = ih.derive_chain_signal_row(_chain_rows(), SESSION, close=100.0)
        assert row["atm_iv_30d"] == pytest.approx(27.0801, abs=1e-3)

    def test_atm_iv_90d_exact_tenor_short_circuits(self) -> None:
        # dte 90 exists → its ATM mean (.32) is used directly, vol points 32
        row = ih.derive_chain_signal_row(_chain_rows(), SESSION, close=100.0)
        assert row["atm_iv_90d"] == pytest.approx(32.0, abs=1e-6)

    def test_term_slope_is_the_difference(self) -> None:
        row = ih.derive_chain_signal_row(_chain_rows(), SESSION, close=100.0)
        assert row["term_slope_30_90"] == pytest.approx(32.0 - 27.0801, abs=1e-3)

    def test_no_bracket_fails_closed(self) -> None:
        # only the 20d expiry → nothing brackets 30d → None, never
        # extrapolated (and term slope needs both tenors)
        df = _chain_rows()
        df = df[df["expiration"] == "2026-07-27"]
        row = ih.derive_chain_signal_row(df, SESSION, close=100.0)
        assert row["atm_iv_30d"] is None
        assert row["term_slope_30_90"] is None


class TestSkew:
    def test_skew_25d_hand_computed(self) -> None:
        # puts at 25Δ: between (−.20, .24) and (−.30, .22) → midpoint .23
        # on BOTH expiries → variance interp of equal IVs stays .23.
        # calls at 25Δ: dte 20 midpoint of (.30, .19)/(.20, .18) = .185;
        # dte 40 wings are .185 flat → .185 at 30d.
        # skew = (.23 − .185) × 100 = 4.5 vol points
        row = ih.derive_chain_signal_row(_chain_rows(), SESSION, close=100.0)
        assert row["skew_25d"] == pytest.approx(4.5, abs=1e-3)

    def test_unbracketed_wing_fails_closed(self) -> None:
        # drop the put wings (only the −.50 ATM put remains): 25Δ is not
        # bracketed below → skew None
        df = _chain_rows()
        df = df[~((df["right"] == "put") & (df["strike"] < 100.0))]
        row = ih.derive_chain_signal_row(df, SESSION, close=100.0)
        assert row["skew_25d"] is None

    def test_deep_itm_delta_is_pin_noise(self) -> None:
        # a |delta| = 1.0 row must not become a wing bracket
        df = _chain_rows()
        extra = pd.DataFrame([{
            "expiration": "2026-07-27", "right": "put", "strike": 130.0,
            "iv": 0.50, "delta": -1.0, "gamma": 0.0, "volume": 0.0,
            "open_interest": 0.0, "spot": 100.0,
        }])  # zero gamma/volume/OI stay inert in every sum
        row = ih.derive_chain_signal_row(
            pd.concat([df, extra], ignore_index=True), SESSION, close=100.0)
        assert row["skew_25d"] == pytest.approx(4.5, abs=1e-3)


class TestPositioningAndFlow:
    def test_net_gex_hand_computed(self) -> None:
        # calls: .05·100(oi)·100(mult)·100²(spot²)·.01 = 50,000
        #      + .0 for dte-40 rows (no gamma banked there)
        # puts:  .04·50·100·100²·.01 = 20,000 → net = +30,000
        row = ih.derive_chain_signal_row(_chain_rows(), SESSION, close=100.0)
        assert row["net_gex"] == pytest.approx(30_000.0, abs=1e-6)

    def test_net_dex_hand_computed(self) -> None:
        # Σ delta·oi·100·spot over delta+oi rows:
        # calls: .50·100 + .50·10 = 55 ; puts: −.50·50 − .50·10 = −30
        # net = 25 · 100 · 100 = 250,000
        row = ih.derive_chain_signal_row(_chain_rows(), SESSION, close=100.0)
        assert row["net_dex"] == pytest.approx(250_000.0, abs=1e-6)

    def test_one_legged_gex_is_none(self) -> None:
        # no put carries gamma+oi → a calls-only sum would be a lie
        df = _chain_rows()
        df.loc[df["right"] == "put", "gamma"] = None
        row = ih.derive_chain_signal_row(df, SESSION, close=100.0)
        assert row["net_gex"] is None

    def test_put_call_ratio_hand_computed(self) -> None:
        # puts 90+60 = 150 ; calls 60+40 = 100 → 1.5
        row = ih.derive_chain_signal_row(_chain_rows(), SESSION, close=100.0)
        assert row["put_call_ratio"] == pytest.approx(1.5, abs=1e-9)

    def test_max_pain_hand_computed(self) -> None:
        # front expiry strictly after 2026-07-07 with OI = 2026-07-27.
        # OI grid: calls 95:10, 100:20, 105:30 ; puts 95:30, 100:10, 105:5.
        # payout(95)  = puts: 5·10 + 10·5           = 100
        # payout(100) = calls: 5·10 ; puts: 5·5     = 75   ← min
        # payout(105) = calls: 10·10 + 5·20         = 200
        # close 98 → (100 − 98)/98 × 100 = 2.0408…
        rows = []
        for right, strike, oi in (
            ("call", 95.0, 10), ("call", 100.0, 20), ("call", 105.0, 30),
            ("put", 95.0, 30), ("put", 100.0, 10), ("put", 105.0, 5),
        ):
            rows.append({"expiration": "2026-07-27", "right": right,
                         "strike": strike, "iv": 0.2, "delta": None,
                         "gamma": None, "volume": None, "open_interest": oi,
                         "spot": 98.0})
        row = ih.derive_chain_signal_row(pd.DataFrame(rows), SESSION, close=98.0)
        assert row["max_pain_dist_pct"] == pytest.approx(2.0408, abs=1e-3)

    def test_max_pain_needs_a_close(self) -> None:
        row = ih.derive_chain_signal_row(_chain_rows(), SESSION, close=None)
        assert row["max_pain_dist_pct"] is None


class TestHv:
    def test_hv30_hand_computed(self) -> None:
        # 31 closes → 30 log returns alternating +1% / −1% (exactly):
        # mean 0, var = 30·0.0001/29, std = √(0.00010344…) = 0.0101710…
        # annualized ×√252 = 0.161459…
        closes = [100.0]
        for i in range(30):
            closes.append(closes[-1] * math.exp(0.01 if i % 2 == 0 else -0.01))
        daily = pd.DataFrame({
            "date": pd.bdate_range("2026-01-02", periods=31),
            "close": closes,
        })
        out = ih.hv30_frame(daily)
        assert len(out) == 1  # only the last row has a full 30-return window
        assert out["hv_30d"].iloc[0] == pytest.approx(0.16146, abs=1e-4)

    def test_short_history_yields_nothing(self) -> None:
        daily = pd.DataFrame({
            "date": pd.bdate_range("2026-01-02", periods=10),
            "close": [100.0 + i for i in range(10)],
        })
        assert ih.hv30_frame(daily).empty


class TestSpliceForward:
    def test_vendor_wins_history_inhouse_extends_forward(self) -> None:
        vendor = {date(2026, 7, 1): 1.0, date(2026, 7, 2): 2.0}
        inhouse = {date(2026, 7, 2): 99.0, date(2026, 7, 6): 3.0,
                   date(2026, 7, 7): 4.0}
        merged, splice = ih.splice_forward(vendor, inhouse)
        assert merged[date(2026, 7, 2)] == 2.0  # vendor value untouched
        assert merged[date(2026, 7, 6)] == 3.0
        assert splice == date(2026, 7, 6)

    def test_no_forward_values_means_no_splice(self) -> None:
        vendor = {date(2026, 7, 6): 1.0}
        inhouse = {date(2026, 7, 2): 9.0}
        merged, splice = ih.splice_forward(vendor, inhouse)
        assert merged == vendor and splice is None

    def test_empty_vendor_series_is_inhouse_alone(self) -> None:
        inhouse = {date(2026, 7, 6): 1.0}
        merged, splice = ih.splice_forward({}, inhouse)
        assert merged == inhouse and splice == date(2026, 7, 6)

    def test_empty_inhouse_is_a_noop(self) -> None:
        vendor = {date(2026, 7, 6): 1.0}
        merged, splice = ih.splice_forward(vendor, {})
        assert merged == vendor and splice is None


def _spec(conditions: list[dict]) -> StrategySpec:
    return StrategySpec.model_validate({
        "spec_version": 7,
        "meta": {"name": "provenance", "description_raw": "x"},
        "underlying": {"ticker": "SPY"},
        "position": {
            "structure": "short_put",
            "legs": [{"right": "put", "side": "short", "ratio": 1,
                      "strike_selection": {"method": "delta", "value": 0.30}}],
            "expiration_selection": {"target_dte": 30, "min_dte": 20, "max_dte": 40},
        },
        "entry": {"schedule": {"frequency": "weekly"},
                  "conditions": conditions, "max_concurrent_positions": 1},
        "exit": {"time_exit_dte": 7},
        "sizing": {"method": "fixed_contracts", "value": 1},
        "costs": {"commission_per_contract": 0.65,
                  "slippage_half_spread_fraction": 0.5, "slippage_half_spread_fraction_sell": 0.5},
        "backtest": {"start": None, "end": None,
                     "initial_capital": 25000, "seed": 42},
    })


class TestDataProvenance:
    SEAM = date(2026, 7, 3)

    def _store(self):
        store = build_fixture_store(
            "SPY", {}, {"2026-06-30": (100.0, 100.0), "2026-07-06": (100.0, 100.0)})
        store.splices = {"ivx_30d": self.SEAM, "hv_30d": self.SEAM}
        return store

    def test_window_crossing_the_seam_discloses_both_series(self) -> None:
        spec = _spec([{"indicator": "hv_iv_spread_30d", "operator": ">", "value": 5}])
        notes = data_provenance(spec, self._store(),
                                date(2026, 6, 1), date(2026, 7, 6))
        assert [n["series"] for n in notes] == ["hv_30d", "ivx_30d"]
        assert all(n["inhouse_from"] == self.SEAM.isoformat() for n in notes)
        assert all("convention change" in n["note"] for n in notes)

    def test_window_ending_before_the_seam_is_undecorated(self) -> None:
        spec = _spec([{"indicator": "hv_iv_spread_30d", "operator": ">", "value": 5}])
        assert data_provenance(spec, self._store(),
                               date(2026, 6, 1), date(2026, 7, 2)) == []

    def test_unrelated_conditions_are_undecorated(self) -> None:
        spec = _spec([{"indicator": "vix_level", "operator": "<", "value": 20}])
        assert data_provenance(spec, self._store(),
                               date(2026, 6, 1), date(2026, 7, 6)) == []

    def test_spliced_series_carry_the_stale_tail_guard(self) -> None:
        # review finding: the in-house continuations can die exactly like a
        # vendor feed (recorder down, derive failing) — skew/term/ivx/hv
        # get the SAME tail protection as the UW families
        from datetime import timedelta

        from app.engine.engine import SliceCoverageError, check_signal_coverage

        days: list[date] = []
        d = date(2026, 6, 1)
        while len(days) < 12:
            if d.weekday() < 5:
                days.append(d)
            d += timedelta(days=1)
        store = build_fixture_store(
            "SPY", {}, {x.isoformat(): (100.0, 100.0) for x in days},
            skew_25d={x.isoformat(): 4.0 for x in days[:3]})
        spec = _spec([{"indicator": "skew_25d", "operator": ">", "value": 3}])
        # 9 uncovered tail sessions (> grace) → refused, last obs named
        with pytest.raises(SliceCoverageError, match="last observed"):
            check_signal_coverage(spec, store, days[0], days[11])
        # exactly the grace (5 sessions past days[2]) → runs
        check_signal_coverage(spec, store, days[0], days[7])

    def test_splice_label_maps_stay_in_sync(self) -> None:
        # the three registries (chains splice keys → _PROVENANCE_SERIES →
        # _SPLICE_LABELS) are hand-synced strings; this pins the two
        # engine-side maps to each other so a new continuation cannot ship
        # half-labeled (review finding)
        from app.engine.engine import _PROVENANCE_SERIES, _SPLICE_LABELS

        mapped = {k for keys in _PROVENANCE_SERIES.values() for k in keys}
        assert mapped == set(_SPLICE_LABELS)
