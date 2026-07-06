"""Scale-in basket engine (D5a) — hand-computed fixtures.

The ladder is a LONG single-leg call whose contracts accumulate into ONE
basket with a blended cost basis; the whole basket exits together. Rungs are
driven here by SMA(period 2) on the 5-min underlying lasts so the firing bar
of each rung is unambiguous and the P&L is hand-computable — the basket state
machine is indicator-agnostic, and the RSI(14) 5-min path the founder's real
prompt uses is exercised by test_conditions_intraday.py, which owns the RSI
math. SMA(2) at bar i = mean(last[i-1], last[i]); a rung `sma <= X` fires the
first bar that mean reaches X.

Fill model (guardrail #1): BUY = mid + 0.5·(ask−mid), SELL = mid − 0.5·(mid−bid);
commission $0.65 per contract per side; OI unknown → base slip, no penalty.

Blended premium: after each add, premium = −(total premium $)/(100·contracts),
so the existing exit math profit_pct = (premium + liq)/|premium| equals
value/cost − 1 on the whole basket.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.engine.market import SessionSlice, build_fixture_slice, build_fixture_store
from app.engine.runner import run_backtest
from app.engine.types import RunResult
from app.models.spec import StrategySpec

EXP = "2025-01-07"  # 1 trading-DTE call (opened 2025-01-06)


class FixtureIntraday:
    def __init__(self, slices: dict[str, SessionSlice], max_dte: int = 2) -> None:
        self._slices = {date.fromisoformat(k): v for k, v in slices.items()}
        self._max_dte = max_dte

    @property
    def slice_max_trading_dte(self) -> int:
        return self._max_dte

    def sessions(self) -> list[date]:
        return sorted(self._slices)

    def slice_for(self, session: date) -> SessionSlice | None:
        return self._slices.get(session)


def _call(bid: float, ask: float) -> dict:
    return {"expiration": EXP, "right": "call", "strike": 100.0, "bid": bid, "ask": ask}


def _rung(value: float, add: int) -> dict:
    return {"indicator": "sma", "timeframe": "5min", "period": 2,
            "operator": "<=", "value": value, "add_contracts": add}


def _ladder_spec(rungs: list[dict], exit_rules: dict, max_total: int) -> StrategySpec:
    return StrategySpec.model_validate({
        "spec_version": 3,
        "meta": {"name": "scale-in fixture", "description_raw": "ladder"},
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
                "mode": "signal_ladder", "basket": True, "rungs": rungs,
                "rearm": {"indicator": "sma", "timeframe": "5min", "period": 2,
                          "operator": ">", "value": 99.5},
                "max_total_contracts": max_total,
            },
        },
        "exit": exit_rules,
        "sizing": {"method": "fixed_contracts", "value": 1},
        # max_spread_pct wide: deep-OTM rung quotes here have big spreads (a
        # real property of cheap options); the entry spread-gate is exercised
        # in test_liquidity.py — these fixtures isolate the ladder mechanics.
        "costs": {"commission_per_contract": 0.65, "slippage_half_spread_fraction": 0.5,
                  "max_spread_pct": 500},
        "backtest": {"start": None, "end": "2025-01-06", "initial_capital": 10_000,
                     "seed": 42, "clock": "5min"},
    })


def _run(slc: SessionSlice, spec: StrategySpec) -> RunResult:
    store = build_fixture_store(
        "SPY", {}, {"2025-01-06": (100.0, 100.0), "2025-01-07": (100.0, 100.0)}
    )
    return run_backtest(spec, store, FixtureIntraday({"2025-01-06": slc}))


def _adds(result: RunResult) -> list:
    return [t for t in result.trades if t.action == "ADD"]


def _closes(result: RunResult) -> list:
    return [t for t in result.trades if t.action == "CLOSE"]


# ─────────────────────────────────────────────────────────── fixture 1: PT
def test_three_rung_basket_hits_profit_target() -> None:
    """Rungs +2/+3/+5 at SMA 99.5/99.0/98.5. Opens with rung0 (+2 @0.675),
    adds +3 @0.475 and +5 @0.325 → 10 ct, blended 0.44. At 10:10 the call
    lifts to 0.60/0.70: sell 0.625 = 42.05% ≥ 40% → PT.
      cost 135 + 142.50 + 162.50 = 440;  commissions in/out = 6.50 + 6.50
      proceeds 0.625×10×100 = 625  →  P/L = 625 − 440 − 13 = +172.00
    """
    slc = build_fixture_slice(
        "2025-01-06",
        quotes={
            "09:30": [_call(1.00, 1.10)], "09:35": [_call(1.00, 1.10)],
            "09:40": [_call(0.60, 0.70)], "09:45": [_call(0.60, 0.70)],  # rung0 opens
            "09:50": [_call(0.40, 0.50)], "09:55": [_call(0.40, 0.50)],  # rung1
            "10:00": [_call(0.25, 0.35)], "10:05": [_call(0.25, 0.35)],  # rung2
            "10:10": [_call(0.60, 0.70)],  # PT
        },
        underlying={"09:30": 100.0, "09:35": 100.0, "09:40": 99.5, "09:45": 99.5,
                    "09:50": 99.0, "09:55": 99.0, "10:00": 98.5, "10:05": 98.5,
                    "10:10": 99.0},
    )
    result = _run(slc, _ladder_spec(
        [_rung(99.5, 2), _rung(99.0, 3), _rung(98.5, 5)],
        {"profit_target_pct": 40, "stop_loss_pct": 75}, max_total=20))

    assert result.filled == 1  # ONE basket, not one per rung
    assert len(_adds(result)) == 2  # rungs 1 & 2 add after the opening bar
    assert len(result.rung_fills) == 3
    assert [rf.qty for rf in result.rung_fills] == [2, 3, 5]
    assert result.rung_fills[0].bar_time == "09:45"
    assert result.rung_fills[-1].bar_time == "10:05"
    closes = _closes(result)
    assert len(closes) == 1 and closes[0].reason == "profit_target"
    assert closes[0].pl == pytest.approx(172.00, abs=0.005)
    assert closes[0].bar_time == "10:10"  # add at 10:05 was NOT exited same bar
    assert result.equity[-1] == pytest.approx(10_172.00, abs=0.005)


# ──────────────────────────────────────────── fixture 2: martingale ruin
def test_cascade_to_loss_force_closed_books_full_loss() -> None:
    """Rungs +2/+3/+5/+10 (cap 25, no clamp) cascade as the call decays; PT
    never hits and the −75% stop is set to −95 so it can't preempt. At 15:45
    close_at_time force-flats the whole basket at a loss.
      cost 135 + 142.50 + 162.50 + 180 = 620 (20 ct, blended 0.31)
      15:45 call 0.05/0.15 → sell 0.075, proceeds 150; commissions 13 + 13
      P/L = 150 − 620 − 26 = −496.00, booked in full (not smoothed)
    """
    slc = build_fixture_slice(
        "2025-01-06",
        quotes={
            "09:30": [_call(1.00, 1.10)], "09:35": [_call(1.00, 1.10)],
            "09:40": [_call(0.60, 0.70)], "09:45": [_call(0.60, 0.70)],  # rung0 opens
            "09:50": [_call(0.40, 0.50)], "09:55": [_call(0.40, 0.50)],  # rung1
            "10:00": [_call(0.25, 0.35)], "10:05": [_call(0.25, 0.35)],  # rung2
            "10:10": [_call(0.12, 0.20)], "10:15": [_call(0.12, 0.20)],  # rung3
            "15:45": [_call(0.05, 0.15)],  # force-flat
        },
        underlying={"09:30": 100.0, "09:35": 100.0, "09:40": 99.5, "09:45": 99.5,
                    "09:50": 99.0, "09:55": 99.0, "10:00": 98.5, "10:05": 98.5,
                    "10:10": 98.0, "10:15": 98.0, "15:45": 97.5},
    )
    result = _run(slc, _ladder_spec(
        [_rung(99.5, 2), _rung(99.0, 3), _rung(98.5, 5), _rung(98.0, 10)],
        {"profit_target_pct": 40, "stop_loss_pct": 95, "close_at_time": "15:45"},
        max_total=25))

    assert result.filled == 1
    assert len(_adds(result)) == 3
    assert [rf.qty for rf in result.rung_fills] == [2, 3, 5, 10]
    # the deepest rung is the biggest single commitment — the martingale tell
    assert max(result.rung_fills, key=lambda rf: rf.qty).threshold == 98.0
    closes = _closes(result)
    assert len(closes) == 1 and closes[0].reason == "session_flat"
    assert closes[0].pl == pytest.approx(-496.00, abs=0.005)  # loss booked in full
    assert result.equity[-1] == pytest.approx(9_504.00, abs=0.005)


# ───────────────────────────────────────────────── fixture 3: re-arm
def test_no_second_basket_until_signal_leaves_the_zone() -> None:
    """Basket 1 opens (+2 @0.675) and hits PT next bar. The signal is STILL
    in the zone (SMA ≤ 99.5) at 09:55 — NO second basket may open there. Only
    after the rearm (SMA > 99.5) at 10:05 does a dip at 10:15 open basket 2.
    """
    slc = build_fixture_slice(
        "2025-01-06",
        quotes={
            "09:30": [_call(1.00, 1.10)], "09:35": [_call(1.00, 1.10)],
            "09:40": [_call(0.60, 0.70)], "09:45": [_call(0.60, 0.70)],  # basket1 opens
            "09:50": [_call(1.00, 1.10)],  # PT (0.925/0.675 = +37%? see below)
            "09:55": [_call(0.60, 0.70)],  # still in zone — must NOT reopen
            "10:00": [_call(0.60, 0.70)], "10:05": [_call(0.60, 0.70)],  # rearm bar
            "10:10": [_call(0.60, 0.70)], "10:15": [_call(0.60, 0.70)],  # basket2 opens
            "10:20": [_call(1.00, 1.10)],  # basket2 PT
        },
        underlying={"09:30": 100.0, "09:35": 100.0, "09:40": 99.5, "09:45": 99.5,
                    "09:50": 99.5, "09:55": 99.5,  # in zone: SMA stays 99.5
                    "10:00": 100.0, "10:05": 100.0,  # SMA back to > 99.5 → rearm
                    "10:10": 99.5, "10:15": 99.5,  # dips again → basket2
                    "10:20": 99.5},
    )
    result = _run(slc, _ladder_spec(
        [_rung(99.5, 2)], {"profit_target_pct": 30}, max_total=10))

    opens = [t for t in result.trades if t.action == "OPEN"]
    closes = _closes(result)
    assert len(opens) == 2, "exactly two baskets — the second only after rearm"
    assert len(closes) == 2 and all(c.reason == "profit_target" for c in closes)
    # basket 1 opened at 09:45; basket 2 could not open at 09:55 (still in zone)
    assert opens[0].bar_time == "09:45"
    assert opens[1].bar_time == "10:15"  # first dip AFTER the rearm cleared


# ─────────────────────────────────────────────── fixture 4: cap clamp
def test_cap_clamp_trims_the_breaching_rung() -> None:
    """Rungs +2/+3/+5/+10 with max_total_contracts 15. A+B+C = 10; rung D
    wants +10 but only 5 remain → clamped to 5 (cap_clamped), and no deeper
    rung fires. Basket tops out at 15 contracts.
    """
    slc = build_fixture_slice(
        "2025-01-06",
        quotes={
            "09:30": [_call(1.00, 1.10)], "09:35": [_call(1.00, 1.10)],
            "09:40": [_call(0.60, 0.70)], "09:45": [_call(0.60, 0.70)],  # rung0
            "09:50": [_call(0.40, 0.50)], "09:55": [_call(0.40, 0.50)],  # rung1
            "10:00": [_call(0.25, 0.35)], "10:05": [_call(0.25, 0.35)],  # rung2
            "10:10": [_call(0.20, 0.30)], "10:15": [_call(0.20, 0.30)],  # rung3 clamps
            "15:45": [_call(0.20, 0.30)],  # force-flat to close the book
        },
        underlying={"09:30": 100.0, "09:35": 100.0, "09:40": 99.5, "09:45": 99.5,
                    "09:50": 99.0, "09:55": 99.0, "10:00": 98.5, "10:05": 98.5,
                    "10:10": 98.0, "10:15": 98.0, "15:45": 98.0},
    )
    result = _run(slc, _ladder_spec(
        [_rung(99.5, 2), _rung(99.0, 3), _rung(98.5, 5), _rung(98.0, 10)],
        {"profit_target_pct": 200, "close_at_time": "15:45"}, max_total=15))

    assert [rf.qty for rf in result.rung_fills] == [2, 3, 5, 5]  # last clamped 10→5
    clamped = [rf for rf in result.rung_fills if rf.cap_clamped]
    assert len(clamped) == 1 and clamped[0].threshold == 98.0
    add_events = _adds(result)
    assert add_events[-1].reason == "cap_clamped"
    # the basket topped out at the cap
    closes = _closes(result)
    assert len(closes) == 1
    total_ct = sum(rf.qty for rf in result.rung_fills)
    assert total_ct == 15
