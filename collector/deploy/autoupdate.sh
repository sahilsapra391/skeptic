#!/usr/bin/env bash
# autoupdate.sh — nightly self-update for the collector VM (owner directive:
# new code on main should reach the recorder with no manual redeploy, see
# docs "self-improvement thesis"). Run by skeptic-autoupdate.timer at 3 AM ET.
#
# Fail-safe by construction: any failure (fetch, non-ff merge, uv sync) exits
# non-zero and leaves the currently-deployed code running untouched — the
# heartbeat keeps guarding the outcome that matters (snapshots landing).
#
# Never touches the recorder inside or near the options session: the timer
# fires at 3 AM ET, but Persistent=true replays a missed run at boot, which
# can be ANY hour — so the script re-checks the session window itself and
# skips from 30 min before the open until close+15min.
set -euo pipefail

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
if [ "$before" = "$after" ]; then
    echo "up to date at ${before:0:9}"
    exit 0
fi

echo "updating ${before:0:9} -> ${after:0:9}"
git -C "$DEST" merge --ff-only "$after"   # non-ff (drift/force-push) = loud failure
chown -R skeptic "$DEST"
sudo -u skeptic env HOME=/home/skeptic /usr/local/bin/uv sync

# units may have changed in the pull; reinstall them all, then restart only
# the long-lived recorder (the heartbeat is a fresh process every fire)
install -m644 "$DEST"/collector/deploy/skeptic-intraday.service /etc/systemd/system/
install -m644 "$DEST"/collector/deploy/skeptic-heartbeat.service /etc/systemd/system/
install -m644 "$DEST"/collector/deploy/skeptic-heartbeat.timer /etc/systemd/system/
install -m644 "$DEST"/collector/deploy/skeptic-autoupdate.service /etc/systemd/system/
install -m644 "$DEST"/collector/deploy/skeptic-autoupdate.timer /etc/systemd/system/
systemctl daemon-reload
systemctl restart skeptic-intraday.service
echo "deployed ${after:0:9}; recorder restarted"
