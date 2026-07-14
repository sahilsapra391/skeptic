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
| `autoupdate.sh` + `skeptic-autoupdate.service` / `.timer` | nightly self-update: pull main, sync deps, restart the recorder — never inside/near the session. |
| `bootstrap.sh` | provision a fresh Ubuntu VM end to end (idempotent). |

## Provision (Oracle Cloud always-free — $0)

1. **Create the VM.** Oracle Cloud → Compute → Instances → Create. Pick an
   **Always Free** shape (Ampere ARM `VM.Standard.A1.Flex`, 1 OCPU / 6 GB is
   plenty, or an `E2.1.Micro`), image **Ubuntu 22.04/24.04**. Add your SSH key.
   No inbound ports are needed — the recorder only makes outbound calls.
2. **Copy secrets.** From your Mac:
   ```
   scp collector/.env ubuntu@<vm-ip>:/tmp/skeptic.env
   ```
   (After bootstrap creates `/opt/skeptic`, move it: `sudo mv /tmp/skeptic.env
   /opt/skeptic/collector/.env`. Or re-run bootstrap once it's in place.)
3. **Bootstrap.**
   ```
   ssh ubuntu@<vm-ip>
   curl -LsSf https://raw.githubusercontent.com/sahilsapra391/skeptic/main/collector/deploy/bootstrap.sh -o bootstrap.sh
   sudo bash bootstrap.sh
   ```
   It installs `uv`, clones the repo to `/opt/skeptic`, builds the venv, and
   enables the recorder + heartbeat. Re-run any time to redeploy after a merge.

## Paging

Set `ALERT_WEBHOOK` in `collector/.env` to page on a stalled session. Any of:
- **ntfy.sh** (no account): `ALERT_WEBHOOK=https://ntfy.sh/<your-random-topic>`,
  then subscribe to that topic on your phone.
- **Slack / Discord**: an incoming-webhook URL.

Without it, the heartbeat still logs to the journal (`journalctl -u
skeptic-heartbeat`), but nothing pushes to your phone.

## Self-update (how merged code reaches the VM)

`skeptic-autoupdate.timer` runs `autoupdate.sh` daily at 3 AM ET: fetch the
checked-out branch (whatever `SKEPTIC_BRANCH` the VM was bootstrapped onto);
if nothing changed, exit; otherwise ff-only merge, `uv sync`, reinstall units,
restart the recorder. Two safety properties:

- **Session-safe.** The script re-checks the XNYS session window with a
  30-min lookahead and skips inside it, because `Persistent=true` replays a
  missed run at boot — which can be any hour. The unit's
  `TimeoutStartSec=1500` then bounds the whole run at 25 min, so even a
  stalled fetch can never carry the recorder restart into the session.
- **Fail-safe.** Any failure (non-ff merge after a force-push, dep
  resolution, the 25-min kill) exits non-zero and leaves the running
  deployment untouched. Check `/var/log/skeptic/autoupdate.log` or
  `systemctl status skeptic-autoupdate`.

What it can never do: deliver secrets. A new data source's API key still has
to be added to `/opt/skeptic/collector/.env` by hand.

Manual redeploy right now (don't wait for 3 AM): `sudo systemctl start
skeptic-autoupdate.service` — same script, same session guard.

## Verify

```
systemctl status skeptic-intraday      # active (running)
journalctl -u skeptic-intraday -f       # cboe SPY: NNNNN rows, every minute in-session
systemctl list-timers skeptic-heartbeat # next run within 5 min
```

Writes go to the same R2 bucket as before, so the Observatory and the engine see
the new snapshots with no other change. Once this is banking a full session,
disable the Mac's `com.skeptic.intraday` launchd job so the two don't double-write.
