"""Alpaca bar-modeled quotes + the D2d honesty additions — hand-computed.

Modeled quote math (spread_frac 0.04): last print close 2.00 →
  half = max(2.00 × 0.04 / 2, 0.01) = 0.04 → bid 1.96 / ask 2.04.
Stress rule: modeled fills ALWAYS pay the full adverse price — a short
put SELLS at the modeled bid 1.96 → cash +196.00 − 0.65 = +195.35.

Stale-print guard: a print at 09:31 is a usable price for the 09:35 bar
(4 min old) but NOT for the 09:45 bar (14 min old) — sparse trade prints
must never masquerade as standing quotes.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from app.data import intraday, r2
from app.engine.market import build_fixture_slice
from app.engine.runner import run_backtest
from app.honesty.stages import session_split
from app.models.spec import StrategySpec
from tests.test_five_min_clock import FixtureIntraday, _put


def _alpaca_opt_frame(d: str) -> pd.DataFrame:
    """One contract with prints at 09:31 and 09:52 ET (UTC in the lake) and
    one far-strike contract the ATM band must drop."""
    rows = []
    for ts_et, close, strike in (
        (f"{d} 13:31:00+00:00", 2.00, 100.0),   # 09:31 ET (July: ET+4)
        (f"{d} 13:52:00+00:00", 1.50, 100.0),   # 09:52 ET
        (f"{d} 13:31:00+00:00", 0.10, 120.0),   # outside ATM±$8 → dropped
    ):
        rows.append({
            "ticker": "QQQ", "trading_date": d, "minute_ts": ts_et,
            "occ_symbol": f"QQQ...{strike}", "expiration": d, "right": "put",
            "strike": strike, "open": close, "high": close, "low": close,
            "close": close, "volume": 7, "trade_count": 1, "vwap": close,
            "source": "alpaca",
        })
    return pd.DataFrame(rows)


def _alpaca_und_frame(d: str) -> pd.DataFrame:
    # 1-min closes 09:30–10:00 ET as UTC; close = 100.0 throughout
    ts = pd.date_range(f"{d} 13:30:00+00:00", f"{d} 14:00:00+00:00", freq="1min")
    return pd.DataFrame({"ticker": "QQQ", "minute_ts": ts, "open": 100.0,
                         "high": 100.0, "low": 100.0, "close": 100.0,
                         "volume": 50, "trade_count": 5})


class TestAlpacaModeledLoader:
    @pytest.fixture()
    def store(self, monkeypatch: pytest.MonkeyPatch, tmp_path) -> intraday.IntradayStore:
        d = "2024-07-01"
        monkeypatch.setattr(intraday, "CACHE_DIR", tmp_path)
        intraday._MONTH_FRAMES.clear()
        monkeypatch.setattr(r2, "r2_client", lambda: object())
        monkeypatch.setattr(
            r2, "list_date_prefixes",
            lambda _s3, prefix: [d] if "options_minute" in prefix else [],
        )
        monkeypatch.setattr(r2, "list_chain_dates", lambda _s3, _src, _t: [])
        monkeypatch.setattr(intraday, "_spread_stats", lambda _s3, _t: 0.04)

        def fake_parquet(_s3, key: str):
            if "options_minute" in key:
                return _alpaca_opt_frame(d)
            if "underlying_minute" in key:
                return _alpaca_und_frame(d)
            return None

        monkeypatch.setattr(r2, "get_parquet", fake_parquet)
        return intraday.IntradayStore("QQQ")

    def test_modeled_quote_math_and_stale_guard(self, store: intraday.IntradayStore) -> None:
        slc = store.slice_for(date(2024, 7, 1))
        assert slc is not None and slc.quote_source == "alpaca_modeled"

        from datetime import datetime

        from app.engine.types import ContractKey
        key = ContractKey(expiration=date(2024, 7, 1), right="put", strike=100.0)

        # 09:35 bar: the 09:31 print (4 min old) is usable → modeled quote
        q = slc.quotes[datetime(2024, 7, 1, 9, 35)][key]
        assert q.bid == pytest.approx(1.96) and q.ask == pytest.approx(2.04)
        assert q.delta is None and q.iv is None  # nothing invented
        assert q.last == pytest.approx(2.00)

        # 09:45 bar: the print is 14 min old → the contract is a GAP
        assert key not in slc.quotes.get(datetime(2024, 7, 1, 9, 45), {})

        # 09:55 bar: the 09:52 print revives it at the new price
        # half = max(1.50 × 0.04 / 2, 0.01) = 0.03 → 1.47 / 1.53
        q2 = slc.quotes[datetime(2024, 7, 1, 9, 55)][key]
        assert q2.bid == pytest.approx(1.47) and q2.ask == pytest.approx(1.53)

        # the far strike never appears (ATM band)
        strikes = {k.strike for per in slc.quotes.values() for k in per}
        assert 120.0 not in strikes

    def test_no_spread_stats_no_modeled_sessions(self, store: intraday.IntradayStore,
                                                 monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(intraday, "_spread_stats", lambda _s3, _t: None)
        fresh = intraday.IntradayStore("QQQ")
        assert fresh.sessions() == []  # modeled quotes impossible → honest absence


class TestModeledFillsAreStressed:
    def test_short_put_sells_at_modeled_bid(self) -> None:
        slc = build_fixture_slice(
            "2025-01-06",
            quotes={"09:35": [_put(1.96, 2.04, None, "2025-01-07")]},
            underlying={"09:35": 100.0},
            quote_source="alpaca_modeled",
        )
        spec = StrategySpec.model_validate({
            "spec_version": 2,
            "meta": {"name": "modeled stress", "description_raw": "fixture"},
            "underlying": {"ticker": "QQQ"},
            "position": {"structure": "short_put",
                "legs": [{"right": "put", "side": "short", "ratio": 1,
                          "strike_selection": {"method": "delta", "value": 0.50}}],
                "expiration_selection": {"target_dte": 1, "min_dte": 0, "max_dte": 2}},
            "entry": {"schedule": {"frequency": "daily"}, "conditions": [],
                      "max_concurrent_positions": 1},
            "exit": {"profit_target_pct": 90},
            "sizing": {"method": "fixed_contracts", "value": 1},
            "costs": {"commission_per_contract": 0.65,
                      "slippage_half_spread_fraction": 0.5},
            "backtest": {"start": None, "end": None, "initial_capital": 10_000,
                         "seed": 42, "clock": "5min"},
        })
        from app.engine.market import build_fixture_store
        store = build_fixture_store(
            "QQQ", {}, {"2025-01-06": (100.0, 100.0), "2025-01-07": (100.0, 100.0)}
        )
        result = run_backtest(spec, store, FixtureIntraday({"2025-01-06": slc}))
        assert result.filled == 1
        assert result.fills_stressed == 1  # slip 1.0, forced by the source
        assert result.fill_sources == {"alpaca_modeled": 1}
        # docstring math: sell at BID 1.96 → +196.00 − 0.65 = 10,195.35 cash;
        # mark buys back at ASK 2.04 (stressed) → −204.00
        by_date = dict(zip([d.isoformat() for d in result.dates], result.equity, strict=True))
        assert by_date["2025-01-06"] == pytest.approx(10_195.35 - 204.00, abs=0.005)


class TestSessionSplit:
    def test_buckets_and_pl_attribution(self) -> None:
        from datetime import date as d_

        from app.engine.types import RunResult, TradeEvent

        r = RunResult(ticker="SPY", effective_start=d_(2025, 1, 6),
                      effective_end=d_(2025, 1, 8), seed=42, clock="5min")
        r.trades = [
            TradeEvent(day=d_(2025, 1, 6), action="OPEN", detail="a",
                       position_id=1, bar_time="09:35"),
            TradeEvent(day=d_(2025, 1, 6), action="CLOSE", detail="a", pl=50.0,
                       position_id=1, bar_time="11:00"),
            TradeEvent(day=d_(2025, 1, 7), action="OPEN", detail="b",
                       position_id=2, bar_time="12:00"),
            TradeEvent(day=d_(2025, 1, 7), action="CLOSE", detail="b", pl=-20.0,
                       position_id=2, bar_time="14:00"),
            TradeEvent(day=d_(2025, 1, 8), action="OPEN", detail="c",
                       position_id=3, bar_time="15:30"),
            TradeEvent(day=d_(2025, 1, 8), action="CLOSE", detail="c", pl=10.0,
                       position_id=3, bar_time="16:00"),
        ]
        split = session_split(r)
        assert split.meaningful
        assert (split.open_.trades, split.open_.wins, split.open_.pl) == (1, 1, 50.0)
        assert (split.mid.trades, split.mid.wins, split.mid.pl) == (1, 0, -20.0)
        assert (split.close.trades, split.close.wins, split.close.pl) == (1, 1, 10.0)

    def test_daily_clock_not_meaningful(self) -> None:
        from datetime import date as d_

        from app.engine.types import RunResult

        r = RunResult(ticker="SPY", effective_start=d_(2025, 1, 6),
                      effective_end=d_(2025, 1, 6), seed=42)
        assert not session_split(r).meaningful


class TestLongHistoryGrounding:
    def test_positive_fold_count_is_grounded(self) -> None:
        """Regression for the D2 acceptance catch: 39 profitable folds of 58
        broke grounding (counts past the 0–30 constant range weren't
        harvestable). The derived count must always be allowed."""
        from app.honesty.report import WalkForward, WalkForwardFold
        from app.honesty.verdict import allowed_numbers
        folds = [WalkForwardFold(start="2024-01-01", end="2024-03-01",
                                 ret=0.01 if i < 39 else -0.01, trades=20)
                 for i in range(58)]
        from app.honesty.report import (
            Coverage,
            Dsr,
            HonestyReport,
            MonteCarlo,
            OosSplit,
            RegimeSample,
            Sensitivity,
            Trust,
        )
        report = HonestyReport(
            oos=OosSplit(split_date="2024-06-01", is_sharpe=1.0, oos_sharpe=0.9,
                         is_return=0.1, oos_return=0.08, is_trades=20, oos_trades=10,
                         degradation=0.9, sign_flip=False, flagged=False),
            walk_forward=WalkForward(meaningful=True, test_sessions=42, folds=folds,
                                     consistency=39 / 58),
            monte_carlo=MonteCarlo(resamples=1000, block=5, seed=42, trades=30,
                                   terminal_p5=None, terminal_p50=None,
                                   terminal_p95=None, max_drawdown_p50=None,
                                   max_drawdown_p95=None, p_loss=None),
            sensitivity=Sensitivity(params=[], verdict=None),
            dsr=Dsr(trials=1, daily_sharpe=None, expected_max_sharpe=None, dsr=None),
            regime_sample=RegimeSample(trades=30, days_low_vix=10, days_mid_vix=10,
                                       days_high_vix=10, regimes_present=3,
                                       capped=False, cap_reason=None),
            coverage=Coverage(requested_start="2024-01-01", requested_end="2024-12-31",
                              effective_start="2024-01-01", effective_end="2024-12-31",
                              requested_sessions=250, chain_sessions=250,
                              coverage_ratio=1.0, materially_short=False, reason=None),
            trust=Trust(level=3, label="suggestive", survived={}, survived_count=3,
                        reasons=[]),
            metrics={},
            effective_start="2024-01-01", effective_end="2024-12-31", seed=42,
        )
        assert 39.0 in allowed_numbers(report)


class TestModeledCaveatGrounded:
    def test_verdict_discloses_modeled_share(self) -> None:
        from app.engine.market import build_fixture_store
        from app.honesty.gauntlet import run_gauntlet
        from app.honesty.verdict import allowed_numbers, template_verdict, validate_numbers

        slc = build_fixture_slice(
            "2025-01-06",
            quotes={"09:35": [_put(1.96, 2.04, None, "2025-01-07")]},
            underlying={"09:35": 100.0},
            quote_source="alpaca_modeled",
        )
        spec = StrategySpec.model_validate({
            "spec_version": 2,
            "meta": {"name": "modeled caveat", "description_raw": "fixture"},
            "underlying": {"ticker": "QQQ"},
            "position": {"structure": "short_put",
                "legs": [{"right": "put", "side": "short", "ratio": 1,
                          "strike_selection": {"method": "delta", "value": 0.50}}],
                "expiration_selection": {"target_dte": 1, "min_dte": 0, "max_dte": 2}},
            "entry": {"schedule": {"frequency": "daily"}, "conditions": [],
                      "max_concurrent_positions": 1},
            "exit": {"profit_target_pct": 90},
            "sizing": {"method": "fixed_contracts", "value": 1},
            "costs": {"commission_per_contract": 0.65,
                      "slippage_half_spread_fraction": 0.5},
            "backtest": {"start": None, "end": None, "initial_capital": 10_000,
                         "seed": 42, "clock": "5min"},
        })
        store = build_fixture_store(
            "QQQ", {}, {"2025-01-06": (100.0, 100.0), "2025-01-07": (100.0, 100.0)}
        )
        result = run_backtest(spec, store, FixtureIntraday({"2025-01-06": slc}))
        report = run_gauntlet(spec, store, result, trials=1,
                              intraday=FixtureIntraday({"2025-01-06": slc}))
        assert report.fill_sources.get("alpaca_modeled", 0) >= 1

        verdict = template_verdict(report)
        assert any("MODELED quotes" in c for c in verdict.caveats)
        joined = " ".join([verdict.headline, *verdict.evidence,
                           *verdict.breaks_where, *verdict.caveats])
        assert validate_numbers(joined, allowed_numbers(report)) == []
