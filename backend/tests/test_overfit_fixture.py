"""THE go/no-go test (PoC risk R3, BUILD-PLAN M3) — permanent required
check: the deliberately-overfit fixture must ALWAYS be flagged by the
gauntlet. A green gauntlet on this strategy is a failing build, forever
(CLAUDE.md conventions)."""

import json
from pathlib import Path

from app.engine.runner import run_backtest
from app.honesty.gauntlet import run_gauntlet
from app.models.spec import StrategySpec
from tests.fixtures.synthetic_market import synthetic_store

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "overfit_strategy.json").read_text())


def _gauntlet_report():
    store = synthetic_store(FIXTURE["data_seed"])
    spec = StrategySpec.model_validate(FIXTURE["spec"])
    result = run_backtest(spec, store)
    return run_gauntlet(spec, store, result, trials=FIXTURE["trials"])


def test_overfit_strategy_is_flagged() -> None:
    report = _gauntlet_report()

    # it LOOKED good in-sample — that is the whole point of the trap
    assert FIXTURE["in_sample_sharpe"] > 0.5

    # judged on the merits (not hiding behind the sample cap)…
    assert not report.regime_sample.capped

    # …and caught: trust ≤ 2 with OOS degradation flagged (M3 acceptance)
    assert report.trust.level is not None and report.trust.level <= 2, (
        f"gauntlet blessed an overfit strategy: trust={report.trust.level} "
        f"({report.trust.label}) — THIS IS THE WORST CLASS OF BUG"
    )
    assert report.oos.flagged, "OOS degradation was not flagged on tuned-on-noise params"


def test_overfit_verdict_leads_with_the_uncomfortable_part() -> None:
    from app.honesty.verdict import allowed_numbers, template_verdict, validate_numbers

    report = _gauntlet_report()
    verdict = template_verdict(report)
    text = verdict.headline.lower()
    assert any(w in text for w in ("fades", "mined", "cliff", "withheld", "noise")), (
        f"headline does not lead with the uncomfortable part: {verdict.headline!r}"
    )
    # and the template is grounded by construction — prove it
    parts = [verdict.headline, *verdict.evidence, *verdict.breaks_where, *verdict.caveats]
    joined = " ".join(parts)
    assert validate_numbers(joined, allowed_numbers(report)) == []


def test_gauntlet_is_deterministic() -> None:
    a, b = _gauntlet_report(), _gauntlet_report()
    assert a.model_dump() == b.model_dump()
