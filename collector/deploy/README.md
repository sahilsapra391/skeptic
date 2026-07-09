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

## Verify

```
systemctl status skeptic-intraday      # active (running)
journalctl -u skeptic-intraday -f       # cboe SPY: NNNNN rows, every minute in-session
systemctl list-timers skeptic-heartbeat # next run within 5 min
```

Writes go to the same R2 bucket as before, so the Observatory and the engine see
the new snapshots with no other change. Once this is banking a full session,
disable the Mac's `com.skeptic.intraday` launchd job so the two don't double-write.
