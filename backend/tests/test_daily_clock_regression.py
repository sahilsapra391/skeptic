"""Daily-clock bit-identical regression — owner amendment 5 (D2b).

Three diverse pinned runs; their sha256 digests were generated on the
PRE-D2b engine (branch point = merged D1, engine identical to main) and may
never change while clock="daily":

  1. assignment + stock-unwind path   (50Δ short put, hold to expiry)
  2. long-option settlement path      (50Δ long call, PT + time exit)
  3. condition-exit + delta-stop path (RSI entries/exits on a persistent
     integer strike grid — the synthetic walk regenerates spot-relative
     strikes daily, so aged contracts never re-quote there and early closes
     can only fill on a grid that persists)

If any digest moves, the declared-clock work changed daily results —
that is a failing build, not a fixture to regenerate.
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import date, timedelta

from app.engine.market import build_fixture_store
from app.engine.runner import run_backtest
from app.models.spec import StrategySpec
from tests.fixtures.synthetic_market import synthetic_store


def _digest(spec_json: dict, store) -> str:
    result = run_backtest(StrategySpec.model_validate(spec_json), store)
    blob = json.dumps({
        "equity": result.equity,
        "trades": [(t.day.isoformat(), t.action, t.detail, t.pl, t.reason)
                   for t in result.trades],
    }, sort_keys=True).encode()
    return hashlib.sha256(blob).hexdigest()


def _spec(name: str, position: dict, entry: dict, exit_: dict, version: int = 1) -> dict:
    return {
        "spec_version": version,
        "meta": {"name": name, "description_raw": "d2b regression pin"},
        "underlying": {"ticker": "SPY"},
        "position": position, "entry": entry, "exit": exit_,
        "sizing": {"method": "fixed_contracts", "value": 1},
        "costs": {"commission_per_contract": 0.65, "slippage_half_spread_fraction": 0.5},
        "backtest": {"start": None, "end": None, "initial_capital": 25000, "seed": 42},
    }


def test_pin_assignment_and_stock_unwind() -> None:
    spec = _spec(
        "50d short put hold to expiry",
        {"structure": "short_put",
         "legs": [{"right": "put", "side": "short", "ratio": 1,
                   "strike_selection": {"method": "delta", "value": 0.50}}],
         "expiration_selection": {"target_dte": 14, "min_dte": 6, "max_dte": 26}},
        {"schedule": {"frequency": "weekly", "day_of_week": "monday"},
         "conditions": [], "max_concurrent_positions": 2},
        {"time_exit_dte": 0},
    )
    assert _digest(spec, synthetic_store(seed=11, sessions=300)) == (
        "8ddb722e53db0325c943e5d66f324b49f834babda85d19f3049aa6d7b162bf23"
    )


def test_pin_long_option_settlement() -> None:
    spec = _spec(
        "50d long call pt+time",
        {"structure": "long_call",
         "legs": [{"right": "call", "side": "long", "ratio": 1,
                   "strike_selection": {"method": "delta", "value": 0.50}}],
         "expiration_selection": {"target_dte": 28, "min_dte": 20, "max_dte": 40}},
        {"schedule": {"frequency": "weekly", "day_of_week": "wednesday"},
         "conditions": [], "max_concurrent_positions": 1},
        {"profit_target_pct": 100, "time_exit_dte": 5},
    )
    assert _digest(spec, synthetic_store(seed=11, sessions=300)) == (
        "e8c32d137af122be01d2164998e0077fb4a0a4a20ef60f8dcc94c18932f7d6bf"
    )


# ---------------------------------------------------------------- third run

def _grid_store():
    """Deterministic store with a PERSISTENT integer strike grid so early
    closes can fill. Spot follows a sine wave (no RNG); puts priced with a
    simple internally-consistent model: intrinsic + time value shrinking
    with DTE; delta a clamped linear function of moneyness."""
    start = date(2024, 1, 1)
    sessions: list[date] = []
    d = start
    while len(sessions) < 120:
        if d.weekday() < 5:
            sessions.append(d)
        d += timedelta(days=1)

    def spot_at(i: int) -> float:
        return 100.0 + 6.0 * math.sin(i / 7.0) + i * 0.02

    # weekly Friday expirations
    expiries = [s for s in sessions if s.weekday() == 4]

    chains: dict[str, list[dict[str, object]]] = {}
    underlying: dict[str, tuple[float, float]] = {}
    for i, s in enumerate(sessions):
        spot = round(spot_at(i), 2)
        underlying[s.isoformat()] = (spot, spot)
        rows: list[dict[str, object]] = []
        for exp in expiries:
            dte = (exp - s).days
            if dte < 1 or dte > 30:
                continue
            for strike in range(85, 116):
                intrinsic = max(strike - spot, 0.0)
                time_value = 0.18 * math.sqrt(dte) * math.exp(-abs(strike - spot) / 6.0)
                mid = intrinsic + time_value
                if mid < 0.05:
                    continue
                delta = max(0.02, min(0.98, 0.5 + (strike - spot) / 12.0))
                rows.append({
                    "expiration": exp.isoformat(), "right": "put",
                    "strike": float(strike),
                    "bid": round(mid - 0.05, 2), "ask": round(mid + 0.05, 2),
                    "delta": round(-delta, 4), "iv": 0.2,
                })
        chains[s.isoformat()] = rows
    return build_fixture_store("SPY", chains, underlying)


def test_pin_condition_and_delta_stop_exits() -> None:
    spec = _spec(
        "30d short put rsi entries, rsi/delta-stop exits",
        {"structure": "short_put",
         "legs": [{"right": "put", "side": "short", "ratio": 1,
                   "strike_selection": {"method": "delta", "value": 0.30}}],
         "expiration_selection": {"target_dte": 14, "min_dte": 5, "max_dte": 30}},
        {"schedule": {"frequency": "signal_only"},
         "conditions": [{"indicator": "rsi", "period": 14, "operator": "<", "value": 48}],
         "max_concurrent_positions": 2},
        {"delta_stop_abs": 0.55,
         "conditions": [{"indicator": "rsi", "period": 14, "operator": ">", "value": 60}]},
        version=2,
    )
    store = _grid_store()
    result = run_backtest(StrategySpec.model_validate(spec), store)
    close_reasons = {t.reason for t in result.trades if t.action == "CLOSE"}
    # the run must actually EXERCISE the early-close paths it pins
    assert "condition_exit" in close_reasons or "delta_stop" in close_reasons, (
        f"third regression run stopped exercising early closes: {close_reasons}"
    )
    assert _digest(spec, store) == (
        "9f662084d555f731132a39d91b79f49c6fa7483613ce9e26979c7a94f334733b"
    )
