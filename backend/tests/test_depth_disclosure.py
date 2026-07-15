"""Fill-vs-displayed-depth disclosure (ENGINE-V4 F5) — hand-computed.

Owner decisions 2026-07-07: DISCLOSE FIRST, model later — fills keep
FX.2 semantics (real NBBO + configured slippage, prices untouched);
every option-leg fill compares its quantity against the traded side's
displayed NBBO size (buy → ask_size, sell → bid_size); exceedances are
counted, named in the trade log, and reported by the liquidity profile
(REPORTED, never scored). A price-impact model must be EARNED by the
D3d calibration loop; a hard depth gate would be pessimistic in a way
reality isn't (beyond-L1 liquidity exists — the FX.2 doctrine). EOD
quotes carry no sizes → depth-unknown, counters untouched, daily
digests bit-identical.
"""

from __future__ import annotations

from datetime import date

from app.engine.market import build_fixture_slice, build_fixture_store
from app.engine.runner import run_backtest
from app.honesty.stages import liquidity_profile
from app.models.spec import StrategySpec
from tests.test_five_min_clock import FixtureIntraday


def _put_sized(bid: float, ask: float, delta: float, expiration: str,
               bid_size: int | None, ask_size: int | None) -> dict:
    return {"expiration": expiration, "right": "put", "strike": 100.0,
            "bid": bid, "ask": ask, "delta": delta,
            "bid_size": bid_size, "ask_size": ask_size}


def _spec(contracts: int, structure: str = "short_put") -> StrategySpec:
    right = "put" if "put" in structure else "call"
    side = "short" if structure == "short_put" else "long"
    return StrategySpec.model_validate({
        "spec_version": 2,
        "meta": {"name": "depth fixture", "description_raw": "f5"},
        "underlying": {"ticker": "SPY"},
        "position": {
            "structure": structure,
            "legs": [{"right": right, "side": side, "ratio": 1,
                      "strike_selection": {"method": "delta", "value": 0.50}}],
            "expiration_selection": {"target_dte": 1, "min_dte": 0, "max_dte": 2},
        },
        "entry": {"schedule": {"frequency": "daily"}, "conditions": [],
                  "max_concurrent_positions": 1},
        "exit": {"profit_target_pct": 500},  # unhittable → session flat/expiry
        "sizing": {"method": "fixed_contracts", "value": contracts},
        "costs": {"commission_per_contract": 0.65,
                  "slippage_half_spread_fraction": 0.5, "slippage_half_spread_fraction_sell": 0.5},
        # capital sized so the buying-power gate (2026-07-15) never binds:
        # these fixtures deliberately sell up to 40 ATM contracts (reserve
        # 40 × $2,000 = $80,000) to exercise DEPTH disclosure, not funding
        "backtest": {"start": None, "end": None, "initial_capital": 250_000,
                     "seed": 42, "clock": "5min"},
    })


def _run(contracts: int, bid_size: int | None, ask_size: int | None):
    """One session, one short-put entry (SELL at bid side) and one
    settlement/flat close. Sized quotes at every bar."""
    session, expiry = "2025-01-06", "2025-01-06"  # 0DTE → settles at close
    bars = ["09:30", "09:35", "09:40"]
    slc = build_fixture_slice(
        session,
        quotes={b: [_put_sized(2.00, 2.10, -0.50, expiry, bid_size, ask_size)]
                for b in bars},
        underlying={b: 100.0 for b in bars},
    )
    store = build_fixture_store(
        "SPY", {}, {session: (100.0, 100.0), "2025-01-07": (100.0, 100.0)})
    return run_backtest(_spec(contracts), store,
                        FixtureIntraday({session: slc}))


class TestDepthCounters:
    def test_within_depth_is_known_not_flagged(self) -> None:
        # short put entry = SELL 5 vs bid_size 40 → known, within
        result = _run(5, bid_size=40, ask_size=40)
        assert result.fills_depth_known >= 1
        assert result.fills_beyond_depth == 0

    def test_exact_depth_boundary_is_within(self) -> None:
        # qty == displayed size fills the whole displayed quote — honest
        result = _run(40, bid_size=40, ask_size=40)
        assert result.fills_beyond_depth == 0

    def test_beyond_depth_counted_and_named(self) -> None:
        # SELL 20 vs bid_size 3 → flagged, and the trade log names it
        result = _run(20, bid_size=3, ask_size=90)
        assert result.fills_beyond_depth >= 1
        opens = [t for t in result.trades if t.action == "OPEN"]
        assert any("qty 20 > bid size 3" in t.detail for t in opens)

    def test_traded_side_is_the_one_checked(self) -> None:
        # the SELL must read bid_size, never ask_size: thin ASK alone
        # must not flag a short entry...
        session, expiry = "2025-01-06", "2025-01-06"
        slc = build_fixture_slice(
            session,
            quotes={
                "09:30": [_put_sized(2.00, 2.10, -0.50, expiry, 90, 3)],
                # the put cheapens → a 25% profit target CLOSE fires as a
                # BUY at the ask, against ask_size 3
                "09:35": [_put_sized(1.30, 1.40, -0.40, expiry, 90, 3)],
                "09:40": [_put_sized(1.30, 1.40, -0.40, expiry, 90, 3)],
            },
            underlying={b: 100.0 for b in ("09:30", "09:35", "09:40")},
        )
        store = build_fixture_store(
            "SPY", {}, {session: (100.0, 100.0), "2025-01-07": (100.0, 100.0)})
        spec_doc = _spec(20).model_dump(mode="json", exclude_none=True)
        spec_doc["exit"] = {"profit_target_pct": 25}
        result = run_backtest(StrategySpec.model_validate(spec_doc), store,
                              FixtureIntraday({session: slc}))
        opens = [t for t in result.trades if t.action == "OPEN"]
        assert not any("size" in t.detail for t in opens)
        # ...and the quote-priced CLOSE is a BUY (close of a short) — it
        # reads ask_size 3 and flags there (a SETTLEMENT close has no NBBO
        # and honestly carries no depth at all)
        closes = [t for t in result.trades if t.action == "CLOSE"]
        assert any("qty 20 > ask size 3" in t.detail for t in closes)
        assert result.fills_beyond_depth >= 1

    def test_missing_sizes_stay_unknown_never_guessed(self) -> None:
        result = _run(20, bid_size=None, ask_size=None)
        assert result.fills_depth_known == 0
        assert result.fills_beyond_depth == 0

    def test_prices_are_untouched_by_depth(self) -> None:
        # owner decision: disclosure only — identical fills either way
        thin = _run(20, bid_size=1, ask_size=1)
        deep = _run(20, bid_size=500, ask_size=500)
        assert thin.equity == deep.equity
        assert [t.pl for t in thin.trades] == [t.pl for t in deep.trades]


class TestDailyClockUnchanged:
    def test_eod_fills_carry_no_depth(self) -> None:
        # EOD chains have no sizes — the daily clock's counters stay zero
        # (the bit-identity guarantee behind the pinned digests)
        days = [date(2024, 1, 1), date(2024, 1, 2)]
        chains = {d.isoformat(): [{
            "expiration": "2024-01-12", "right": "put", "strike": 100.0,
            "bid": 1.00, "ask": 1.10, "delta": -0.50, "iv": 0.2,
        }] for d in days}
        underlying = {d.isoformat(): (100.0, 100.0) for d in days}
        store = build_fixture_store("SPY", chains, underlying)
        spec = StrategySpec.model_validate({
            "spec_version": 1,
            "meta": {"name": "daily depth", "description_raw": "f5"},
            "underlying": {"ticker": "SPY"},
            "position": {
                "structure": "short_put",
                "legs": [{"right": "put", "side": "short", "ratio": 1,
                          "strike_selection": {"method": "delta", "value": 0.50}}],
                "expiration_selection": {"target_dte": 10, "min_dte": 5,
                                         "max_dte": 15},
            },
            "entry": {"schedule": {"frequency": "daily"}, "conditions": [],
                      "max_concurrent_positions": 1},
            "exit": {"time_exit_dte": 0},
            "sizing": {"method": "fixed_contracts", "value": 50},
            "costs": {"commission_per_contract": 0.65,
                      "slippage_half_spread_fraction": 0.5,
                      "slippage_half_spread_fraction_sell": 0.5},
            # 50 ATM contracts reserve $100,000 under the buying-power gate
            # (2026-07-15) — capital sized so the gate never binds here
            "backtest": {"start": None, "end": None,
                         "initial_capital": 250_000, "seed": 42},
        })
        result = run_backtest(spec, store)
        assert result.option_leg_fills > 0
        assert result.fills_depth_known == 0
        assert result.fills_beyond_depth == 0


class TestLiquidityProfileDisclosure:
    def test_shares_and_note(self) -> None:
        result = _run(20, bid_size=3, ask_size=90)
        prof = liquidity_profile(result, _spec(20))
        assert prof.depth_known_share == 1.0
        assert prof.beyond_depth_share is not None
        assert prof.beyond_depth_share > 0
        assert prof.material
        assert prof.note is not None and "displayed NBBO size" in prof.note

    def test_no_exceedance_no_depth_note(self) -> None:
        result = _run(5, bid_size=40, ask_size=40)
        prof = liquidity_profile(result, _spec(5))
        assert prof.beyond_depth_share == 0.0
        assert prof.note is None or "displayed NBBO size" not in prof.note

    def test_unknown_depth_is_none_not_zero(self) -> None:
        result = _run(5, bid_size=None, ask_size=None)
        prof = liquidity_profile(result, _spec(5))
        assert prof.depth_known_share == 0.0
        assert prof.beyond_depth_share is None


class TestReviewFixes:
    def test_counts_are_grounded_numbers(self) -> None:
        # review fix F5 #1: the note quotes its own counts ("45 of 120") —
        # they must be harvestable or the verdict/Q&A that echoes the
        # disclosure is falsely flagged ungrounded (the WF fold-count class)
        from app.honesty.verdict import _harvest_numbers

        result = _run(20, bid_size=3, ask_size=90)
        prof = liquidity_profile(result, _spec(20))
        nums: set[float] = set()
        _harvest_numbers(prof.model_dump(), nums)
        assert float(prof.fills_beyond_depth) in nums
        assert float(prof.fills_depth_known) in nums

    def test_opening_rung_note_reaches_the_open_event(self) -> None:
        # review fix F5 #2: a beyond-depth FIRST rung (often the ladder's
        # largest) is named on the basket OPEN, not just counted
        session, expiry = "2025-01-06", "2025-01-07"
        bars = ["09:30", "09:35", "09:40", "09:45", "09:50", "09:55"]
        # monotonic-down lasts → 5-min RSI(3) = 0 once seeded (bar index 3)
        lasts = [100.0, 99.5, 99.0, 98.5, 98.0, 97.5]
        call = {"expiration": expiry, "right": "call", "strike": 100.0,
                "bid": 2.00, "ask": 2.10, "delta": 0.50,
                "bid_size": 90, "ask_size": 2}
        slc = build_fixture_slice(
            session,
            quotes={b: [call] for b in bars},
            underlying={b: v for b, v in zip(bars, lasts, strict=True)},
        )
        store = build_fixture_store(
            "SPY", {}, {session: (100.0, 100.0), "2025-01-07": (100.0, 100.0)})
        spec = StrategySpec.model_validate({
            "spec_version": 3,
            "meta": {"name": "ladder depth", "description_raw": "f5"},
            "underlying": {"ticker": "SPY"},
            "position": {
                "structure": "long_call",
                "legs": [{"right": "call", "side": "long", "ratio": 1,
                          "strike_selection": {"method": "delta", "value": 0.50}}],
                "expiration_selection": {"target_dte": 1, "min_dte": 0,
                                         "max_dte": 2},
            },
            "entry": {"schedule": {"frequency": "signal_only"},
                      "conditions": [], "max_concurrent_positions": 1,
                      "scale_in": {
                          "mode": "signal_ladder", "basket": True,
                          "rungs": [{"indicator": "rsi", "period": 3,
                                     "timeframe": "5min", "operator": "<",
                                     "value": 50, "add_contracts": 5}],
                          "rearm": {"indicator": "rsi", "period": 3,
                                    "timeframe": "5min", "operator": ">",
                                    "value": 99},
                          "max_total_contracts": 5,
                      }},
            "exit": {"profit_target_pct": 500},
            "sizing": {"method": "fixed_contracts", "value": 1},
            "costs": {"commission_per_contract": 0.65,
                      "slippage_half_spread_fraction": 0.5,
                      "slippage_half_spread_fraction_sell": 0.5},
            "backtest": {"start": None, "end": None, "initial_capital": 25000,
                         "seed": 42, "clock": "5min"},
        })
        result = run_backtest(spec, store, FixtureIntraday({session: slc}))
        opens = [t for t in result.trades if t.action == "OPEN"]
        assert opens, "ladder never opened — fixture broken"
        assert any("qty 5 > ask size 2" in t.detail for t in opens)

    def test_negative_vendor_size_is_unknown_not_exceedance(self) -> None:
        # review fix F5 #6: a negative displayed size is garbage, not depth
        from app.data.intraday import _size_i

        assert _size_i(-3) is None
        assert _size_i(0) == 0
        assert _size_i(7) == 7
        assert _size_i(None) is None

