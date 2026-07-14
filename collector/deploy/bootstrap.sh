#!/usr/bin/env bash
# bootstrap.sh — stand up the Skeptic collector on a fresh Ubuntu VM (Oracle
# Cloud always-free, or any always-on host). Run as root: `sudo bash bootstrap.sh`.
# Idempotent: safe to re-run to redeploy after a `git pull`.
set -euo pipefail

REPO="${SKEPTIC_REPO:-https://github.com/sahilsapra391/skeptic.git}"
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
if [ -d "$DEST/.git" ]; then
    git -C "$DEST" fetch origin "$BRANCH" && git -C "$DEST" checkout "$BRANCH" && git -C "$DEST" pull --ff-only
else
    git clone --branch "$BRANCH" "$REPO" "$DEST"
fi
chown -R "$SVC_USER" "$DEST"

echo "== python env =="
cd "$DEST/collector"
sudo -u "$SVC_USER" env HOME=/home/"$SVC_USER" /usr/local/bin/uv sync

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
install -m644 deploy/skeptic-intraday.service /etc/systemd/system/
install -m644 deploy/skeptic-heartbeat.service /etc/systemd/system/
install -m644 deploy/skeptic-heartbeat.timer   /etc/systemd/system/
install -m644 deploy/skeptic-autoupdate.service /etc/systemd/system/
install -m644 deploy/skeptic-autoupdate.timer   /etc/systemd/system/
chmod +x deploy/autoupdate.sh
systemctl daemon-reload
systemctl enable --now skeptic-intraday.service
systemctl enable --now skeptic-heartbeat.timer
systemctl enable --now skeptic-autoupdate.timer

echo
echo "== recorder status =="
systemctl --no-pager status skeptic-intraday.service | head -12
echo
echo "Done. Tail the recorder with:  journalctl -u skeptic-intraday -f"
echo "Tail the heartbeat with:       journalctl -u skeptic-heartbeat -f"
