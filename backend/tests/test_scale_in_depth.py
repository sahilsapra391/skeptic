"""Ladder depth attribution (D5b) — the crown-jewel output.

Two views of a scale-in run's realized P&L: the per-tier table (baskets
grouped by the MAX rung depth they reached — iVol's P&L-by-ladder-depth) and
the marginal-rung analysis (P&L attributable to the fills added AT each depth
— are the deep adds themselves net negative?). Both MUST sum to the same
realized total. Hand-computed on one basket, tied out on a 20-basket run.
"""

from __future__ import annotations

import pytest

from app.api.payload import _ladder_depth_block, build_run_payload
from app.engine.market import build_fixture_slice, build_fixture_store
from app.engine.runner import run_backtest
from app.honesty.gauntlet import run_gauntlet
from app.honesty.stages import ladder_depth_attribution
from app.honesty.verdict import allowed_numbers, template_verdict, validate_numbers
from tests.fixtures.scale_in_market import scale_in_ladder_spec, scale_in_multi_session
from tests.test_scale_in_engine import FixtureIntraday, _call, _ladder_spec, _rung


def _fixture1_run():
    """The D5a fixture-1 basket: +2/+3/+5 @ 0.675/0.475/0.325 → 10 ct, exit
    0.625 (PT), realized +$172.00."""
    slc = build_fixture_slice(
        "2025-01-06",
        quotes={
            "09:30": [_call(1.00, 1.10)], "09:35": [_call(1.00, 1.10)],
            "09:40": [_call(0.60, 0.70)], "09:45": [_call(0.60, 0.70)],
            "09:50": [_call(0.40, 0.50)], "09:55": [_call(0.40, 0.50)],
            "10:00": [_call(0.25, 0.35)], "10:05": [_call(0.25, 0.35)],
            "10:10": [_call(0.60, 0.70)],
        },
        underlying={"09:30": 100.0, "09:35": 100.0, "09:40": 99.5, "09:45": 99.5,
                    "09:50": 99.0, "09:55": 99.0, "10:00": 98.5, "10:05": 98.5,
                    "10:10": 99.0},
    )
    spec = _ladder_spec(
        [_rung(99.5, 2), _rung(99.0, 3), _rung(98.5, 5)],
        {"profit_target_pct": 40, "stop_loss_pct": 75}, max_total=20)
    store = build_fixture_store(
        "SPY", {}, {"2025-01-06": (100.0, 100.0), "2025-01-07": (100.0, 100.0)})
    return run_backtest(spec, store, FixtureIntraday({"2025-01-06": slc})), spec


# ───────────────────────────── hand-computed marginal on one basket ────────
def test_marginal_attribution_matches_hand_calc() -> None:
    """Exit 0.625; each rung's marginal = (0.625 − fill)·qty·100 − 2·0.65·qty:
      rung0 (2 @ 0.675): (−0.05)·200 − 2.60 = −12.60   ← bought highest
      rung1 (3 @ 0.475): ( 0.15)·300 − 3.90 = +41.10
      rung2 (5 @ 0.325): ( 0.30)·500 − 6.50 = +143.50
    Sum = +172.00 = the basket's realized P&L (tie-out)."""
    result, spec = _fixture1_run()
    ld = ladder_depth_attribution(result, spec)
    assert ld is not None
    assert ld.baskets == 1
    assert ld.realized_total == pytest.approx(172.00, abs=0.005)

    marg = {r.rung_index: r.marginal_pl for r in ld.rungs}
    assert marg[0] == pytest.approx(-12.60, abs=0.005)
    assert marg[1] == pytest.approx(41.10, abs=0.005)
    assert marg[2] == pytest.approx(143.50, abs=0.005)
    assert sum(r.marginal_pl for r in ld.rungs) == pytest.approx(172.00, abs=0.005)

    # one basket, reached rung2 → a single tier at depth 3
    assert len(ld.tiers) == 1
    assert ld.tiers[0].depth == 3 and ld.tiers[0].baskets == 1
    assert ld.tiers[0].total_pl == pytest.approx(172.00, abs=0.005)


# ───────────────────────── per-tier table + tie-out on 20 baskets ──────────
def test_depth_table_ties_out_and_locates_the_loss() -> None:
    store, intraday = scale_in_multi_session(n=20, ruin_every=3)
    spec = scale_in_ladder_spec()
    result = run_backtest(spec, store, intraday)
    report = run_gauntlet(spec, store, result, trials=1, intraday=intraday)
    ld = report.ladder_depth
    assert ld is not None and ld.baskets == 20

    # tie-out: both views sum to the realized total, which equals the trade log
    realized = round(sum(t.pl for t in result.trades if t.pl is not None), 2)
    assert ld.realized_total == pytest.approx(realized, abs=0.01)
    assert sum(t.total_pl for t in ld.tiers) == pytest.approx(realized, abs=0.01)
    assert sum(r.marginal_pl for r in ld.rungs) == pytest.approx(realized, abs=0.01)

    # two tiers: shallow (rung0 only) wins, deep (rung1) loses — the martingale
    tiers = {t.depth: t for t in ld.tiers}
    assert set(tiers) == {1, 2}
    assert tiers[1].total_pl > 0  # shallow entries carry the profit
    assert tiers[2].total_pl < 0  # the deep baskets are the loss
    assert tiers[2].pct_gross_loss == pytest.approx(1.0)  # ALL of the gross loss

    # the deep adds themselves are net negative — the crown-jewel finding
    deep_rung = ld.rungs[-1]
    assert deep_rung.net_negative is True
    assert ld.deepest_net_negative is True


# ─────────────────────────── verdict references depth, grounded ────────────
def test_verdict_mentions_depth_and_stays_grounded() -> None:
    store, intraday = scale_in_multi_session(n=20, ruin_every=3)
    spec = scale_in_ladder_spec()
    result = run_backtest(spec, store, intraday)
    report = run_gauntlet(spec, store, result, trials=1, intraday=intraday)

    verdict = template_verdict(report)
    depth_lines = [c for c in verdict.caveats if "depth" in c.lower()]
    assert depth_lines, "verdict must mention depth attribution when a ladder ran"

    # every number in the whole verdict is grounded in the report (guardrail #4)
    joined = " ".join(
        [verdict.headline, *verdict.evidence, *verdict.breaks_where, *verdict.caveats]
    )
    assert validate_numbers(joined, allowed_numbers(report)) == []
    # and it names the deepest rung's marginal loss (net negative here)
    deep = report.ladder_depth.rungs[-1]
    assert f"{abs(deep.marginal_pl):,.0f}" in depth_lines[0]


# ─────────────────────────────── payload shape / no leakage ────────────────
def test_payload_carries_depth_panel_for_ladders_only() -> None:
    store, intraday = scale_in_multi_session(n=20, ruin_every=3)
    spec = scale_in_ladder_spec()
    result = run_backtest(spec, store, intraday)
    report = run_gauntlet(spec, store, result, trials=1, intraday=intraday)
    verdict = template_verdict(report)

    payload = build_run_payload("run1", spec, result, report, verdict)
    block = payload["ladderDepth"]
    assert block is not None
    assert len(block["tiers"]) == 2 and len(block["rungs"]) == 2
    assert block["deepestNetNegative"] is True
    # the deep tier's bar carries a negative P/L sign (red/green is fine on a
    # DATA panel — never on the verdict, per the color rule)
    deep_tier = next(t for t in block["tiers"] if t["depth"] == 2)
    assert deep_tier["plSign"] == "neg"
    assert block["rungs"][-1]["netNeg"] is True

    # a non-ladder report carries no depth panel — zero leakage
    non_ladder = report.model_copy(update={"ladder_depth": None})
    assert _ladder_depth_block(non_ladder) is None
