"""FX.2 — continuous opportunity scanning (spec v4 entry.intraday_scan).

Hand-computed fixtures. Entry math convention: short put K=100 quoted
2.00/2.10 → SELL = 2.05 − 0.5×0.05 = 2.025 → cash +202.50 − 0.65 = +201.85.

Signal: price_vs_sma_pct(period 2, 5min) > 0. The sampled series drives it,
so with underlying 100 → 101 → 102 the first evaluable TRUE lands at 09:40
(the 09:35 pct series has one non-NaN value; the evaluator needs two).
Owner-decided semantics pinned here:
  * one entry per EPISODE (false→true edge) — a persistent signal never
    bursts; a reset-and-refire is a second setup;
  * episodes at the concurrency cap are consumed (skip counted), never
    re-armed on the same dip;
  * re-entry after an intraday exit frees the slot;
  * ARMED orders: an edge at a quote-less bar fills at the NEXT quoted
    bar's real NBBO even if the signal faded (one-quoted-bar validity, a
    submitted order can't be recalled), both bars disclosed;
  * condition-less strategies cycle: the position LIFECYCLE is the episode
    (arm at window start, re-arm when a position closes);
  * every skip counted (skip_reasons distribution), log stays deduped;
  * absent ≡ once_per_session ≡ pre-FX.2 behavior, bit-identical.
"""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from app.engine.market import SessionSlice, build_fixture_slice, build_fixture_store
from app.engine.runner import run_backtest
from app.models.spec import StrategySpec


class ScanFixtureIntraday:
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

    def minute_sessions(self) -> set[date]:
        return set()

    def minute_slice_for(self, session: date) -> SessionSlice | None:
        return None


def _put(bid: float, ask: float, expiration: str) -> dict:
    return {"expiration": expiration, "right": "put", "strike": 100.0,
            "bid": bid, "ask": ask, "delta": -0.50, "iv": 0.2}


SESSION = "2025-01-06"
EXP = "2025-01-07"
UNDERLYING_STORE = {"2025-01-06": (100.0, 105.0), "2025-01-07": (105.0, 105.5)}
SIGNAL_COND = [{"indicator": "price_vs_sma_pct", "operator": ">", "value": 0,
                "period": 2, "timeframe": "5min"}]

# underlying series: rising (edge at 09:40) → crash at 09:50 (signal off)
# → recovery at 09:55 (second edge). pct(2) hand-computed per docstring.
RISE_RESET_RISE = {"09:30": 100.0, "09:35": 101.0, "09:40": 102.0,
                   "09:45": 103.0, "09:50": 85.0, "09:55": 104.0,
                   "10:00": 105.0}


def _spec(exit_rules: dict, *, scan: str | None = "every_setup",
          spec_version: int = 4, cap: int = 3,
          conditions: list | None = SIGNAL_COND, clock: str = "5min",
          scale_in: dict | None = None,
          target_dte: int = 1, max_dte: int = 2) -> StrategySpec:
    entry: dict = {"schedule": {"frequency": "daily"},
                   "conditions": conditions or [],
                   "max_concurrent_positions": cap}
    if scan is not None:
        entry["intraday_scan"] = scan
    if scale_in is not None:
        entry["scale_in"] = scale_in
    return StrategySpec.model_validate({
        "spec_version": spec_version,
        "meta": {"name": "scan fixture", "description_raw": "fixture"},
        "underlying": {"ticker": "SPY"},
        "position": {
            "structure": "short_put",
            "legs": [{"right": "put", "side": "short", "ratio": 1,
                      "strike_selection": {"method": "delta", "value": 0.50}}],
            "expiration_selection": {"target_dte": target_dte, "min_dte": 0,
                                     "max_dte": max_dte},
        },
        "entry": entry,
        "exit": exit_rules,
        "sizing": {"method": "fixed_contracts", "value": 1},
        "costs": {"commission_per_contract": 0.65,
                  "slippage_half_spread_fraction": 0.5},
        "backtest": {"start": None, "end": SESSION, "initial_capital": 10_000,
                     "seed": 42, "clock": clock},
    })


def _slice(quotes: dict[str, list[dict]] | None = None,
           underlying: dict[str, float] | None = None) -> SessionSlice:
    und = underlying if underlying is not None else RISE_RESET_RISE
    q = quotes if quotes is not None else {
        hhmm: [_put(2.00, 2.10, EXP)] for hhmm in und
    }
    return build_fixture_slice(SESSION, quotes=q, underlying=und)


def _run(spec: StrategySpec, slc: SessionSlice):
    store = build_fixture_store("SPY", {}, UNDERLYING_STORE)
    return run_backtest(spec, store, ScanFixtureIntraday({SESSION: slc}))


def _opens(result) -> list:  # type: ignore[no-untyped-def]
    return [t for t in result.trades if t.action == "OPEN"]


class TestEpisodes:
    def test_persistent_signal_is_one_setup(self) -> None:
        # signal true 09:40 → 09:45 continuously: exactly ONE entry (09:40),
        # despite cap 3 — a persistent signal never bursts
        und = {k: RISE_RESET_RISE[k] for k in
               ("09:30", "09:35", "09:40", "09:45")}
        result = _run(_spec({"profit_target_pct": 500}), _slice(underlying=und))
        opens = _opens(result)
        assert len(opens) == 1 and opens[0].bar_time == "09:40"
        assert result.filled == 1

    def test_reset_and_refire_is_a_second_setup(self) -> None:
        result = _run(_spec({"profit_target_pct": 500}), _slice())
        opens = _opens(result)
        assert [o.bar_time for o in opens] == ["09:40", "09:55"]
        assert result.filled == 2
        # two independent credits: 2 × (+201.85) over 10,000 minus marks —
        # the OPEN details pin the fill price (2.025 each)
        assert all("2.02" in o.detail for o in opens)

    def test_episode_at_cap_is_consumed_not_queued(self) -> None:
        result = _run(_spec({"profit_target_pct": 500}, cap=1), _slice())
        opens = _opens(result)
        assert [o.bar_time for o in opens] == ["09:40"]  # second setup capped
        assert result.skip_reasons.get("max_concurrent") == 1
        # signal persists at 10:00 — the consumed episode never re-arms
        assert result.filled == 1

    def test_reentry_after_intraday_exit(self) -> None:
        # PT 25%: entry 09:40 at 2.025; 09:45 quote 1.40/1.50 → btc
        # 1.45 + 0.025 = 1.475 → profit 27.2% ≥ 25 → close at 09:45
        # (exits evaluate from the bar AFTER entry). pl = 201.85 − 148.15
        # = +53.70. The 09:55 setup re-enters the freed slot.
        quotes = {hhmm: [_put(2.00, 2.10, EXP)] for hhmm in RISE_RESET_RISE}
        quotes["09:45"] = [_put(1.40, 1.50, EXP)]
        result = _run(_spec({"profit_target_pct": 25}, cap=1), _slice(quotes=quotes))
        opens = _opens(result)
        closes = [t for t in result.trades if t.action == "CLOSE"]
        assert [o.bar_time for o in opens] == ["09:40", "09:55"]
        assert len(closes) >= 1 and closes[0].reason == "profit_target"
        assert closes[0].bar_time == "09:45"
        assert closes[0].pl == pytest.approx(53.70, abs=0.005)


class TestArmedOrders:
    def test_edge_at_quote_gap_fills_next_quoted_bar_even_if_faded(self) -> None:
        # the signal edge lands at 09:40 — a bar whose chain is EMPTY (a
        # real iVol stamp gap). The armed order waits and FILLS at 09:45's
        # real quote (3.00/3.10 → credit 3.025 → +301.85) even though the
        # signal has faded (underlying crashed to 85) — a submitted order
        # can't be recalled. Both bars disclosed.
        und = {"09:30": 100.0, "09:35": 101.0, "09:40": 102.0, "09:45": 85.0}
        quotes = {"09:30": [_put(2.00, 2.10, EXP)],
                  "09:35": [_put(2.00, 2.10, EXP)],
                  # 09:40 deliberately missing — the quote gap
                  "09:45": [_put(3.00, 3.10, EXP)]}
        result = _run(_spec({"profit_target_pct": 500}), _slice(quotes, und))
        opens = _opens(result)
        assert len(opens) == 1
        assert opens[0].bar_time == "09:45"
        assert "armed 09:40" in opens[0].detail
        assert "3.02" in opens[0].detail  # filled at 09:45's quote, not 09:40's
        assert result.skip_reasons.get("no_quote_this_bar") == 1

    def test_armed_order_dies_at_session_flat(self) -> None:
        # an edge just before close_at_time never fills — the flatten bar
        # mints nothing and the armed order dies with the session
        und = {"09:30": 100.0, "09:35": 101.0, "09:40": 102.0, "09:45": 103.0}
        quotes = {"09:30": [_put(2.00, 2.10, EXP)],
                  "09:35": [_put(2.00, 2.10, EXP)],
                  # 09:40 gap arms; 09:45 is at/after the flatten time
                  "09:45": [_put(2.00, 2.10, EXP)]}
        spec = _spec({"profit_target_pct": 500, "close_at_time": "09:45"})
        result = _run(spec, _slice(quotes, und))
        assert result.filled == 0


class TestUnconditionalCycling:
    def test_lifecycle_rearm_after_exit(self) -> None:
        # no conditions: enter at the window start (09:30, credit 2.025);
        # 09:40 quote 1.40/1.50 hits PT 25% → close (+53.70) and the SAME
        # bar re-arms + re-enters at 1.425 credit (+141.85) — the
        # always-in-the-market premium seller cycles all day
        und = {"09:30": 100.0, "09:35": 100.5, "09:40": 100.5, "09:45": 100.5}
        quotes = {"09:30": [_put(2.00, 2.10, EXP)],
                  "09:35": [_put(2.00, 2.10, EXP)],
                  "09:40": [_put(1.40, 1.50, EXP)],
                  "09:45": [_put(1.40, 1.50, EXP)]}
        result = _run(_spec({"profit_target_pct": 25}, cap=1, conditions=[]),
                      _slice(quotes, und))
        opens = _opens(result)
        closes = [t for t in result.trades if t.action == "CLOSE"]
        assert [o.bar_time for o in opens] == ["09:30", "09:40"]
        assert closes[0].reason == "profit_target"
        assert closes[0].bar_time == "09:40"
        assert closes[0].pl == pytest.approx(53.70, abs=0.005)
        assert "1.42" in opens[1].detail  # re-entry at the NEW quote


class TestBitIdentity:
    def test_absent_equals_once_per_session(self) -> None:
        slc = _slice()
        a = _run(_spec({"profit_target_pct": 500}, scan=None, spec_version=2),
                 slc)
        b = _run(_spec({"profit_target_pct": 500}, scan="once_per_session"),
                 slc)
        assert a.equity == b.equity
        assert [(t.day, t.action, t.detail) for t in a.trades] == \
               [(t.day, t.action, t.detail) for t in b.trades]
        # once_per_session: the run stops at the first fill of the session
        assert a.filled == b.filled == 1


class TestSpecValidation:
    def test_scan_on_v3_is_loud(self) -> None:
        with pytest.raises(ValidationError, match="cannot use v4 vocabulary"):
            _spec({"profit_target_pct": 50}, spec_version=3)

    def test_scan_requires_intraday_clock(self) -> None:
        with pytest.raises(ValidationError, match='requires clock "5min"'):
            _spec({"profit_target_pct": 50}, clock="daily", target_dte=30,
                  max_dte=45)

    def test_scan_refuses_scale_in(self) -> None:
        with pytest.raises(ValidationError, match="scale_in"):
            _spec({"profit_target_pct": 50}, conditions=[], scale_in={
                "rungs": [{"condition": {"indicator": "rsi", "operator": "<",
                                         "value": 30, "timeframe": "5min"},
                           "add_contracts": 1}],
                "max_total_contracts": 2,
            })

    def test_spec_v4_scan_matches_json_schema(self) -> None:
        import json
        from pathlib import Path

        schema_path = (Path(__file__).resolve().parents[2]
                       / "docs" / "strategy-spec.schema.json")
        schema = json.loads(schema_path.read_text())
        try:
            import jsonschema
        except ImportError:
            pytest.skip("jsonschema not installed")
        raw = _spec({"profit_target_pct": 50}).model_dump(mode="json",
                                                          exclude_none=True)
        jsonschema.validate(raw, schema)
