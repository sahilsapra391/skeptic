"""Single-flight background-job admission (app.api.jobs.claim_run_job).

The 2026-07-14 consolidation replaced the audit/reproduce endpoints'
duplicated read-check-write marker logic with ONE compare-and-swap claim.
These tests pin the shared semantics: 404 without a completed run, 409
while a fresh marker says a job is in flight, takeover once the marker is
stale or non-running — and, the reason the CAS exists, a deliberate race
where two claims read the same prior marker admits exactly one.
"""

from __future__ import annotations

import json
import threading
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException

from app import db
from app.api import jobs
from app.api.jobs import STALE_TAKEOVER_MINUTES, claim_run_job
from app.api.payload import sweep_coverage_notes


def _add_run(status: str = "done", spec_json: str = "{}",
             audit_json: str | None = None) -> str:
    run_id = uuid.uuid4().hex[:12]
    db.init_db()
    with db.session() as s:
        s.add(db.Run(id=run_id, status=status, spec_json=spec_json,
                     audit_json=audit_json))
        s.commit()
    return run_id


def _claim(run_id: str) -> None:
    claim_run_job(run_id, column="audit_json",
                  running_status="__running__", verb="audit")


def _stored_marker(run_id: str) -> dict[str, object]:
    with db.session() as s:
        run = s.get(db.Run, run_id)
        assert run is not None and run.audit_json
        marker = json.loads(run.audit_json)
    assert isinstance(marker, dict)
    return marker


class TestClaimRunJob:
    def test_404_when_run_is_missing(self) -> None:
        db.init_db()
        with pytest.raises(HTTPException) as exc:
            _claim("nope")
        assert exc.value.status_code == 404
        assert "no completed run to audit" in exc.value.detail

    def test_404_when_run_is_not_done(self) -> None:
        run_id = _add_run(status="running")
        with pytest.raises(HTTPException) as exc:
            _claim(run_id)
        assert exc.value.status_code == 404

    def test_claim_writes_the_running_marker(self) -> None:
        run_id = _add_run()
        _claim(run_id)
        marker = _stored_marker(run_id)
        assert marker["status"] == "__running__"
        # the start stamp is what a later staleness check reasons from
        datetime.fromisoformat(str(marker["started_at"]))

    def test_second_claim_refused_while_fresh(self) -> None:
        run_id = _add_run()
        _claim(run_id)
        with pytest.raises(HTTPException) as exc:
            _claim(run_id)
        assert exc.value.status_code == 409
        assert "audit already running" in exc.value.detail

    def test_stale_running_marker_is_taken_over(self) -> None:
        stale = datetime.now(UTC) - timedelta(minutes=STALE_TAKEOVER_MINUTES + 1)
        run_id = _add_run(audit_json=json.dumps(
            {"status": "__running__", "started_at": stale.isoformat()}))
        _claim(run_id)  # a worker died mid-job; the slot must not wedge
        started = datetime.fromisoformat(str(_stored_marker(run_id)["started_at"]))
        assert started > stale

    def test_finished_marker_is_reclaimable(self) -> None:
        run_id = _add_run(audit_json=json.dumps({"status": "done", "match": True}))
        _claim(run_id)
        assert _stored_marker(run_id)["status"] == "__running__"

    def test_corrupt_marker_is_reclaimable(self) -> None:
        # a marker that cannot be read must never wedge the slot shut
        run_id = _add_run(audit_json="{not json")
        _claim(run_id)
        assert _stored_marker(run_id)["status"] == "__running__"

    def test_racing_claims_admit_exactly_one(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The TOCTOU the CAS closes: both racers pass the staleness check
        on the same prior marker before either writes. Rendezvous inside
        the claim (at the marker serialization, after the read, before the
        swap) so the interleaving is deterministic — exactly one may win."""
        run_id = _add_run()
        barrier = threading.Barrier(2, timeout=10)

        class _RendezvousJson:
            loads = staticmethod(json.loads)

            @staticmethod
            def dumps(*args: object, **kwargs: object) -> str:
                barrier.wait()
                return json.dumps(*args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(jobs, "json", _RendezvousJson)
        outcomes: list[int] = []

        def race() -> None:
            try:
                _claim(run_id)
                outcomes.append(200)
            except HTTPException as exc:
                outcomes.append(exc.status_code)

        threads = [threading.Thread(target=race) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)
        assert sorted(outcomes) == [200, 409]
        assert _stored_marker(run_id)["status"] == "__running__"


class TestSweepCoverageNotes:
    """payload.sweep_coverage_notes — THE selector for the F8 sweep-coverage
    disclosure keys, shared by the app serializer and the notebook export."""

    def test_selects_both_notes_in_disclosure_order(self) -> None:
        assert sweep_coverage_notes(
            {"conditions_note": "a", "window_note": "b"}) == ["a", "b"]

    def test_absent_and_empty_notes_are_dropped(self) -> None:
        assert sweep_coverage_notes({"window_note": "b"}) == ["b"]
        assert sweep_coverage_notes({"conditions_note": "", "window_note": None}) == []
        assert sweep_coverage_notes({}) == []
        assert sweep_coverage_notes(None) == []
