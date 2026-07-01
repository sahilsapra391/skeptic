# Skeptic — Data Pipeline Specification
*Consumer: Claude Code (Milestone M1). This pipeline is deliberately built and
started BEFORE the app, because history only accrues forward.*

> **DECIDED (owner, 2026-07-01): Alpha Vantage HISTORICAL_OPTIONS became
> premium-only (~$50/mo) and is out of budget. The EOD source of record is
> the nightly Yahoo snapshot (greeks computed at ingest per TECH-SPEC §4).
> The AV leg stays in the collector, dormant, and resumes automatically if
> the key ever gains access; the free backfill-to-2008 drip is suspended.
> Historical backfill candidate under evaluation: the DoltHub community
> options archive (the PRD §10 "community-crowdsourced archive"), which
> requires a quality-verification pass before any of it is trusted.
> Wherever this document assumes AV as record or backfill engine, read it
> through this decision. Full context: docs/BUILD-LOG.md, M1 addendum.**

## 1. Strategy: two sources, three jobs

**Sources**
- **Alpha Vantage `HISTORICAL_OPTIONS`** (free key, 25 requests/day). One
  request returns the FULL end-of-day chain (all expirations, all strikes)
  for one ticker on one trading date, **including IV and greeks**, with
  history reaching back to roughly 2008. This is the EOD **source of record**
  and, crucially, the free backfill engine.
- **yfinance (Yahoo)**: live chain snapshots. Unofficial, no greeks, quotes
  can be stale, but unlimited-ish and intraday-capable. Role: same-day
  redundancy now, intraday collection later (Phase 2 of the PRD).

**Jobs**
1. **EOD collect** (nightly, weekdays): 3 AV requests capture yesterday's/
   today's closed chains for SPY, QQQ, IWM as the record; one yfinance sweep
   captures the same tickers as redundancy; underlying OHLCV + VIX appended.
2. **Backfill drip** (nightly, same workflow): spend the remaining ~22 AV
   requests walking BACKWARD through history from a persisted frontier.
   Priority: SPY first to exhaustion (deepest research value), then QQQ, then
   IWM. Math: ~22 ticker-dates/day ≈ a month of trading history per
   day-and-a-half; SPY back to 2008 (~4,500 sessions) completes in roughly
   7 months of unattended dripping. Every morning you own more history than
   yesterday, in both directions.
3. **Quality check** (weekly): cross-source and internal sanity flags (see §6).

## 2. Scheduling: GitHub Actions

- `collect-eod.yml`: cron `30 21 * * 1-5` (UTC) ≈ 4:30–5:30 pm ET across DST.
  GH cron can be delayed or occasionally skipped; the job is idempotent
  (object keys include the trading date; re-runs overwrite same-day data
  safely) and ends with a Healthchecks.io ping. A second cron `30 22 * * 1-5`
  runs the same job as a catch-up; it no-ops if the day's record objects
  already exist.
- Manual `workflow_dispatch` enabled on everything for testing.
- Secrets (repo-level): `ALPHAVANTAGE_API_KEY`, `R2_ACCOUNT_ID`,
  `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET`, `HEALTHCHECK_URL`.

## 3. R2 lake layout

```
s3://skeptic-data/
  options/
    source=alphavantage/ticker=SPY/date=2026-07-01/chain.parquet
    source=yahoo/ticker=SPY/date=2026-07-01/snap_20260701T2031Z.parquet
  underlying/ticker=SPY/daily.parquet          (full history, rewritten append)
  reference/vix_daily.parquet
  reference/exdiv_calendar.parquet
  state/backfill_frontier.json                 {ticker: earliest_date_done}
  state/trial_notes.json                       (optional pipeline metadata)
```

Yahoo keys carry a timestamp so the SAME layout absorbs future intraday
snapshots (multiple `snap_*.parquet` per date) with zero migration. Size
reality: EOD chains for 3 ETFs ≈ 2–6 MB/day compressed; years fit comfortably
inside R2's 10 GB free tier.

## 4. Canonical schema (normalize BOTH sources to this)

| column | type | notes |
|---|---|---|
| ticker | str | underlying |
| trading_date | date | session the quote belongs to |
| snapshot_ts | timestamp | capture time UTC (EOD record: session close) |
| expiration | date | |
| dte | int | expiration − trading_date |
| right | str | `call` / `put` |
| strike | float | |
| bid, ask, last | float | nullable |
| volume, open_interest | int | nullable |
| iv | float | AV-provided or Yahoo-provided |
| delta, gamma, theta, vega, rho | float | AV-provided; for Yahoo rows computed via Black-Scholes at ingest |
| greeks_source | str | `vendor` / `computed` |
| spot | float | underlying at snapshot |
| source | str | `alphavantage` / `yahoo` |

Source precedence for a given (ticker, trading_date) at query time:
alphavantage > yahoo. The backend's `preferred_chain()` view implements this.

## 5. Collector implementation notes

Reference implementation: `reference/collector_v2.py`. Productionize as
`collector/collect.py` with subcommands `--mode eod|backfill|quality`.

- AV request budget is a hard wall: a persisted daily counter (in the run,
  simply: 3 for EOD + up to 22 backfill, stop). Respect ~5 req/min pacing.
- Backfill frontier: read `state/backfill_frontier.json`, request the next
  N older trading dates (NYSE calendar via `exchange_calendars`), write
  chains, advance frontier, write state back. Crash-safe: state written after
  each successful date.
- yfinance hardening: pin `yfinance>=0.2.54` (curl_cffi transport), retry
  with backoff, and treat total failure as non-fatal (AV record still lands;
  log a warning). **Known risk:** Yahoo throttles datacenter IPs; if GH
  runners get blocked persistently, the yfinance leg moves to your laptop
  cron or a free Oracle VM. The AV leg is unaffected either way.
- Underlying + VIX: yfinance daily download (fallback Stooq), full-history
  overwrite weekly + daily append; cheap insurance against drift.
- Never commit data to git. R2 only.

## 6. Quality checks (weekly job + surfaced in /api/data/coverage)

- Missing sessions: NYSE calendar dates with no record object → flag.
- Null-quote rate: % rows with null/zero bid AND ask per chain > 20% → flag.
- Crossed markets: bid > ask rows counted; > 1% → flag.
- Cross-source spot drift: |yahoo.spot − av.spot| / spot > 0.5% → flag.
- Backfill progress: frontier date per ticker + ETA at current drip rate
  (this powers the Observatory progress bar).

## 7. Honest limits (encode these in product copy, not just docs)

- AV free history ≈ 2008 onward, EOD only. Intraday history is NOT
  obtainable free; it begins the day the intraday collector is switched on.
- Self-collected + AV data is approximate research data: no OPRA-grade
  point-in-time guarantees, EOD quotes can be wide/stale at the close.
  The Observatory and every run's methodology note say so.
- 0DTE/1DTE strategies (the owner's live style) remain out of scope until
  intraday history exists; the coverage endpoint must make this refusal
  explainable in-product.

## 8. Definition of done for M1

1. Both workflows exist, run green on `workflow_dispatch`, and are scheduled.
2. R2 contains: AV chains + Yahoo snapshots for all 3 tickers (today),
   underlying dailies to the 1990s, VIX daily, a moving backfill frontier.
3. Healthchecks receives pings; killing the workflow makes the check go red.
4. `/api/data/coverage` (or a temporary script until M2) reports ranges,
   counts, frontier, and quality flags from the lake alone.
