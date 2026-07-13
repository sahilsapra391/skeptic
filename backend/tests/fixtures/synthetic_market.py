"""Synthetic no-edge market + the deliberately-overfit strategy builder.

The market is a zero-drift geometric random walk (NO true edge exists by
construction) with Black-Scholes-priced chains and a VIX that cycles
through all three regime buckets. The optimizer sweeps 108 parameter
combinations against the FULL sample and keeps the best in-sample Sharpe —
textbook curve-fitting. The gauntlet must catch it, forever
(tests/test_overfit_fixture.py is in the permanent required set).

Run `uv run python -m tests.fixtures.synthetic_market` to (re)generate
tests/fixtures/overfit_strategy.json.
"""

from __future__ import annotations

import json
import math
from datetime import date, timedelta
from pathlib import Path

import numpy as np

from app.engine.market import MarketStore
from app.engine.types import ContractKey, Quote

FIXTURE_PATH = Path(__file__).parent / "overfit_strategy.json"

SESSIONS = 700
DAILY_VOL = 0.011
IV = 0.20
SPREAD_PCT = 0.04

_SQRT2 = math.sqrt(2.0)


def _ncdf(x: np.ndarray) -> np.ndarray:
    return 0.5 * (1.0 + np.vectorize(math.erf)(x / _SQRT2))


def _bs(spot: float, strikes: np.ndarray, dte_years: float, iv: float):
    """Black-Scholes prices + deltas at r=0. Returns (call_px, put_px, call_delta)."""
    t = max(dte_years, 1e-6)
    sig = iv * math.sqrt(t)
    d1 = (np.log(spot / strikes) + 0.5 * sig * sig) / sig
    d2 = d1 - sig
    nd1, nd2 = _ncdf(d1), _ncdf(d2)
    call = spot * nd1 - strikes * nd2
    put = call - spot + strikes  # parity at r=0
    return call, put, nd1


def _sessions(start: date, n: int) -> list[date]:
    out: list[date] = []
    d = start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def synthetic_store(seed: int = 11, sessions: int = SESSIONS) -> MarketStore:
    rng = np.random.RandomState(seed)
    days = _sessions(date(2020, 1, 6), sessions)

    rets = rng.normal(0.0, DAILY_VOL, size=sessions)
    closes = 100.0 * np.exp(np.cumsum(rets))
    opens = np.concatenate([[100.0], closes[:-1]])

    # VIX cycles through <15 / 15–20 / >20 so the regime guard is satisfied
    # and the overfit fixture is caught on the MERITS, not capped
    idx = np.arange(sessions)
    vix = 17.0 + 6.0 * np.sin(idx / 40.0) + rng.normal(0, 0.8, size=sessions)

    session_index = {d: i for i, d in enumerate(days)}
    chains: dict[date, dict[ContractKey, Quote]] = {}
    for i, d in enumerate(days):
        spot = float(closes[i])
        per: dict[ContractKey, Quote] = {}
        for target in (14, 28, 45):
            # snap expiration to an actual session so settlement fires
            j = i + int(round(target * 5 / 7))
            if j >= sessions:
                continue
            exp = days[j]
            dte_years = (exp - d).days / 365.0
            strikes = np.round(spot * np.arange(0.85, 1.155, 0.01), 2)
            call_px, put_px, call_delta = _bs(spot, strikes, dte_years, IV)
            for k, cp, pp, cd in zip(strikes, call_px, put_px, call_delta, strict=True):
                half = max(float(cp) * SPREAD_PCT, 0.02)
                if cp > 0.03:
                    per[ContractKey(expiration=exp, right="call", strike=float(k))] = Quote(
                        bid=round(max(float(cp) - half, 0.01), 2),
                        ask=round(float(cp) + half, 2),
                        delta=round(float(cd), 4),
                        iv=IV,
                    )
                halfp = max(float(pp) * SPREAD_PCT, 0.02)
                if pp > 0.03:
                    per[ContractKey(expiration=exp, right="put", strike=float(k))] = Quote(
                        bid=round(max(float(pp) - halfp, 0.01), 2),
                        ask=round(float(pp) + halfp, 2),
                        delta=round(float(cd) - 1.0, 4),
                        iv=IV,
                    )
        chains[d] = per

    _ = session_index
    return MarketStore(
        ticker="SPY",
        sessions=days,
        underlying_open={d: float(o) for d, o in zip(days, opens, strict=True)},
        underlying_close={d: float(c) for d, c in zip(days, closes, strict=True)},
        chains=chains,
        chain_dates=list(days),
        vix_dates=list(days),
        vix_close={d: float(v) for d, v in zip(days, vix, strict=True)},
    )


def _spec(delta: float, dte: int, profit: float, stop: float) -> dict:
    return {
        "spec_version": 1,
        "meta": {
            "name": f"overfit SPY {int(delta * 100)}d {dte}dte {int(profit)}pt {int(stop)}sl",
            "description_raw": "deliberately overfit fixture — tuned on the full sample",
        },
        "underlying": {"ticker": "SPY"},
        "position": {
            "structure": "short_put",
            "legs": [
                {"right": "put", "side": "short", "ratio": 1,
                 "strike_selection": {"method": "delta", "value": delta}}
            ],
            "expiration_selection": {
                "target_dte": dte, "min_dte": max(1, dte - 8), "max_dte": dte + 12,
            },
        },
        "entry": {"schedule": {"frequency": "weekly", "day_of_week": "monday"},
                  "conditions": [], "max_concurrent_positions": 3},
        "exit": {"profit_target_pct": profit, "stop_loss_pct": stop},
        "sizing": {"method": "fixed_contracts", "value": 1},
        "costs": {"commission_per_contract": 0.65, "slippage_half_spread_fraction": 0.5,
                                                   "slippage_half_spread_fraction_sell": 0.5},
        "backtest": {"start": None, "end": None, "initial_capital": 25_000, "seed": 42},
    }


GRID = {
    "delta": [0.15, 0.20, 0.25, 0.30, 0.35, 0.40],
    "dte": [14, 28, 45],
    "profit": [25.0, 50.0, 75.0],
    "stop": [100.0, 200.0],
}
N_TRIALS = (
    len(GRID["delta"]) * len(GRID["dte"]) * len(GRID["profit"]) * len(GRID["stop"])
)  # 108


def optimize_on_full_sample(seed: int) -> tuple[dict, float]:
    """The sin itself: pick the params with the best FULL-SAMPLE Sharpe on
    data that has no edge. Returns (spec_json, in_sample_sharpe)."""
    from app.engine.runner import run_backtest
    from app.models.spec import StrategySpec

    store = synthetic_store(seed)
    best_spec: dict | None = None
    best_sharpe = -math.inf
    for delta in GRID["delta"]:
        for dte in GRID["dte"]:
            for profit in GRID["profit"]:
                for stop in GRID["stop"]:
                    spec_json = _spec(delta, dte, profit, stop)
                    result = run_backtest(StrategySpec.model_validate(spec_json), store)
                    sharpe = result.metrics.get("sharpe")
                    if sharpe is not None and sharpe > best_sharpe:
                        best_sharpe = sharpe
                        best_spec = spec_json
    assert best_spec is not None
    return best_spec, best_sharpe


def build_fixture() -> dict:
    """Search data seeds until the optimizer's pick is (a) not sample-capped
    — so the gauntlet judges it on the merits — and (b) caught: trust ≤ 2
    with the OOS degradation flagged. The mechanism is fully real; the seed
    is pinned so the required test is deterministic forever."""
    from app.engine.runner import run_backtest
    from app.honesty.gauntlet import run_gauntlet
    from app.models.spec import StrategySpec

    for seed in range(1, 40):
        spec_json, is_sharpe = optimize_on_full_sample(seed)
        if is_sharpe <= 0.5:
            continue  # not a seductive backtest; keep looking
        store = synthetic_store(seed)
        spec = StrategySpec.model_validate(spec_json)
        result = run_backtest(spec, store)
        report = run_gauntlet(spec, store, result, trials=N_TRIALS)
        if report.regime_sample.capped:
            continue
        caught = (report.trust.level or 0) <= 2 and report.oos.flagged
        if caught:
            return {
                "spec": spec_json,
                "trials": N_TRIALS,
                "data_seed": seed,
                "in_sample_sharpe": round(is_sharpe, 4),
                "note": (
                    "parameters tuned on the FULL zero-edge sample by "
                    "tests/fixtures/synthetic_market.py — the gauntlet must always catch this"
                ),
            }
    raise RuntimeError("no seed produced a seductive-but-catchable overfit — widen the search")


if __name__ == "__main__":
    fixture = build_fixture()
    FIXTURE_PATH.write_text(json.dumps(fixture, indent=2))
    print(f"wrote {FIXTURE_PATH}")
    print(f"seed={fixture['data_seed']} in-sample sharpe={fixture['in_sample_sharpe']}")
