# Running the collector off the laptop

The intraday recorder is a ~6.75h/day, 1-minute loop. Private-repo GitHub
Actions free minutes (2,000/mo) cannot host it (~8,200 runner-min/mo), so it has
lived on the owner's Mac under launchd. That coupled data collection to a laptop
that sleeps: on 2026-07-09 the Mac slept overnight, macOS suspended the
recorder's `time.sleep()`, and it woke too late and missed 164 min of the
session while looking alive.

This directory moves the recorder to an **always-on VM under systemd**, which
never sleeps, restarts on any crash, and pages you if a session goes quiet.

Since 2026-08-04 it also owns the three scheduled collection jobs that used to
run on GitHub Actions — see "Scheduled jobs" below for why they followed.

## What's here

| File | Role |
|---|---|
| `skeptic-intraday.service` | systemd unit for `intraday.py`, `Restart=always`. |
| `heartbeat.py` | alerts if no fresh snapshot lands during a session. |
| `skeptic-heartbeat.service` / `.timer` | run the heartbeat every 5 min. |
| `autoupdate.sh` + `skeptic-autoupdate.service` / `.timer` | nightly self-update: pull main, sync deps, restart the recorder — never inside/near the session. |
| `collect-eod.sh` + `skeptic-collect-eod.service` / `.timer` | nightly EOD collection chain, 21:30 UTC + 22:30 UTC catch-up, Mon–Fri. |
| `skeptic-quality.service` / `.timer` | weekly data-quality scan, Sat 13:00 UTC. |
| `skeptic-improve.service` / `.timer` | nightly unlock scan (ENGINE-V3 D3), Tue–Sat 07:00 UTC. |
| `skeptic-keepwarm.service` / `.timer` | ping `skeptic.fyi/api/health` every 5 min so the first idea of the day never lands on a cold Railway box (one ping warms the Vercel proxy AND the backend; a GitHub Actions cron at */5 would bill ~9k private-repo minutes/month — the VM timer is free). |
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

## Scheduled jobs (moved off GitHub Actions, 2026-08-04)

The recorder came here because a sleeping laptop cost 164 min of a session.
The scheduled collectors came here for a different reason: private-repo Actions
minutes bill against the **account**, and a billing block refused to start the
nightly EOD job outright. Both scheduled runs died in under 5 s with "the job
was not started", which means `collect.py` never ran — so it pinged neither
success nor `/fail`, and the only evidence was the Healthchecks tile going
quiet. A data pipeline whose scheduler can be switched off by a payment problem
is not a pipeline you can trust overnight.

| Timer | When (UTC) | Runs |
|---|---|---|
| `skeptic-collect-eod.timer` | `Mon-Fri 21:30` + `22:30` | `collect-eod.sh` — the full 11-step chain |
| `skeptic-quality.timer` | `Sat 13:00` | `collect.py --mode quality` |
| `skeptic-improve.timer` | `Tue-Sat 07:00` | `backend/scripts/nightly_improve.py --execute` |

Three things worth knowing before you touch any of it:

- **The UTC slots are load-bearing.** They are the exact crons the workflows
  carried, which is what let the Healthchecks check survive the move without a
  dashboard edit. Re-pinning them to `America/New_York` is a real improvement
  (no DST drift) but you must update the check's schedule in the same change,
  or it alerts falsely twice a year.
- **`collect-eod.timer` is deliberately not `Persistent=`.** A replay fires at
  boot, which can be any hour; mid-session it would write a partial chain as if
  it were the close. The 22:30 catch-up is the intended safety net instead.
- **What did NOT move:** the Saturday calibration + priorities pass in
  `nightly-improve.yml`. It opens a proposal PR, which needs repo write, and
  this VM's deploy key is read-only on purpose. Giving an always-on box push
  access to the repo that deploys prod is a bigger change than it looks. Its
  cron is now `30 7 * * 6`, 30 min behind the VM's scan, which preserves the
  original scan-then-weekly order across the two hosts.

### Deploying a new or changed timer

`autoupdate.sh` installs unit files by glob every night, but deliberately does
not `enable` anything — a unit you disabled on purpose must not come back on a
pull. So a NEW timer needs one manual enable after the code lands:

```
sudo systemctl start skeptic-autoupdate.service     # pull now — see the guard note
sudo -u skeptic env HOME=/home/skeptic /usr/local/bin/uv sync --project /opt/skeptic/backend
sudo systemctl enable --now skeptic-collect-eod.timer skeptic-quality.timer skeptic-improve.timer
systemctl list-timers "skeptic-*"                   # confirm next elapse
```

Three things that will bite you on the cutover day, in order:

1. **Merging deletes the Actions crons immediately.** Until the enable below
   lands there is no scheduler on either host, so finish this before the next
   slot — 21:30 UTC for the EOD chain. The `workflow_dispatch` fallback covers
   a night you miss.
2. **`systemctl start skeptic-autoupdate.service` exits 0 without pulling
   inside the session guard** (30 min before the open through close+15 min).
   That is a *silent* no-op — the only evidence is `guard said 'skip'` in
   `/var/log/skeptic/autoupdate.log`. Run the cutover outside that window, and
   confirm with `git -C /opt/skeptic log -1` before enabling.
3. **`backend/`'s venv does not exist on a VM provisioned before this change**
   (bootstrap's backend sync is new here, and the *old* autoupdate on the box
   at cutover time has no backend step). Hence the explicit `uv sync` above —
   without it the first improve run pays for a cold resolve inside its own
   `TimeoutStartSec`, which is the most OOM-prone thing this 1 GB box does.

Prefer that over re-running `bootstrap.sh` when the VM is already provisioned:
bootstrap also re-enables everything in its list, which will switch
`skeptic-keepwarm.timer` back on if you had turned it off. (Bootstrap *is*
the better path if you also want its new `.env` completeness check.)

Secrets are the one thing neither path can deliver. The three jobs need
`ALPHAVANTAGE_API_KEY`, `APCA_API_KEY_ID`, `APCA_API_SECRET_KEY`,
`DATABASE_URL`, `HEALTHCHECK_URL`, `SKEPTIC_API_URL` and
`SKEPTIC_ACCESS_TOKEN` in `/opt/skeptic/collector/.env` on top of what the
recorder already used — see `collector/.env.example` for the full set.
(`SKEPTIC_ACCESS_TOKEN` is the automation/service **bearer** the backend
accepts as `Authorization` — the same value the workflows used — not the
proxy's `x-skeptic-gate` secret; the improve scan posts straight to the
Railway URL.) A run with `HEALTHCHECK_URL` missing still collects, but nothing
watches it, which is the exact failure mode this whole section exists to
prevent.

Do **not** delete the GitHub repo secrets after the move: the
`workflow_dispatch` fallbacks and the Saturday weekly pass still read them.

## Paging

Set `ALERT_WEBHOOK` in `collector/.env` to page on a stalled session. Any of:
- **ntfy.sh** (no account): `ALERT_WEBHOOK=https://ntfy.sh/<your-random-topic>`,
  then subscribe to that topic on your phone.
- **Slack / Discord**: an incoming-webhook URL.

Without it, the heartbeat still logs to the journal (`journalctl -u
skeptic-heartbeat`), but nothing pushes to your phone.

How each lane reports failure after the move:

- **The EOD chain pages on both paths.** `collect-eod.sh` pings `<url>/fail`
  (naming the failed steps) when a *step* fails, and
  `skeptic-collect-eod.service` carries an `ExecStopPost=` running
  `hc-fail.sh`, which is the only hook that survives the unit being *killed* —
  the 45-min wall, an OOM kill, a reboot. That second path matters because
  `collect.py` pings SUCCESS as step 1 of 11: without it a chain killed at
  minute 44 would leave a green tile over derivations that never ran.
- **`skeptic-improve` / `skeptic-quality` page only if you create their
  checks.** Both units call the same `hc-fail.sh` against
  `HEALTHCHECK_URL_IMPROVE` / `HEALTHCHECK_URL_QUALITY`; blank (the default)
  means log-only. Create two checks on healthchecks.io, drop the URLs in
  `.env`, done. They are deliberately separate from the EOD tile, whose
  meaning ("tonight's lake is whole") must stay single-purpose.

Remaining known gaps (owner decisions, deliberately not half-wired here):

- **The Saturday weekly pass kept its GitHub cron** (needs repo write for the
  proposal PR) and therefore kept the billing-failure exposure — and it has
  no dead-man check. A repeat billing block silences it in the exact shape
  the Jul 27–31 outage took. Give it its own Healthchecks check (ping at the
  end of the weekly step) or accept a silent-failure window of ≤1 week.
- **No cross-host lock.** The workflows' `concurrency: group: collector`
  only serializes Actions runs against each other, so a manual
  `alpaca-backfill` or `collect-eod` dispatch can now run *while* the VM's
  chain does, splitting the shared 200 req/min Alpaca budget. Avoid
  dispatching inside 21:00–24:00 UTC on weekdays; a real fix is an R2 lease
  object both hosts acquire.

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
