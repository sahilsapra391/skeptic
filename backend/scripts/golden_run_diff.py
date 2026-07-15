"""Golden-run harness — the acceptance gate for result-identical work.

Some changes must provably not move a single number: the gauntlet
speed-ups (sweep dedup, the daily indicator cache) are pure reuse of
values the engine would have recomputed. "The suite is green" does not
prove that — the suite asserts properties, not the whole payload.

This script runs the FULL pipeline (engine + gauntlet, fixed seed) over a
few representative specs and dumps everything a user would ever see:
metrics, the entire honesty report, the equity curve, and the trade log.
Byte-compare the dumps across a change; any difference is a semantic
change, wanted or not.

    # baseline (a worktree/checkout at the merge-base)
    cd backend && PYTHONPATH=. uv run python scripts/golden_run_diff.py --out /tmp/golden_before
    # the branch
    cd backend && PYTHONPATH=. uv run python scripts/golden_run_diff.py --out /tmp/golden_after
    diff -r /tmp/golden_before /tmp/golden_after   # MUST be empty

Timings are deliberately absent from the dumps (they live on the run row,
not the report), so a faster run is byte-identical to a slower one.

The R2 credentials are stripped below: `data_confidence` reads the live
lake, and a network stage's answer is not a property of the engine — it
would make the gate flaky for reasons unrelated to the change under test.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any

# BEFORE any app import: the cross-source stage must report absence, not
# race the lake (see module docstring)
for _var in ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET"):
    os.environ.pop(_var, None)

from app.engine.market import MarketStore, build_fixture_slice  # noqa: E402
from app.engine.runner import run_backtest  # noqa: E402
from app.engine.types import RunResult  # noqa: E402
from app.honesty.gauntlet import run_gauntlet  # noqa: E402
from app.models.spec import StrategySpec  # noqa: E402
from tests.fixtures.synthetic_market import synthetic_store  # noqa: E402
from tests.test_five_min_clock import FixtureIntraday  # noqa: E402

FIXTURE = json.loads(
    (Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "overfit_strategy.json")
    .read_text()
)


def _spec(name: str, position: dict, entry: dict, exit_: dict, version: int = 1) -> dict:
    return {
        "spec_version": version,
        "meta": {"name": name, "description_raw": "golden run"},
        "underlying": {"ticker": "SPY"},
        "position": position,
        "entry": entry,
        "exit": exit_,
        "sizing": {"method": "fixed_contracts", "value": 1},
        "costs": {
            "commission_per_contract": 0.65,
            "slippage_half_spread_fraction": 0.5,
            "slippage_half_spread_fraction_sell": 0.5,
        },
        "backtest": {"start": None, "end": None, "initial_capital": 25000, "seed": 42},
    }


def _five_min_case() -> tuple[str, dict, MarketStore, int, FixtureIntraday]:
    """A 5-MINUTE run — the clock the other cases cannot reach.

    It is not here for coverage tidiness: the 5-min clock is the ONLY
    path where `_sweep_base_spec` bounds the window, which is exactly
    where `sensitivity` must SUPPRESS the base_result seed (the sweep
    cells run a different, shorter spec than the main backtest). It is
    also the only path that exercises the entry-time nudge's keying and
    `BarView.daily_series_pair`'s previous-session bound. Invert that
    seed guard and a daily-only gate stays green while every 5-min sweep
    inherits a full-history Sharpe into a windowed grid.
    """
    session, expiry = "2025-01-06", "2025-01-07"
    bars = [f"{9 + (i * 5) // 60:02d}:{(30 + i * 5) % 60:02d}" for i in range(12)]
    # a gentle intraday drift so RSI/SMA have something to say
    prices = [100.0 + (i % 5) * 0.25 - (i % 3) * 0.15 for i in range(len(bars))]
    slc = build_fixture_slice(
        session,
        quotes={
            b: [{"expiration": expiry, "right": "put", "strike": 100.0,
                 "bid": 1.00 + i * 0.02, "ask": 1.10 + i * 0.02, "delta": -0.45}]
            for i, b in enumerate(bars)
        },
        underlying=dict(zip(bars, prices, strict=True)),
    )
    store = build_fixture_store_5min(session)
    spec = {
        "spec_version": 2,
        "meta": {"name": "5min short put", "description_raw": "golden 5min"},
        "underlying": {"ticker": "SPY"},
        "position": {
            "structure": "short_put",
            "legs": [{"right": "put", "side": "short", "ratio": 1,
                      "strike_selection": {"method": "delta", "value": 0.45}}],
            "expiration_selection": {"target_dte": 1, "min_dte": 0, "max_dte": 2},
        },
        "entry": {"schedule": {"frequency": "daily", "time_of_day": "09:45"},
                  "conditions": [], "max_concurrent_positions": 1},
        "exit": {"profit_target_pct": 40, "stop_loss_pct": 80},
        "sizing": {"method": "fixed_contracts", "value": 1},
        "costs": {"commission_per_contract": 0.65,
                  "slippage_half_spread_fraction": 0.5,
                  "slippage_half_spread_fraction_sell": 0.5},
        "backtest": {"start": None, "end": session, "initial_capital": 25000,
                     "seed": 42, "clock": "5min"},
    }
    return ("five_min_short_put", spec, store, 1, FixtureIntraday({session: slc}))


def build_fixture_store_5min(session: str) -> MarketStore:
    from app.engine.market import build_fixture_store

    return build_fixture_store(
        "SPY", {}, {session: (100.0, 100.0), "2025-01-07": (100.0, 100.0)})


def _cases() -> list[tuple[str, dict, MarketStore, int]]:
    """(name, spec_json, store, trials). Chosen to cover the sweep families
    that the dedup and the indicator cache touch:

      overfit_short_put — the permanent trap fixture: delta + dte + PT + SL
        sweeps, three concurrent positions, judged on the merits.
      rsi_condition_short_put — entry AND exit conditions on the daily
        clock: the ONLY family whose cells hit the indicator prefix path
        (the O(n²) the cache removes) and the condition threshold sweeps.
      long_call_pt_sl — clean PT/SL decimals, so the base cells of both
        sweeps serialize byte-identically to the main run: the dedup's
        reuse path is exercised here, not merely available.

    The 5-min clock rides separately (`_five_min_case`) because it needs
    an intraday provider — see that function for why it is load-bearing.
    """
    cases: list[tuple[str, dict, MarketStore, int]] = []

    cases.append((
        "overfit_short_put",
        FIXTURE["spec"],
        synthetic_store(FIXTURE["data_seed"]),
        int(FIXTURE["trials"]),
    ))

    cases.append((
        "rsi_condition_short_put",
        _spec(
            "30d short put rsi in/out",
            {
                "structure": "short_put",
                "legs": [{"right": "put", "side": "short", "ratio": 1,
                          "strike_selection": {"method": "delta", "value": 0.30}}],
                "expiration_selection": {"target_dte": 14, "min_dte": 5, "max_dte": 30},
            },
            {
                "schedule": {"frequency": "signal_only"},
                "conditions": [
                    {"indicator": "rsi", "period": 14, "operator": "<", "value": 45},
                    {"indicator": "price_vs_sma_pct", "period": 20,
                     "operator": "<", "value": 2.0},
                ],
                "max_concurrent_positions": 2,
            },
            {
                "profit_target_pct": 50,
                "conditions": [{"indicator": "rsi", "period": 14,
                                "operator": ">", "value": 62}],
            },
            version=2,
        ),
        synthetic_store(seed=11, sessions=700),
        3,
    ))

    cases.append((
        "long_call_pt_sl",
        _spec(
            "50d long call pt+sl",
            {
                "structure": "long_call",
                "legs": [{"right": "call", "side": "long", "ratio": 1,
                          "strike_selection": {"method": "delta", "value": 0.50}}],
                "expiration_selection": {"target_dte": 28, "min_dte": 20, "max_dte": 40},
            },
            {"schedule": {"frequency": "weekly", "day_of_week": "wednesday"},
             "conditions": [], "max_concurrent_positions": 1},
            {"profit_target_pct": 100, "stop_loss_pct": 50},
        ),
        synthetic_store(seed=11, sessions=700),
        1,
    ))
    return cases


def _dump(result: RunResult, report: Any) -> dict[str, Any]:
    return {
        "metrics": result.metrics,
        "equity": result.equity,
        "dates": [d.isoformat() for d in result.dates],
        "filled": result.filled,
        "skipped": result.skipped,
        "skip_reasons": result.skip_reasons,
        "ruined": result.ruined,
        "ruin_date": result.ruin_date.isoformat() if result.ruin_date else None,
        "trades": [
            [t.day.isoformat(), t.action, t.detail, t.pl, t.reason,
             t.position_id, t.bar_time]
            for t in result.trades
        ],
        "honesty_report": report.model_dump(mode="json"),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, help="directory for the dumps")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    digests: dict[str, str] = {}
    runs: list[tuple[str, dict, MarketStore, int, Any]] = [
        (n, s, st, t, None) for n, s, st, t in _cases()
    ]
    runs.append(_five_min_case())
    for name, spec_json, store, trials, intraday in runs:
        spec = StrategySpec.model_validate(spec_json)
        result = run_backtest(spec, store, intraday)
        report = run_gauntlet(spec, store, result, trials=trials, intraday=intraday)
        blob = json.dumps(_dump(result, report), sort_keys=True, indent=1)
        (out / f"{name}.json").write_text(blob)
        digests[name] = hashlib.sha256(blob.encode()).hexdigest()
        print(f"{name}: {digests[name]}  ({len(result.equity)} sessions, "
              f"{result.filled} fills)")

    (out / "DIGESTS.txt").write_text(
        "\n".join(f"{k}  {v}" for k, v in sorted(digests.items())) + "\n"
    )
    print(f"\nwrote {len(digests)} dumps → {out}")


if __name__ == "__main__":
    main()
