#!/usr/bin/env bash
# autoupdate.sh — nightly self-update for the collector VM (owner directive:
# new code on main should reach the recorder with no manual redeploy, see
# docs "self-improvement thesis"). Run by skeptic-autoupdate.timer at 3 AM ET.
#
# Fail-safe by construction: any failure (fetch, non-ff merge, uv sync) exits
# non-zero and leaves the currently-deployed code running untouched — the
# heartbeat keeps guarding the outcome that matters (snapshots landing). And
# because the "already deployed" gate reads a marker written only after the
# FULL chain succeeds, a failed run is retried the next night rather than
# latching a half-applied deploy behind an "up to date" message.
#
# Never touches the recorder inside or near the options session: the timer
# fires at 3 AM ET, but Persistent=true replays a missed run at boot, which
# can be ANY hour — so the script re-checks the session window itself and
# skips from 30 min before the open until close+15min.
set -euo pipefail

# This script runs as root and drives git as root (deploy key in /root/.ssh,
# safe.directory in /root/.gitconfig). systemd starts a service with no HOME,
# so without this every run died at "fatal: $HOME not set" before deploying —
# a silent stall the snapshot heartbeat can't see (07-14..16, VM stuck 3 days
# behind main). Set it here, at the source, so the script is correct under the
# unit, a manual `sudo bash autoupdate.sh`, or a boot-time replay alike.
export HOME=/root

DEST=/opt/skeptic
LOG=/var/log/skeptic/autoupdate.log
exec >>"$LOG" 2>&1
echo "== $(date -u +%FT%TZ) autoupdate =="

cd "$DEST/collector"

guard=$(sudo -u skeptic env HOME=/home/skeptic /usr/local/bin/uv run python - <<'PY'
import sys
sys.path.insert(0, "deploy")
import pandas as pd
from collect import nyse
from heartbeat import session_window

cal = nyse()
now = pd.Timestamp.now(tz="UTC")
# also test 30 min ahead so a late replayed run can't restart the recorder
# moments before the open
inside = any(session_window(t, cal) for t in (now, now + pd.Timedelta(minutes=30)))
print("skip" if inside else "go")
PY
)
if [ "$guard" != "go" ]; then
    echo "guard said '$guard' — skipping this run"
    exit 0
fi

# /opt/skeptic is owned by the skeptic user but git here runs as root; without
# this, every fetch dies on "dubious ownership" (idempotent: adds only once)
git config --global --get-all safe.directory 2>/dev/null | grep -qx "$DEST" \
    || git config --global --add safe.directory "$DEST"

# follow whatever branch the VM was bootstrapped onto (SKEPTIC_BRANCH pin),
# not a hardcoded main
BRANCH=$(git -C "$DEST" rev-parse --abbrev-ref HEAD)

before=$(git -C "$DEST" rev-parse HEAD)
git -C "$DEST" fetch origin "$BRANCH"
after=$(git -C "$DEST" rev-parse "origin/$BRANCH")
# Gate on the last FULLY-DEPLOYED sha, not on HEAD. The ff-merge below moves
# HEAD before the syncs, unit install and restart run, so any failure after it
# used to latch permanently: the next night saw HEAD == origin, printed "up to
# date", and exited — leaving units never installed and the recorder never
# restarted, with nothing paging (the heartbeat only watches snapshots). Every
# step here is idempotent, so re-running the whole chain until it succeeds once
# is always safe.
MARKER=/var/lib/skeptic/last-deployed
mkdir -p "$(dirname "$MARKER")"
deployed=$(cat "$MARKER" 2>/dev/null || echo "")
if [ "$after" = "$deployed" ]; then
    echo "up to date at ${after:0:9} (fully deployed)"
    exit 0
fi
if [ "$before" = "$after" ] && [ -n "$deployed" ]; then
    echo "code at ${after:0:9} but last full deploy was ${deployed:0:9} — re-running the deploy chain"
fi

echo "updating ${before:0:9} -> ${after:0:9}"
git -C "$DEST" merge --ff-only "$after"   # non-ff (drift/force-push) = loud failure
chown -R skeptic "$DEST"
sudo -u skeptic env HOME=/home/skeptic /usr/local/bin/uv sync
# backend/ deps too: the improve scan imports app.* from $DEST/backend, and
# bootstrap.sh only syncs it once at provision. Without this, the first
# backend dependency change that lands via a nightly pull makes the improve
# timer pay for a cold `uv sync` inside its own TimeoutStartSec — the most
# OOM-prone operation on this 1 GB box. Pay it here instead, at 3 AM ET with
# the session guard already passed and swap available.
#
# NON-FATAL on purpose (hence the `|| echo` under `set -e`): the improve unit
# runs through `uv run`, which re-syncs on its own, so a backend-only hiccup
# must not block the unit install and recorder restart below — that ordering
# is what turned a single OOM into a stuck deploy.
(cd "$DEST/backend" && sudo -u skeptic env HOME=/home/skeptic /usr/local/bin/uv sync) \
    || echo "!! backend uv sync failed — skeptic-improve's own 'uv run' will self-heal at 07:15"

# Units may have changed in the pull; reinstall them ALL — by glob, because
# the hand-listed version silently skipped units added after it was written
# (skeptic-keepwarm landed in #109 and this list never learned about it), so
# a VM that only ever self-updates would keep running yesterday's unit set
# while the comment claimed otherwise. Enabling stays with bootstrap.sh: a
# unit the owner deliberately disabled must not come back on a nightly pull.
install -m644 "$DEST"/collector/deploy/skeptic-*.service /etc/systemd/system/
install -m644 "$DEST"/collector/deploy/skeptic-*.timer /etc/systemd/system/
systemctl daemon-reload
# restart only the long-lived recorder (the heartbeat is a fresh process
# every fire, and the keep-warm ping is a oneshot)
systemctl restart skeptic-intraday.service
# Only NOW is the deploy complete; anything above failing means tomorrow
# re-runs the whole chain instead of declaring itself up to date.
echo "$after" > "$MARKER"
echo "deployed ${after:0:9}; recorder restarted"
