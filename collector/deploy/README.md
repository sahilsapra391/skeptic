# Running the collector off the laptop

The intraday recorder is a ~6.75h/day, 1-minute loop. Private-repo GitHub
Actions free minutes (2,000/mo) cannot host it (~8,200 runner-min/mo), so it has
lived on the owner's Mac under launchd. That coupled data collection to a laptop
that sleeps: on 2026-07-09 the Mac slept overnight, macOS suspended the
recorder's `time.sleep()`, and it woke too late and missed 164 min of the
session while looking alive.

This directory moves the recorder to an **always-on VM under systemd**, which
never sleeps, restarts on any crash, and pages you if a session goes quiet.

## What's here

| File | Role |
|---|---|
| `skeptic-intraday.service` | systemd unit for `intraday.py`, `Restart=always`. |
| `heartbeat.py` | alerts if no fresh snapshot lands during a session. |
| `skeptic-heartbeat.service` / `.timer` | run the heartbeat every 5 min. |
| `bootstrap.sh` | provision a fresh Ubuntu VM end to end (idempotent). |

## Provision (Oracle Cloud always-free — $0)

The repo is **private**, so nothing on the VM can fetch it anonymously: the
raw.githubusercontent.com URL for `bootstrap.sh` returns 404, and an
unauthenticated `git clone` fails. The steps below (validated on the real
Oracle VM, 2026-07-13/14) copy the script over scp and give the VM a
read-only deploy key for the clone.

1. **Create the VM.** Oracle Cloud → Compute → Instances → Create. Pick an
   **Always Free** shape, image **Ubuntu 22.04/24.04**, and add your SSH key.
   No inbound ports are needed — the recorder only makes outbound calls.
   `VM.Standard.A1.Flex` (Ampere ARM, 1 OCPU / 6 GB) is the comfortable pick,
   but A1 capacity is often unavailable ("Out of capacity" at launch); the
   realistic fallback is `VM.Standard.E2.1.Micro` (x86, 1 GB RAM), which works
   fine with the swapfile from step 2.
2. **E2.1.Micro only: add a 2 GB swapfile** so `uv sync` isn't OOM-killed on
   1 GB of RAM:
   ```
   sudo fallocate -l 2G /swapfile
   sudo chmod 600 /swapfile
   sudo mkswap /swapfile
   sudo swapon /swapfile
   echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
   ```
3. **Give the VM read access to the repo.** Bootstrap runs `git` as root, so
   the deploy key belongs to root. On the VM:
   ```
   sudo ssh-keygen -t ed25519 -N '' -f /root/.ssh/id_ed25519
   sudo cat /root/.ssh/id_ed25519.pub
   ```
   Add the printed public key at GitHub → repo → **Settings → Deploy keys →
   Add deploy key**. Leave **"Allow write access" unchecked** — the VM only
   ever pulls. (Bootstrap adds github.com to root's `known_hosts` itself.)
4. **Copy secrets + the script.** From your Mac's checkout:
   ```
   scp collector/.env ubuntu@<vm-ip>:/tmp/skeptic.env
   scp collector/deploy/bootstrap.sh ubuntu@<vm-ip>:/tmp/
   ```
   (The first bootstrap run stops at its missing-`.env` check after creating
   `/opt/skeptic`. Move the env into place — `sudo mv /tmp/skeptic.env
   /opt/skeptic/collector/.env` — then re-run bootstrap to finish.)
5. **Bootstrap.**
   ```
   ssh ubuntu@<vm-ip>
   sudo bash /tmp/bootstrap.sh
   ```
   It installs `uv`, clones the repo to `/opt/skeptic` over SSH
   (`git@github.com:sahilsapra391/skeptic.git` by default; override with
   `sudo SKEPTIC_REPO=... bash /tmp/bootstrap.sh`), builds the venv, and
   enables the recorder + heartbeat. Re-run any time to redeploy after a
   merge — the clone pulls with the same read-only deploy key.

## Paging

Set `ALERT_WEBHOOK` in `collector/.env` to page on a stalled session. Any of:
- **ntfy.sh** (no account): `ALERT_WEBHOOK=https://ntfy.sh/<your-random-topic>`,
  then subscribe to that topic on your phone.
- **Slack / Discord**: an incoming-webhook URL.

Without it, the heartbeat still logs to the journal (`journalctl -u
skeptic-heartbeat`), but nothing pushes to your phone.

## Verify

```
systemctl status skeptic-intraday      # active (running)
journalctl -u skeptic-intraday -f       # cboe SPY: NNNNN rows, every minute in-session
systemctl list-timers skeptic-heartbeat # next run within 5 min
```

Writes go to the same R2 bucket as before, so the Observatory and the engine see
the new snapshots with no other change. Once this is banking a full session,
disable the Mac's `com.skeptic.intraday` launchd job so the two don't double-write.
