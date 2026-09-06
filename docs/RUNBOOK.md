# RUNBOOK — operating Skeptic in production (M6)

Single-user deployment: FastAPI backend on **Railway**, Next.js frontend on
**Vercel**, market lake on **Cloudflare R2**, runs DB on **Neon Postgres**
(with automatic local-SQLite fallback), LLM via **OpenRouter**.

## Topology

```
browser ── Vercel (Next.js, same-origin /api proxy, attaches bearer)
                │  SKEPTIC_API_URL + SKEPTIC_ACCESS_TOKEN (server-side only)
                ▼
        Railway (FastAPI backend, bearer-guarded)
           │            │              │
           ▼            ▼              ▼
        R2 lake     Neon runs DB   OpenRouter
       (parquet)   (SQLite fallback  (parser · verdicts · Q&A)
                    when unreachable)
```

## Environment variables

Backend (Railway service):
| var | purpose |
|---|---|
| `R2_ACCOUNT_ID` / `R2_ACCESS_KEY_ID` / `R2_SECRET_ACCESS_KEY` / `R2_BUCKET` | market lake (same values the collector uses) |
| `OPENROUTER_API_KEY` | parser, verdict narration, grounded Q&A (absent → parser 501s, template verdicts) |
| `OPENROUTER_MODEL` | optional; narration (verdict · ask · health). Default `deepseek/deepseek-v4-pro` |
| `OPENROUTER_PARSER_MODEL` | optional; parser only. Default `deepseek/deepseek-v4-pro` |
| `SKEPTIC_ACCESS_TOKEN` | bearer auth for every /api route except /api/health |
| `DATABASE_URL` | Neon Postgres; omit or unreachable → local SQLite (ephemeral on Railway — runs vanish on redeploy) |

Frontend (Vercel project, root `frontend/`):
| var | purpose |
|---|---|
| `SKEPTIC_API_URL` | the Railway public URL |
| `SKEPTIC_ACCESS_TOKEN` | same token; attached server-side, never shipped to the browser |
| `SKEPTIC_DEMO_FALLBACK` | set `0` in prod — never serve demo fixtures |

## Rotate the access token

1. Generate: `openssl rand -hex 24`
2. Set the new value in **both** Railway (`SKEPTIC_ACCESS_TOKEN`) and Vercel
   (same var), redeploy both. There is one consumer (the Vercel proxy), so
   no staged rollout is needed — a minute of 401s at worst.

## Deploys

- Both platforms auto-deploy on push to `main`. Backend health gate:
  `/api/health` (Railway waits up to 120 s — first boot may pull Python deps).
- Cold-start note: the first backtest per ticker after a deploy pulls the
  chain lake from R2 (~1,100 sessions, 24-thread fetch) — expect the first
  run to take ~1–3 min before the parquet manifest cache warms. Subsequent
  runs are seconds.
- Production smoke (run after every deploy that touches the run pipeline):
  ```
  SKEPTIC_ACCESS_TOKEN=… python scripts/smoke_prod.py https://<railway-app>.up.railway.app
  ```
  It walks health → parse → backtest → verdict on the canonical strategy
  and fails loudly on any demo flag, refusal-shaped hole, or timeout.

## Collector operations

- Nightly EOD: `skeptic-collect-eod.timer` on the collector VM (21:30 UTC +
  22:30 UTC catch-up, Mon–Fri), running `collector/deploy/collect-eod.sh`.
  Moved off Actions 2026-08-04; `collect-eod.yml` still exists but is
  `workflow_dispatch`-only, the fallback for when the VM itself is down.
- Healthchecks.io tiles ping on success — a silent tile means the run didn't
  complete. Check the VM first now, not the Actions log:
  ```
  systemctl list-timers "skeptic-*"
  journalctl -u skeptic-collect-eod -n 200
  tail -100 /var/log/skeptic/collect-eod.log
  ```
  A tile that goes quiet with **no** `/fail` ping means the job never ran at
  all (timer disabled, VM down, unit failed to start) rather than that the run
  errored — four separate things ping `/fail` on the VM: `collect.py` on any
  error it survives to see, `collect-eod.sh` when a later step fails (the body
  names which), `collect-eod.sh` again when it cannot take the collector lease
  (body: `did not start: the collector lane is leased elsewhere` — a dispatch
  is mid-run; see the next bullet), and the unit's `ExecStopPost=` hook when
  the run is *killed*
  (45-min wall, OOM, reboot). So a red tile tells you it broke; a silent tile
  tells you nothing started. That silent shape is what the 2026-07-27 Actions
  billing block looked like, and it is why the tile is the load-bearing alarm.
- Re-run a missed night: `sudo systemctl start skeptic-collect-eod.service`
  (the frontier state in R2 makes it idempotent). If the VM is the problem,
  Actions → collect-eod → Run workflow still works.
- **A step that says `budget exhausted` is a slow lake, not a broken one.**
  `derive_flow_inhouse.py` reads ~405 recorder snapshots per session and carries
  a wall-clock budget (`--budget-seconds`, default 1200, or
  `SKEPTIC_FLOW_BUDGET_SECONDS`). Past it the step banks the sessions it
  finished, exits non-zero and lets the chain finish its remaining steps; the
  skipped sessions are re-derived on the next run, so a single such night needs
  no action. Two nights running means R2 is genuinely slow: check the
  `N/405 snapshots (Ns elapsed)` progress lines in
  `/var/log/skeptic/collect-eod.log` against the ~150s a healthy ticker takes.
  This bound exists because on 2026-09-04 an unbounded stall in that loop ate
  the unit's whole 2700s wall and the SIGKILL took the last four steps of the
  chain with it.
- Both hosts hold a lease in R2 (`state/collector.lock`) before spending the
  shared 200 req/min Alpaca budget, so a dispatch and the VM chain can no
  longer collide — whichever is second refuses, naming the first. If a lane
  looks stuck, `cd /opt/skeptic/collector && uv run --env-file .env python -m
  lock status` says who holds it and for how long; `python lock.py release
  --force` takes it back when that holder is known dead. Every lease also
  self-expires (the chain's is 3000s), so a wedge is bounded, never overnight.
  Full design: `collector/deploy/README.md` → "The cross-host lock".
- Nightly unlock scan: `skeptic-improve.timer` on the VM (Tue–Sat 07:15 UTC),
  log at `/var/log/skeptic/improve.log`. It pages ONLY if
  `HEALTHCHECK_URL_IMPROVE` is set in `collector/.env`; blank means a failed
  scan is a line in that log and nothing else. The Saturday calibration +
  priorities pass stayed on Actions (`nightly-improve.yml`, `30 7 * * 6`)
  because it opens a proposal PR, and a red run there emails you. Both run
  `backend/scripts/*` against Neon and must never migrate it (see "Neon").
- Quality flags: `/api/data/coverage` (or the Data Observatory page) shows
  per-source ranges, quarantines and blind spots. DoltHub quarantine list
  lives in `collector/state/dolthub_backfill.json` (flag-and-exclude —
  objects are never deleted).
- The intraday minute recorder runs on the same collector VM
  (`skeptic-intraday.service`, `Restart=always`), log at
  `/var/log/skeptic/intraday.log`. It is independent of the production
  deploy — and of GitHub entirely, which is why it sailed through the
  Jul 27–31 Actions billing block. (The old Mac launchd agent was retired
  2026-07-14; its plist is archived in `collector/deploy/retired/`.)

## Neon (runs DB)

- If Neon is unreachable (e.g. the 2026-07 free-tier transfer-quota
  exhaustion), the backend logs it, **falls back to local SQLite** and
  keeps serving; Settings → System Status shows "local SQLite fallback".
  On Railway that fallback is ephemeral: runs stored during an outage are
  lost on redeploy. Restore by fixing/upgrading Neon and redeploying.
- The quota-killer was fixed 2026-07-03 (listings read a ~500 B summary
  column instead of 50 full payloads); post-fix egress should sit well
  inside the free 5 GB/month.
- Never reset the `trial_counter` table casually — the deflated Sharpe's
  honesty depends on its cumulative counts.
- **Only the deploy migrates.** `app.db` refuses to alter a remote schema
  unless `SKEPTIC_ALLOW_REMOTE_MIGRATION=1`, and the ONE place that sets it is
  `backend/Dockerfile`, because the container is the deploy and a schema
  change should be something someone chose (V-149). A test reads the
  Dockerfile for it and CI reads it back out of the built image.
- **Scripts attach, they do not migrate.** Everything under `backend/scripts/`
  connects through `db.connect_existing()`, which proves the database answers,
  logs `DATABASE TARGET: ...`, and cannot create tables, alter columns, or
  fall back to local SQLite. `init_db()` is the server's boot path and does
  all three. If a script's log shows `RemoteMigrationRefused`, that script is
  calling `init_db()`: change the call. Never give a unit, a workflow or a
  laptop the flag to make a script run.
- What it looked like when this went wrong: from 2026-08-22 the Saturday
  `nightly-improve` workflow died in 15 s on that refusal three weeks running
  (GitHub emailed each time), and the VM's `skeptic-improve` lane runs the
  same guard with no flag and no `HEALTHCHECK_URL_IMPROVE`, so the same
  refusal there reaches no tile at all. Fixed 2026-09-06 by making the four
  database scripts attach instead of migrate. `/var/log/skeptic/improve.log`
  holds what the VM lane actually did on those nights; read it, and give that
  lane a tile.

## Cost dashboards

- R2: Cloudflare dashboard → R2 → bucket metrics (storage + class A/B ops).
  Recorder self-caps at 6 GB.
- Neon: console → Monitoring → data transfer (watch after the 2026-07 event).
- OpenRouter: dashboard → usage (parser + two verdict narrations + Q&A per
  run; temperature 0–0.2, small responses).
- Railway / Vercel: hobby-tier dashboards; the backend is a single small
  service.

## Known behaviors

- `/api/health` is intentionally unauthenticated (uptime probes).
- Verdict narration and Q&A silently fall back to grounded templates if
  OpenRouter misbehaves — a run NEVER fails because the LLM did.
- The overfit-fixture test and lookahead canary are permanent CI gates;
  if either goes red, stop shipping until understood.
