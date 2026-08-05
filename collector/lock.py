#!/usr/bin/env python3
"""
lock.py — the cross-host mutex for the collector lanes.

Why this exists
---------------
Until 2026-08-04 every scheduled collection job ran on GitHub Actions under
`concurrency: group: collector`, which serialized them against each other.
The schedules then moved to systemd timers on the collector VM (deploy/
README.md), and that group quietly stopped meaning anything: it only covers
Actions runs, so a manual `workflow_dispatch` of collect-eod.yml or
alpaca-backfill.yml can now run *while* the VM's nightly chain does. Both
sides spend the same Alpaca account (one shared 200 req/min budget, which
alpaca.py paces against at REQS_PER_MIN=185 assuming it is alone) and write
the same R2 lake. The overlap corrupts nothing — it just makes both sides
crawl, which is worse than it sounds: the VM chain is killed at
TimeoutStartSec=2700, so a split budget turns a slow night into a truncated
one, and the derivations behind the top-up never run.

The lease
---------
One JSON object in R2 at state/collector.lock:

    {"host", "pid", "started_at", "ttl_seconds",   # the lease proper
     "holder", "token", "expires_at", "released_at"}

`acquire` refuses while a lease is live — not released, and not past
started_at + ttl_seconds. `release` does NOT delete the object: it stamps
released_at and puts it back, so the only R2 verb this needs is the
PutObject the collector already uses everywhere. A delete would read more
naturally, but it would be the single place in the pipeline that needs
DeleteObject, and finding out mid-release that the token lacks it would wedge
the lane for a full TTL — the exact failure this lock exists to prevent.

What it guarantees, and what it does not
----------------------------------------
Acquire is read → claim → settle → read back. Whoever's write lands last
wins; the loser reads back a token that is not its own and stands down. Two
hosts starting within a few seconds of each other therefore resolve to
exactly one winner. This is NOT a consensus primitive: if one side reads the
free lease before the other's claim lands *and* writes after the other has
already read back, both can win (and once that happens the late writer's
object is the one on record, so its release opens the lane while the first
winner is still working). That needs a claim to stall for longer than
SETTLE_SECONDS — a retried PUT — against a threat model measured in hours:
a person dispatching a backfill in the evening while tonight's chain runs.
The exact fix is a conditional PUT (`IfMatch` on the read ETag — same verb,
no new permission); it is deliberately not used here because its behaviour
against R2 cannot be verified from this test suite, and a conditional that
is silently ignored looks identical to one that works while quietly removing
the read-back that does.

Expiry is judged on the reader's clock against a `started_at` stamped by the
writer's, so cross-host skew shifts it by the skew. Both hosts run NTP;
sub-second drift against a 3000s TTL is not worth defending.

TTL is the only thing that recovers a holder which died without releasing (a
SIGKILL at the wall, a vanished runner, a reboot), so every caller passes a
TTL that covers its own wall and no more: 3000s for the 45-min EOD chain,
`max_minutes + 20` for a backfill dispatch. Nothing renews a lease mid-run —
a renewer that starved would drop the lease *under* a live holder and hand
the lane away silently, which is strictly worse than the bounded wedge it
would save. `release --force` is the manual escape hatch, and the refusal
pages through Healthchecks on the VM so a wedge is never discovered by
noticing missing data days later.

Usage:
    python lock.py acquire --holder vm-collect-eod --ttl 3000 \
        --token-file /tmp/collector.lock.token
    python lock.py release --token-file /tmp/collector.lock.token
    python lock.py status
"""

from __future__ import annotations

import argparse
import json
import math
import os
import socket
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from collect import r2_client, r2_get_json, r2_put_json

LOCK_KEY = "state/collector.lock"

# The EOD chain's own 2700s systemd wall plus 5 min of margin. Long enough
# that a legitimately slow chain never loses the lane it is still using,
# short enough that a hard-killed holder frees it before the 22:30 catch-up.
DEFAULT_TTL_SECONDS = 3000

# Nothing may lease the lane for longer than the longest legitimate holder:
# alpaca-backfill's 350-min job wall plus the 20 min of margin its TTL adds.
# Without this a fat-fingered `max_minutes` (3300 for 330) mints a multi-day
# lease and every nightly chain after it refuses — the wedge the TTL exists
# to bound in the first place.
MAX_TTL_SECONDS = 22200

# How long to wait between claiming the lease and reading it back. Has to
# exceed one R2 write→read round trip, or a concurrent claim that is still
# in flight reads back as our own win.
SETTLE_SECONDS = 3.0

# A transient R2 error on the way in must not cost a whole night. boto3
# retries within a call; this covers the rest (a blip landing exactly on
# 21:30:02) and never weakens a refusal, which is not an error.
ACQUIRE_ATTEMPTS = 3
ACQUIRE_RETRY_SECONDS = 5.0

EXIT_HELD = 75  # EX_TEMPFAIL — someone else holds the lane; try later


class LeaseHeld(RuntimeError):
    """Raised when the lane is not ours to take."""

    def __init__(self, lease: dict | None, reason: str,
                 now: datetime | None = None) -> None:
        self.lease = lease or {}
        self.reason = reason
        # `now` is the same instant the refusal was decided on, so the
        # expires_in the owner reads is the one we actually refused against.
        detail = describe(self.lease, now) if self.lease else "holder unknown"
        super().__init__(f"{reason} ({detail})")


# ------------------------------- lease reads -------------------------------

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(ts: datetime) -> str:
    return ts.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_ts(raw: object) -> datetime | None:
    if not isinstance(raw, str):
        return None
    try:
        ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def _expiry(lease: dict) -> datetime | None:
    """None when the lease cannot be read as one — see is_live."""
    started = _parse_ts(lease.get("started_at"))
    ttl = lease.get("ttl_seconds")
    if started is None or isinstance(ttl, bool) or not isinstance(ttl, (int, float)):
        return None
    if not math.isfinite(ttl):        # json.loads accepts Infinity and NaN
        return None
    try:
        return started + timedelta(seconds=float(ttl))
    except OverflowError:             # a ttl of 1e18, or a year-9999 started_at
        return None


def read_lease(s3) -> dict | None:
    """The current lease, or None if there isn't a readable one.

    Anything we cannot decode reads as "no lease" (see is_live for why that
    is the safe direction). This has to catch the DECODE, not just the shape:
    r2_get_json only handles NoSuchKey, so a truncated write, an empty body
    or non-UTF8 bytes would otherwise raise straight out of every subcommand
    — including the `release --force` the RUNBOOK sends you to run to clear
    exactly that object. Genuine R2 failures (unreachable, denied) still
    propagate: those are not "no lease", and the caller pages differently.
    """
    try:
        lease = r2_get_json(s3, LOCK_KEY, None)
    except (ValueError, UnicodeDecodeError) as exc:   # JSONDecodeError ⊂ ValueError
        print(f"WARNING: {LOCK_KEY} is not readable JSON ({exc}) — "
              "treating the lane as free", file=sys.stderr)
        return None
    return lease if isinstance(lease, dict) else None


def is_live(lease: dict | None, now: datetime | None = None) -> bool:
    """Whether `lease` still owns the lane.

    A lease we cannot parse (truncated write, hand-edited object, a schema
    change from a future version, a ttl of NaN) counts as FREE on purpose.
    Fail-open is the right default for exactly one reason: the alternative is
    a corrupt object silently stopping collection every night until someone
    reads a journal. Over-collecting is recoverable; a lane that never runs
    is the outage this whole subsystem was built after. Note the fail-open
    covers the OBJECT, not the store — an unreachable R2 still refuses, and
    says so differently, because a collector that cannot reach R2 has nothing
    to collect into anyway.
    """
    if not isinstance(lease, dict) or lease.get("released_at"):
        return False
    expiry = _expiry(lease)
    return expiry is not None and (now or _now()) < expiry


def describe(lease: dict, now: datetime | None = None) -> str:
    """One-line identification of a holder, for refusal messages."""
    now = now or _now()
    parts = [
        f"holder={lease.get('holder', '?')}",
        f"host={lease.get('host', '?')}",
        f"pid={lease.get('pid', '?')}",
        f"started_at={lease.get('started_at', '?')}",
    ]
    started = _parse_ts(lease.get("started_at"))
    if started is not None:
        parts.append(f"held_for={int((now - started).total_seconds())}s")
    expiry = _expiry(lease)
    if expiry is not None:
        parts.append(f"expires_in={int((expiry - now).total_seconds())}s")
    return " ".join(parts)


# ---------------------------- acquire / release ----------------------------

def acquire(
    s3,
    holder: str,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    settle_seconds: float = SETTLE_SECONDS,
    host: str | None = None,
    pid: int | None = None,
    now: datetime | None = None,
    token: str | None = None,
) -> dict:
    """Take the lane, or raise LeaseHeld naming whoever has it.

    Refuses a live lease from ANY host, including this one: a second local
    run overlapping the first is the same shared-budget problem, and the
    22:30 catch-up must not pile onto a 21:30 chain that is still going.

    `token` lets the caller mint the identity BEFORE this runs. That matters:
    between the claim landing in R2 and this function returning there is a
    settle window, and a process that dies inside it (a cancelled job, a
    reclaimed runner) would otherwise leave a live lease whose token exists
    nowhere — unreleasable until the TTL, which is up to six hours for a
    backfill. A token that never won releases as `foreign`, a no-op, so
    writing it down early is free.
    """
    ttl_seconds = int(ttl_seconds)
    if ttl_seconds <= 0:
        raise ValueError(f"ttl_seconds must be positive, got {ttl_seconds}")
    if ttl_seconds > MAX_TTL_SECONDS:
        print(f"WARNING: a ttl of {ttl_seconds}s exceeds the {MAX_TTL_SECONDS}s "
              "ceiling — clamping (no legitimate holder runs that long)",
              file=sys.stderr)
        ttl_seconds = MAX_TTL_SECONDS
    now = now or _now()

    current = read_lease(s3)
    if is_live(current, now):
        if token is not None and current.get("token") == token:
            # Our own claim, from an attempt whose read-back never came back.
            # Refusing here would page "leased elsewhere" naming ourselves and
            # wedge the lane until the TTL over a lease we already own.
            return current
        raise LeaseHeld(current, "the collector lane is leased", now)

    lease = {
        "host": host or socket.gethostname(),
        "pid": int(os.getpid() if pid is None else pid),
        "started_at": _iso(now),
        "ttl_seconds": ttl_seconds,
        "holder": holder,
        "token": token or uuid.uuid4().hex,
        # Informational only — every expiry decision recomputes from
        # started_at + ttl_seconds, so editing this by hand frees nothing.
        "expires_at": _iso(now + timedelta(seconds=ttl_seconds)),
        "released_at": None,
    }
    r2_put_json(s3, LOCK_KEY, lease)

    # Read back after the settle window: if a second host claimed the lane in
    # the same breath, exactly one of us sees its own token here.
    if settle_seconds > 0:
        time.sleep(settle_seconds)
    winner = read_lease(s3)
    if winner is None or winner.get("token") != lease["token"]:
        raise LeaseHeld(winner, "lost a concurrent acquire of the collector lane", now)
    return lease


def release(s3, token: str | None = None, force: bool = False,
            now: datetime | None = None) -> str:
    """Give the lane back. Returns what actually happened.

    `released` · `absent` (nothing to release) · `already-released` ·
    `expired` (ours, but the TTL beat us to it) · `foreign` (someone else's
    lease — leaving it alone is the whole point of the token).
    """
    now = now or _now()
    lease = read_lease(s3)
    if lease is None:
        return "absent"
    if lease.get("released_at"):
        return "already-released"
    ours = token is not None and token == lease.get("token")
    if not ours and not force:
        return "foreign"
    if ours and not is_live(lease, now):
        # Our own lease, already expired: the lane is free by TTL and this
        # write would gain nothing. It can LOSE something, though — between
        # the read above and the put below another host can legitimately
        # acquire, and a tombstone stamped on our stale copy would erase a
        # live lease and let a third run in. Not writing is the fix; the
        # precondition for that race is exactly this branch.
        return "expired"
    r2_put_json(s3, LOCK_KEY, {**lease, "released_at": _iso(now)})
    return "released"


# ---------------------------------- CLI ------------------------------------

def _token_from(args) -> str | None:
    if args.token:
        return args.token
    if args.token_file:
        path = Path(args.token_file)
        if path.is_file():
            return path.read_text().strip() or None
    return None


def build_parser() -> argparse.ArgumentParser:
    """Split out so the tests can run the REAL argv from collect-eod.sh and
    the workflows through it — a renamed flag in a caller is otherwise a
    green suite and a chain that refuses to start tonight."""
    ap = argparse.ArgumentParser(
        prog="lock", description="Cross-host lease lock for the collector lanes.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    acq = sub.add_parser("acquire", help="take the lane or refuse")
    acq.add_argument("--holder", required=True,
                     help="who is asking, e.g. vm-collect-eod (rides the refusal message)")
    acq.add_argument("--ttl", type=int, default=DEFAULT_TTL_SECONDS,
                     help=f"seconds before the lease self-expires (default {DEFAULT_TTL_SECONDS})")
    acq.add_argument("--settle", type=float, default=SETTLE_SECONDS,
                     help="seconds between claiming and reading back")
    acq.add_argument("--token-file", help="write the lease token here for release")

    rel = sub.add_parser("release", help="give the lane back")
    rel.add_argument("--token")
    rel.add_argument("--token-file")
    rel.add_argument("--force", action="store_true",
                     help="release someone else's lease (manual wedge recovery)")

    sub.add_parser("status", help="print the current lease")
    return ap


def main(argv: list[str] | None = None, s3=None) -> int:
    args = build_parser().parse_args(argv)
    s3 = s3 if s3 is not None else r2_client()

    if args.cmd == "acquire":
        # Written down BEFORE the claim can land, so there is no window where
        # a live lease exists with its token nowhere on disk.
        token = uuid.uuid4().hex
        if args.token_file:
            Path(args.token_file).write_text(token)
        for attempt in range(1, ACQUIRE_ATTEMPTS + 1):
            try:
                lease = acquire(s3, args.holder, ttl_seconds=args.ttl,
                                settle_seconds=args.settle, token=token)
                break
            except LeaseHeld as held:
                # Not an error: someone is working. Retrying would only delay
                # the page, and the 22:30 catch-up is the real second chance.
                print(f"REFUSING to start — {held}", file=sys.stderr)
                print("wait for it to finish, or `python lock.py release --force` "
                      "if you know that holder is dead", file=sys.stderr)
                return EXIT_HELD
            except Exception as exc:      # R2 unreachable, denied, throttled
                if attempt == ACQUIRE_ATTEMPTS:
                    raise
                print(f"lease attempt {attempt}/{ACQUIRE_ATTEMPTS} failed ({exc}) — "
                      f"retrying in {ACQUIRE_RETRY_SECONDS}s", file=sys.stderr)
                time.sleep(ACQUIRE_RETRY_SECONDS)
        print(f"lease acquired: {describe(lease)} token={lease['token']}")
        return 0

    if args.cmd == "release":
        outcome = release(s3, _token_from(args), force=args.force)
        if outcome == "foreign":
            print("NOT releasing: the lane belongs to someone else "
                  f"({describe(read_lease(s3) or {})}) — either our lease expired "
                  "under us, or we never won the claim", file=sys.stderr)
        elif outcome == "expired":
            # Worth a line in the journal: it means the run outlived its own
            # TTL, so another host could have started while it was working.
            print("lease had already EXPIRED before we released it — the lane "
                  "was free (and possibly taken) while this run was still going",
                  file=sys.stderr)
        else:
            print(f"lease {outcome}")
        return 0

    lease = read_lease(s3)
    if lease is None:
        print("no lease object — the lane is free")
        return 0
    print(json.dumps(lease, indent=2))
    print(("LEASED " if is_live(lease) else "free   ") + describe(lease))
    return 0


if __name__ == "__main__":
    sys.exit(main())
