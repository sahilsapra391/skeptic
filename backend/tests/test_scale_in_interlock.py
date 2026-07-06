"""The D5a interlock — the whole thesis of shipping scale-in before D5c.

One story across two runs: a scale-in ladder is a martingale, so until its
dedicated defenses land (D5c) the system refuses to bless ANY ladder — no
matter how good the stats look or how many baskets it cleared. This keeps a
blessable-but-undefended martingale structurally impossible in the interim.

  * ruin_single_session (fixture 2's run): the martingale blows up, and even
    though the sample is thin the interlock is the LEADING refusal reason.
  * scale_in_multi_session (fixture 5): a ladder with ≥15 baskets across two
    volatility regimes — NOT sample-capped — is STILL refused, and with the
    flag off the identical stage numbers grade to a real trust level. So the
    ONLY thing forcing insufficient_evidence is the interlock, not luck of
    the sample.
"""

from __future__ import annotations

from app.engine.runner import run_backtest
from app.honesty.gauntlet import run_gauntlet
from app.honesty.stages import MIN_TRADES, unlock_conditions
from app.honesty.trust import compute_trust
from tests.fixtures.scale_in_market import (
    ruin_single_session,
    scale_in_ladder_spec,
    scale_in_multi_session,
)

INTERLOCK = "scale-in safety checks pending (D5c)"


def test_ruin_basket_refused_and_the_interlock_leads() -> None:
    store, intraday, spec = ruin_single_session()
    result = run_backtest(spec, store, intraday)

    # the martingale blew up and the loss is booked (fixture 2 proves the cent)
    closed = [t for t in result.trades if t.pl is not None]
    assert len(closed) == 1 and closed[0].pl < 0

    report = run_gauntlet(spec, store, result, trials=1, intraday=intraday)
    assert report.trust.label == "insufficient_evidence"
    # the sample IS thin here too — but the interlock is the reason that LEADS,
    # so the refusal reads "defenses pending", not "just not enough trades"
    assert report.trust.reasons[0] == INTERLOCK
    assert any("closed trades" in r for r in report.trust.reasons)


def test_ample_sample_martingale_refused_only_by_the_interlock() -> None:
    store, intraday = scale_in_multi_session(n=20, ruin_every=3)
    spec = scale_in_ladder_spec()
    result = run_backtest(spec, store, intraday)

    # one basket per session — adds never inflate the trade count
    assert result.filled == 20
    closed = [t for t in result.trades if t.pl is not None]
    assert len(closed) == 20

    report = run_gauntlet(spec, store, result, trials=1, intraday=intraday)

    # cleared the bar on the merits: NOT sample-capped, NOT coverage-short
    assert report.regime_sample.trades >= MIN_TRADES
    assert report.regime_sample.capped is False
    assert report.regime_sample.regimes_present >= 2
    assert report.coverage.materially_short is False

    # refused anyway, and the interlock is the sole leading reason
    assert report.trust.label == "insufficient_evidence"
    assert report.trust.level is None
    assert report.trust.reasons[0] == INTERLOCK

    # the decisive control: identical stage numbers with the interlock OFF grade
    # to a real trust level — so the interlock, not the sample, is the cap
    graded = compute_trust(
        report.oos, report.walk_forward, report.monte_carlo, report.sensitivity,
        report.regime_sample, report.dsr, report.coverage, report.concentration,
        scale_in_pending=False,
    )
    assert graded.label != "insufficient_evidence"
    assert graded.level is not None


def test_interlock_refusal_is_not_chased_by_auto_unlock() -> None:
    # a code-pending refusal (defenses, not data) must not enter the D3b
    # auto-unlock scan, or it would re-run and re-refuse forever
    store, intraday = scale_in_multi_session(n=20, ruin_every=3)
    spec = scale_in_ladder_spec()
    result = run_backtest(spec, store, intraday)
    report = run_gauntlet(spec, store, result, trials=1, intraday=intraday)
    assert report.trust.label == "insufficient_evidence"
    assert unlock_conditions(report, spec) is None
