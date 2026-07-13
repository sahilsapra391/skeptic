"""Fill model unit tests — guardrail #1 arithmetic, hand-checked.

bid 2.00 / ask 2.20, slip 0.5:
  mid = 2.10
  BUY  = 2.10 + 0.5×(2.20 − 2.10) = 2.15
  SELL = 2.10 − 0.5×(2.10 − 2.00) = 2.05
slip 1.0 → full adverse quote: BUY = ask, SELL = bid.
"""

import pytest

from app.engine.fills import base_slip, fill_price, quote_problem
from app.engine.types import Quote
from app.models.spec import Costs

Q = Quote(bid=2.00, ask=2.20, delta=-0.30)


def test_buy_toward_ask() -> None:
    assert fill_price(Q, "buy", 0.5) == pytest.approx(2.15)
    assert fill_price(Q, "buy", 1.0) == pytest.approx(2.20)


def test_sell_toward_bid() -> None:
    assert fill_price(Q, "sell", 0.5) == pytest.approx(2.05)
    assert fill_price(Q, "sell", 1.0) == pytest.approx(2.00)


def test_never_at_or_better_than_mid() -> None:
    for slip in (0.1, 0.5, 0.9):
        buy = fill_price(Q, "buy", slip)
        sell = fill_price(Q, "sell", slip)
        assert buy is not None and buy > 2.10
        assert sell is not None and sell < 2.10


def test_quote_problems() -> None:
    assert quote_problem(None, "sell") == "missing_quote"
    assert quote_problem(Quote(bid=None, ask=1.0, delta=None), "buy") == "missing_quote"
    assert quote_problem(Quote(bid=2.0, ask=1.0, delta=None), "buy") == "crossed_market"
    assert quote_problem(Quote(bid=0.0, ask=0.4, delta=None), "sell") == "zero_bid_short"
    assert quote_problem(Quote(bid=0.0, ask=0.4, delta=None), "buy") is None  # per TECH-SPEC §5
    assert quote_problem(Q, "sell") is None


class TestSideAwareEarnedDefaults:
    """D3d-earned defaults (owner 2026-07-13): buys 0.85 / sells 0.90 —
    measured from 233M tape prints, replacing the assumed flat 0.5.
    Hand-computed at bid 2.00 / ask 2.20 (mid 2.10, half-spread 0.10)."""

    def test_defaults_are_the_earned_values(self) -> None:
        c = Costs()
        assert c.slippage_half_spread_fraction == 0.85
        assert c.slippage_half_spread_fraction_sell == 0.90

    def test_base_slip_is_side_aware(self) -> None:
        c = Costs()
        assert base_slip(c, "buy") == 0.85
        assert base_slip(c, "sell") == 0.90

    def test_default_fills_hand_computed(self) -> None:
        q = Quote(bid=2.00, ask=2.20, delta=None)
        c = Costs()
        # BUY: 2.10 + 0.85 × 0.10 = 2.185; SELL: 2.10 − 0.90 × 0.10 = 2.01
        assert fill_price(q, "buy", base_slip(c, "buy")) == pytest.approx(2.185)
        assert fill_price(q, "sell", base_slip(c, "sell")) == pytest.approx(2.01)

    def test_explicit_both_sides_equalize(self) -> None:
        # the parser writes BOTH fields when the user states one number —
        # an equalized spec behaves exactly like the old single-knob model
        c = Costs(slippage_half_spread_fraction=0.5,
                  slippage_half_spread_fraction_sell=0.5)
        q = Quote(bid=2.00, ask=2.20, delta=None)
        assert fill_price(q, "buy", base_slip(c, "buy")) == pytest.approx(2.15)
        assert fill_price(q, "sell", base_slip(c, "sell")) == pytest.approx(2.05)
