"""The wall-clock budget on the in-house flow step (2026-09-04 outage).

The step reads ~405 recorder snapshots per session as sequential R2 GETs and
logged nothing until the whole loop finished. On 2026-09-04 a transient R2
stall inside that loop went silent for 39 minutes, consumed the collect-eod
unit's entire 2700s TimeoutStartSec wall, and the SIGKILL took the last four
steps of the chain (cross-source validation, fill calibration, coverage
ledger) down with it. Healthchecks only saw it because the unit's
ExecStopPost= hook survives a SIGKILL.

These pin the bound: past its budget the step stops, banks what it derived,
and exits NON-ZERO so the chain marks one step red and keeps going. A budget
that let main() return 0 would be worse than none at all, because the tile
would stay green over sessions that were never derived.
"""

from __future__ import annotations

import sys
import time

import pytest

import derive_flow_inhouse as flow


@pytest.fixture(autouse=True)
def _clear_budget():
    flow._start_budget(0)
    yield
    flow._start_budget(0)


def test_budget_zero_never_fires() -> None:
    """0 disables the bound, for an operator draining a backlog by hand."""
    flow._start_budget(0)
    flow._check_budget()  # must not raise


def _expire() -> None:
    """Force the deadline into the past. NOT _start_budget(-1): a negative
    budget disables the bound (same contract as 0), so using it here would
    make every assertion below pass for the wrong reason."""
    flow._deadline = time.monotonic() - 1


def test_negative_budget_disables_rather_than_expiring() -> None:
    flow._start_budget(-1)
    flow._check_budget()  # must not raise


def test_expired_budget_raises() -> None:
    _expire()
    with pytest.raises(flow._BudgetExhausted):
        flow._check_budget()


def _stub_lake(monkeypatch: pytest.MonkeyPatch, written: list) -> None:
    """Every R2 edge run() touches, with no session already banked."""
    monkeypatch.setattr(flow, "r2_client", lambda: object())
    monkeypatch.setattr(flow, "r2_get_parquet", lambda *a, **k: None)
    monkeypatch.setattr(flow, "r2_put_parquet",
                        lambda s3, key, df: written.append((key, len(df))))


def test_run_banks_completed_sessions_before_unwinding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A budget abort must not throw away sessions already computed: they are
    whole rows, deduped by date on write, so re-deriving them next run is
    pure waste."""
    written: list = []
    _stub_lake(monkeypatch, written)
    monkeypatch.setattr(
        flow, "_sessions",
        lambda s3, prefix: ["2026-09-01", "2026-09-02", "2026-09-03"])

    seen: list[str] = []

    def fake_derive(s3, ticker, d, has_tape):
        seen.append(d)
        if len(seen) == 2:
            _expire()   # the stall lands during the second session
        return {"net_premium": float(len(seen))}

    monkeypatch.setattr(flow, "derive_session", fake_derive)
    flow._start_budget(600)

    with pytest.raises(flow._BudgetExhausted):
        flow.run(object(), "SPY")

    # The check sits at the TOP of each iteration, so the session that was
    # already in flight finishes and the third never starts.
    assert seen == ["2026-09-01", "2026-09-02"]
    assert written, "the completed sessions were dropped instead of checkpointed"
    assert written[-1][1] == 2, "checkpoint lost a derived row"


def test_a_budget_that_expires_on_the_last_session_still_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nothing is left to do, so there is nothing to report as failed. Raising
    here would turn a complete night red for no reason."""
    written: list = []
    _stub_lake(monkeypatch, written)
    monkeypatch.setattr(flow, "_sessions", lambda s3, prefix: ["2026-09-01"])

    def fake_derive(s3, ticker, d, has_tape):
        _expire()
        return {"net_premium": 1.0}

    monkeypatch.setattr(flow, "derive_session", fake_derive)
    flow._start_budget(600)

    assert flow.run(object(), "SPY") == 1
    assert written[-1][1] == 1


def test_main_exits_non_zero_when_the_budget_runs_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole point of the bound: ONE red step the chain reports and walks
    past, instead of a SIGKILL that deletes the steps behind it."""
    monkeypatch.setattr(flow, "r2_client", lambda: object())

    def boom(s3, ticker):
        raise flow._BudgetExhausted("wall-clock budget exhausted")

    monkeypatch.setattr(flow, "run", boom)
    monkeypatch.setattr(sys, "argv",
                        ["derive_flow_inhouse.py", "--budget-seconds", "1"])
    assert flow.main() == 1


def test_main_returns_zero_on_a_healthy_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(flow, "r2_client", lambda: object())
    monkeypatch.setattr(flow, "run", lambda s3, ticker: 1)
    monkeypatch.setattr(sys, "argv", ["derive_flow_inhouse.py"])
    assert flow.main() == 0


def test_snapshot_loop_checks_the_budget_between_reads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The 405-GET loop is where the 39-minute stall actually lived, so the
    check has to sit INSIDE it, not only between sessions."""
    import pandas as pd

    bars = pd.DataFrame({
        "minute_ts": pd.to_datetime(["2026-09-03T14:00:00Z"]),
        "expiration": ["2026-09-19"], "right": ["c"], "strike": [500.0],
        "vwap": [1.5], "volume": [10],
    })
    monkeypatch.setattr(flow, "r2_get_parquet", lambda *a, **k: bars.copy())
    monkeypatch.setattr(flow, "_snap_keys",
                        lambda s3, t, d: [f"snap_{i}.parquet" for i in range(50)])

    reads = {"n": 0}

    def fake_read(s3, key, tier):
        reads["n"] += 1
        if reads["n"] == 3:
            _expire()   # R2 goes slow partway through the loop
        return None

    monkeypatch.setattr(flow, "_read_snap", fake_read)

    flow._start_budget(600)
    with pytest.raises(flow._BudgetExhausted):
        flow.derive_session(object(), "SPY", "2026-09-03", has_tape=False)

    assert reads["n"] < 50, "the loop ran to completion despite the budget"
