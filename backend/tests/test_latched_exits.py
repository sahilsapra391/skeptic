"""FX.3 — latched exits + the live-price condition side (finest mode).

The intrabar-unknown rule, applied where minute data shrinks the blind
spot: a minute-grid bar can OBSERVE the underlying touch an exit level
between 5-min NBBO stamps. Once seen, the touch COUNTS (worse-path /
directional honesty — a forgotten exit is optimism): the close completes
at the first quoted bar that can fill it, without re-evaluation, no
expiry, trigger + fill bars disclosed. The same touch is honestly
INVISIBLE at the 5-min grid — finer data shrinks the blind spot, never
removes it (both pinned here).

Entry math convention: short put K=100 quoted 2.00/2.10 → credit 2.025 →
+201.85. Exit at 3.00/3.10 → btc 3.075 → −308.15 → pl −106.30.

Condition: price_vs_sma_pct(period 2, 5min) < −5. The evaluator needs two
valid pct values, so three stamps seed the series (100 / 100.1 / 100.2 →
SMA(2)=100.15); a minute print of 88 → (88/100.15 − 1)·100 = −12.13%
fires; the recovered prints (≥99.5) do not.
"""

from __future__ import annotations

import pytest

from app.engine.market import SessionSlice, build_fixture_slice, build_fixture_store
from app.engine.runner import run_backtest
from app.models.spec import StrategySpec
from tests.test_finest_resolution import FinestFixtureIntraday

SESSION = "2025-01-06"
EXP = "2025-01-07"
UNDERLYING_STORE = {"2025-01-06": (100.0, 100.3), "2025-01-07": (100.3, 100.5)}
CRASH_COND = [{"indicator": "price_vs_sma_pct", "operator": "<", "value": -5,
               "period": 2, "timeframe": "5min"}]


def _put(bid: float, ask: float) -> dict:
    return {"expiration": EXP, "right": "put", "strike": 100.0,
            "bid": bid, "ask": ask, "delta": -0.50, "iv": 0.2}


def _spec(*, exit_conditions: list | None = None,
          entry_conditions: list | None = None,
          scan: str | None = None,
          resolution: str | None = "finest") -> StrategySpec:
    entry: dict = {"schedule": {"frequency": "daily"},
                   "conditions": entry_conditions or [],
                   "max_concurrent_positions": 1}
    if scan is not None:
        entry["intraday_scan"] = scan
    exit_block: dict = {"profit_target_pct": 500}
    if exit_conditions is not None:
        exit_block["conditions"] = exit_conditions
    backtest: dict = {"start": None, "end": SESSION, "initial_capital": 10_000,
                      "seed": 42, "clock": "5min"}
    if resolution is not None:
        backtest["resolution"] = resolution
    return StrategySpec.model_validate({
        "spec_version": 4,
        "meta": {"name": "latch fixture", "description_raw": "fixture"},
        "underlying": {"ticker": "SPY"},
        "position": {
            "structure": "short_put",
            "legs": [{"right": "put", "side": "short", "ratio": 1,
                      "strike_selection": {"method": "delta", "value": 0.50}}],
            "expiration_selection": {"target_dte": 1, "min_dte": 0, "max_dte": 2},
        },
        "entry": entry,
        "exit": exit_block,
        "sizing": {"method": "fixed_contracts", "value": 1},
        "costs": {"commission_per_contract": 0.65,
                  "slippage_half_spread_fraction": 0.5},
        "backtest": backtest,
    })


# minute grid: stamps at 09:30/09:35/09:40/09:45 (indicator + quote bars),
# a crash print at 09:41 that RECOVERS by 09:42 — minute-grid-only
MINUTE_UND = {"09:30": 100.0, "09:35": 100.1, "09:40": 100.2, "09:41": 88.0,
              "09:42": 99.5, "09:43": 100.0, "09:44": 100.1, "09:45": 100.3}
STAMP_UND = {"09:30": 100.0, "09:35": 100.1, "09:40": 100.2, "09:45": 100.3}


def _minute_slice(quotes: dict[str, list[dict]]) -> SessionSlice:
    return build_fixture_slice(SESSION, quotes=quotes, underlying=MINUTE_UND,
                               bar_resolution="1min")


def _five_slice(quotes: dict[str, list[dict]]) -> SessionSlice:
    return build_fixture_slice(SESSION, quotes=quotes, underlying=STAMP_UND)


def _run(spec: StrategySpec, minute: SessionSlice | None,
         five: SessionSlice):
    store = build_fixture_store("SPY", {}, UNDERLYING_STORE)
    provider = FinestFixtureIntraday(
        slices={SESSION: five},
        minute={SESSION: minute} if minute is not None else None,
    )
    return run_backtest(spec, store, provider)


QUOTES_ALL_STAMPS = {"09:30": [_put(2.00, 2.10)],
                     "09:35": [_put(2.00, 2.10)],
                     "09:40": [_put(2.00, 2.10)],
                     "09:45": [_put(3.00, 3.10)]}


class TestLatchedExit:
    def test_touch_at_quoteless_bar_closes_at_next_quote(self) -> None:
        spec = _spec(exit_conditions=CRASH_COND)
        result = _run(spec, _minute_slice(QUOTES_ALL_STAMPS),
                      _five_slice(QUOTES_ALL_STAMPS))
        closes = [t for t in result.trades if t.action == "CLOSE"]
        assert len(closes) == 1
        assert closes[0].reason == "condition_exit"
        assert closes[0].bar_time == "09:45"  # first quoted bar after 09:41
        assert "triggered 09:41" in closes[0].detail
        # the fill is the 09:45 REAL quote (worse than the touch — visible):
        # pl = 201.85 − 308.15 = −106.30
        assert closes[0].pl == pytest.approx(-106.30, abs=0.005)
        # fade-proof: at 09:45 the condition is FALSE (100.3 vs sma) — the
        # close happened anyway because the latch never re-evaluates

    def test_latch_persists_across_unfillable_bars_no_expiry(self) -> None:
        # the 09:45 stamp carries NO quote for the contract; the next quoted
        # bar is 09:50 — the latched exit completes there (never forgotten)
        und = dict(MINUTE_UND)
        und["09:50"] = 100.4
        quotes = {"09:30": [_put(2.00, 2.10)],
                  "09:35": [_put(2.00, 2.10)],
                  "09:40": [_put(2.00, 2.10)],
                  "09:50": [_put(3.00, 3.10)]}
        slc = build_fixture_slice(SESSION, quotes=quotes, underlying=und,
                                  bar_resolution="1min")
        spec = _spec(exit_conditions=CRASH_COND)
        result = _run(spec, slc, _five_slice(QUOTES_ALL_STAMPS))
        closes = [t for t in result.trades if t.action == "CLOSE"]
        assert len(closes) == 1 and closes[0].bar_time == "09:50"
        assert "triggered 09:41" in closes[0].detail

    def test_same_touch_is_invisible_at_five_min(self) -> None:
        # the honest blind spot, pinned: the 5-min grid never sees the 09:41
        # print — no exit fires, the position survives the session
        spec = _spec(exit_conditions=CRASH_COND)
        result = _run(spec, None, _five_slice(QUOTES_ALL_STAMPS))
        closes = [t for t in result.trades if t.action == "CLOSE"]
        assert closes == []
        assert result.resolution_mix == {"five_min": 1}


class TestLatchDisclosure:
    def test_flatten_bar_completes_latch_under_its_own_reason(self) -> None:
        # review finding: a pending latch first fillable at the close_at_time
        # bar closes as condition_exit (trigger disclosed), NEVER
        # misattributed to session_flat
        spec_raw = _spec(exit_conditions=CRASH_COND).model_dump(mode="json",
                                                                exclude_none=True)
        spec_raw["exit"]["close_at_time"] = "09:45"
        spec = StrategySpec.model_validate(spec_raw)
        result = _run(spec, _minute_slice(QUOTES_ALL_STAMPS),
                      _five_slice(QUOTES_ALL_STAMPS))
        closes = [t for t in result.trades if t.action == "CLOSE"]
        assert len(closes) == 1
        assert closes[0].reason == "condition_exit"  # not session_flat
        assert "triggered 09:41" in closes[0].detail

    def test_settlement_supersession_is_disclosed(self) -> None:
        # review finding: a pending latch swallowed by same-session expiry
        # leaves a trace — the settlement event names the trigger
        und = dict(MINUTE_UND)
        quotes = {"09:30": [dict(_put(2.00, 2.10), expiration=SESSION)],
                  "09:35": [dict(_put(2.00, 2.10), expiration=SESSION)],
                  "09:40": [dict(_put(2.00, 2.10), expiration=SESSION)]}
        slc = build_fixture_slice(SESSION, quotes=quotes, underlying=und,
                                  bar_resolution="1min")
        spec = _spec(exit_conditions=CRASH_COND)
        result = _run(spec, slc, _five_slice(QUOTES_ALL_STAMPS))
        closes = [t for t in result.trades if t.action == "CLOSE"]
        assert closes == []  # no quoted bar after the trigger — settle wins
        settles = [t for t in result.trades
                   if t.action in ("EXPIRE", "SETTLE", "ASSIGN")]
        assert settles, "same-session expiry must settle"
        assert any("pending condition_exit (triggered 09:41) superseded"
                   in t.detail for t in settles)


class TestLivePriceEntryIntegration:
    def test_minute_dip_arms_entry_fills_at_next_quote(self) -> None:
        # FX.2 + FX.3 end-to-end: the dip at 09:41 (quote-less) trips the
        # ENTRY condition via the live price side, arms the order, and it
        # fills at 09:45's real NBBO even though the dip recovered — the
        # symmetric counterpart of the latched exit, per the one-semantic
        # owner decision. armed bar disclosed.
        spec = _spec(entry_conditions=CRASH_COND, scan="every_setup")
        result = _run(spec, _minute_slice(QUOTES_ALL_STAMPS),
                      _five_slice(QUOTES_ALL_STAMPS))
        opens = [t for t in result.trades if t.action == "OPEN"]
        assert len(opens) == 1
        assert opens[0].bar_time == "09:45"
        assert "armed 09:41" in opens[0].detail
        assert "3.02" in opens[0].detail  # 09:45's quote: 3.05 − 0.025 = 3.025


class TestLivePriceUnits:
    def test_vwap_live_price_side(self) -> None:
        from app.engine.conditions import evaluate_condition
        from app.models.spec import Condition, Indicator, Operator, Timeframe
        from tests.test_conditions_intraday import _FakeBar

        cond = Condition(indicator=Indicator.PRICE_VS_VWAP_PCT,
                         operator=Operator.LT, value=-1,
                         timeframe=Timeframe.FIVE_MIN)
        # sampled last 98 vs vwap 99 = −1.01% fires; at an OFF-STAMP bar a
        # live print of 99.5 (recovered) does NOT — the live side drives it
        assert evaluate_condition(_FakeBar([100.0, 99.0, 98.0], 99.0), cond)
        assert not evaluate_condition(
            _FakeBar([100.0, 99.0, 98.0], 99.0, live=99.5,
                     is_indicator_stamp=False), cond)

    def test_price_vs_sma_live_price_side(self) -> None:
        from app.engine.conditions import evaluate_condition
        from app.models.spec import Condition, Indicator, Operator, Timeframe
        from tests.test_conditions_intraday import _FakeBar

        cond = Condition(indicator=Indicator.PRICE_VS_SMA_PCT,
                         operator=Operator.LT, value=-5, period=2,
                         timeframe=Timeframe.FIVE_MIN)
        # sampled [100, 100.1, 100.2]: sma(2) 100.15, sampled pct +0.05 →
        # False; an off-stamp live print of 88 → −12.13% → True
        assert not evaluate_condition(_FakeBar([100.0, 100.1, 100.2], None), cond)
        assert evaluate_condition(
            _FakeBar([100.0, 100.1, 100.2], None, live=88.0,
                     is_indicator_stamp=False), cond)

    def test_crosses_are_stamp_anchored(self) -> None:
        # review finding 1, both directions pinned. Sampled stamps
        # 100/100/90 vs SMA(2): pcts [nan, 0.00, −5.263].
        from app.engine.conditions import evaluate_condition
        from app.models.spec import Condition, Indicator, Operator, Timeframe
        from tests.test_conditions_intraday import _FakeBar

        cross_up = Condition(indicator=Indicator.PRICE_VS_SMA_PCT,
                             operator=Operator.CROSSES_ABOVE, value=-5,
                             period=2, timeframe=Timeframe.FIVE_MIN)
        # GENUINE inter-stamp cross: latest sampled pct −5.263, live 96 →
        # (96/95 − 1) = +1.05: pair (−5.263, +1.05) crosses −5 → True
        # (the pre-fix pair (0.00, +1.05) missed it — a forgotten exit)
        assert evaluate_condition(
            _FakeBar([100.0, 100.0, 90.0], None, live=96.0,
                     is_indicator_stamp=False), cross_up)
        # NO spurious re-fire: stamps 100/90/100 → the cross resolved AT
        # the last stamp (pair [−5.263, +5.263]); at following minute bars
        # the pair is (+5.263, live) — prev is above the threshold → False
        assert not evaluate_condition(
            _FakeBar([100.0, 90.0, 100.0], None, live=100.0,
                     is_indicator_stamp=False), cross_up)
        # ...and AT the stamp itself the cross fires exactly once
        assert evaluate_condition(
            _FakeBar([100.0, 90.0, 100.0], None), cross_up)
