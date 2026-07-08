"""Cross-source validation + fill audit (ENGINE-V4 F7) — hand-computed.

Owner decisions 2026-07-08: per-pair agreement rates with audited-share
denominators, NO blended score; REPORTED never scored (thresholds are
earned from accumulated history, the D3d staging); the fill audit is
on-demand and checks deterministically regenerated fills against Alpaca
minute TRADES — a vendor no fill price ever came from. no_trades is
honest absence, never counted against the run.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from app.data.cross_validation import (
    compare_dolthub_alpaca,
    compare_dolthub_uw,
    compare_massive_ivol5m,
    compare_quote_close,
)
from app.data.fill_audit import audit_fills
from app.engine.market import build_fixture_slice, build_fixture_store
from app.engine.runner import run_backtest
from app.honesty.stages import data_confidence
from app.models.spec import StrategySpec
from tests.test_five_min_clock import FixtureIntraday


class TestDolthubVsAlpaca:
    def _frames(self, last_minute: str = "15:55", trade: float = 2.03):
        eod = pd.DataFrame({
            "expiration": ["2025-06-20"], "right": ["put"], "strike": [100.0],
            "bid": [2.00], "ask": [2.10], "delta": [-0.50], "spot": [500.0],
        })
        bars = pd.DataFrame({
            "expiration": ["2025-06-20"], "right": ["put"], "strike": [100.0],
            "minute_ts": [f"2025-06-02T{last_minute}:00-04:00"],
            "close": [trade], "volume": [10],
        })
        spots = pd.Series(
            [500.0],
            index=pd.to_datetime(["2025-06-02 15:55:00"]).tz_localize(
                "America/New_York"))
        return eod, bars, spots

    def test_near_close_trade_inside_band_agrees(self) -> None:
        rec = compare_dolthub_alpaca(*self._frames())
        assert rec == {"joined": 1, "checked": 1, "within_band": 1,
                       "agreement_rate": 1.0, "capture_offset": 0.0}

    def test_stale_print_is_excluded_not_flagged(self) -> None:
        # a 13:00 print is honestly UNCHECKED (kind: stale), never a violation
        rec = compare_dolthub_alpaca(*self._frames(last_minute="13:00"))
        assert rec is not None
        assert rec["checked"] == 0 and rec["within_band"] == 0

    def test_price_outside_band_disagrees(self) -> None:
        # trade 2.30 vs quotes 2.00/2.10: band = ask + max(0.05, 2% mid)
        # = 2.10 + 0.05… ≈ 2.15/2.16 → outside
        rec = compare_dolthub_alpaca(*self._frames(trade=2.30))
        assert rec is not None
        assert rec["checked"] == 1 and rec["within_band"] == 0
        assert rec["agreement_rate"] == 0.0


class TestDolthubVsUw:
    def test_expiry_totals_within_bands(self) -> None:
        chain = pd.DataFrame({
            "expiration": ["2025-06-20", "2025-06-20", "2025-07-18"],
            "volume": [100, 200, 50],
            "open_interest": [1000, 2000, 500],
        })
        voe = pd.DataFrame({
            "expires": ["2025-06-20", "2025-07-18"],
            # 06-20: vol 300 vs 310 (3.3% ≤ 10% ✓), oi 3000 vs 3100
            # (3.3% ≤ 5% ✓); 07-18: oi 500 vs 600 (16.7% > 5% ✗)
            "volume": [310, 50], "oi": [3100, 600],
        })
        rec = compare_dolthub_uw(chain, voe)
        assert rec == {"joined": 2, "checked": 2, "within_band": 1,
                       "agreement_rate": 0.5}

    def test_missing_columns_is_none(self) -> None:
        assert compare_dolthub_uw(pd.DataFrame({"x": [1]}),
                                  pd.DataFrame({"y": [2]})) is None


class TestQuoteClose:
    def test_mid_within_band(self) -> None:
        ref = pd.DataFrame({
            "expiration": ["2025-06-20"], "right": ["put"], "strike": [100.0],
            "bid": [2.00], "ask": [2.10],
        })
        liv = pd.DataFrame({
            "expiration": ["2025-06-20"], "right": ["put"], "strike": [100.0],
            "bid": [2.01], "ask": [2.09],  # mid 2.05 vs 2.05 ✓
        })
        rec = compare_quote_close(ref, liv)
        assert rec == {"joined": 1, "checked": 1, "within_band": 1,
                       "agreement_rate": 1.0}

    def test_disjoint_contracts_join_zero(self) -> None:
        ref = pd.DataFrame({
            "expiration": ["2025-06-20"], "right": ["put"], "strike": [100.0],
            "bid": [2.0], "ask": [2.1]})
        liv = pd.DataFrame({
            "expiration": ["2025-07-18"], "right": ["put"], "strike": [100.0],
            "bid": [2.0], "ask": [2.1]})
        rec = compare_quote_close(ref, liv)
        assert rec is not None and rec["joined"] == 0


class TestMassiveVsIvol5m:
    def test_close_inside_day_nbbo_range(self) -> None:
        massive = pd.DataFrame({"occ_symbol": ["O:QQQ250620P00100000"],
                                "c": [2.05]})
        ivol = pd.DataFrame({
            "occ_symbol": ["O:QQQ250620P00100000"] * 2,
            "bid": [2.00, 1.90], "ask": [2.10, 2.00],
        })
        rec = compare_massive_ivol5m(massive, ivol)
        # day range [1.90, 2.10]; close 2.05 inside
        assert rec == {"joined": 1, "checked": 1, "within_band": 1,
                       "agreement_rate": 1.0}

    def test_close_outside_range_flags(self) -> None:
        massive = pd.DataFrame({"occ_symbol": ["O:QQQ250620P00100000"],
                                "c": [3.00]})
        ivol = pd.DataFrame({"occ_symbol": ["O:QQQ250620P00100000"],
                             "bid": [2.00], "ask": [2.10]})
        rec = compare_massive_ivol5m(massive, ivol)
        assert rec is not None and rec["within_band"] == 0


class TestDataConfidenceStage:
    def _result(self):
        days = [date(2024, 1, 2), date(2024, 1, 3)]
        chains = {d.isoformat(): [{
            "expiration": "2024-01-19", "right": "put", "strike": 100.0,
            "bid": 1.00, "ask": 1.10, "delta": -0.50, "iv": 0.2,
        }] for d in days}
        underlying = {d.isoformat(): (100.0, 100.0) for d in days}
        store = build_fixture_store("SPY", chains, underlying)
        spec = _spec(days[0].isoformat())
        return run_backtest(spec, store), spec

    def test_window_scoped_aggregation(self) -> None:
        result, spec = self._result()
        summaries = {"dolthub_vs_alpaca": {
            "2024-01-02": {"joined": 100, "checked": 40, "within_band": 39,
                           "agreement_rate": 0.975},
            "2024-01-03": {"joined": 120, "checked": 60, "within_band": 60,
                           "agreement_rate": 1.0},
            "2030-01-01": {"joined": 9, "checked": 9, "within_band": 0,
                           "agreement_rate": 0.0},  # outside → excluded
        }}
        dc = data_confidence(result, spec, summaries=summaries)
        assert dc is not None and len(dc.pairs) == 1
        p = dc.pairs[0]
        assert p.audited_sessions == 2
        assert p.checked == 100 and p.within_band == 99
        assert p.agreement_rate == 0.99
        assert p.worst_session == "2024-01-02"
        assert p.worst_session_rate == 0.975
        assert dc.note is not None and "99.0%" in dc.note

    def test_no_overlap_is_none_never_fabricated(self) -> None:
        result, spec = self._result()
        dc = data_confidence(result, spec, summaries={
            "dolthub_vs_alpaca": {"2030-01-01": {
                "joined": 1, "checked": 1, "within_band": 1,
                "agreement_rate": 1.0}}})
        assert dc is None


def _spec(start: str, clock: str = "daily") -> StrategySpec:
    doc = {
        "spec_version": 2,
        "meta": {"name": "f7 fixture", "description_raw": "audit"},
        "underlying": {"ticker": "SPY"},
        "position": {
            "structure": "short_put",
            "legs": [{"right": "put", "side": "short", "ratio": 1,
                      "strike_selection": {"method": "delta", "value": 0.50}}],
            "expiration_selection": {"target_dte": 10, "min_dte": 5,
                                     "max_dte": 20},
        },
        "entry": {"schedule": {"frequency": "daily"}, "conditions": [],
                  "max_concurrent_positions": 1},
        "exit": {"time_exit_dte": 0},
        "sizing": {"method": "fixed_contracts", "value": 2},
        "costs": {"commission_per_contract": 0.65,
                  "slippage_half_spread_fraction": 0.5},
        "backtest": {"start": start, "end": None, "initial_capital": 25000,
                     "seed": 42, "clock": clock},
    }
    if clock == "5min":
        doc["position"]["expiration_selection"] = {
            "target_dte": 1, "min_dte": 0, "max_dte": 2}
    return StrategySpec.model_validate(doc)


class TestFillLog:
    def test_entry_and_close_fills_are_recorded(self) -> None:
        session, expiry = "2025-01-06", "2025-01-06"
        slc = build_fixture_slice(
            session,
            quotes={"09:30": [{"expiration": expiry, "right": "put",
                               "strike": 100.0, "bid": 2.00, "ask": 2.10,
                               "delta": -0.50}],
                    "09:35": [{"expiration": expiry, "right": "put",
                               "strike": 100.0, "bid": 1.30, "ask": 1.40,
                               "delta": -0.40}]},
            underlying={"09:30": 100.0, "09:35": 100.0},
        )
        store = build_fixture_store(
            "SPY", {}, {session: (100.0, 100.0), "2025-01-07": (100.0, 100.0)})
        doc = _spec(session, clock="5min").model_dump(mode="json",
                                                      exclude_none=True)
        doc["exit"] = {"profit_target_pct": 25}
        result = run_backtest(StrategySpec.model_validate(doc), store,
                              FixtureIntraday({session: slc}))
        assert len(result.fill_log) == 2  # sell entry + buy PT close
        sell, buy = result.fill_log
        assert sell["action"] == "sell" and sell["price"] > buy["price"]
        assert sell["strike"] == 100.0 and sell["qty"] == 2


class TestAuditFills:
    def _bars(self, low: float, high: float) -> pd.DataFrame:
        return pd.DataFrame({
            "expiration": ["2025-01-06"], "right": ["put"], "strike": [100.0],
            "minute_ts": ["2025-01-06T09:33:00-05:00"],
            "low": [low], "high": [high],
        })

    def _fill(self, price: float) -> dict:
        return {"pid": 1, "day": "2025-01-06", "action": "sell",
                "expiration": "2025-01-06", "right": "put", "strike": 100.0,
                "qty": 2, "price": price, "source": "ivol_5min"}

    def test_within_traded_range(self) -> None:
        audit = audit_fills([self._fill(2.00)], {1: "09:30"},
                            lambda d: self._bars(1.95, 2.05))
        assert audit["audited"] == 1 and audit["within"] == 1
        assert audit["agreement_rate"] == 1.0

    def test_outside_range_is_an_example(self) -> None:
        audit = audit_fills([self._fill(3.00)], {1: "09:30"},
                            lambda d: self._bars(1.95, 2.05))
        assert audit["outside"] == 1
        assert audit["examples"][0]["fill_price"] == 3.0
        assert audit["examples"][0]["kind"] == "bar_window"

    def test_no_trades_is_honest_absence(self) -> None:
        bars = self._bars(1.95, 2.05)
        bars["strike"] = 95.0  # different contract only
        audit = audit_fills([self._fill(2.00)], {1: "09:30"}, lambda d: bars)
        assert audit["no_trades"] == 1 and audit["audited"] == 0
        assert audit["agreement_rate"] is None

    def test_no_coverage_counted_separately(self) -> None:
        audit = audit_fills([self._fill(2.00)], {}, lambda d: None)
        assert audit["no_coverage"] == 1

    def test_missing_bar_time_degrades_to_session_range(self) -> None:
        audit = audit_fills([self._fill(2.00)], {},
                            lambda d: self._bars(1.95, 2.05))
        assert audit["within"] == 1  # still audited, session-range kind
