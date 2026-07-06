"""Scale-in martingale defenses (D5c) — the checks that LIFT the D5a interlock.

Until D5c a ladder was hard-capped no matter what (the interlock). Now the
interlock is replaced by two real, strategy-specific defenses: a ruin-tail
Monte Carlo on the basket P&L sequence and a deep-rung-dependency check.

  * A martingale-overfit ladder (one lucky deep reversal in a sea of ruinous
    ones) is REFUSED — BOTH defenses fire, and NOT because the sample is thin.
  * A clean ladder that clears the defenses is now BLESSABLE — the whole point
    of shipping the primitive behind the interlock and lifting it here.
  * Sample counting still uses BASKETS, not per-rung fills — adds are not
    trades, so a ladder can't inflate its way past the 15-trade bar.
"""

from __future__ import annotations

import pytest

from app.engine.runner import run_backtest
from app.honesty.gauntlet import run_gauntlet
from app.honesty.stages import MIN_TRADES, unlock_conditions
from tests.fixtures.scale_in_market import (
    martingale_overfit_multi_session,
    ruin_single_session,
    scale_in_ladder_spec,
    scale_in_multi_session,
)


def _report(mk, n=20):
    store, intraday = mk(n)
    spec = scale_in_ladder_spec()
    result = run_backtest(spec, store, intraday)
    report = run_gauntlet(spec, store, result, trials=1, intraday=intraday)
    return spec, result, report


# ───────────────────────── the acceptance: overfit refused by both ─────────
def test_martingale_overfit_refused_by_both_defenses() -> None:
    _spec, result, report = _report(martingale_overfit_multi_session)
    si = report.scale_in
    assert si is not None

    # cleared the sample bar — the refusal is the martingale, NOT thin evidence
    assert report.regime_sample.trades >= MIN_TRADES
    assert report.regime_sample.capped is False
    assert report.regime_sample.regimes_present >= 2

    # BOTH defenses fire (brief acceptance)
    assert si.deep_rung_sign_flip is True
    assert si.ruin_flagged is True
    assert si.caps_trust is True

    assert report.trust.label == "insufficient_evidence"
    assert report.trust.level is None
    # the deep-rung dependency leads; the ruin tail follows
    assert "depends on the deepest rung" in report.trust.reasons[0]
    assert any("ruinous tail" in r for r in report.trust.reasons)


# ─────────────────────── deep-rung dependency, hand-computed ────────────────
def test_deep_rung_dependency_hand_computed() -> None:
    """17 ruin baskets @ −251.10 + 3 lucky @ +1855.90 → realized +1299.00.
    The deepest rung (rung1) marginal is +1785.00 (the lucky wins live there),
    so removing it: 1299.00 − 1785.00 = −486.00 → the positive edge FLIPS."""
    _spec, _result, report = _report(martingale_overfit_multi_session)
    si = report.scale_in
    assert si.realized_total == pytest.approx(1299.00, abs=0.01)
    assert si.total_without_deepest == pytest.approx(-486.00, abs=0.01)
    assert si.deep_rung_sign_flip is True


# ──────────────────────── ruin tail: overfit yes, clean no ──────────────────
def test_ruin_tail_fires_on_overfit_not_on_clean() -> None:
    _s1, _r1, overfit = _report(martingale_overfit_multi_session)
    _s2, _r2, clean = _report(scale_in_multi_session)

    assert overfit.scale_in.ruin_flagged is True
    assert overfit.scale_in.p_ruin is not None and overfit.scale_in.p_ruin >= 0.10

    assert clean.scale_in.ruin_flagged is False
    assert clean.scale_in.p_ruin is not None and clean.scale_in.p_ruin < 0.10


# ─────────────── the interlock is LIFTED: a clean ladder can be blessed ─────
def test_clean_ladder_clears_the_defenses_and_is_blessed() -> None:
    _spec, _result, report = _report(scale_in_multi_session)
    si = report.scale_in
    assert si is not None
    # neither hard cap fires — removing the deepest rung IMPROVES the total,
    # and the ruin tail is contained
    assert si.deep_rung_sign_flip is False
    assert si.ruin_flagged is False
    assert si.caps_trust is False

    # so the ladder is judged like any strategy — and here it grades (no
    # "scale-in safety checks pending" refusal anywhere)
    assert report.trust.label != "insufficient_evidence"
    assert report.trust.level is not None
    assert not any("pending" in r for r in report.trust.reasons)


# ───────────────────────── adds are not trades (baskets) ────────────────────
def test_sample_counts_baskets_not_per_rung_fills() -> None:
    store, intraday, spec = ruin_single_session()
    result = run_backtest(spec, store, intraday)
    report = run_gauntlet(spec, store, result, trials=1, intraday=intraday)

    # ONE basket built from FOUR rung fills — the sample counts the basket
    assert len(result.rung_fills) == 4
    assert report.regime_sample.trades == 1
    # so a lone ladder is still sample-capped — a ladder can't inflate its way
    # to 15 "trades" by adding more rungs
    assert report.trust.label == "insufficient_evidence"
    assert report.regime_sample.capped is True


# ──────────────── a martingale refusal is not a DATA unlock ─────────────────
def test_martingale_refusal_not_chased_by_auto_unlock() -> None:
    spec, _result, report = _report(martingale_overfit_multi_session)
    assert report.trust.label == "insufficient_evidence"
    # the caps are strategy properties, not thin data — the auto-unlock scan
    # must not re-run and re-refuse them forever
    assert unlock_conditions(report, spec) is None
