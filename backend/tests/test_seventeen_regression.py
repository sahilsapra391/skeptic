"""Permanent regression guard for the seventeen-fills anomaly
(diagnostics/SEVENTEEN.md).

Two invariants, forever:
  1. NO hidden fill cap: a strategy over a 60-chain-date lake fills on every
     one of those dates — > 17, and exactly the analytically expected count.
     Any future cap (a stray [:N], LIMIT, max_fills, pagination truncation)
     fails this test.
  2. Fills TRACK the chain-date count: over a lake with few chain dates
     spread across a long session history, fills equal the chain-date count
     and the balance is `no_chain_data` — and the honesty layer refuses to
     bless it (coverage cap → insufficient_evidence).
"""

from __future__ import annotations

from copy import deepcopy
from datetime import date, timedelta

from app.engine.market import build_fixture_store
from app.engine.runner import run_backtest
from app.honesty.gauntlet import run_gauntlet
from app.models.spec import StrategySpec
from tests.fixtures.synthetic_market import synthetic_store


def _weekdays(start: date, n: int) -> list[date]:
    out: list[date] = []
    d = start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _dense_store(n_sessions: int):
    """n_sessions consecutive weekday chain dates, each quoting one put chain
    at a single far expiration E (after the whole window, so nothing settles
    mid-run). Spot pinned at 100; a clean grid with deltas."""
    sessions = _weekdays(date(2024, 1, 1), n_sessions)
    exp = (sessions[0] + timedelta(days=115)).isoformat()  # far, non-session
    strikes = [(90, -0.20), (95, -0.35), (100, -0.50), (105, -0.65), (110, -0.80)]
    chains: dict[str, list[dict[str, object]]] = {}
    underlying: dict[str, tuple[float, float]] = {}
    for d in sessions:
        underlying[d.isoformat()] = (100.0, 100.0)
        chains[d.isoformat()] = [
            {"expiration": exp, "right": "put", "strike": float(k),
             "bid": 1.00, "ask": 1.10, "delta": dl, "iv": 0.20}
            for k, dl in strikes
        ]
    return build_fixture_store("SPY", chains, underlying)


def _daily_short_put(max_concurrent: int = 10) -> dict:
    return {
        "spec_version": 1,
        "meta": {"name": "daily atm short put", "description_raw": "x"},
        "underlying": {"ticker": "SPY"},
        "position": {
            "structure": "short_put",
            "legs": [{"right": "put", "side": "short", "ratio": 1,
                      "strike_selection": {"method": "atm", "value": 0}}],
            "expiration_selection": {"target_dte": 45, "min_dte": 20, "max_dte": 120},
        },
        "entry": {"schedule": {"frequency": "daily"}, "conditions": [],
                  "max_concurrent_positions": max_concurrent},
        "exit": {"time_exit_dte": 108},  # closes ~next session → concurrency stays low
        "sizing": {"method": "fixed_contracts", "value": 1},
        "costs": {"commission_per_contract": 0.65, "slippage_half_spread_fraction": 0.5,
                                                   "slippage_half_spread_fraction_sell": 0.5},
        "backtest": {"start": None, "end": None, "initial_capital": 25_000, "seed": 42},
    }


def test_no_hidden_fill_cap_sixty_chain_dates() -> None:
    """60 chain dates, daily entry that never blocks on capacity → 60 fills.
    The load-bearing assertion is `> 17`: it fails loudly on any hidden cap."""
    store = _dense_store(60)
    result = run_backtest(StrategySpec.model_validate(_daily_short_put()), store)

    assert result.filled == 60, (
        f"expected one fill per chain date (60), got {result.filled} — a hidden "
        "cap or capacity block is truncating fills"
    )
    assert result.filled > 17, "SEVENTEEN regression: fills capped at/under 17"
    assert result.skipped == 0, (
        f"no session should skip in a fully-covered lake, got {result.skipped}: "
        f"{[t.reason for t in result.trades if t.action == 'SKIP'][:5]}"
    )


def test_fill_count_scales_with_chain_dates() -> None:
    """Same strategy, three lake sizes → fills == chain-date count each time.
    A constant fill count across sizes would be the bug."""
    counts = {}
    for n in (30, 45, 60):
        store = _dense_store(n)
        result = run_backtest(StrategySpec.model_validate(_daily_short_put()), store)
        counts[n] = result.filled
    assert counts == {30: 30, 45: 45, 60: 60}, counts


def _sparse_store(n_sessions: int, n_keep: int):
    store = deepcopy(synthetic_store(seed=11, sessions=n_sessions))
    step = max(1, len(store.chain_dates) // n_keep)
    keep = store.chain_dates[::step][:n_keep]
    store.chains = {d: store.chains[d] for d in keep}
    store.chain_dates = sorted(store.chains)
    store.atm_iv = {d: v for d, v in store.atm_iv.items() if d in store.chains}
    return store


def test_sparse_lake_fills_track_chain_dates_and_are_refused() -> None:
    """20 chain dates over 600 sessions: daily fills == 20, the rest are
    `no_chain_data`, and the honesty layer caps trust at insufficient_evidence
    with a coverage reason (the seventeen-fills disclosure)."""
    store = _sparse_store(n_sessions=600, n_keep=20)
    assert len(store.chain_dates) == 20

    result = run_backtest(StrategySpec.model_validate(_daily_short_put()), store)
    assert result.filled == 20, f"fills should equal the 20 chain dates, got {result.filled}"
    skip_reasons = {t.reason for t in result.trades if t.action == "SKIP"}
    assert skip_reasons == {"no_chain_data"}, skip_reasons
    assert result.skipped > result.filled

    report = run_gauntlet(
        StrategySpec.model_validate(_daily_short_put()), store, result, trials=1
    )
    cov = report.coverage
    assert cov.materially_short is True
    assert cov.chain_sessions == 20
    assert cov.requested_sessions == 600
    assert cov.coverage_ratio < 0.5
    assert report.trust.level is None
    assert report.trust.label == "insufficient_evidence"
    assert any("usable options chain" in r for r in report.trust.reasons), report.trust.reasons


def test_full_coverage_is_not_flagged_short() -> None:
    """The converse guard: a densely-covered synthetic run must NOT trip the
    coverage cap — otherwise the overfit fixture and every honest run break."""
    store = synthetic_store(seed=11, sessions=200)
    result = run_backtest(StrategySpec.model_validate(_daily_short_put()), store)
    report = run_gauntlet(
        StrategySpec.model_validate(_daily_short_put()), store, result, trials=1
    )
    assert report.coverage.materially_short is False
    assert report.coverage.coverage_ratio == 1.0
