"""The cross-host mutex for the collector lanes (collector/lock.py).

What these protect: the VM's nightly EOD chain and a manual `workflow_dispatch`
of collect-eod.yml / alpaca-backfill.yml spend the SAME Alpaca account (one
200 req/min budget) and write the same R2 lake. `concurrency: group: collector`
used to keep them apart, but it only covers Actions runs, and the schedules
moved to the VM on 2026-08-04. An overlap corrupts nothing — it halves both
sides' throughput, and the VM chain has a 2700s wall to run into.

Two properties matter more than the happy path, and both are here: a lease
must never wedge the lane past its TTL (a lane that silently stops collecting
is the outage this subsystem was built after), and release must never touch a
lease that is not ours.

Nothing here reaches the network: R2 is a fake, and the chain tests run
against a staged deploy/ with a loopback HEALTHCHECK_URL — a passing suite
that pages the owner is a bug, not a detail.
"""

from __future__ import annotations

import io
import json
import os
import re
import signal
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import lock

REPO = Path(__file__).resolve().parents[2]
DEPLOY = REPO / "collector" / "deploy"
WORKFLOWS = REPO / ".github" / "workflows"

NOW = datetime(2026, 8, 4, 21, 30, 0, tzinfo=timezone.utc)
HC_TEST_URL = "http://127.0.0.1:9/hc-test"


class FakeS3:
    """The three bytes-in/bytes-out calls collect.py's R2 helpers make."""

    class exceptions:
        class NoSuchKey(Exception):
            pass

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.on_put = None  # fires AFTER a put, to simulate a racing writer

    def get_object(self, Bucket, Key):
        if Key not in self.objects:
            raise self.exceptions.NoSuchKey(Key)
        return {"Body": io.BytesIO(self.objects[Key])}

    def put_object(self, Bucket, Key, Body):
        self.objects[Key] = Body
        hook, self.on_put = self.on_put, None
        if hook is not None:
            hook(self)


@pytest.fixture(autouse=True)
def _bucket(monkeypatch):
    monkeypatch.setenv("R2_BUCKET", "test-bucket")


@pytest.fixture
def s3():
    return FakeS3()


def _write_lease(s3, **over) -> dict:
    """Plant a lease held by someone else, live as of NOW unless overridden."""
    lease = {
        "host": "oracle-collector-vm",
        "pid": 4242,
        "started_at": lock._iso(NOW - timedelta(seconds=120)),
        "ttl_seconds": 3000,
        "holder": "vm-collect-eod",
        "token": "aaaaaaaa",
        "expires_at": lock._iso(NOW + timedelta(seconds=2880)),
        "released_at": None,
    }
    lease.update(over)
    lock.r2_put_json(s3, lock.LOCK_KEY, lease)
    return lease


def _stored(s3) -> dict:
    return json.loads(s3.objects[lock.LOCK_KEY])


# --------------------------------- acquire ---------------------------------

def test_acquire_on_a_free_lane_writes_the_documented_lease(s3):
    lease = lock.acquire(s3, "vm-collect-eod", ttl_seconds=3000,
                         settle_seconds=0, now=NOW)

    for key in ("host", "pid", "started_at", "ttl_seconds"):
        assert key in lease, key
    assert lease["started_at"] == lock._iso(NOW)
    assert lease["ttl_seconds"] == 3000
    assert lease["holder"] == "vm-collect-eod"
    assert lease["token"]
    assert _stored(s3) == lease
    assert lock.is_live(lease, NOW)


def test_a_live_lease_refuses_the_second_host(s3):
    _write_lease(s3)
    with pytest.raises(lock.LeaseHeld) as held:
        lock.acquire(s3, "github-actions/alpaca-backfill#9", settle_seconds=0, now=NOW)
    # the refusal has to name the holder: it is the whole content of the
    # Healthchecks page and of the red Actions log the owner reads next.
    message = str(held.value)
    assert "vm-collect-eod" in message
    assert "oracle-collector-vm" in message
    assert "pid=4242" in message
    assert "expires_in=2880s" in message
    # and it must not have stomped the holder's lease on the way out
    assert _stored(s3)["token"] == "aaaaaaaa"


def test_the_same_host_cannot_pile_onto_its_own_running_chain(s3):
    """A second local run overlapping the first is the same shared-budget
    collision as a cross-host overlap, so `another host` is not the test — a
    live lease is."""
    lock.acquire(s3, "vm-collect-eod", settle_seconds=0, now=NOW)
    with pytest.raises(lock.LeaseHeld):
        lock.acquire(s3, "vm-collect-eod", settle_seconds=0,
                     now=NOW + timedelta(minutes=40))


def test_a_lease_past_its_ttl_is_free(s3):
    """The only recovery path for a holder that died without releasing."""
    _write_lease(s3, started_at=lock._iso(NOW - timedelta(seconds=3001)))
    lease = lock.acquire(s3, "vm-collect-eod", settle_seconds=0, now=NOW)
    assert _stored(s3)["token"] == lease["token"]


def test_a_lease_one_second_short_of_its_ttl_still_holds(s3):
    _write_lease(s3, started_at=lock._iso(NOW - timedelta(seconds=2999)))
    with pytest.raises(lock.LeaseHeld):
        lock.acquire(s3, "vm-collect-eod", settle_seconds=0, now=NOW)


def test_an_unreadable_lease_never_wedges_the_lane(s3):
    """Fail-open on purpose. A truncated or hand-edited object must not stop
    collection every night until someone reads a journal — over-collecting is
    recoverable, a lane that never runs is the outage we are preventing."""
    for broken in ({"host": "x"}, {"started_at": "not-a-date", "ttl_seconds": 60},
                   {"started_at": lock._iso(NOW), "ttl_seconds": "3000"},
                   ["not", "a", "lease"]):
        lock.r2_put_json(s3, lock.LOCK_KEY, broken)
        assert lock.acquire(s3, "vm-collect-eod", settle_seconds=0, now=NOW)


def test_a_zero_or_negative_ttl_is_refused(s3):
    """A lease that expires the instant it is written protects nothing."""
    for ttl in (0, -60):
        with pytest.raises(ValueError):
            lock.acquire(s3, "vm-collect-eod", ttl_seconds=ttl, settle_seconds=0)
    assert lock.LOCK_KEY not in s3.objects


def test_a_claim_that_lands_first_loses_to_the_one_that_lands_last(s3):
    """Two hosts claiming in the same breath: last write wins in R2, and the
    read-back is how the loser finds out instead of both proceeding."""
    def racing_writer(store):
        lock.r2_put_json(store, lock.LOCK_KEY, {
            "host": "runner-7", "pid": 99, "started_at": lock._iso(NOW),
            "ttl_seconds": 3000, "holder": "github-actions/alpaca-backfill#9",
            "token": "zzzz", "released_at": None,
        })

    s3.on_put = racing_writer
    with pytest.raises(lock.LeaseHeld) as held:
        lock.acquire(s3, "vm-collect-eod", settle_seconds=0, now=NOW)
    assert "alpaca-backfill#9" in str(held.value)
    assert _stored(s3)["token"] == "zzzz"  # the winner keeps the lane


def test_a_lease_that_vanishes_under_us_is_not_treated_as_a_win(s3):
    def deleter(store):
        store.objects.pop(lock.LOCK_KEY)

    s3.on_put = deleter
    with pytest.raises(lock.LeaseHeld):
        lock.acquire(s3, "vm-collect-eod", settle_seconds=0, now=NOW)


# --------------------------------- release ---------------------------------

def test_release_frees_the_lane_for_the_next_run(s3):
    lease = lock.acquire(s3, "vm-collect-eod", settle_seconds=0, now=NOW)
    assert lock.release(s3, lease["token"], now=NOW) == "released"
    assert not lock.is_live(lock.read_lease(s3), NOW)
    # the object survives with its forensics; the next acquire simply takes it
    assert _stored(s3)["holder"] == "vm-collect-eod"
    assert lock.acquire(s3, "github-actions/collect-eod#1", settle_seconds=0, now=NOW)


def test_release_never_touches_a_lane_that_moved_on(s3):
    """Our lease expired, someone else took the lane, and only then did we
    exit. Releasing on the way out would hand our overlap to a third run."""
    _write_lease(s3, holder="github-actions/alpaca-backfill#9", token="theirs")
    assert lock.release(s3, "ours") == "foreign"
    assert lock.is_live(lock.read_lease(s3), NOW)
    assert _stored(s3)["token"] == "theirs"


def test_release_with_no_token_is_not_a_free_pass(s3):
    _write_lease(s3)
    assert lock.release(s3, None) == "foreign"
    assert lock.is_live(lock.read_lease(s3), NOW)


def test_release_is_idempotent_and_survives_a_missing_lease(s3):
    assert lock.release(s3, "whatever") == "absent"
    lease = lock.acquire(s3, "vm-collect-eod", settle_seconds=0, now=NOW)
    assert lock.release(s3, lease["token"], now=NOW) == "released"
    assert lock.release(s3, lease["token"], now=NOW) == "already-released"


def test_releasing_our_own_expired_lease_writes_nothing(s3):
    """The run outlived its own TTL. The lane is already free, so a tombstone
    buys nothing — and it can LOSE something: between the read and the write
    another host can legitimately acquire, and stamping our stale copy would
    erase their live lease and let a third run in. Not writing closes it,
    because that race needs exactly this precondition."""
    lease = lock.acquire(s3, "vm-collect-eod", ttl_seconds=3000,
                         settle_seconds=0, now=NOW)
    before = s3.objects[lock.LOCK_KEY]

    assert lock.release(s3, lease["token"],
                        now=NOW + timedelta(seconds=3001)) == "expired"
    assert s3.objects[lock.LOCK_KEY] == before, "released an already-expired lease"


def test_a_lease_that_lapsed_and_was_retaken_is_never_touched(s3):
    """The same window, one step later: the other host already has it."""
    lease = lock.acquire(s3, "vm-collect-eod", settle_seconds=0, now=NOW)
    later = NOW + timedelta(seconds=3001)
    theirs = lock.acquire(s3, "github-actions/alpaca-backfill#9",
                          settle_seconds=0, now=later)

    assert lock.release(s3, lease["token"], now=later) == "foreign"
    still = lock.read_lease(s3)
    assert still["token"] == theirs["token"], "we clobbered a live lease"
    assert lock.is_live(still, later)


def test_force_release_recovers_a_wedged_lane(s3):
    """The documented escape hatch for a holder that is known dead but whose
    TTL still has hours to run."""
    _write_lease(s3, ttl_seconds=21000)
    assert lock.release(s3, None, force=True) == "released"
    assert lock.acquire(s3, "vm-collect-eod", settle_seconds=0, now=NOW)


# ----------------------------------- CLI -----------------------------------

def test_cli_acquire_writes_a_token_that_release_reads_back(s3, tmp_path, capsys):
    token_file = tmp_path / "collector.lock.token"

    assert lock.main(["acquire", "--holder", "vm-collect-eod", "--ttl", "3000",
                      "--settle", "0", "--token-file", str(token_file)], s3=s3) == 0
    assert token_file.read_text().strip() == _stored(s3)["token"]

    assert lock.main(["release", "--token-file", str(token_file)], s3=s3) == 0
    assert not lock.is_live(lock.read_lease(s3))
    assert "lease released" in capsys.readouterr().out


def test_cli_refusal_exits_75_and_says_who_holds_the_lane(s3, tmp_path, capsys):
    # the CLI reads the wall clock, so the holder has to be live against it
    _write_lease(s3, started_at=lock._iso(lock._now()))
    token_file = tmp_path / "collector.lock.token"

    rc = lock.main(["acquire", "--holder", "github-actions/alpaca-backfill#9",
                    "--settle", "0", "--token-file", str(token_file)], s3=s3)

    assert rc == lock.EXIT_HELD == 75
    err = capsys.readouterr().err
    assert "REFUSING" in err and "vm-collect-eod" in err
    # The token file is written BEFORE the claim (so a death mid-acquire is
    # still releasable), so a refusal leaves one behind. What matters is that
    # releasing with it cannot touch the holder's lease.
    assert lock.main(["release", "--token-file", str(token_file)], s3=s3) == 0
    assert lock.is_live(lock.read_lease(s3)), "a refused run released the holder's lease"


def test_cli_release_of_a_foreign_lease_stays_quiet_and_exits_zero(s3, capsys):
    """`if: always()` runs this after a failed job; turning the job red over a
    lease that already moved on would cry wolf about the wrong thing."""
    _write_lease(s3)
    assert lock.main(["release", "--token", "ours"], s3=s3) == 0
    assert "NOT releasing" in capsys.readouterr().err
    assert lock.is_live(lock.read_lease(s3), NOW)


def test_bytes_that_are_not_json_at_all_read_as_a_free_lane(s3):
    """r2_get_json only handles NoSuchKey, so a truncated write or an empty
    body raises out of every subcommand — including the `release --force`
    the RUNBOOK sends you to run to clear exactly that object."""
    for junk in (b'{"host": "vm", "started_at": "2026-08-', b"", b"\xff\xfe\x00",
                 b'{"started_at": "2026-08-04T21:30:00Z", "ttl_seconds": NaN}',
                 b'{"started_at": "9999-12-31T23:59:59Z", "ttl_seconds": 1e18}'):
        s3.objects[lock.LOCK_KEY] = junk
        assert lock.read_lease(s3) is None or not lock.is_live(lock.read_lease(s3), NOW)
        assert lock.acquire(s3, "vm-collect-eod", settle_seconds=0, now=NOW)
        assert lock.main(["release", "--force"], s3=s3) == 0


def test_a_ttl_beyond_the_ceiling_is_clamped_not_honoured(s3, capsys):
    """A fat-fingered max_minutes (3300 for 330) would otherwise mint a
    55-hour lease and refuse every nightly chain until someone forced it."""
    lease = lock.acquire(s3, "github-actions/alpaca-backfill#9",
                         ttl_seconds=198000, settle_seconds=0, now=NOW)
    assert lease["ttl_seconds"] == lock.MAX_TTL_SECONDS
    assert "clamping" in capsys.readouterr().err
    assert not lock.is_live(lease, NOW + timedelta(seconds=lock.MAX_TTL_SECONDS + 1))


def test_the_token_reaches_disk_before_the_claim_reaches_r2(s3, tmp_path):
    """A process that dies between the claim landing and the token being
    written leaves a live lease nobody can release — up to six hours for a
    backfill. The ordering is the fix, so pin the ordering."""
    token_file = tmp_path / "collector.lock.token"
    seen = {}
    s3.on_put = lambda store: seen.update(
        exists=token_file.exists(),
        token=token_file.read_text() if token_file.exists() else None)

    lock.main(["acquire", "--holder", "vm-collect-eod", "--settle", "0",
               "--token-file", str(token_file)], s3=s3)

    assert seen["exists"], "the claim landed before its token was on disk"
    assert seen["token"] == _stored(s3)["token"]


def test_a_retried_acquire_recognises_its_own_claim(s3, tmp_path, monkeypatch):
    """A transient failure on the read-back must not leave the retry refusing
    against the lease it just wrote — that pages 'leased elsewhere' naming
    ourselves and wedges the lane for the whole TTL over a lease we own."""
    calls = {"n": 0}
    real = lock.read_lease

    def flaky(store):
        calls["n"] += 1
        if calls["n"] == 2:                      # the read-back
            raise RuntimeError("R2 reset the connection")
        return real(store)

    monkeypatch.setattr(lock, "read_lease", flaky)
    monkeypatch.setattr(lock.time, "sleep", lambda s: None)
    token_file = tmp_path / "t"

    rc = lock.main(["acquire", "--holder", "vm-collect-eod", "--settle", "0",
                    "--token-file", str(token_file)], s3=s3)

    assert rc == 0, "the retry refused against its own claim"
    assert real(s3)["token"] == token_file.read_text()


# ----------------------------- CLI: recovery -------------------------------

def test_cli_status_reports_who_holds_the_lane(s3, capsys):
    """What the RUNBOOK tells the owner to run on a lane that looks stuck."""
    assert lock.main(["status"], s3=s3) == 0
    assert "free" in capsys.readouterr().out

    _write_lease(s3, started_at=lock._iso(lock._now()))
    assert lock.main(["status"], s3=s3) == 0
    out = capsys.readouterr().out
    assert "LEASED" in out and "vm-collect-eod" in out


def test_cli_force_release_actually_forces(s3, capsys):
    """The other half of the documented recovery. Without --force wired
    through, this silently no-ops and the owner finds out mid-incident."""
    _write_lease(s3, started_at=lock._iso(lock._now()), ttl_seconds=21000)
    assert lock.main(["release", "--force"], s3=s3) == 0
    assert not lock.is_live(lock.read_lease(s3))


def test_cli_release_with_an_empty_token_file_frees_nothing(s3, tmp_path):
    """An empty token file must not read as 'no token, so force it'."""
    _write_lease(s3, started_at=lock._iso(lock._now()))
    empty = tmp_path / "t"
    empty.write_text("")
    assert lock.main(["release", "--token-file", str(empty)], s3=s3) == 0
    assert lock.is_live(lock.read_lease(s3))


# --------------------- the VM chain honours the lease ----------------------

def _stage(tmp_path: Path) -> Path:
    """Copy deploy/ next to a fabricated .env, exactly as the sibling schedule
    tests do — run from the real deploy/ these scripts read the developer's own
    .env and POST to the LIVE Healthchecks endpoint."""
    (tmp_path / ".env").write_text(f"HEALTHCHECK_URL={HC_TEST_URL}\n")
    deploy = tmp_path / "deploy"
    deploy.mkdir(exist_ok=True)
    for src in DEPLOY.glob("*.sh"):
        dst = deploy / src.name
        dst.write_text(src.read_text())
        dst.chmod(0o755)
    return deploy


def _run_chain(tmp_path: Path, acquire_rc: int = 0,
               failing: frozenset[str] = frozenset()) -> dict:
    """Drive collect-eod.sh against a stub `uv` that answers the lock CLI.

    `failing` names data steps that should exit non-zero, so the release can
    be asserted on a RED chain — the case where keeping the lane would take
    the 22:30 catch-up down with it.
    """
    script = _stage(tmp_path) / "collect-eod.sh"
    calls, locks, pings = (tmp_path / n for n in ("calls.log", "locks.log", "pings.log"))

    stub = tmp_path / "uv"
    fail_arm = f'  {"|".join(sorted(failing))}) exit 3 ;;\n' if failing else ""
    stub.write_text(
        "#!/usr/bin/env bash\n"
        'args=("$@")\n'
        'for ((i=0; i<${#args[@]}; i++)); do\n'
        '  case "${args[i]}" in\n'
        # the HEALTHCHECK_URL read-back, same shape as the real thing
        f'    -c) grep -E "^HEALTHCHECK_URL=" "{tmp_path}/.env" | cut -d= -f2-; exit 0 ;;\n'
        # the lease is infrastructure, not a data step: answer it and get out
        # before the chain-step recorder below ever sees it
        '    lock.py)\n'
        f'      echo "${{args[i+1]:-?}}" >> "{locks}"\n'
        '      case "${args[i+1]:-}" in\n'
        f'        acquire) exit {acquire_rc} ;;\n'
        "        *) exit 0 ;;\n"
        "      esac ;;\n"
        "  esac\n"
        "done\n"
        'script=""\n'
        'for a in "${args[@]}"; do case "$a" in *.py) script="$a" ;; esac; done\n'
        f'echo "$script" >> "{calls}"\n'
        'case "$script" in\n'
        f"{fail_arm}"
        "  *) exit 0 ;;\n"
        "esac\n"
    )
    stub.chmod(0o755)

    curl = tmp_path / "curl"
    curl.write_text(f'#!/usr/bin/env bash\necho "$*" >> "{pings}"\nexit 0\n')
    curl.chmod(0o755)

    proc = subprocess.run(
        ["bash", str(script)],
        env={**os.environ, "UV": str(stub), "PATH": f"{tmp_path}:{os.environ['PATH']}"},
        capture_output=True, text=True,
    )
    return {
        "rc": proc.returncode,
        "ran": calls.read_text().split() if calls.exists() else [],
        "lock": locks.read_text().split() if locks.exists() else [],
        "pings": pings.read_text().splitlines() if pings.exists() else [],
        "out": proc.stdout,
    }


def test_the_chain_takes_the_lease_first_and_hands_it_back_last(tmp_path):
    result = _run_chain(tmp_path)
    assert result["rc"] == 0
    assert result["lock"] == ["acquire", "release"]
    assert result["ran"][0] == "collect.py"   # nothing ran before the lease


def test_a_held_lane_stops_the_chain_before_it_spends_a_single_request(tmp_path):
    result = _run_chain(tmp_path, acquire_rc=lock.EXIT_HELD)
    assert result["rc"] == lock.EXIT_HELD
    assert result["ran"] == []
    # nothing to release — we never held it
    assert result["lock"] == ["acquire"]


def test_a_refused_chain_pages_instead_of_disappearing(tmp_path):
    """collect.py pings the tile itself, so a chain that never starts would
    otherwise leave the tile untouched and the night silently empty — the
    exact shape of the Jul 27-31 outage."""
    result = _run_chain(tmp_path, acquire_rc=lock.EXIT_HELD)
    assert len(result["pings"]) == 1, result["pings"]
    assert f"{HC_TEST_URL}/fail" in result["pings"][0]
    assert "leased elsewhere" in result["pings"][0]


def test_a_broken_lock_pages_differently_from_a_busy_lane(tmp_path):
    """`someone is holding it` and `the lock itself is broken` need opposite
    responses (wait vs. fix R2 credentials), and the ping body is all the
    owner has at 21:30."""
    result = _run_chain(tmp_path, acquire_rc=1)
    assert result["rc"] == 1
    assert result["ran"] == []
    assert len(result["pings"]) == 1, result["pings"]
    assert "could not be read or written" in result["pings"][0]
    assert "leased elsewhere" not in result["pings"][0]


def test_the_chain_releases_the_lease_even_when_a_step_fails(tmp_path):
    """The release rides an EXIT trap, not the happy path: a chain that fails
    and keeps the lane would take tonight's catch-up down with it."""
    result = _run_chain(tmp_path, failing=frozenset({"derive_ivs_signals.py"}))
    assert result["rc"] != 0
    assert result["lock"] == ["acquire", "release"]


def test_the_chain_releases_the_lease_when_the_collector_itself_fails(tmp_path):
    """The other shape: collect.py dies at step 1, the minute top-up is
    skipped, the derivations still run — and the lane still comes back."""
    result = _run_chain(tmp_path, failing=frozenset({"collect.py"}))
    assert result["rc"] != 0
    assert "alpaca.py" not in result["ran"]
    assert result["lock"] == ["acquire", "release"]


def test_the_chain_hands_the_lease_back_when_a_signal_kills_it(tmp_path):
    """The systemd-wall case. Bash runs EXIT traps on an untrapped signal
    only if a handler exists for it, so without the TERM trap the 2700s kill
    — the one death most likely to leave a lease behind — would keep the lane
    for the rest of the TTL. Asserted by sending the signal, not by grepping
    for the trap line."""
    script = _stage(tmp_path) / "collect-eod.sh"
    locks = tmp_path / "locks.log"
    stub = tmp_path / "uv"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        'args=("$@")\n'
        'for ((i=0; i<${#args[@]}; i++)); do\n'
        '  case "${args[i]}" in\n'
        f'    -c) echo "{HC_TEST_URL}"; exit 0 ;;\n'
        f'    lock.py) echo "${{args[i+1]:-?}}" >> "{locks}"; exit 0 ;;\n'
        # the first data step hangs, so the signal lands mid-chain
        '    collect.py) sleep 30; exit 0 ;;\n'
        "  esac\n"
        "done\n"
        "exit 0\n"
    )
    stub.chmod(0o755)
    curl = tmp_path / "curl"
    curl.write_text("#!/usr/bin/env bash\nexit 0\n")
    curl.chmod(0o755)

    proc = subprocess.Popen(
        ["bash", str(script)],
        env={**os.environ, "UV": str(stub), "PATH": f"{tmp_path}:{os.environ['PATH']}"},
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    deadline = time.time() + 10
    while time.time() < deadline and not locks.exists():
        time.sleep(0.05)
    time.sleep(0.3)                       # let the chain reach the hanging step
    # the whole process group, the way systemd's default KillMode does it —
    # bash defers a trap while a foreground child runs, so signalling only
    # the shell would just wait out the child
    os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    proc.wait(timeout=20)

    assert locks.read_text().split() == ["acquire", "release"], \
        "a signalled chain kept the lane"


def test_the_vm_ttl_covers_the_units_own_wall(tmp_path):
    """A TTL under TimeoutStartSec hands the lane away while the chain is
    still legitimately using it; far over it wedges the lane overnight."""
    sh = (DEPLOY / "collect-eod.sh").read_text()
    ttl = int(re.search(r'LOCK_TTL="\$\{SKEPTIC_LOCK_TTL:-(\d+)\}"', sh).group(1))
    wall = int(re.search(r"^TimeoutStartSec=(\d+)$",
                         (DEPLOY / "skeptic-collect-eod.service").read_text(), re.M).group(1))
    assert wall < ttl <= wall + 600


# ------------------- the workflows honour the same lease -------------------

LEASED_WORKFLOWS = ("collect-eod.yml", "alpaca-backfill.yml")


def _steps(text: str) -> list[tuple[str, str]]:
    return [(b.split("\n", 1)[0].strip(), b.split("\n", 1)[1])
            for b in re.split(r"\n      - name: ", text)[1:]]


@pytest.mark.parametrize("workflow", LEASED_WORKFLOWS)
def test_the_workflow_leases_the_lane_before_it_touches_the_account(workflow):
    steps = _steps((WORKFLOWS / workflow).read_text())
    names = [n for n, _ in steps]
    bodies = dict(steps)

    acquire = next(i for i, (_, b) in enumerate(steps) if "lock.py acquire" in b)
    assert "id: lease" in steps[acquire][1]
    work = [i for i, (_, b) in enumerate(steps)
            if "uv run python" in b and "lock.py" not in b]
    assert work and min(work) > acquire, f"{workflow}: work runs before the lease"

    release = next(n for n, b in steps if "lock.py release" in b)
    assert "if: always()" in bodies[release], f"{workflow}: a cancelled job keeps the lane"
    assert names[-1] == release, f"{workflow}: release must be the last step"


@pytest.mark.parametrize("workflow", LEASED_WORKFLOWS)
def test_no_always_step_outruns_a_refused_lease(workflow):
    """`if: always()` steps run even when an earlier step failed — including
    when the failure IS the refusal. Ungated, every derivation would still
    write the lake the holder is writing, which is the overlap this prevents."""
    for name, body in _steps((WORKFLOWS / workflow).read_text()):
        if "lock.py release" in body:
            continue
        # only real conditions, never the prose in a comment above one
        for cond in re.findall(r"^\s+if: (.+)$", body, re.M):
            if "always()" not in cond and "cancelled()" not in cond:
                continue
            assert "steps.lease.outcome == 'success'" in cond, (
                f"{workflow}: step {name!r} runs regardless of the lease")


def test_the_dispatch_ttl_covers_the_job_wall_it_replaces():
    text = (WORKFLOWS / "collect-eod.yml").read_text()
    wall = int(re.search(r"^\s*timeout-minutes: (\d+)$", text, re.M).group(1)) * 60
    ttl = int(re.search(r"--ttl (\d+)", text).group(1))
    assert wall < ttl <= wall + 600


def test_the_backfill_ttl_tracks_the_budget_it_was_given():
    """Its job wall is 350 min. Pinning the lease to that would let a 30-min
    backfill that died lock the VM chain out for six hours."""
    text = (WORKFLOWS / "alpaca-backfill.yml").read_text()
    assert re.search(r"--ttl \"\$\(\( \(MAX_MINUTES \+ \d+\) \* 60 \)\)\"", text)
    assert re.search(r"case \"\$MAX_MINUTES\" in ''\|\*\[!0-9\]\*\)", text), \
        "max_minutes is free text from a dispatch form — validate before arithmetic"
    clamp = re.search(r'if \[ "\$MAX_MINUTES" -gt (\d+) \]; then MAX_MINUTES=(\d+)', text)
    assert clamp and clamp.group(1) == clamp.group(2), \
        "digits-only is not enough: 99999 is digits and mints a two-year lease"
    wall = int(re.search(r"^\s*timeout-minutes: (\d+)$", text, re.M).group(1))
    assert int(clamp.group(1)) <= wall


def _lock_argv(text: str) -> list[list[str]]:
    """Every `python lock.py …` invocation in a caller, as argv.

    Shell variables are substituted with a plausible value: what is under
    test is the FLAGS, and `--ttl "$(( … ))"` must survive as a number.
    """
    joined = text.replace("\\\n", " ")             # shell line continuations
    joined = re.sub(r"\n\s+(--)", r" \1", joined)  # YAML `run: >` folded lines
    invocations = []
    for line in joined.splitlines():
        m = re.search(r"python lock\.py (.+)", line)
        if not m:
            continue
        # whatever the caller computes the TTL from ("$LOCK_TTL", "$(( … ))"),
        # the parser must see an int — the flag is what is under test
        raw = re.sub(r'(--ttl\s+)("[^"]*"|\S+)', r"\g<1>3000", m.group(1))
        raw = re.sub(r'"[^"]*\$\{?\w+\}?[^"]*"', "substituted", raw)  # "$HOLDER", "$X/y"
        raw = re.sub(r"\s*(\|\||&&|;|2>&1).*$", "", raw)              # shell tails
        invocations.append(raw.split())
    return invocations


@pytest.mark.parametrize("caller", [
    DEPLOY / "collect-eod.sh",
    WORKFLOWS / "collect-eod.yml",
    WORKFLOWS / "alpaca-backfill.yml",
])
def test_every_caller_speaks_the_cli_the_module_actually_implements(caller):
    """A renamed flag in a caller is otherwise a green suite and a chain that
    will not start tonight: the chain tests stub `uv` wholesale so they never
    see the flags, and the CLI tests build their own argv. This runs the REAL
    argv through the REAL parser."""
    invocations = _lock_argv(caller.read_text())
    assert len(invocations) == 2, f"{caller.name}: expected acquire + release, got {invocations}"
    # a set, not a sequence: collect-eod.sh defines release_lease() above the
    # acquire it guards, so file order is not call order
    assert {inv[0] for inv in invocations} == {"acquire", "release"}
    for argv in invocations:
        lock.build_parser().parse_args(argv)   # SystemExit on an unknown flag


def test_no_caller_disables_the_settle_window():
    """settle=0 makes acquire a bare read-then-write, which is the race the
    read-back exists to resolve. Every test passes 0 for speed, so nothing
    else would notice a production caller doing the same."""
    assert lock.SETTLE_SECONDS >= 1
    for caller in (DEPLOY / "collect-eod.sh", WORKFLOWS / "collect-eod.yml",
                   WORKFLOWS / "alpaca-backfill.yml"):
        assert "--settle" not in caller.read_text(), caller.name


def test_acquire_waits_for_the_settle_window_before_reading_back(s3, monkeypatch):
    """The one thing that makes two near-simultaneous claims resolve to a
    single winner against a real R2 round trip."""
    slept: list[float] = []
    order: list[str] = []
    monkeypatch.setattr(lock.time, "sleep", lambda s: (slept.append(s), order.append("slept")))
    real_put, real_read = lock.r2_put_json, lock.read_lease
    monkeypatch.setattr(lock, "r2_put_json",
                        lambda *a, **k: (order.append("put"), real_put(*a, **k))[1])
    monkeypatch.setattr(lock, "read_lease",
                        lambda *a, **k: (order.append("read"), real_read(*a, **k))[1])

    lock.acquire(s3, "vm-collect-eod", now=NOW)   # default settle

    assert slept == [lock.SETTLE_SECONDS]
    assert order == ["read", "put", "slept", "read"]


def test_the_ttl_clears_the_catch_up_slot_it_must_not_swallow():
    """The comment on DEFAULT_TTL_SECONDS claims a hard-killed 21:30 holder
    frees the lane before the 22:30 catch-up. That is a property of the gap
    between the timer's two slots, not of TimeoutStartSec — they agree today
    by five minutes, and nothing else would notice them diverging."""
    slots = re.findall(r"^OnCalendar=.*?(\d{2}):(\d{2}):00 UTC$",
                       (DEPLOY / "skeptic-collect-eod.timer").read_text(), re.M)
    assert len(slots) == 2, slots
    (h1, m1), (h2, m2) = ((int(h), int(m)) for h, m in slots)
    gap = (h2 * 60 + m2 - h1 * 60 - m1) * 60
    ttl = int(re.search(r'LOCK_TTL="\$\{SKEPTIC_LOCK_TTL:-(\d+)\}"',
                        (DEPLOY / "collect-eod.sh").read_text()).group(1))
    assert ttl < gap, (
        f"a hard-killed 21:30 chain holds the lane for {ttl}s, past the "
        f"catch-up {gap}s later — the catch-up would refuse over a dead holder")
    assert lock.DEFAULT_TTL_SECONDS == ttl


def test_the_advisory_comment_is_gone_from_the_backfill_workflow():
    """It told the owner to hand-schedule around the VM. The lock is the fix,
    and a stale 'avoid dispatching' note would outlive its own reason."""
    header = (WORKFLOWS / "alpaca-backfill.yml").read_text().split("\non:")[0]
    assert "Avoid dispatching" not in header
    assert "lock.py" in header
