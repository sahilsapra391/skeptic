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
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import lock
import pytest

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
    assert lock.release(s3, lease["token"]) == "released"
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
    assert lock.release(s3, lease["token"]) == "released"
    assert lock.release(s3, lease["token"]) == "already-released"


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
    # no token file means the release step has nothing to undo
    assert not token_file.exists()


def test_cli_release_of_a_foreign_lease_stays_quiet_and_exits_zero(s3, capsys):
    """`if: always()` runs this after a failed job; turning the job red over a
    lease that already moved on would cry wolf about the wrong thing."""
    _write_lease(s3)
    assert lock.main(["release", "--token", "ours"], s3=s3) == 0
    assert "NOT releasing" in capsys.readouterr().err
    assert lock.is_live(lock.read_lease(s3), NOW)


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


def _run_chain(tmp_path: Path, acquire_rc: int = 0) -> dict:
    """Drive collect-eod.sh against a stub `uv` that answers the lock CLI."""
    script = _stage(tmp_path) / "collect-eod.sh"
    calls, locks, pings = (tmp_path / n for n in ("calls.log", "locks.log", "pings.log"))

    stub = tmp_path / "uv"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        'args=("$@")\n'
        'for ((i=0; i<${#args[@]}; i++)); do\n'
        '  case "${args[i]}" in\n'
        # the HEALTHCHECK_URL read-back, same shape as the real thing
        f'    -c) grep -E "^HEALTHCHECK_URL=" "{tmp_path}/.env" | cut -d= -f2-; exit 0 ;;\n'
        '    -m) if [ "${args[i+1]:-}" = "lock" ]; then\n'
        f'          echo "${{args[i+2]:-?}}" >> "{locks}"\n'
        '          case "${args[i+2]:-}" in\n'
        f'            acquire) exit {acquire_rc} ;;\n'
        "            *) exit 0 ;;\n"
        "          esac\n"
        "        fi ;;\n"
        "  esac\n"
        "done\n"
        'script=""\n'
        'for a in "${args[@]}"; do case "$a" in *.py) script="$a" ;; esac; done\n'
        f'echo "$script" >> "{calls}"\n'
        "exit 0\n"
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
    result = _run_chain(tmp_path)
    sh = (DEPLOY / "collect-eod.sh").read_text()
    assert "trap release_lease EXIT" in sh
    # bash skips the EXIT trap on an untrapped signal, which is precisely the
    # 2700s-wall case
    assert re.search(r"^trap .*TERM INT$", sh, re.M)
    assert result["lock"][-1] == "release"


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

    acquire = next(i for i, (_, b) in enumerate(steps) if "-m lock acquire" in b)
    assert "id: lease" in steps[acquire][1]
    work = [i for i, (_, b) in enumerate(steps)
            if "uv run python" in b and "-m lock" not in b]
    assert work and min(work) > acquire, f"{workflow}: work runs before the lease"

    release = next(n for n, b in steps if "-m lock release" in b)
    assert "if: always()" in bodies[release], f"{workflow}: a cancelled job keeps the lane"
    assert names[-1] == release, f"{workflow}: release must be the last step"


@pytest.mark.parametrize("workflow", LEASED_WORKFLOWS)
def test_no_always_step_outruns_a_refused_lease(workflow):
    """`if: always()` steps run even when an earlier step failed — including
    when the failure IS the refusal. Ungated, every derivation would still
    write the lake the holder is writing, which is the overlap this prevents."""
    for name, body in _steps((WORKFLOWS / workflow).read_text()):
        if "-m lock release" in body:
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


def test_the_advisory_comment_is_gone_from_the_backfill_workflow():
    """It told the owner to hand-schedule around the VM. The lock is the fix,
    and a stale 'avoid dispatching' note would outlive its own reason."""
    header = (WORKFLOWS / "alpaca-backfill.yml").read_text().split("\non:")[0]
    assert "Avoid dispatching" not in header
    assert "lock.py" in header
