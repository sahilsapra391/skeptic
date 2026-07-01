# Skeptic — Build Log

Session notes per milestone (cross-milestone rule in docs/BUILD-PLAN.md):
date, what shipped, deviations from spec and why.

## 2026-07-01 — Phase 2 step 1: handoff docs landed

PR #1: handoff package into the repo (CLAUDE.md at root, specs under docs/,
reference collector under collector/reference/). One content edit vs. the
original package: DECIDED note in docs/TECH-SPEC.md §1 — LLM access is via
OpenRouter (`OPENROUTER_API_KEY`), not a direct Anthropic key.

## 2026-07-01 — M1: data pipeline live

**Shipped:** `collector/collect.py` (modes eod/backfill/underlying/quality/all;
AV 25/day budget wall persisted in R2; crash-safe backfill frontier advanced
per date; NYSE calendar via exchange_calendars; yfinance retry hardening with
Stooq fallback for dailies; healthcheck success/fail pings),
`collector/coverage.py` (lake coverage report, stand-in for
/api/data/coverage until M2), `.github/workflows/collect-eod.yml`
(21:30 UTC weekdays + 22:30 catch-up + workflow_dispatch with mode /
backfill-limit inputs), `.github/workflows/quality-weekly.yml`
(Saturday 13:00 UTC + workflow_dispatch).

**Deviations from spec, and why (owner informed in-session):**

1. **One nightly workflow + one weekly quality workflow** instead of a
   separate `backfill-drip.yml` (named in TECH-SPEC §1 diagram / CLAUDE.md
   layout). DATA-PIPELINE §2 — the authoritative pipeline doc — describes a
   single nightly job running eod + backfill; "both workflows" of §8 is
   satisfied by collect-eod + quality-weekly.
2. **EOD leg fetches explicit session dates** (last 3 completed NYSE
   sessions, skipping ones already recorded) instead of AV's `date=None`
   "latest". Deterministic object keys before spending budget, true no-op
   catch-up runs, and self-healing capture when AV finalizes a session late.
3. **A missing latest session is logged, not failed** — vendor finalization
   lag is normal; the catch-up cron and next runs' lookback pick it up.
   Older gaps inside the lookback window do fail the run (record at risk).
4. **Underlying/VIX full-history overwrite runs nightly** (not
   weekly-overwrite + daily-append): ~9k rows/symbol, trivial cost, always
   fresh. `--mode underlying` doubles as the one-time deep backfill.
5. **Cross-source spot drift** computed as Yahoo snapshot spot vs underlying
   daily close (AV chains carry no spot by design; spot joins from dailies at
   query time per TECH-SPEC §4).
6. **Quality scan bounded** to the last 10 AV sessions per ticker per run.
7. **Committed directly to main** rather than via PR: `workflow_dispatch`
   and cron schedules only operate on the default branch, and M1 acceptance
   requires live workflow runs.

**Coverage output (first live runs):** see the addendum resolution below.

## 2026-07-01 — M1 addendum: Alpha Vantage HISTORICAL_OPTIONS is premium-gated

First live run (Actions run 28552781791) failed on the AV leg with
"This is a premium endpoint." Verified against alphavantage.co/documentation:
Historical Options and Realtime Options are both badged **Premium** as of
July 2026. The handoff's free-25/day assumption (DATA-PIPELINE §1) no longer
holds — the free EOD source of record AND the free backfill-to-2008 engine
are gone. AV premium starts at ~$50/mo, which violates the locked ~$25/mo
budget (README-START-HERE, Decisions #3).

What the same run proved works: Yahoo snapshots (3/3 tickers), underlying
dailies (SPY→1993, QQQ→1999, IWM→2000), VIX→1990, healthcheck fail-path
alerting, coverage report.

**Interim posture (pending owner decision on a replacement source):** a
premium-gated AV key is treated as a known condition, not a nightly
incident — the run is green if the Yahoo leg fully covers the day, and the
AV rejection logs at error level. Nightly Yahoo + dailies collection
continues so forward history keeps accruing. If the key ever gains access,
the AV leg resumes automatically (detection is response-based, no config).

**Open decision (owner):** replacement data strategy — Yahoo-forward only /
community historical archive (e.g. DoltHub options) for backfill / paid
source. DATA-PIPELINE.md and M1's acceptance criteria to be amended to
match the decision.

## 2026-07-01 — M1 addendum resolution + verification (owner decision)

Owner decision: **Yahoo-forward as the EOD record + evaluate the DoltHub
community archive for backfill** ($0/mo; AV premium rejected at ~2x the
whole project budget). The DECIDED block now leads docs/DATA-PIPELINE.md.
The AV leg stays in the collector, dormant; it resumes automatically if the
key ever gains access. DoltHub evaluation spun off as a separate session.

Verification runs, both green on workflow_dispatch:

- collect-eod run 28553071331 — premium-gated AV handled as a known
  condition; Yahoo snapshots 3/3 (SPY 3,543 / QQQ 3,247 / IWM 1,594 rows,
  2026-07-01); underlying + VIX full history; **healthcheck success ping
  received** — and the earlier failing run proved the fail path pages, so
  DoD §8.3 is demonstrated in both directions.
- quality-weekly run 28553211481 — flags computed and written to
  r2://state/quality_flags.json.

M1 acceptance as amended: §8.1 ✓ (two workflows, dispatch-green, scheduled);
§8.2 ✓ Yahoo chains 3/3, underlying to the 1990s, VIX to 1990 (AV chains and
the moving frontier suspended per the DECIDED block); §8.3 ✓ both directions;
§8.4 ✓ (coverage.py, also runs at the end of every nightly workflow).

Coverage output (verification run):

    ==============================================================
    Skeptic data lake coverage
    ==============================================================

    Options chains
       alphavantage SPY:     0 sessions
       alphavantage QQQ:     0 sessions
       alphavantage IWM:     0 sessions
              yahoo SPY:     1 sessions   2026-07-01 -> 2026-07-01
              yahoo QQQ:     1 sessions   2026-07-01 -> 2026-07-01
              yahoo IWM:     1 sessions   2026-07-01 -> 2026-07-01

    Underlying dailies + VIX
        SPY:   8412 rows   1993-01-29 -> 2026-07-01
        QQQ:   6870 rows   1999-03-10 -> 2026-07-01
        IWM:   6562 rows   2000-05-26 -> 2026-07-01
       ^VIX:   9192 rows   1990-01-02 -> 2026-07-01

    Backfill frontier (walking backward toward 2008-01-02)
      (no frontier state yet)

    Quality flags: none yet (run --mode quality)
