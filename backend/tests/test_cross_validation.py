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
    HV_ABS_TOL,
    HV_REL_TOL,
    MPD_ABS_TOL,
    PCR_REL_TOL,
    compare_dolthub_alpaca,
    compare_dolthub_uw,
    compare_massive_ivol5m,
    compare_quote_close,
    compare_recorder_tape_window,
    compare_signal_values,
    merge_records,
    recorder_tape_window,
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
    # the two vendors format the OCC differently — Massive "O:QQQ...",
    # iVol pads the root ("QQQ   250620P00100000"). The comparator must
    # NORMALIZE before joining (real-lake acceptance 2026-07-08: the raw
    # join found nothing, so every session read 0/0).
    def test_close_inside_day_nbbo_range_across_occ_formats(self) -> None:
        massive = pd.DataFrame({"occ_symbol": ["O:QQQ250620P00100000"],
                                "c": [2.05]})
        ivol = pd.DataFrame({
            "occ_symbol": ["QQQ   250620P00100000"] * 2,  # iVol padding
            "bid": [2.00, 1.90], "ask": [2.10, 2.00],
        })
        rec = compare_massive_ivol5m(massive, ivol)
        # day range [1.90, 2.10]; close 2.05 inside — and the join SUCCEEDS
        assert rec == {"joined": 1, "checked": 1, "within_band": 1,
                       "agreement_rate": 1.0}

    def test_close_outside_range_flags(self) -> None:
        massive = pd.DataFrame({"occ_symbol": ["O:QQQ250620P00100000"],
                                "c": [3.00]})
        ivol = pd.DataFrame({"occ_symbol": ["QQQ   250620P00100000"],
                             "bid": [2.00], "ask": [2.10]})
        rec = compare_massive_ivol5m(massive, ivol)
        assert rec is not None and rec["joined"] == 1 and rec["within_band"] == 0


class TestSignalValues:
    """The forward-record continuation comparator (hand-computed)."""

    def test_band_mode_uses_the_larger_tolerance(self) -> None:
        # HV: |0.155 − 0.150| = 0.005 ≤ max(0.01, 0.05·0.150 = 0.0075) → in
        rec = compare_signal_values(
            [(0.155, 0.150, "band", HV_ABS_TOL, HV_REL_TOL)])
        assert rec == {"joined": 1, "checked": 1, "within_band": 1,
                       "agreement_rate": 1.0}

    def test_band_violation_counts_against(self) -> None:
        # PCR: |1.50 − 1.00| = 0.5 > 0.25·1.00 → out
        rec = compare_signal_values([(1.50, 1.00, "band", 0.0, PCR_REL_TOL)])
        assert rec is not None
        assert rec["within_band"] == 0 and rec["checked"] == 1

    def test_sign_mode_agrees_on_sign_only(self) -> None:
        # GEX −4.7e9 vs +8.9e5 disagree; DEX 4.8e10 vs 8.6e7 agree —
        # the real 2026-07-02 overlap shape
        rec = compare_signal_values([
            (-4.7e9, 8.9e5, "sign", 0.0, 0.0),
            (4.8e10, 8.6e7, "sign", 0.0, 0.0),
        ])
        assert rec == {"joined": 2, "checked": 2, "within_band": 1,
                       "agreement_rate": 0.5}

    def test_sign_of_zero_is_joined_but_unchecked(self) -> None:
        rec = compare_signal_values([(0.0, 5.0, "sign", 0.0, 0.0)])
        assert rec == {"joined": 1, "checked": 0, "within_band": 0,
                       "agreement_rate": None}

    def test_missing_sides_never_fabricate_a_violation(self) -> None:
        # one None side and one NaN side → nothing joined → None
        assert compare_signal_values([
            (None, 1.0, "band", 0.1, 0.0),
            (float("nan"), 1.0, "band", 0.1, 0.0),
        ]) is None

    def test_max_pain_band_is_absolute(self) -> None:
        # the identical-formula field: |−0.239 − (−0.239)| = 0 ≤ 0.5 → in
        rec = compare_signal_values(
            [(-0.239, -0.239, "band", MPD_ABS_TOL, 0.0)])
        assert rec is not None and rec["within_band"] == 1


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
                  "slippage_half_spread_fraction": 0.5, "slippage_half_spread_fraction_sell": 0.5},
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
        # review #4: each fill carries ITS OWN bar time
        assert sell["bar_time"] == "09:30"
        assert buy["bar_time"] == "09:35"


class TestAuditFills:
    def _bars(self, low: float, high: float) -> pd.DataFrame:
        return pd.DataFrame({
            "expiration": ["2025-01-06"], "right": ["put"], "strike": [100.0],
            "minute_ts": ["2025-01-06T09:33:00-05:00"],
            "low": [low], "high": [high],
        })

    def _fill(self, price: float, bar_time: str | None = "09:30",
              source: str = "ivol_5min") -> dict:
        out = {"pid": 1, "day": "2025-01-06", "action": "sell",
               "expiration": "2025-01-06", "right": "put", "strike": 100.0,
               "qty": 2, "price": price, "source": source}
        if bar_time:
            out["bar_time"] = bar_time
        return out

    def test_within_traded_range(self) -> None:
        audit = audit_fills([self._fill(2.00)],
                            lambda d: self._bars(1.95, 2.05))
        assert audit["audited"] == 1 and audit["within"] == 1
        assert audit["agreement_rate"] == 1.0

    def test_outside_range_is_an_example(self) -> None:
        audit = audit_fills([self._fill(3.00)],
                            lambda d: self._bars(1.95, 2.05))
        assert audit["outside"] == 1
        assert audit["examples"][0]["fill_price"] == 3.0
        assert audit["examples"][0]["kind"] == "bar_window"

    def test_no_trades_is_honest_absence(self) -> None:
        bars = self._bars(1.95, 2.05)
        bars["strike"] = 95.0  # different contract only
        audit = audit_fills([self._fill(2.00)], lambda d: bars)
        assert audit["no_trades"] == 1 and audit["audited"] == 0
        assert audit["agreement_rate"] is None

    def test_no_coverage_counted_separately(self) -> None:
        audit = audit_fills([self._fill(2.00)], lambda d: None)
        assert audit["no_coverage"] == 1

    def test_missing_bar_time_degrades_to_session_range(self) -> None:
        # review #15: the kind is observable on OUTSIDE examples — a
        # time-less fill outside the day range must say session_range
        audit = audit_fills([self._fill(3.00, bar_time=None)],
                            lambda d: self._bars(1.95, 2.05))
        assert audit["outside"] == 1
        assert audit["examples"][0]["kind"] == "session_range"

    def test_modeled_fills_are_never_self_audited(self) -> None:
        # review BLOCKER #2: alpaca_modeled prices were built FROM these
        # prints — self-confirmation is not independent verification
        audit = audit_fills([self._fill(2.00, source="alpaca_modeled")],
                            lambda d: self._bars(1.95, 2.05))
        assert audit["self_source"] == 1 and audit["audited"] == 0

    def test_close_audits_around_its_own_bar(self) -> None:
        # review MAJOR #4: a 14:10 close must be checked near 14:10 —
        # trades exist near the open at very different prices
        bars = pd.DataFrame({
            "expiration": ["2025-01-06"] * 2, "right": ["put"] * 2,
            "strike": [100.0] * 2,
            "minute_ts": ["2025-01-06T09:33:00-05:00",
                          "2025-01-06T14:08:00-05:00"],
            "low": [1.95, 0.28], "high": [2.05, 0.32],
        })
        close_fill = self._fill(0.30, bar_time="14:10")
        close_fill["action"] = "buy"
        audit = audit_fills([close_fill], lambda d: bars)
        assert audit["within"] == 1  # honest against ITS bar, not the open


class TestRecorderVsUwTape:
    """Hand-computed: recorder displayed quotes vs tape prints in one
    snap window. Contract A quotes 2.00/2.10 (mid 2.05, tol 0.05 → band
    [1.95, 2.15]); C quotes 5.00/5.50 (mid 5.25, tol 0.105 → band
    [4.895, 5.605]); B has no bid — joined, never checked."""

    def _snap(self, right_a: str = "call") -> pd.DataFrame:
        return pd.DataFrame({
            "expiration": ["2026-07-18"] * 3,
            "right": [right_a, "put", "call"],
            "strike": [100.0, 100.0, 105.0],
            "bid": [2.00, 0.00, 5.00],
            "ask": [2.10, 0.05, 5.50],
        })

    def _trades(self) -> pd.DataFrame:
        return pd.DataFrame({
            "expiry": ["2026-07-18"] * 5,
            "option_type": ["call", "call", "call", "put", "call"],
            "strike": [100.0, 100.0, 105.0, 100.0, 999.0],
            "price": [2.05, 1.80, 5.70, 0.03, 1.00],
        })

    def test_hand_computed_counts_and_directions(self) -> None:
        rec = compare_recorder_tape_window(self._snap(), self._trades())
        assert rec is not None
        # the 999 strike is unlisted — joins zero (honest absence)
        assert rec["joined"] == 4
        # the no-bid put print is joined but never checked
        assert rec["checked"] == 3
        # 2.05 within [1.95, 2.15]; 1.80 below; 5.70 beyond
        assert rec["within_band"] == 1
        assert rec["below_bid"] == 1
        assert rec["beyond_ask"] == 1
        assert rec["agreement_rate"] == round(1 / 3, 4)

    def test_right_normalizes_across_conventions(self) -> None:
        # the recorder writes "call"/"put"; an OCC-lettered source ("C")
        # must join the tape's "call" all the same
        rec = compare_recorder_tape_window(self._snap(right_a="C"),
                                           self._trades())
        assert rec is not None and rec["joined"] == 4

    def test_duplicate_snap_row_never_double_counts(self) -> None:
        snap = pd.concat([self._snap(), self._snap().iloc[[0]]],
                         ignore_index=True)
        rec = compare_recorder_tape_window(snap, self._trades())
        assert rec is not None and rec["joined"] == 4 and rec["checked"] == 3

    def test_unrecognized_shape_is_none(self) -> None:
        assert compare_recorder_tape_window(
            self._snap().drop(columns=["ask"]), self._trades()) is None
        assert compare_recorder_tape_window(
            self._snap(), self._trades().drop(columns=["price"])) is None
        assert compare_recorder_tape_window(
            self._snap(), self._trades().iloc[0:0]) is None


class TestRecorderTapeWindow:
    """Hand-computed: the measured 15-min shift and the 60 s half-open
    window. Prints at 13:44:59, 13:45:00, 13:45:30, 13:45:59.9, 13:46:00
    UTC against a snap stamped 14:00:00 → window [13:45:00, 13:46:00)."""

    def _ts(self) -> pd.Series:
        return pd.Series(pd.to_datetime([
            "2026-07-08 13:44:59.000+00:00", "2026-07-08 13:45:00.000+00:00",
            "2026-07-08 13:45:30.000+00:00", "2026-07-08 13:45:59.900+00:00",
            "2026-07-08 13:46:00.000+00:00",
        ], utc=True))

    def test_shift_direction_and_boundaries(self) -> None:
        lo, hi, end = recorder_tape_window(self._ts(), "2026-07-08 14:00:00")
        # 15 min BEHIND the stamp, start inclusive, end exclusive
        assert (lo, hi) == (1, 4)
        assert end == pd.Timestamp("2026-07-08 13:46:00", tz="UTC")

    def test_stalled_feed_window_collapses_to_empty(self) -> None:
        ts = self._ts()
        _, _, end = recorder_tape_window(ts, "2026-07-08 14:00:00")
        # the next snap repeats the stamp (feed stall): clamped to the
        # previous end, the window is empty — the same prints are never
        # judged twice
        lo2, hi2, _ = recorder_tape_window(ts, "2026-07-08 14:00:00", end)
        assert lo2 >= hi2

    def test_partial_overlap_clamps_to_new_coverage_only(self) -> None:
        ts = self._ts()
        _, _, end = recorder_tape_window(ts, "2026-07-08 14:00:00")
        # 30 s later stamp: only [13:46:00, 13:46:30) is new — the print
        # at 13:46:00 is picked up exactly once
        lo2, hi2, _ = recorder_tape_window(ts, "2026-07-08 14:00:30", end)
        assert (lo2, hi2) == (4, 5)


class TestMergeRecords:
    def test_hand_computed_fold(self) -> None:
        parts = [
            {"joined": 4, "checked": 3, "within_band": 1,
             "below_bid": 1, "beyond_ask": 1},
            {"joined": 2, "checked": 2, "within_band": 2,
             "below_bid": 0, "beyond_ask": 0},
        ]
        row = merge_records(parts, "below_bid", "beyond_ask",
                            tape_trades=10, snaps=2)
        assert row["joined"] == 6 and row["checked"] == 5
        assert row["within_band"] == 3
        assert row["agreement_rate"] == round(3 / 5, 4)
        assert row["below_bid"] == 1 and row["beyond_ask"] == 1
        assert row["tape_trades"] == 10 and row["snaps"] == 2

    def test_empty_fold_is_zero_coverage_not_a_rate(self) -> None:
        row = merge_records([], "below_bid", "beyond_ask", snaps=0)
        assert row["joined"] == 0 and row["checked"] == 0
        assert row["agreement_rate"] is None  # never a fabricated 100%


class TestCrossedQuoteGuard:
    def test_crossed_quote_is_joined_never_checked(self) -> None:
        # bid 5.00 / ask 1.00 is not an honest market: without the guard
        # a 3.00 print lands in below_bid AND beyond_ask
        snap = pd.DataFrame({
            "expiration": ["2026-07-18"], "right": ["call"],
            "strike": [100.0], "bid": [5.00], "ask": [1.00],
        })
        trades = pd.DataFrame({
            "expiry": ["2026-07-18"], "option_type": ["call"],
            "strike": [100.0], "price": [3.00],
        })
        rec = compare_recorder_tape_window(snap, trades)
        assert rec is not None
        assert rec["joined"] == 1 and rec["checked"] == 0
        assert rec["below_bid"] == 0 and rec["beyond_ask"] == 0
