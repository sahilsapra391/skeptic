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
already read back, both can win. That window is about as wide as one R2
round trip, against a threat model measured in hours — a person dispatching
a backfill in the evening while tonight's chain runs.

TTL is the only thing that recovers a holder which died without releasing (a
SIGKILL at the wall, a vanished runner, a reboot), so every caller passes a
TTL that covers its own wall and no more: 3000s for the 45-min EOD chain,
`max_minutes + 20` for a backfill dispatch. Nothing renews a lease mid-run —
a renewer that starves would drop the lease *under* a live holder and hand
the lane away silently, which is strictly worse than the bounded wedge it
would save. `release --force` is the manual escape hatch, and the refusal
pages through Healthchecks on the VM so a wedge is never discovered by
noticing missing data days later.

Usage:
    python -m lock acquire --holder vm-collect-eod --ttl 3000 \
        --token-file /tmp/collector.lock.token
    python -m lock release --token-file /tmp/collector.lock.token
    python -m lock status
"""

from __future__ import annotations

import argparse
import json
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

# How long to wait between claiming the lease and reading it back. Has to
# exceed one R2 write→read round trip, or a concurrent claim that is still
# in flight reads back as our own win.
SETTLE_SECONDS = 3.0

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
    return started + timedelta(seconds=float(ttl))


def read_lease(s3) -> dict | None:
    lease = r2_get_json(s3, LOCK_KEY, None)
    return lease if isinstance(lease, dict) else None


def is_live(lease: dict | None, now: datetime | None = None) -> bool:
    """Whether `lease` still owns the lane.

    A lease we cannot parse (truncated write, hand-edited object, a schema
    change from a future version) counts as FREE on purpose. Fail-open is the
    right default for exactly one reason: the alternative is a corrupt object
    silently stopping collection every night until someone reads a journal.
    Over-collecting is recoverable; a lane that never runs is the outage this
    whole subsystem was built after.
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
) -> dict:
    """Take the lane, or raise LeaseHeld naming whoever has it.

    Refuses a live lease from ANY host, including this one: a second local
    run overlapping the first is the same shared-budget problem, and the
    22:30 catch-up must not pile onto a 21:30 chain that is still going.
    """
    ttl_seconds = int(ttl_seconds)
    if ttl_seconds <= 0:
        raise ValueError(f"ttl_seconds must be positive, got {ttl_seconds}")
    now = now or _now()

    current = read_lease(s3)
    if is_live(current, now):
        raise LeaseHeld(current, "the collector lane is leased", now)

    lease = {
        "host": host or socket.gethostname(),
        "pid": int(os.getpid() if pid is None else pid),
        "started_at": _iso(now),
        "ttl_seconds": ttl_seconds,
        "holder": holder,
        "token": uuid.uuid4().hex,
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
    `foreign` (our lease expired and someone else took the lane — leaving
    theirs alone is the whole point of the token).
    """
    lease = read_lease(s3)
    if lease is None:
        return "absent"
    if lease.get("released_at"):
        return "already-released"
    if not force and (token is None or token != lease.get("token")):
        return "foreign"
    r2_put_json(s3, LOCK_KEY, {**lease, "released_at": _iso(now or _now())})
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


def main(argv: list[str] | None = None, s3=None) -> int:
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

    args = ap.parse_args(argv)
    s3 = s3 if s3 is not None else r2_client()

    if args.cmd == "acquire":
        try:
            lease = acquire(s3, args.holder, ttl_seconds=args.ttl,
                            settle_seconds=args.settle)
        except LeaseHeld as held:
            print(f"REFUSING to start — {held}", file=sys.stderr)
            print("wait for it to finish, or `python -m lock release --force` "
                  "if you know that holder is dead", file=sys.stderr)
            return EXIT_HELD
        if args.token_file:
            Path(args.token_file).write_text(lease["token"])
        print(f"lease acquired: {describe(lease)} token={lease['token']}")
        return 0

    if args.cmd == "release":
        outcome = release(s3, _token_from(args), force=args.force)
        if outcome == "foreign":
            print("NOT releasing: the lane now belongs to someone else "
                  f"({describe(read_lease(s3) or {})}) — our lease had expired",
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
