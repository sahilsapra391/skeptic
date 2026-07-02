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

> **DECIDED (owner, 2026-07-02): minute-level options history via Alpaca.**
> **Alpaca Market Data (Basic, free) is adopted as the intraday options
> source for SPY, QQQ, and IWM: one-time bulk backfill of 1-minute option
> bars from 2024-02 (vendor history start) to present, then a nightly
> top-up leg. Option quotes are fetched lazily at backtest decision
> timestamps and cached — never bulk-pulled (rate-limit math in
> docs/INTRADAY-OPTIONS-DATA-EVAL.md; pre-2024-02 intraday does not exist
> at $0, per the same eval). Implementation: BUILD-PLAN M1.5. Greeks for
> minute data are computed, not stored per bar (§4b).**

## 1. Strategy: sources and jobs

**Sources**
- **Alpha Vantage `HISTORICAL_OPTIONS`** (free key, 25 requests/day). One
  request returns the FULL end-of-day chain (all expirations, all strikes)
  for one ticker on one trading date, **including IV and greeks**, with
  history reaching back to roughly 2008. This is the EOD **source of record**
  and, crucially, the free backfill engine.
- **yfinance (Yahoo)**: live chain snapshots. Unofficial, no greeks, quotes
  can be stale, but unlimited-ish and intraday-capable. Role: same-day
  redundancy now, intraday collection later (Phase 2 of the PRD).
- **DoltHub community archive** (`post-no-preference/options`, CC BY-SA
  4.0): **SPY EOD backfill only**, 2020-01-06 → 2026-06-30, ingested
  one-shot by `collector/dolthub.py` under the conditions of
  docs/DOLTHUB-EVAL.md §7 (XNYS filter, duplicate guard, spot joined from
  our dailies, vendor greeks, commit hash pinned in
  `state/dolthub_backfill.json`). Static history — never re-collected; the
  archive has no QQQ/IWM. M/W/F-cadence granularity before 2024-09 and the
  2024-08-05 vol-spike outage are disclosed by coverage.
- **Alpaca Market Data (Basic plan, free)**: historical **option 1-minute
  bars** (OPRA-trade-derived OHLCV) for full chains since **2024-02**, plus
  underlying 1-minute equity bars from the same API. Mechanics: ~200
  req/min account budget, 100 contract symbols per request, `limit=10000`
  rows/page with `page_token` pagination; contract universe (incl. expired)
  via the trading API `GET /v2/options/contracts` (`status=inactive`;
  expired depth to 2024-02 verified at M1.5 step 0). **Historical option
  quotes are NOT served on the Basic plan** (step-0 finding C: HTTP 404 on
  every feed — only latest quotes exist), so minute-granularity fills must
  come from a disclosed spread model (real per-contract EOD spreads from
  our own lake) and/or forward-collected quote snapshots — designed at the
  minute-engine milestone. Role: the intraday options history and its
  nightly forward accrual.

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
4. **Minute-lake top-up** (nightly, same workflow): append yesterday's
   1-minute option bars + underlying minute bars for the 3 tickers from
   Alpaca (small pull). The one-time `alpaca-backfill` mode walks
   2024-02 → present with a resumable month×ticker frontier.
5. **Intraday quote recorder** (`collector/intraday.py`, launchd agent on
   the owner's Mac — Actions free minutes cannot host a 6.75 h/day loop):
   every session minute, the CBOE delayed-quote JSON full chain per ticker
   (bid/ask, IV, greeks, OI; ~3 req/min; quotes ~15-min delayed —
   `snapshot_ts` = capture, `source_ts` = feed stamp) + a Yahoo chain
   snapshot every 15 min as cross-source redundancy (Yahoo at 1-min would
   need ~120 req/min and endangers the nightly EOD source). These forward
   quotes are the fill-model record the Alpaca bar history lacks (§4b).
   Uptime is best-effort (Mac must be awake); coverage reports gaps
   honestly.

## 2. Scheduling: GitHub Actions

- `collect-eod.yml`: cron `30 21 * * 1-5` (UTC) ≈ 4:30–5:30 pm ET across DST.
  GH cron can be delayed or occasionally skipped; the job is idempotent
  (object keys include the trading date; re-runs overwrite same-day data
  safely) and ends with a Healthchecks.io ping. A second cron `30 22 * * 1-5`
  runs the same job as a catch-up; it no-ops if the day's record objects
  already exist.
- Manual `workflow_dispatch` enabled on everything for testing.
- Secrets (repo-level): `ALPHAVANTAGE_API_KEY`, `R2_ACCOUNT_ID`,
  `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET`, `HEALTHCHECK_URL`,
  `APCA_API_KEY_ID`, `APCA_API_SECRET_KEY` (Alpaca paper keys; SDK-default
  env names).

## 3. R2 lake layout

```
s3://skeptic-data/
  options/
    source=alphavantage/ticker=SPY/date=2026-07-01/chain.parquet
    source=yahoo/ticker=SPY/date=2026-07-01/snap_20260701T2031Z.parquet
    source=dolthub/ticker=SPY/date=2020-01-06/chain.parquet   (static backfill)
  underlying/ticker=SPY/daily.parquet          (full history, rewritten append)
  reference/vix_daily.parquet
  reference/exdiv_calendar.parquet
  options_minute/
    source=alpaca/ticker=SPY/date=2026-07-01/bars.parquet
  options_intraday/                 (forward quote record, job 5)
    source=cboe_delayed/ticker=SPY/date=2026-07-02/snap_20260702T1330Z.parquet
    source=yahoo/ticker=SPY/date=2026-07-02/snap_20260702T1330Z.parquet
  underlying_minute/ticker=SPY/month=2026-07/bars.parquet
  state/backfill_frontier.json                 {ticker: earliest_date_done}
  state/alpaca_backfill.json                   {ticker: {month: done}}
  state/trial_notes.json                       (optional pipeline metadata)
```

Yahoo keys carry a timestamp so the SAME layout absorbs future intraday
snapshots (multiple `snap_*.parquet` per date) with zero migration. Size
reality: EOD chains for 3 ETFs ≈ 2–6 MB/day compressed; years fit comfortably
inside R2's 10 GB free tier. **The minute lakes do not.** Measured
reality (M1.5 step 0 + first live snapshot): the Alpaca bar backfill is
~2.5 GB one-time (+~1.3 GB/yr forward), and the intraday quote recorder
writes **~430 MB/session-day ≈ 109 GB/yr** at full-chain 1-min cadence —
the free tier's headroom lasts ~17 trading days. The recorder therefore
ships with a hard cap (`--max-lake-gb`, default 6 GB): it pauses itself
rather than fill the shared bucket and break the nightly EOD record.
**Owner decision, open:** enable R2 paid storage (~$0.015/GB-mo ≈
$1–2/mo at year-one scale — well inside budget) and raise the cap, or
direct a thinner lake (DTE/moneyness filter and/or 5-min cadence; any
useful configuration still exceeds 10 GB within months).

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
alphavantage > yahoo > dolthub. The backend's `preferred_chain()` view
implements this (dolthub exists only before 2026-07, so it never actually
contends with the live record).

### 4b. Minute-bar schema (`options_minute/`, Alpaca)

| column | type | notes |
|---|---|---|
| ticker | str | underlying |
| trading_date | date | NYSE session |
| minute_ts | timestamp | bar start, UTC |
| occ_symbol | str | e.g. `QQQ260702C00725000` |
| expiration | date | parsed from OCC symbol |
| right | str | `call` / `put` |
| strike | float | |
| open, high, low, close | float | trade-derived (OPRA) |
| volume | int | contracts |
| trade_count | int | |
| vwap | float | |
| source | str | `alpaca` |

Bars exist only for (contract, minute) cells with ≥1 trade — options trade
sparsely and the lake reflects that honestly. Bars for expirations more
than ~400 days out are not pulled (all-empty batches; constant
`MAX_EXP_DAYS` in collector/alpaca.py). Greeks/IV are **not** stored per
bar: computed on demand (TECH-SPEC §4 method, `greeks_source='computed'`)
from underlying minute closes. **Minute-granularity fills may never use
bar closes raw** — guardrail #1 applies at every timescale. Because Alpaca
serves no historical quotes (step-0 finding C), the minute-engine
milestone must pick and disclose a fill model: per-contract spread
estimates from our own EOD lake applied around bar prices, and/or true
quote snapshots collected forward (CBOE leg) or bought (ThetaData).

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
- Minute lake: sessions missing vs the XNYS calendar → flag; per-session
  bar count < 50% of the trailing 20-session median → flag; sampled
  cross-check of minute-bar closes vs the same day's EOD chain quotes.

## 7. Honest limits (encode these in product copy, not just docs)

- AV free history ≈ 2008 onward, EOD only. Intraday history is NOT
  obtainable free; it begins the day the intraday collector is switched on.
- Self-collected + AV data is approximate research data: no OPRA-grade
  point-in-time guarantees, EOD quotes can be wide/stale at the close.
  The Observatory and every run's methodology note say so.
- Intraday options history exists from **2024-02 only** (Alpaca minute
  bars, §4b) — nothing free reaches earlier at minute granularity
  (docs/INTRADAY-OPTIONS-DATA-EVAL.md). 0DTE/1DTE strategies (the owner's
  live style) stay refused until the engine gains a minute mode (post-M2
  milestone); when it does, such runs are bounded to 2024-02→ and the
  coverage endpoint says exactly that. Minute bars are trade-derived and
  sparse on illiquid contracts; fills come from lazily-fetched quotes,
  never bar closes.

## 8. Definition of done for M1

1. Both workflows exist, run green on `workflow_dispatch`, and are scheduled.
2. R2 contains: AV chains + Yahoo snapshots for all 3 tickers (today),
   underlying dailies to the 1990s, VIX daily, a moving backfill frontier.
3. Healthchecks receives pings; killing the workflow makes the check go red.
4. `/api/data/coverage` (or a temporary script until M2) reports ranges,
   counts, frontier, and quality flags from the lake alone.
