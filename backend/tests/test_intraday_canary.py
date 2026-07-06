"""Minute-scale lookahead canary — permanent required check (guardrail #2
at the 5-minute clock, mirror of test_lookahead_canary.py). A view at bar T
must never see bar T+1's quotes or underlying — the profitable strategy at
minute scale is exactly "read the next bar", and it must be structurally
impossible, forever. If this goes red, everything stops."""

from datetime import date, datetime

import pytest

from app.engine.market import (
    IntradayView,
    LookaheadError,
    SessionSlice,
    build_fixture_slice,
    build_fixture_store,
)
from app.engine.runner import run_backtest
from app.engine.types import ContractKey
from app.models.spec import StrategySpec

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


# ─────────────────── scale-in add-on-current-bar bound (D5a) ───────────────
# The profitable strategy at minute scale is "add at the NEXT bar's better
# price". A ladder add must fill at the bar it is REACHED, never a future bar —
# structurally impossible (the fill only ever reads the current IntradayView).

def _scale_in_spec() -> StrategySpec:
    def rung(value: float, add: int) -> dict:
        return {"indicator": "sma", "timeframe": "5min", "period": 2,
                "operator": "<=", "value": value, "add_contracts": add}

    return StrategySpec.model_validate({
        "spec_version": 3,
        "meta": {"name": "add canary", "description_raw": "ladder"},
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
                "rungs": [rung(99.5, 2), rung(99.0, 3)],
                "rearm": {"indicator": "sma", "timeframe": "5min", "period": 2,
                          "operator": ">", "value": 99.5},
                "max_total_contracts": 10,
            },
        },
        "exit": {"profit_target_pct": 200, "close_at_time": "15:45"},
        "sizing": {"method": "fixed_contracts", "value": 1},
        "costs": {"commission_per_contract": 0.65, "slippage_half_spread_fraction": 0.5,
                  "max_spread_pct": 500},
        "backtest": {"start": None, "end": "2025-01-06", "initial_capital": 10_000,
                     "seed": 42, "clock": "5min"},
    })


class _OneSession:
    def __init__(self, slc: SessionSlice) -> None:
        self._slc = slc

    @property
    def slice_max_trading_dte(self) -> int:
        return 2

    def sessions(self) -> list[date]:
        return [self._slc.session]

    def slice_for(self, session: date) -> SessionSlice | None:
        return self._slc if session == self._slc.session else None


def test_scale_in_add_fills_at_the_reached_bar_never_the_next() -> None:
    def call(bid: float, ask: float) -> dict:
        return {"expiration": "2025-01-07", "right": "call", "strike": 100.0,
                "bid": bid, "ask": ask}

    # rung1 (SMA ≤ 99.0) is REACHED at 09:55 (ask 0.50 → buy 0.475). The very
    # next bar collapses to ask 0.10 — the cheaper add a peeker would bank. The
    # add must fill at 09:55, not 10:00.
    slc = build_fixture_slice(
        "2025-01-06",
        quotes={
            "09:30": [call(1.00, 1.10)], "09:35": [call(1.00, 1.10)],
            "09:40": [call(0.60, 0.70)], "09:45": [call(0.60, 0.70)],  # rung0 opens
            "09:50": [call(0.40, 0.50)], "09:55": [call(0.40, 0.50)],  # rung1 reached
            "10:00": [call(0.05, 0.10)],  # the "future" cheap add — must be invisible
            "15:45": [call(0.40, 0.50)],
        },
        underlying={"09:30": 100.0, "09:35": 100.0, "09:40": 99.5, "09:45": 99.5,
                    "09:50": 99.0, "09:55": 99.0, "10:00": 99.0, "15:45": 99.0},
    )
    store = build_fixture_store(
        "SPY", {}, {"2025-01-06": (100.0, 100.0), "2025-01-07": (100.0, 100.0)}
    )
    result = run_backtest(_scale_in_spec(), store, _OneSession(slc))

    rung1 = [rf for rf in result.rung_fills if rf.rung_index == 1]
    assert len(rung1) == 1
    assert rung1[0].bar_time == "09:55"  # reached here — not the next bar
    assert rung1[0].fill_price == pytest.approx(0.475)  # 09:55 ask, not 10:00's 0.10

