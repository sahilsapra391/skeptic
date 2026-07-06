"""Regression pins for the 2026-07-06 production incident: a full-history
5-min run (SPY 1DTE long call, RSI<30, no date window ⇒ ~2,978 sessions)
OOM-killed the Railway box and left the run spinning as 'running' forever.

Two mechanisms, two pins:
1. BarView.intraday_closes_upto copied the ENTIRE rolling prefix on every
   bar — O(bars²) time and multi-MB-per-bar allocator churn. The view must
   return AT MOST the trailing INTRADAY_LOOKBACK_BARS values.
2. A run left queued/running by a dead process must be swept to an honest
   error at boot — background tasks never survive the process.
"""

from __future__ import annotations

import json

from app.engine.conditions import INTRADAY_LOOKBACK_BARS
from app.engine.engine import BarView, MarketView
from app.engine.market import build_fixture_store
from tests.fixtures.engine import fx_short_put_assigned as fx


class TestBoundedIntradayCloses:
    def _bar_view(self, lasts: list[float], lasts_len: int) -> BarView:
        store = build_fixture_store("SPY", fx.CHAINS, fx.UNDERLYING)
        prev = MarketView(store, store.sessions[0])

        class _IView:
            session = store.sessions[0]
            quote_source = "ivol_5min"

            def chain(self) -> dict:
                return {}

            def quote_at(self, key: object) -> None:
                return None

            def underlying_last(self) -> float | None:
                return None

        return BarView(_IView(), prev, intraday_lasts=lasts, lasts_len=lasts_len)

    def test_returns_at_most_the_lookback_window(self) -> None:
        n = INTRADAY_LOOKBACK_BARS * 3  # a long run's accumulated lasts
        lasts = [float(i) for i in range(n)]
        got = self._bar_view(lasts, lasts_len=n).intraday_closes_upto()
        assert len(got) == INTRADAY_LOOKBACK_BARS  # never the whole prefix
        assert got == lasts[n - INTRADAY_LOOKBACK_BARS : n]  # the TRAILING window

    def test_short_history_and_snapshot_bound(self) -> None:
        lasts = [1.0, 2.0, 3.0, 4.0]
        # lasts_len snapshots the current bar — later appends invisible
        got = self._bar_view(lasts, lasts_len=2).intraday_closes_upto()
        assert got == [1.0, 2.0]


class TestOrphanedRunSweep:
    def test_boot_sweep_marks_dead_runs_error(self, monkeypatch) -> None:
        from app import db
        from app.main import _sweep_orphaned_runs

        db.init_db()
        with db.session() as s:
            s.add(db.Run(id="orphan-test-run", status="running", stage=0,
                         seed=42, spec_json=json.dumps(fx.SPEC)))
            s.add(db.Run(id="done-test-run", status="done", stage=6,
                         seed=42, spec_json=json.dumps(fx.SPEC)))
            s.commit()

        _sweep_orphaned_runs()

        with db.session() as s:
            orphan = s.get(db.Run, "orphan-test-run")
            assert orphan is not None and orphan.status == "error"
            assert "interrupted" in (orphan.error or "")
            done = s.get(db.Run, "done-test-run")
            assert done is not None and done.status == "done"  # untouched
            events = (s.query(db.RunEvent)
                      .filter(db.RunEvent.run_id == "orphan-test-run").all())
            assert any("interrupted" in e.label for e in events)
            # cleanup so other tests' scans stay pristine
            s.delete(orphan)
            s.delete(done)
            for e in events:
                s.delete(e)
            s.commit()
