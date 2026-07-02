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

## 2026-07-01 — DoltHub backfill evaluation (spun-off session)

Evaluated `post-no-preference/options` (DoltHub) as the historical EOD
backfill per the DECIDED block. Full findings + conditions:
**docs/DOLTHUB-EVAL.md**. Headline: **GO, scoped to SPY 2020-01-06 →
2026-06-30** — §6 quality passes with headroom (0 crossed rows in 166k,
2/1,169 sessions breach the dead-quote flag), IV/greeks/parity track known
regimes; but QQQ/IWM are absent, snapshots are M/W/F-only before 2024-09,
and each snapshot quotes only ~3 expirations (~14/~28/44–66 DTE, strikes
~±30%), so pre-2024-09 backtests are checkpoint-marked, not daily-marked.
Read-only evaluation over the SQL API; nothing imported to R2, no data in
git. Ingest work (collector `--mode dolthub-backfill`) awaits owner
acceptance of the conditions in the eval doc §7.

## 2026-07-01 — Intraday (1-min) QQQ/IWM options history: acquisition evaluation

Owner asked for 5–10y of minute-level QQQ/IWM options pricing, free API
or scraped. Findings + provider matrix + verified probes:
**docs/INTRADAY-OPTIONS-DATA-EVAL.md**. Headline: **free + 5–10y + minute
does not exist** (OPRA-licensed; Yahoo 404s expired contracts — the past
is unscrapeable). Best free: Alpaca 1-min bars+quotes from 2024-02 (~2.4y,
growing) + start a $0 forward minute collector on CBOE's delayed JSON
(full chain, greeks+OI, tested: 10,606 QQQ contracts/request) + ThetaData
free EOD to 2023-06. The literal ask is purchasable once: ThetaData $40/mo
→ 1-min quotes to 2020-01, $80 → 2016-01 (single-month bulk pull, ToS
check pending); Databento cost-preflight to 2013-04 with $125 credit.
Ongoing subs violate the locked $25/mo budget. Recommended: adopt the $0
stack now (Path A), hold the one-time purchase (Path B) as owner decision.
Nothing bought, nothing scraped, nothing ingested in this session.

## 2026-07-02 — Owner decision: Alpaca minute lake for SPY/QQQ/IWM (M1.5 planned)

Owner adopted the Alpaca solution for all three tickers: full 1-minute
option-bar history 2024-02 → present plus a nightly accrual leg; option
quotes lazy-fetched at backtest decision points only (bulk quote pulls are
rate-limit-impossible and stay banned in code). Docs amended on the
intraday-data-eval branch: DATA-PIPELINE.md (second DECIDED block; Alpaca
in §1 sources + job 4; APCA_* secrets in §2; options_minute/ + quotes_cache/
+ underlying_minute/ + alpaca_backfill state in §3 with the minute-lake
size call-out; new §4b minute-bar schema; §6 minute-lake quality flags;
§7 honest-limits rewrite — intraday exists 2024-02→ only), BUILD-PLAN.md
(new M1.5 with step-0 verification gate), INTRADAY-OPTIONS-DATA-EVAL.md
(decision recorded). Verified against Alpaca docs: history since 2024-02,
1Min bars, 100 symbols/request, 10k rows/page; expired-contract listing
depth is step 0's job. Blocked on owner: Alpaca account + APCA_* repo
secrets, R2 full-vs-filtered choice, PR #2/#3 merges. Implementation is
the next session (M1.5 prompt in BUILD-PLAN).

## 2026-07-02 — M1.5 step 0 verified; minute-lake collector shipped

Step-0 probe (Actions run 28566239710, collector/alpaca_probe.py) findings:

- **A ✓** `/v2/options/contracts` lists expired contracts back to
  2024-02-01 for all 3 tickers (SPY 4,816 / QQQ 5,900 / IWM 1,780 expiring
  Feb-2024 alone) — universe source confirmed, no fallback needed.
- **B ✓** 1-min bars return for long-expired contracts.
- **C ✗** Historical option QUOTES are not served on Basic — HTTP 404 on
  default/indicative/opra feeds (latest-only endpoint exists). The
  "lazy quote cache" design is dead; §3/§4b amended: minute fills will use
  a disclosed EOD-spread-derived model and/or forward/paid quote snapshots,
  decided at the minute-engine milestone. quotes_cache/ prefix reserved.
- **D ✓** Density probe (2 sessions, 2025-03): ~297k bar-rows/session all
  3 tickers → ~178M rows ≈ **2.5 GB parquet total** → fits the R2 free
  tier; the §3 full-vs-filtered owner choice is moot (full chain, free).
  ~22k requests ≈ ~2h at the confirmed 200 req/min account limit.
- **E ✓** Underlying 1-min bars served (SPY/QQQ/IWM).

Shipped on main: collector/alpaca.py (`--mode backfill` resumable via
state/alpaca_backfill.json month×ticker frontier, `--mode eod` 5-session
self-healing top-up; both write §4b parquet per (ticker, date)),
alpaca-backfill.yml (dispatch, wall-clock budget input, `collector`
concurrency group so it never races the nightly for the shared rate
limit), a minute-top-up step in collect-eod.yml, minute-lake section in
coverage.py, collector/alpaca_probe.py + alpaca-probe.yml (kept as a
diagnostic).

Deviations from spec, and why: (1) minute-leg failures surface as red
workflow runs, not healthcheck pages — the healthcheck stays scoped to the
EOD record; the 5-session lookback self-heals missed nights. (2) Bars for
expirations >400 days out are not pulled (MAX_EXP_DAYS): those batches are
~all-empty and only burn rate limit; documented in §4b. (3)
underlying_minute/ is keyed month=YYYY-MM (idempotent monthly unit), not
year=.

## 2026-07-02 — Intraday quote recorder (DATA-PIPELINE job 5) + backfill fix

Owner asked for minute-by-minute forward recording of options quotes.
Shipped `collector/intraday.py`: every session minute (XNYS-aware incl.
early closes, open → close+15 min) it captures the CBOE delayed-quote
full chain per ticker (bid/ask/IV/greeks/OI, one request per underlying,
~3 req/min, quotes ~15-min delayed; snapshot_ts = capture, source_ts =
feed stamp) + a Yahoo chain snapshot every 15 min as cross-source
redundancy — Yahoo at 1-min cadence (~120 req/min) would risk throttling
the source the nightly EOD record depends on, so CBOE is the minute leg
by design (owner asked "Yahoo minute-by-minute"; Yahoo rides at 15-min).
Writes to options_intraday/source={cboe_delayed,yahoo}/… per §3. Runs as
a launchd agent on the owner's Mac (Actions minutes math per the intraday
eval); best-effort uptime, gaps honest in coverage.

Smoke test (dry-run, live feeds): CBOE SPY 13,706 / QQQ 10,606 /
IWM 4,890 rows per snapshot; Yahoo 3,755/3,292/1,471 (≤60 DTE). Measured
36.2 B/row parquet → **~430 MB/session-day ≈ 109 GB/yr** — the recorder
ships with a self-cap (--max-lake-gb, default 6) so it pauses rather
than fill the shared free-tier bucket and break the EOD record. **Owner
decision open: enable R2 paid (~$1–2/mo at yr-1 scale) and raise the
cap, or direct a thinner lake.** Free-tier headroom at full cadence ≈ 17
trading days.

Also this session: first alpaca-backfill dispatch crashed on an
adjusted-series symbol (1SPY…, penny strike) the data API rejects —
universe now filters to standard roots and a bisect guard skips any
remaining rejects (run 28566653508 failed clean, nothing written;
re-dispatched as 28567008737).

## 2026-07-02 — DoltHub SPY ingest executed; cross-source validation built

`collector/dolthub.py` ran locally (owner's Mac, R2 creds in .env):
**1,115 sessions ingested, 2020-01-06 → 2026-06-30, 158,156 rows**, 514
archive gaps recorded (M/W/F-era weekdays + known outages), 0 duplicates,
dead-quote flags on 2021-03-03 / 2025-03-26 — all matching DOLTHUB-EVAL's
predictions exactly (1,116 valid sessions minus the out-of-window 2019
stray). Archive commit pinned in state/dolthub_backfill.json. Coverage now
shows `dolthub SPY: 1115 sessions`. Lesson: the SQL API has a response
row cap surfaced as status "RowLimit" — deterministic, not retryable; the
ingest bisects date batches down to per-date call/put halves. §4
precedence extended: alphavantage > yahoo > dolthub.

`collector/validate_minute_vs_eod.py` (one-off, owner-requested):
cross-validates DoltHub EOD quotes vs Alpaca minute bars over the overlap.
Findings so far (2024-02→08 partial, 81 sessions): joins on exact
(expiration, right, strike) work at the expected rate — the two
independently written pipelines agree on structure (parsing, strike
scaling, date attribution). Raw price comparison flagged 15% → diagnosed
as stale prints (deep-ITM strikes last traded hours before the close,
deviation = delta x intraday move, not data error); near-close filter +
delta-adjustment via vendor delta and our underlying minute bars added.
Final full-window run pends the Alpaca backfill (underlying minute bars
land last). Alpaca backfill hardening: network-level exceptions
(connection reset after ~2 h) now retried in _get.

## 2026-07-02 — Alpaca minute lake frozen (owner decision); forward = Yahoo + live recorder

Bulk minute-bar backfill COMPLETED inside Alpaca's new-account grace
window: **29 months × 3 tickers (2024-02 → 2026-06), 159.4M bars**
(SPY 81.3M / QQQ 58.6M / IWM 19.6M) — then the account hit 403 "OPRA
agreement is not signed" and the dashboard errors when the owner tries to
sign it. Owner decision: **keep the lake frozen as a static research
asset; do not chase the entitlement.** Going forward the record is the
nightly Yahoo EOD snapshot + the live intraday recorder (CBOE full-chain
minute quotes — bid/ask/IV/greeks/OI — with Yahoo 15-min redundancy).
Collector treats the missing OPRA entitlement as a known condition (green
nightly, error-level log, automatic resume if it ever appears — the AV
pattern). Underlying minute bars are stock data, not OPRA-gated: backfill
re-dispatched for those 30 months, which also unblocks the full
cross-source validation. July options gap: 2026-07-01 has EOD coverage
only; the recorder covers 2026-07-02 onward.

## 2026-07-02 — Cross-source validation closeout: 45 archive sessions quarantined

The owner-requested DoltHub-vs-Alpaca validation completed its arc:
(1) structural agreement proven — 24,597 exact (expiration, right,
strike) joins across 506 overlap sessions, zero evidence of parsing/
scaling/date bugs in our pipelines; (2) stale-print semantics fixed
(near-close trades only, delta-adjusted, per-session capture-offset
self-calibration); (3) **real vendor defect found**: archive sessions
with quotes from the wrong date or intraday-stale in shape. Remediated
with two permanent gates (parity ≤0.75% of close; cross-source
violation ≤50%) — **17 + 28 = 45 sessions quarantined (flag-and-exclude,
objects retained), lake = 1,070 verified sessions**, per-session scores
in state/dolthub_backfill.json. Verified-overlap residual: 4.5% of
joined contracts outside the widened spread, attributable to EOD wing
spreads + vendor capture-minute fuzz + first-order adjustment. Full
story in DOLTHUB-EVAL.md addendum. Also today: intraday recorder's
first live session confirmed writing (SPY 14,124 / QQQ 11,350 /
IWM 5,168 rows per minute snapshot).
