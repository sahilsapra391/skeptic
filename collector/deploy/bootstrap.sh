#!/usr/bin/env bash
# bootstrap.sh — stand up the Skeptic collector on a fresh Ubuntu VM (Oracle
# Cloud always-free, or any always-on host). Run as root: `sudo bash bootstrap.sh`.
# Idempotent: safe to re-run to redeploy after a `git pull`.
set -euo pipefail

# The repo is private, so an unauthenticated https clone fails. Default to SSH,
# which needs a read-only deploy key for root on this VM (bootstrap runs git as
# root) — see deploy/README.md "Provision" for the setup.
REPO="${SKEPTIC_REPO:-git@github.com:sahilsapra391/skeptic.git}"
BRANCH="${SKEPTIC_BRANCH:-main}"
DEST=/opt/skeptic
SVC_USER=skeptic

echo "== packages =="
apt-get update -y
apt-get install -y git curl ca-certificates

echo "== service user + log dir =="
id "$SVC_USER" &>/dev/null || useradd --system --create-home --shell /usr/sbin/nologin "$SVC_USER"
mkdir -p /var/log/skeptic && chown "$SVC_USER" /var/log/skeptic

echo "== uv (pinned python + venv) =="
# The systemd units call /usr/local/bin/uv by absolute path, so guarantee uv is
# there. UV_INSTALL_DIR is honored by current installers, but pin the result with
# a symlink so a version that ignores it (landing uv in ~/.local/bin or
# ~/.cargo/bin) still leaves a working /usr/local/bin/uv.
if [ ! -x /usr/local/bin/uv ]; then
    curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh || true
    if [ ! -x /usr/local/bin/uv ]; then
        found="$(command -v uv || echo /root/.local/bin/uv)"
        [ -x "$found" ] || { echo "!! uv install failed — install it manually" >&2; exit 1; }
        ln -sf "$found" /usr/local/bin/uv
    fi
fi

echo "== code ($BRANCH) =="
# git runs here as root, but the tree gets chowned to $SVC_USER below, so
# without this every re-run dies on "detected dubious ownership".
git config --global --get-all safe.directory 2>/dev/null | grep -qx "$DEST" \
    || git config --global --add safe.directory "$DEST"
# Trust github.com's host key so the SSH clone doesn't stall on an interactive
# prompt (the deploy key itself is created manually — see deploy/README.md).
if ! ssh-keygen -F github.com -f /root/.ssh/known_hosts >/dev/null 2>&1; then
    mkdir -p /root/.ssh && chmod 700 /root/.ssh
    ssh-keyscan github.com >> /root/.ssh/known_hosts
fi
if [ -d "$DEST/.git" ]; then
    # Keep the remote in sync with $REPO so a clone made before the SSH default
    # (or with a different SKEPTIC_REPO) doesn't keep fetching from a dead URL.
    git -C "$DEST" remote set-url origin "$REPO"
    git -C "$DEST" fetch origin "$BRANCH" && git -C "$DEST" checkout "$BRANCH" && git -C "$DEST" pull --ff-only
else
    git clone --branch "$BRANCH" "$REPO" "$DEST"
fi
chown -R "$SVC_USER" "$DEST"

echo "== python env (collector) =="
cd "$DEST/collector"
sudo -u "$SVC_USER" env HOME=/home/"$SVC_USER" /usr/local/bin/uv sync

# The nightly unlock scan runs out of backend/ and imports app.db (sqlalchemy +
# psycopg2), which the collector venv does not carry. Sync it here so the timer
# never pays for a first-run resolve inside its own TimeoutStartSec. On the 1 GB
# E2.1.Micro this is the step most likely to OOM — the 2 GB swapfile from the
# Provision section above is what makes it fit.
echo "== python env (backend) =="
cd "$DEST/backend"
sudo -u "$SVC_USER" env HOME=/home/"$SVC_USER" /usr/local/bin/uv sync
cd "$DEST/collector"

# .env is supplied out of band and NEVER committed. It carries R2_* and the
# vendor keys, plus optional ALERT_WEBHOOK for the heartbeat page.
if [ ! -f "$DEST/collector/.env" ]; then
    cat >&2 <<EOF

!! Missing $DEST/collector/.env
   Copy your collector/.env (R2_* + vendor keys, and optionally ALERT_WEBHOOK)
   to the VM, then re-run this script:
       scp collector/.env <vm>:$DEST/collector/.env
EOF
    exit 1
fi
chown "$SVC_USER" "$DEST/collector/.env"
chmod 600 "$DEST/collector/.env"

echo "== systemd units =="
# Install by GLOB, matching autoupdate.sh. The hand-listed version there
# silently skipped skeptic-keepwarm when it landed in #109, and this list was
# drifting the same way as the collect-eod/quality/improve units arrived.
# Enabling stays explicit below: a unit deliberately disabled must not come
# back just because someone re-ran bootstrap.
install -m644 deploy/skeptic-*.service /etc/systemd/system/
install -m644 deploy/skeptic-*.timer   /etc/systemd/system/
chmod +x deploy/autoupdate.sh deploy/collect-eod.sh
systemctl daemon-reload
systemctl enable --now skeptic-intraday.service
systemctl enable --now skeptic-heartbeat.timer
systemctl enable --now skeptic-autoupdate.timer
systemctl enable --now skeptic-keepwarm.timer
# Scheduled collection moved off GitHub Actions 2026-08-04 (billing block took
# the EOD job down silently). See "Scheduled jobs" in README.md.
systemctl enable --now skeptic-collect-eod.timer
systemctl enable --now skeptic-quality.timer
systemctl enable --now skeptic-improve.timer

echo
echo "== recorder status =="
systemctl --no-pager status skeptic-intraday.service | head -12
echo
echo "Done. Tail the recorder with:  journalctl -u skeptic-intraday -f"
echo "Tail the heartbeat with:       journalctl -u skeptic-heartbeat -f"
