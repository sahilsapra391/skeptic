"""Phase 0.0 reproduction: prove the fill count is gated by the number of
chain dates in the lake, not by strategy logic and not by any code cap.

    uv run --project backend python diagnostics/repro_seventeen.py

Runs three maximally-different specs (daily short put, weekly iron condor,
monthly covered call) against two synthetic lakes:
  A) DENSE: 60 sessions, chains on all 60  -> daily fills must be > 17
     (this alone refutes a hidden code cap at 17).
  B) SPARSE: 1600 sessions, chains on only 17 -> ~17 fills, ~1500 skips,
     every fill date a subset of the 17 chain dates (the constant is data).
"""
from __future__ import annotations

import sys
from collections import Counter
from copy import deepcopy
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.engine.runner import run_backtest  # noqa: E402
from app.models.spec import StrategySpec  # noqa: E402
from tests.fixtures.synthetic_market import synthetic_store  # noqa: E402

_COMMON = {
    "spec_version": 1,
    "underlying": {"ticker": "SPY"},
    "sizing": {"method": "fixed_contracts", "value": 1},
    "costs": {"commission_per_contract": 0.65, "slippage_half_spread_fraction": 0.5},
    "backtest": {"start": None, "end": None, "initial_capital": 100_000, "seed": 42},
    "exit": {"time_exit_dte": 2},
}
_EXP = {"target_dte": 28, "min_dte": 18, "max_dte": 40}


def _spec(name: str, structure: str, legs: list, sched: dict) -> dict:
    return {
        **_COMMON,
        "meta": {"name": name, "description_raw": "x"},
        "position": {"structure": structure, "legs": legs, "expiration_selection": _EXP},
        "entry": {"schedule": sched, "conditions": [], "max_concurrent_positions": 10},
    }


TRIO = {
    "daily_short_put": _spec(
        "daily 20d short put", "short_put",
        [{"right": "put", "side": "short", "ratio": 1,
          "strike_selection": {"method": "delta", "value": 0.20}}],
        {"frequency": "daily"},
    ),
    "weekly_iron_condor": _spec(
        "weekly 20d iron condor", "iron_condor",
        [{"right": "put", "side": "short", "ratio": 1,
          "strike_selection": {"method": "delta", "value": 0.20}},
         {"right": "put", "side": "long", "ratio": 1,
          "strike_selection": {"method": "width_from_leg", "value": 5, "reference_leg": 0}},
         {"right": "call", "side": "short", "ratio": 1,
          "strike_selection": {"method": "delta", "value": 0.20}},
         {"right": "call", "side": "long", "ratio": 1,
          "strike_selection": {"method": "width_from_leg", "value": 5, "reference_leg": 2}}],
        {"frequency": "weekly", "day_of_week": "monday"},
    ),
    "monthly_covered_call": _spec(
        "monthly 30d covered call", "covered_call",
        [{"right": "call", "side": "short", "ratio": 1,
          "strike_selection": {"method": "delta", "value": 0.30}}],
        {"frequency": "monthly", "day_of_month": 1},
    ),
}


def sparse_store(full, n_keep: int):
    store = deepcopy(full)
    dates = store.chain_dates
    step = max(1, len(dates) // n_keep)
    keep = dates[::step][:n_keep]
    store.chains = {d: store.chains[d] for d in keep}
    store.chain_dates = sorted(store.chains)
    store.atm_iv = {d: v for d, v in store.atm_iv.items() if d in store.chains}
    return store


def run_all(store, label: str) -> dict[str, list]:
    print(f"\n{'=' * 70}\n{label}: {len(store.sessions)} sessions, "
          f"{len(store.chain_dates)} chain dates "
          f"({store.chain_dates[0]} .. {store.chain_dates[-1]})\n{'=' * 70}")
    fills: dict[str, list] = {}
    for name, spec_json in TRIO.items():
        res = run_backtest(StrategySpec.model_validate(spec_json), store)
        skips = Counter(t.reason for t in res.trades if t.action == "SKIP")
        opens = sorted(t.day for t in res.trades if t.action == "OPEN")
        fills[name] = opens
        print(f"\n  {name}\n    filled={res.filled}  skipped={res.skipped}  "
              f"sessions_with_chain={res.sessions_with_chain}\n    skips={dict(skips)}")
    return fills


def main() -> None:
    dense_fills = run_all(synthetic_store(seed=11, sessions=60), "STORE A (DENSE)")
    n = len(dense_fills["daily_short_put"])
    print(f"\n  >>> DENSE daily fills = {n}  "
          f"({'PASS: > 17 — no hidden cap' if n > 17 else 'FAIL'})")

    sparse = sparse_store(synthetic_store(seed=11, sessions=1600), n_keep=17)
    sparse_fills = run_all(sparse, "STORE B (SPARSE: 17 chain dates)")
    chain_set = set(sparse.chain_dates)
    print("\n  >>> DECISIVE COMPARISON (Store B)")
    for name, opens in sparse_fills.items():
        print(f"    {name}: {len(opens)} fills, all within the 17 chain dates? "
              f"{set(opens) <= chain_set}")


if __name__ == "__main__":
    main()
