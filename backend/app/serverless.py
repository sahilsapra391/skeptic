"""The backend's contract with Railway's Serverless setting.

`backend/railway.json` turns on `sleepApplication`. Railway then stops the
container once it has sent no OUTBOUND packet for 5 to 10 minutes, and the next
request boots a fresh one. A month nobody uses the product costs the plan's
floor that way, instead of the container's memory held around the clock. Two
things in this process have to cooperate for that to work, and both live here.

1. Work in flight keeps the container awake.

   Runs, fill audits and notebook reproduces execute INSIDE this process as
   background tasks, and most of their wall-clock is arithmetic on market data
   already in memory. The gauntlet's sensitivity sweep alone re-runs the engine
   about twenty times without touching the network. Railway cannot tell an hour
   of that from an idle container, so without a signal it would stop the
   process mid-run. The run would be lost, the boot sweep in `app/main.py` would
   mark it interrupted, and the person would be refunded for a verdict they
   never got.

   `holds_awake` is the signal. While any decorated job is running, one daemon
   thread sends a trivial query to the database every `HEARTBEAT_SECONDS`. That
   is outbound traffic well inside Railway's window, and it stops the moment the
   last job returns. The beat goes to the database rather than to a public
   endpoint on purpose: the job writes its result there anyway, so the
   heartbeat adds no dependency the job does not already have.

2. Idle means no open sockets.

   Railway's documentation lists open database connections among the things
   that keep a service awake. The SQLAlchemy pool keeps connections open between
   requests, which is what makes a busy minute fast, so the pool stays. Once
   nothing has happened for `IDLE_RELEASE_SECONDS` (no request, no job), the
   pool is emptied and nothing in the process is left talking to the network.
   The next request opens a fresh connection, exactly as it would after a
   restart.
"""

from __future__ import annotations

import functools
import logging
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager

log = logging.getLogger("serverless")

# Railway samples inactivity on an interval and stops a service 5 to 10 minutes
# after its last outbound packet. A beat a minute leaves room for a slow
# database round trip, or a beat that fails outright, without coming close.
HEARTBEAT_SECONDS = 60.0

# How long the process must be completely quiet before its pooled database
# connections are closed. Short enough that the sockets are gone well inside
# Railway's inactivity window, long enough that a person clicking between two
# screens never pays for a reconnect.
IDLE_RELEASE_SECONDS = 90.0
IDLE_CHECK_SECONDS = 15.0

_cond = threading.Condition()
_in_flight = 0
_beating = False
_reaper_started = False

_last_activity = time.monotonic()
# boot opened connections (init_db, the orphan sweep), so there is something to
# release the first time the process goes quiet
_released = False


def in_flight() -> int:
    """How many decorated jobs are running right now."""
    with _cond:
        return _in_flight


def _ping_database() -> None:
    from sqlalchemy import text

    from app import db

    with db.session() as s:
        s.execute(text("SELECT 1"))


def _beat() -> None:
    global _beating
    while True:
        with _cond:
            # sleeps a full beat, or returns early the moment the last job ends
            _cond.wait_for(lambda: _in_flight == 0, timeout=HEARTBEAT_SECONDS)
            if _in_flight == 0:
                _beating = False
                return
        try:
            _ping_database()
        except Exception:  # noqa: BLE001 (a missed beat is retried a beat later)
            log.warning("keep-awake heartbeat failed, retrying next beat", exc_info=True)


@contextmanager
def awake() -> Iterator[None]:
    """Keep Railway from stopping the container for the length of the block."""
    global _in_flight, _beating
    with _cond:
        _in_flight += 1
        if not _beating:
            _beating = True
            threading.Thread(target=_beat, name="keep-awake", daemon=True).start()
    try:
        yield
    finally:
        with _cond:
            _in_flight -= 1
            _cond.notify_all()
        note_activity()


def holds_awake[**P, R](fn: Callable[P, R]) -> Callable[P, R]:
    """Decorate an in-process background job so Railway cannot stop the
    container while it runs. Any job that can outlast Railway's inactivity
    window without touching the network needs this, and a test checks every
    job that exists today."""

    @functools.wraps(fn)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        with awake():
            return fn(*args, **kwargs)

    wrapper.__holds_awake__ = True  # type: ignore[attr-defined]
    return wrapper


def note_activity() -> None:
    """Record that the process just did something a person is waiting on."""
    global _last_activity, _released
    _last_activity = time.monotonic()
    _released = False


def _idle_tick(release: Callable[[], bool]) -> bool:
    """One idle check. Releases the pool when the process has been quiet for
    IDLE_RELEASE_SECONDS and nothing is in flight. Returns whether it did."""
    global _released
    if in_flight():
        # a running job is activity: the quiet window starts when it ends
        note_activity()
        return False
    if _released or time.monotonic() - _last_activity < IDLE_RELEASE_SECONDS:
        return False
    seen = _last_activity
    try:
        released = release()
    except Exception:  # noqa: BLE001 (the next idle check tries again)
        log.warning("could not release idle database connections", exc_info=True)
        return False
    # a request that landed mid-release opened a fresh connection, so the next
    # quiet window has something to release again
    if released and _last_activity == seen:
        _released = True
    return bool(released)


def _release_when_idle(release: Callable[[], bool]) -> None:
    while True:
        time.sleep(IDLE_CHECK_SECONDS)
        _idle_tick(release)


def start_idle_release(release: Callable[[], bool]) -> None:
    """Start the one thread that empties the connection pool when the process
    goes quiet. Idempotent."""
    global _reaper_started
    with _cond:
        if _reaper_started:
            return
        _reaper_started = True
    threading.Thread(
        target=_release_when_idle, args=(release,), name="idle-release", daemon=True
    ).start()
