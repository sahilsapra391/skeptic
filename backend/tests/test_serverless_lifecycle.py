"""app/serverless.py: awake exactly while work runs, no open sockets once quiet.

Railway stops the container when it has sent nothing for a few minutes
(CLAUDE.md, "The backend sleeps when idle"). These pin the two halves of the
process's side of that bargain. A job in flight must keep a heartbeat going, and
the heartbeat must stop when the job ends, or the container never sleeps. And
pooled database connections must be released once the process is quiet, but
never while a job runs and never before the quiet window has passed.

No test here reaches a database through the heartbeat: the autouse fixture
replaces the ping, and the tests that count beats replace it again.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

import pytest

from app import serverless


def _wait_until(predicate: Callable[[], bool], seconds: float = 2.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


def _no_heartbeat_thread() -> bool:
    return not any(t.name == "keep-awake" for t in threading.enumerate())


def _must_not_release() -> bool:
    pytest.fail("released the pool when it should not have")


@pytest.fixture(autouse=True)
def _no_database_heartbeat(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(serverless, "_ping_database", lambda: None)


# ------------------------------------------------------------ work in flight


def test_awake_counts_the_job_and_lets_go_of_it_on_an_exception() -> None:
    before = serverless.in_flight()
    with pytest.raises(RuntimeError):
        with serverless.awake():
            assert serverless.in_flight() == before + 1
            raise RuntimeError("the engine blew up mid-run")
    assert serverless.in_flight() == before


def test_the_heartbeat_beats_while_work_runs_and_stops_after(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    beats: list[float] = []
    monkeypatch.setattr(serverless, "HEARTBEAT_SECONDS", 0.01)
    monkeypatch.setattr(serverless, "_ping_database", lambda: beats.append(time.monotonic()))

    with serverless.awake():
        assert _wait_until(lambda: len(beats) >= 3), "no heartbeat while a job ran"

    assert _wait_until(lambda: not serverless._beating), "the heartbeat never stood down"
    settled = len(beats)
    time.sleep(0.05)
    assert len(beats) == settled, "the heartbeat kept beating with nothing in flight"


def test_a_failing_beat_does_not_stop_the_heartbeat(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts: list[int] = []

    def flaky() -> None:
        attempts.append(1)
        raise OSError("database blip")

    monkeypatch.setattr(serverless, "HEARTBEAT_SECONDS", 0.01)
    monkeypatch.setattr(serverless, "_ping_database", flaky)
    with serverless.awake():
        assert _wait_until(lambda: len(attempts) >= 3), "one failed beat ended the heartbeat"


def test_overlapping_jobs_share_one_heartbeat(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(serverless, "HEARTBEAT_SECONDS", 0.01)
    assert _wait_until(_no_heartbeat_thread), "a heartbeat from an earlier test is still alive"
    with serverless.awake(), serverless.awake():
        beaters = [t for t in threading.enumerate() if t.name == "keep-awake"]
        assert len(beaters) == 1


def test_holds_awake_wraps_and_marks_a_job() -> None:
    @serverless.holds_awake
    def job(x: int) -> int:
        assert serverless.in_flight() >= 1
        return x * 2

    assert job(21) == 42
    assert job.__name__ == "job"
    assert getattr(job, "__holds_awake__", False) is True


# ------------------------------------------------------------- idle release


def test_the_pool_is_released_once_the_process_goes_quiet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    released: list[int] = []

    def release() -> bool:
        released.append(1)
        return True

    monkeypatch.setattr(serverless, "IDLE_RELEASE_SECONDS", 0.0)
    serverless.note_activity()
    assert serverless._idle_tick(release) is True
    # already released: another quiet check has nothing to close
    assert serverless._idle_tick(release) is False
    # a request opened a connection again, so the next quiet window releases it
    serverless.note_activity()
    assert serverless._idle_tick(release) is True
    assert len(released) == 2


def test_nothing_is_released_while_a_job_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(serverless, "IDLE_RELEASE_SECONDS", 0.0)
    with serverless.awake():
        assert serverless._idle_tick(_must_not_release) is False


def test_nothing_is_released_before_the_quiet_window_ends(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(serverless, "IDLE_RELEASE_SECONDS", 3600.0)
    serverless.note_activity()
    assert serverless._idle_tick(_must_not_release) is False


def test_a_failed_release_is_tried_again_on_the_next_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken() -> bool:
        raise OSError("dispose failed")

    monkeypatch.setattr(serverless, "IDLE_RELEASE_SECONDS", 0.0)
    serverless.note_activity()
    assert serverless._idle_tick(broken) is False
    assert serverless._idle_tick(lambda: True) is True


def test_sqlite_is_never_disposed() -> None:
    """conftest points the suite at a throwaway SQLite file. A file has no
    socket to close, and disposing an in-memory SQLite database destroys it."""
    from app import db

    assert db.release_idle_connections() is False


def test_every_request_counts_as_activity(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi.testclient import TestClient

    from app.main import app

    monkeypatch.setattr(serverless, "_released", True)
    assert TestClient(app).get("/api/health").status_code == 200
    assert serverless._released is False, "a request did not reopen the quiet window"
