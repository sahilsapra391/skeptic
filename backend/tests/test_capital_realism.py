"""Buying-power gate + ruin halt (owner decision 2026-07-15) — hand-computed.

Engine-level to-the-cent scenarios live in tests/fixtures/engine/
(fx_insufficient_buying_power_skip, fx_margin_reserve_short_put,
fx_credit_spread_reserve, fx_short_put_ruin_halt). This file covers the
margin arithmetic, the Monte Carlo absorption, trust/coverage/verdict/
payload plumbing, and the ladder unaffordable-rung attribution (owner
amendment: an unfundable deep add reads as UNAFFORDABLE, never as merely
unprofitable).
"""

from __future__ import annotations

import math
from datetime import date

import numpy as np
import pytest

from app.api.payload import _verdict_block, build_run_payload, run_summary
from app.engine import margin
from app.engine.market import build_fixture_slice, build_fixture_store
from app.engine.metrics import compute_metrics
from app.engine.runner import run_backtest
from app.engine.types import ContractKey, RunResult, TradeEvent
from app.honesty.gauntlet import run_gauntlet
from app.honesty.stages import (
    _absorb_at_zero,
    coverage,
    funding_profile,
    ladder_depth_attribution,
    monte_carlo,
    ruin_disclosure,
)
from app.honesty.trust import compute_trust
from app.honesty.verdict import (
    allowed_numbers,
    retail_template_verdict,
    template_verdict,
    validate_numbers,
)
from app.models.spec import Leg, StrategySpec
from tests.fixtures.engine import fx_short_put_ruin_halt
from tests.test_five_min_clock import FixtureIntraday

EXP = date(2025, 1, 17)


def _leg(right: str, side: str) -> Leg:
    return Leg.model_validate({
        "right": right, "side": side, "ratio": 1,
        "strike_selection": {"method": "atm", "value": 0},
    })


def _key(right: str, strike: float) -> ContractKey:
    return ContractKey(expiration=EXP, right=right, strike=strike)


# ────────────────────────────────────────────── margin.py, hand-computed
class TestShortLegRequirement:
    def test_atm_put_is_broad_pct(self) -> None:
        # max(0.20·100 − 0, 0.10·100) = 20.00 per share
        assert margin.short_leg_requirement("put", 100.0, 100.0) == 20.0

    def test_otm_put_subtracts_otm_amount(self) -> None:
        # max(0.20·100 − 10, 0.10·90) = max(10, 9) = 10.00
        assert margin.short_leg_requirement("put", 90.0, 100.0) == 10.0

    def test_deep_otm_put_hits_strike_floor(self) -> None:
        # max(0.20·100 − 50, 0.10·50) = max(−30, 5) = 5.00
        assert margin.short_leg_requirement("put", 50.0, 100.0) == 5.0

    def test_otm_call_floors_on_spot(self) -> None:
        # max(0.20·100 − 10, 0.10·100) = max(10, 10) = 10.00
        assert margin.short_leg_requirement("call", 110.0, 100.0) == 10.0

    def test_cash_secured_mode_reserves_full_strike(self) -> None:
        assert margin.short_leg_requirement("put", 100.0, 100.0,
                                            mode="cash_secured") == 100.0
        assert margin.short_leg_requirement("call", 100.0, 100.0,
                                            mode="cash_secured") == float("inf")


class TestPositionRequirement:
    def test_naked_short_put_scales_by_contracts(self) -> None:
        # 20.00/share × 2 contracts × 100 = $4,000
        req = margin.position_requirement(
            [_leg("put", "short")], [_key("put", 100.0)], 100.0, 2)
        assert req == 4_000.0

    def test_long_only_reserves_nothing(self) -> None:
        req = margin.position_requirement(
            [_leg("call", "long")], [_key("call", 100.0)], 100.0, 3)
        assert req == 0.0

    def test_credit_spread_reserves_width(self) -> None:
        # short K100 / long K95 puts → (100 − 95) × 100 = $500
        req = margin.position_requirement(
            [_leg("put", "short"), _leg("put", "long")],
            [_key("put", 100.0), _key("put", 95.0)], 100.0, 1)
        assert req == 500.0

    def test_debit_spread_reserves_nothing(self) -> None:
        # long ABOVE the short: pair width clamps to 0 (max loss = debit)
        req = margin.position_requirement(
            [_leg("put", "short"), _leg("put", "long")],
            [_key("put", 95.0), _key("put", 100.0)], 100.0, 1)
        assert req == 0.0

    def test_iron_condor_reserves_worse_side_only(self) -> None:
        # put side width 5, call side width 8, one expiration → max = $800
        req = margin.position_requirement(
            [_leg("put", "short"), _leg("put", "long"),
             _leg("call", "short"), _leg("call", "long")],
            [_key("put", 95.0), _key("put", 90.0),
             _key("call", 105.0), _key("call", 113.0)], 100.0, 1)
        assert req == 800.0

    def test_covered_call_reserves_nothing(self) -> None:
        req = margin.position_requirement(
            [_leg("call", "short")], [_key("call", 105.0)], 100.0, 1,
            stock_cover_shares=100)
        assert req == 0.0

    def test_uncovered_portion_of_short_calls_reserves(self) -> None:
        # 2 short calls, 100 shares → 1 covered, 1 naked @ max(20−5,10)=15
        req = margin.position_requirement(
            [Leg.model_validate({"right": "call", "side": "short", "ratio": 2,
                                 "strike_selection": {"method": "atm", "value": 0}})],
            [_key("call", 105.0)], 100.0, 1, stock_cover_shares=100)
        assert req == 1_500.0


# ─────────────────────────────────────────── Monte Carlo absorption at $0
class TestAbsorbAtZero:
    def test_hand_matrix(self) -> None:
        paths = np.array([
            [5.0, 3.0, 8.0, 10.0],   # never crosses → untouched
            [4.0, -1.0, 6.0, 7.0],   # dies at step 1 → frozen at −1
            [-2.0, 5.0, -9.0, 1.0],  # dies at step 0 → frozen at −2
            [3.0, 0.0, 2.0, 9.0],    # exactly $0 counts as dead
        ])
        absorbed, hit = _absorb_at_zero(paths)
        assert absorbed.tolist() == [
            [5.0, 3.0, 8.0, 10.0],
            [4.0, -1.0, -1.0, -1.0],
            [-2.0, -2.0, -2.0, -2.0],
            [3.0, 0.0, 0.0, 0.0],
        ]
        assert hit.tolist() == [False, True, True, True]

    def test_noop_when_no_path_crosses(self) -> None:
        paths = np.array([[5.0, 1.0, 9.0], [2.0, 4.0, 3.0]])
        absorbed, hit = _absorb_at_zero(paths)
        assert absorbed.tolist() == paths.tolist()
        assert hit.tolist() == [False, False]


def _mc_result(pls: list[float], seed: int = 42) -> RunResult:
    r = RunResult(ticker="SPY", effective_start=date(2025, 1, 6),
                  effective_end=date(2025, 3, 1), seed=seed)
    r.trades = [TradeEvent(day=date(2025, 1, 6), action="CLOSE", detail="", pl=p)
                for p in pls]
    return r


class TestMonteCarloAbsorption:
    def test_non_ruined_run_is_field_identical_to_legacy(self) -> None:
        """Absorption is a NO-OP when no resampled path crosses $0 — every
        field must equal the pre-absorption formula recomputed here."""
        pls = [200.0, -100.0, 150.0, -80.0, 300.0, -50.0, 120.0, 90.0]
        initial = 100_000.0
        mc = monte_carlo(_mc_result(pls), initial)
        assert mc.p_ruin == 0.0

        # legacy math, replicated independently (same seed, same RNG order)
        arr = np.array(pls)
        n = len(arr)
        rng = np.random.RandomState(42)
        n_blocks = math.ceil(n / 5)
        starts = rng.randint(0, n, size=(1000, n_blocks))
        offsets = np.arange(5)
        idx = (starts[:, :, None] + offsets[None, None, :]) % n
        sampled = arr[idx.reshape(1000, -1)[:, :n]]
        paths = initial + np.cumsum(sampled, axis=1)
        terminals = paths[:, -1]
        peak = np.maximum.accumulate(np.maximum(paths, 1e-9), axis=1)
        max_dd = (1.0 - paths / peak).max(axis=1)
        assert mc.terminal_p5 == float(np.percentile(terminals, 5))
        assert mc.terminal_p50 == float(np.percentile(terminals, 50))
        assert mc.terminal_p95 == float(np.percentile(terminals, 95))
        assert mc.max_drawdown_p50 == float(np.percentile(max_dd, 50))
        assert mc.max_drawdown_p95 == float(np.percentile(max_dd, 95))
        assert mc.p_loss == float(np.mean(terminals < initial))

    def test_p_ruin_matches_independent_scan(self) -> None:
        pls = [-600.0, -600.0, 5_000.0, -600.0, 100.0]
        initial = 1_000.0
        mc = monte_carlo(_mc_result(pls), initial)

        arr = np.array(pls)
        n = len(arr)
        rng = np.random.RandomState(42)
        n_blocks = math.ceil(n / 5)
        starts = rng.randint(0, n, size=(1000, n_blocks))
        offsets = np.arange(5)
        idx = (starts[:, :, None] + offsets[None, None, :]) % n
        sampled = arr[idx.reshape(1000, -1)[:, :n]]
        hits = 0
        for row in sampled:  # independent per-path scan
            c = initial
            for v in row:
                c += v
                if c <= 0:
                    hits += 1
                    break
        assert mc.p_ruin == pytest.approx(hits / 1000)
        assert mc.p_ruin is not None and mc.p_ruin > 0
        # ruin caps the resampled drawdown at exactly 100%, never above
        assert mc.max_drawdown_p95 is not None and mc.max_drawdown_p95 <= 1.0


# ────────────────────────────────── the ruined fixture, end to end
@pytest.fixture(scope="module")
def ruined() -> tuple[StrategySpec, RunResult, object]:
    spec = StrategySpec.model_validate(fx_short_put_ruin_halt.SPEC)
    store = build_fixture_store(
        "SPY", fx_short_put_ruin_halt.CHAINS, fx_short_put_ruin_halt.UNDERLYING)
    result = run_backtest(spec, store)
    report = run_gauntlet(spec, store, result, trials=1)
    return spec, result, report


class TestRuinPlumbing:
    def test_ruin_disclosure_built(self, ruined) -> None:
        _spec, result, report = ruined
        d = ruin_disclosure(result)
        assert d is not None
        assert d.ruin_date == "2025-01-10"
        assert d.final_equity == pytest.approx(-4_295.65)
        assert d.positions_closed_at_halt == 1
        assert report.ruin == d

    def test_refusal_carries_ruin_reason_first(self, ruined) -> None:
        # 1 trade in one regime → refused; the wipeout is disclosed FIRST
        _spec, _result, report = ruined
        assert report.trust.label == "insufficient_evidence"
        assert "wiped out" in report.trust.reasons[0]
        assert len(report.trust.reasons) >= 2  # ruin + the refusal cause(s)

    def test_ruin_hard_caps_a_gradeable_run_at_level_1(self, ruined) -> None:
        _spec, _result, report = ruined
        sample_ok = report.regime_sample.model_copy(
            update={"capped": False, "cap_reason": None,
                    "trades": 20, "regimes_present": 2})
        cov_ok = report.coverage.model_copy(
            update={"materially_short": False, "reason": None})
        trust = compute_trust(
            report.oos, report.walk_forward, report.monte_carlo,
            report.sensitivity, sample_ok, report.dsr, cov_ok,
            ruin=report.ruin)
        assert trust.level == 1
        assert trust.label == "noise"
        assert "wiped out" in trust.reasons[0]

    def test_coverage_attributed_to_ruin_not_data(self) -> None:
        # 9 chain sessions of the 10 the run LIVED — not materially short,
        # even though the full requested window was 100 sessions
        r = RunResult(ticker="SPY", effective_start=date(2025, 1, 6),
                      effective_end=date(2025, 1, 17), seed=42)
        r.ruined = True
        r.ruin_date = date(2025, 1, 17)
        r.requested_sessions = 100
        r.requested_sessions_to_ruin = 10
        r.sessions_with_chain = 9
        cov = coverage(r)
        assert cov.halted_at_ruin
        assert cov.requested_sessions == 10
        assert cov.coverage_ratio == pytest.approx(0.9)
        assert not cov.materially_short
        assert cov.reason is not None and "ruin halt" in cov.reason

    def test_metrics_drawdown_capped_at_100(self, ruined) -> None:
        _spec, result, _report = ruined
        m = compute_metrics(result)
        assert m["max_drawdown"] == 1.0

    def test_verdicts_grounded_and_name_the_wipeout(self, ruined) -> None:
        _spec, _result, report = ruined
        allowed = allowed_numbers(report)
        for build in (template_verdict, retail_template_verdict):
            v = build(report)
            joined = " ".join([v.headline, *v.evidence, *v.breaks_where, *v.caveats])
            assert validate_numbers(joined, allowed) == [], joined
            # the latest-possible disclosure rides the caveats always
            assert any("latest possible" in c or "even earlier" in c
                       for c in v.caveats)

    def test_graded_ruin_headline_arm(self, ruined) -> None:
        _spec, _result, report = ruined
        sample_ok = report.regime_sample.model_copy(
            update={"capped": False, "cap_reason": None,
                    "trades": 20, "regimes_present": 2})
        cov_ok = report.coverage.model_copy(
            update={"materially_short": False, "reason": None})
        trust = compute_trust(
            report.oos, report.walk_forward, report.monte_carlo,
            report.sensitivity, sample_ok, report.dsr, cov_ok,
            ruin=report.ruin)
        graded = report.model_copy(update={
            "trust": trust, "regime_sample": sample_ok, "coverage": cov_ok})
        allowed = allowed_numbers(graded)
        v = template_verdict(graded)
        assert "wiped out" in v.headline
        assert validate_numbers(v.headline, allowed) == []
        r = retail_template_verdict(graded)
        assert "blew up" in r.headline

    def test_payload_ruin_surfaces(self, ruined) -> None:
        spec, result, report = ruined
        verdict = template_verdict(report)
        retail = retail_template_verdict(report)
        payload = build_run_payload("t1", spec, result, report, verdict, retail)
        assert payload["verdict"]["ruined"] is True
        assert payload["ruin"] == {
            "date": "2025-01-10",
            "finalEquity": pytest.approx(-4_295.65),
            "haltedPositions": 1,
        }
        assert payload["funding"] is not None
        summary = run_summary("t1", payload, "Jul 15")
        assert "wiped out" in summary["meta"]
        # drawdown strip never exceeds 100% on the ruined curve
        assert all(p["v"] <= 100.0 for p in payload["drawdownSeries"])

    def test_graded_block_carries_ruined_flag(self, ruined) -> None:
        _spec, _result, report = ruined
        sample_ok = report.regime_sample.model_copy(
            update={"capped": False, "cap_reason": None,
                    "trades": 20, "regimes_present": 2})
        cov_ok = report.coverage.model_copy(
            update={"materially_short": False, "reason": None})
        trust = compute_trust(
            report.oos, report.walk_forward, report.monte_carlo,
            report.sensitivity, sample_ok, report.dsr, cov_ok,
            ruin=report.ruin)
        graded = report.model_copy(update={
            "trust": trust, "regime_sample": sample_ok, "coverage": cov_ok})
        block = _verdict_block(graded, template_verdict(graded))
        assert block["kind"] == "graded"
        assert block["ruined"] is True


# ─────────────────────────────────────────── funding profile thresholds
class TestFundingProfile:
    def _spec(self) -> StrategySpec:
        return StrategySpec.model_validate(fx_short_put_ruin_halt.SPEC)

    def test_material_when_share_and_count_cross(self) -> None:
        r = _mc_result([])
        r.filled = 5
        r.skip_reasons = {"insufficient_buying_power": 5}
        prof = funding_profile(r, self._spec())
        assert prof.skipped_buying_power == 5
        assert prof.skip_share == pytest.approx(0.5)
        assert prof.material
        assert prof.note is not None and "3,000" in prof.note

    def test_not_material_below_min_count(self) -> None:
        r = _mc_result([])
        r.filled = 2
        r.skip_reasons = {"insufficient_buying_power": 2}
        prof = funding_profile(r, self._spec())
        assert not prof.material
        assert prof.note is None

    def test_rung_skips_counted_once_per_basket(self) -> None:
        r = _mc_result([])
        r.filled = 4
        r.rung_funding_skips = {1: 2, 2: 1}
        prof = funding_profile(r, self._spec())
        assert prof.skipped_buying_power == 3
        assert prof.skip_share == round(3 / 7, 4)  # stored rounded to 4dp
        assert prof.material


# ──────────── ladder: unaffordable deep adds (owner amendment 2026-07-15)
def _ladder_rung(value: float, add: int) -> dict:
    return {"indicator": "sma", "timeframe": "5min", "period": 2,
            "operator": "<=", "value": value, "add_contracts": add}


def test_martingale_deep_add_skips_and_reads_unaffordable() -> None:
    """Rungs +2/+3/+5 at SMA 99.5/99.0/98.5 on $3,000 (owner amendment 1):
      rung0: 2 @ 6.75 = 1,350.00 + 1.30 comm → cash 1,648.70
      rung1: 3 @ 4.75 = 1,425.00 + 1.95 comm → cash   221.75
      rung2: 5 @ 3.25 needs 1,628.25 → SKIP insufficient_buying_power
      PT 10%: blended 5.55, sell 6.25 (+12.6%) → close, P/L
        = 6.25×500 − 3.25 − 1,351.30 − 1,426.95 = +343.50
    The depth table shows rung2 as UNAFFORDABLE (fires 0), never merely
    unprofitable — buying power is reality's cap; max_total_contracts is
    only the user's."""
    exp = "2025-01-07"

    def call_q(bid: float, ask: float) -> dict:
        return {"expiration": exp, "right": "call", "strike": 100.0,
                "bid": bid, "ask": ask}

    slc = build_fixture_slice(
        "2025-01-06",
        quotes={
            "09:30": [call_q(10.0, 11.0)], "09:35": [call_q(10.0, 11.0)],
            "09:40": [call_q(6.0, 7.0)], "09:45": [call_q(6.0, 7.0)],   # rung0
            "09:50": [call_q(4.0, 5.0)], "09:55": [call_q(4.0, 5.0)],   # rung1
            "10:00": [call_q(2.5, 3.5)], "10:05": [call_q(2.5, 3.5)],   # rung2 → skip
            "10:10": [call_q(6.0, 7.0)],                                # PT close
        },
        underlying={"09:30": 100.0, "09:35": 100.0, "09:40": 99.5, "09:45": 99.5,
                    "09:50": 99.0, "09:55": 99.0, "10:00": 98.5, "10:05": 98.5,
                    "10:10": 99.0},
    )
    spec = StrategySpec.model_validate({
        "spec_version": 3,
        "meta": {"name": "unaffordable rung", "description_raw": "ladder"},
        "underlying": {"ticker": "SPY"},
        "position": {
            "structure": "long_call",
            "legs": [{"right": "call", "side": "long", "ratio": 1,
                      "strike_selection": {"method": "atm", "value": 0}}],
            "expiration_selection": {"target_dte": 1, "min_dte": 0, "max_dte": 2},
        },
        "entry": {
            "schedule": {"frequency": "signal_only"}, "conditions": [],
            "max_concurrent_positions": 1,
            "scale_in": {
                "mode": "signal_ladder", "basket": True,
                "rungs": [_ladder_rung(99.5, 2), _ladder_rung(99.0, 3),
                          _ladder_rung(98.5, 5)],
                "rearm": {"indicator": "sma", "timeframe": "5min", "period": 2,
                          "operator": ">", "value": 99.5},
                "max_total_contracts": 10,
            },
        },
        "exit": {"profit_target_pct": 10},
        "sizing": {"method": "fixed_contracts", "value": 1},
        "costs": {"commission_per_contract": 0.65,
                  "slippage_half_spread_fraction": 0.5,
                  "slippage_half_spread_fraction_sell": 0.5,
                  "max_spread_pct": 500},
        "backtest": {"start": None, "end": "2025-01-06",
                     "initial_capital": 3_000, "seed": 42, "clock": "5min"},
    })
    store = build_fixture_store(
        "SPY", {}, {"2025-01-06": (100.0, 100.0), "2025-01-07": (100.0, 100.0)})
    result = run_backtest(spec, store, FixtureIntraday({"2025-01-06": slc}))

    # the deep add hit the gate, named
    skips = [t for t in result.trades if t.action == "SKIP"]
    assert any(t.reason == "insufficient_buying_power" for t in skips)
    # …and it never filled: basket = rung0 + rung1 only
    assert result.rung_funding_skips == {2: 1}
    closes = [t for t in result.trades if t.action == "CLOSE"]
    assert len(closes) == 1 and closes[0].pl == pytest.approx(343.50)
    assert result.equity[-1] == pytest.approx(3_343.50)

    # depth attribution: rung2 reads UNAFFORDABLE, not merely unprofitable
    ladder = ladder_depth_attribution(result, spec)
    assert ladder is not None
    by_idx = {r.rung_index: r for r in ladder.rungs}
    assert by_idx[2].unaffordable_baskets == 1
    assert by_idx[2].fires == 0
    assert by_idx[2].contracts == 0
    assert by_idx[2].marginal_pl == 0.0
    assert not by_idx[2].net_negative
    assert by_idx[0].unaffordable_baskets == 0
    assert by_idx[1].unaffordable_baskets == 0
    # tie-out still holds with the unaffordable row present
    assert sum(r.marginal_pl for r in ladder.rungs) == pytest.approx(
        ladder.realized_total)
