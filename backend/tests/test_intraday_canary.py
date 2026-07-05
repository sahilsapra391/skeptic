"""Minute-scale lookahead canary — permanent required check (guardrail #2
at the 5-minute clock, mirror of test_lookahead_canary.py). A view at bar T
must never see bar T+1's quotes or underlying — the profitable strategy at
minute scale is exactly "read the next bar", and it must be structurally
impossible, forever. If this goes red, everything stops."""

from datetime import date, datetime

import pytest

from app.engine.market import IntradayView, LookaheadError, build_fixture_slice
from app.engine.types import ContractKey

SESSION = "2025-01-06"
KEY = ContractKey(expiration=date(2025, 1, 6), right="put", strike=100.0)


def _row(bid: float, ask: float) -> dict:
    return {"expiration": "2025-01-06", "right": "put", "strike": 100.0,
            "bid": bid, "ask": ask, "delta": -0.5, "iv": 0.2}


def _slice():
    # the 09:35 quote collapses (the "future profit" a peeker would bank)
    return build_fixture_slice(
        SESSION,
        quotes={
            "09:30": [_row(2.00, 2.10)],
            "09:35": [_row(0.50, 0.60)],
            "09:40": [_row(0.10, 0.20)],
        },
        underlying={"09:30": 100.0, "09:35": 97.0, "09:40": 95.0},
    )


def test_quote_at_serves_only_the_current_bar() -> None:
    view = IntradayView(_slice(), datetime(2025, 1, 6, 9, 30))
    got = view.quote_at(KEY)
    assert got is not None
    quote, source = got
    assert quote.bid == 2.00  # the 09:30 quote — not the collapsed 09:35 one
    assert source == "ivol_5min"
    # there is deliberately NO API to ask for another bar's quote
    assert not hasattr(view, "quote_at_ts")


def test_underlying_beyond_bar_raises() -> None:
    view = IntradayView(_slice(), datetime(2025, 1, 6, 9, 30))
    with pytest.raises(LookaheadError):
        view.underlying_last(datetime(2025, 1, 6, 9, 35))


def test_histories_are_bounded_by_the_bar() -> None:
    slc = _slice()
    early = IntradayView(slc, datetime(2025, 1, 6, 9, 30))
    late = IntradayView(slc, datetime(2025, 1, 6, 9, 40))

    assert early.bars_upto() == [datetime(2025, 1, 6, 9, 30)]
    assert early.underlying_history() == [100.0]
    assert 97.0 not in early.underlying_history()  # the crash stays invisible
    assert 95.0 not in early.underlying_history()

    assert len(late.bars_upto()) == 3
    assert late.underlying_history() == [100.0, 97.0, 95.0]


def test_chain_is_current_bar_only() -> None:
    view = IntradayView(_slice(), datetime(2025, 1, 6, 9, 35))
    chain = view.chain()
    assert chain[KEY].bid == 0.50  # 09:35's quote, not 09:30's or 09:40's
