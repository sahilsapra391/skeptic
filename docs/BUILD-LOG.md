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

**Coverage output (first live run):** _pending — appended after the
verification run._

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
