"""M2 acceptance: the engine must match every hand-computed fixture to the
cent. The math lives in the fixture files' docstrings — if these numbers
and the engine disagree, the fixture math wins until proven wrong by hand."""

from datetime import date

import pytest

from app.engine.market import build_fixture_store
from app.engine.runner import run_backtest
from app.models.spec import StrategySpec
from tests.fixtures.engine import (
    fx_covered_call_called_away,
    fx_credit_spread_stop,
    fx_iron_condor_target,
    fx_short_put_assigned,
    fx_short_put_otm,
    fx_skip_illiquid,
    fx_skip_zero_bid,
)

FIXTURES = [
    fx_short_put_otm,
    fx_short_put_assigned,
    fx_credit_spread_stop,
    fx_iron_condor_target,
    fx_covered_call_called_away,
    fx_skip_zero_bid,
    fx_skip_illiquid,
]


def _run(module):
    spec = StrategySpec.model_validate(module.SPEC)
    store = build_fixture_store("SPY", module.CHAINS, module.UNDERLYING)
    return run_backtest(spec, store)


@pytest.mark.parametrize("module", FIXTURES, ids=[m.__name__.split(".")[-1] for m in FIXTURES])
def test_fixture_to_the_cent(module) -> None:
    result = _run(module)
    expect = module.EXPECT

    assert result.equity, "engine produced no equity curve"
    assert result.equity[-1] == pytest.approx(expect["final_equity"], abs=0.005), (
        f"final equity {result.equity[-1]} != hand-computed {expect['final_equity']}"
    )

    closed = [t for t in result.trades if t.pl is not None]
    assert len(closed) == expect["closed_trades"]
    if expect.get("trade_pl") is not None and closed:
        assert closed[-1].pl == pytest.approx(expect["trade_pl"], abs=0.005)

    actions = [t.action for t in result.trades]
    for required in expect["actions"]:
        assert required in actions, f"missing {required} in {actions}"

    if "close_reason" in expect:
        close_events = [t for t in result.trades if t.action == "CLOSE"]
        assert close_events and close_events[-1].reason == expect["close_reason"]

    if "skip_reasons" in expect:
        skips = [t.reason for t in result.trades if t.action == "SKIP"]
        for reason in expect["skip_reasons"]:
            assert reason in skips, f"expected skip {reason}, got {skips}"

    if "equity_on" in expect:
        by_date = dict(zip(result.dates, result.equity, strict=True))
        for ds, value in expect["equity_on"].items():
            assert by_date[date.fromisoformat(ds)] == pytest.approx(value, abs=0.005)


def test_determinism_same_spec_same_data_same_output() -> None:
    module = fx_credit_spread_stop
    a, b = _run(module), _run(module)
    assert a.equity == b.equity
    assert [(t.day, t.action, t.pl) for t in a.trades] == [
        (t.day, t.action, t.pl) for t in b.trades
    ]
