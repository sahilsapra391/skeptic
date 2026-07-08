# Skeptic — Build Log

Session notes per milestone (cross-milestone rule in docs/BUILD-PLAN.md):
date, what shipped, deviations from spec and why.

## 2026-07-02 — M2: backtest engine core (branch m2-engine)

**Shipped, fixtures first per the plan.** Six hand-computed fixtures
(tests/fixtures/engine/, math in docstrings): short put OTM expiry, short
put assigned, credit spread stop, iron condor profit target, covered call
called away, skip on zero bid — the engine matches every one to the cent.
Then the engine that passes them:

- `app/engine/market.py` — `MarketView(as_of)`: every accessor hard-bounded
  by as_of; `LookaheadError` canary tests are in the permanent required set.
  Fixture stores and the real loader produce the identical shape, so
  fixtures exercise the exact production path.
- `app/engine/{fills,selection,conditions,engine,metrics,runner}.py` —
  fill model per TECH-SPEC §5 (mid + slip toward adverse; commissions per
  contract per side, option legs only), expiration/strike selection (delta
  / offset % / ATM / width-from-leg), daily loop (open-stock unwinds →
  exits by priority stop→target→time→condition → expiration settlement →
  entries → conservative-liquidation marks), assignment modeling
  (shares at strike, liquidated next open), full trade log with skip
  reason codes, metrics (documented conventions: 252d annualization,
  ddof=1, CAGR by calendar days; uncomputable ⇒ None, never 0).
  mypy runs STRICT on app/engine/*.
- `app/data/chains.py` — lake loader: source precedence av>yahoo>dolthub,
  dolthub quarantine honored (state `done` list), ~1,100 per-session
  objects fetched concurrently + local disk cache keyed by a listing
  manifest. **Deviation:** thread-parallel object reads instead of DuckDB
  httpfs (TECH-SPEC §4) — the one-object-per-date layout globs poorly;
  cold load 8.9 s, warm < 1 s, engine itself 0.03 s (target < 15 s ✓).
- Runs storage (`app/db.py`, SQLAlchemy): `runs` + `run_events`;
  DATABASE_URL → Neon when provided, local SQLite otherwise — same code
  path, so Neon is purely an env change. POST /api/backtest +
  GET /api/runs{,/{id}} are real; parse/ask/sweep stay explicit 501s.
- **Real runs render VERDICT-WITHHELD until M3** — the refusal state from
  the approved design, templated server-side from computed numbers only
  (guardrail #4 by construction). The frontend demo fixtures now serve
  demo- ids exclusively; a real run id can never receive fixture data.

**Acceptance:** 50 backend tests green (fixtures to the cent, canary,
API happy path over a fixture store, determinism); canonical strategy on
the real lake: 212 filled / 92 skipped over 1,072 chain sessions
2020-01-06 → 2026-07-02, sane skip reasons (16 no-chain Mondays = archive
gaps) and COVID-era assignments exactly where they belong; browser E2E:
compose → spec → gauntlet → real verdict-withheld results.

**Documented approximations (per TECH-SPEC §5):** stock legs fill at
reference prints (strike at assignment, next session open at liquidation,
close at covered-call entry) with no added spread/commission; long ITM
legs cash-settle intrinsic at expiry; `time_exit_dte: 0` = hold to
settlement. Exits requiring quotes wait for the next quoted session on
checkpoint-marked history (DOLTHUB-EVAL §7 honored — no interpolation).
Early-assignment-through-ex-div modeling is deferred with the ex-div
calendar (noted for M6 methodology notes).

**Also this session (owner-requested):** voice dictation in the composer
(native SpeechRecognition: streaming interim results, silence-timeout
auto-restart, honest permission errors) and the inert auto-scale label
removed from the chart controls.

**M2 addendum (same day, owner-requested):**

- **Neon live.** Owner added DATABASE_URL to collector/.env (the line used
  `KEY: value`; fixed to `KEY=value`); psycopg2-binary added; run storage
  verified writing to the Neon host. Local SQLite remains the no-env
  fallback.
- **Chart-teach now infers the structure from the pins** instead of always
  compiling a short put: each pinned move is z-scored against the series'
  own same-span volatility (timeframe-adaptive), then classified — gentle
  drift up → short put; conviction move up → long call; gentle drift down
  → call credit spread; conviction down → long put; mixed/near-sideways →
  iron condor. Direction threshold is deliberately permissive (a pinned
  move is an intent signal); structure defaults set delta/exit per class.
  Pins raised 3 → 10; tickets show each example's % move.
- **Spec screen fully editable:** ticker + structure steppers, strike in
  .05Δ steps down to .05Δ, DTE 0–50 with direct input, editable anchor, a
  structured TRIGGER editor (indicator × operator × value × period —
  maps 1:1 onto a spec Condition, so what's edited is what the engine
  evaluates), and re-editable exit with preset chips + custom text.
  **0DTE is refused honestly** at the run button (minute engine pending,
  DATA-PIPELINE §7) — the dial allows it, the run explains why not yet.
- Chart toolbar reduced to one row: presets · intervals · ƒ indicators;
  the candles/line switch moved inside the indicators menu.

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

## 2026-07-02 — Design handoff implemented: M0 scaffold + app frontend + real coverage API

Owner delivered the approved Claude Design export ("Skeptic Options Research
Tool-handoff.zip") and asked for it to be implemented. Imported to
docs/design/ (Skeptic App.dc.html is the consolidated final; the Wireframes
file is exploration iterations).

**Shipped (branch `design-implementation`):**

- **M0 scaffold:** `backend/` (uv + FastAPI, `/api/health`, bearer
  middleware, CORS) with pydantic-v2 models matching
  strategy-spec.schema.json (20 tests: canonical-spec round-trip +
  guardrail rejections — slippage 0 = mid fills, empty exit, bad ticker,
  extra keys); `frontend/` (Next.js 14 app router, TS, Tailwind, tokens
  extracted from the design: Archivo + IBM Plex Mono, #14161a ground,
  trust hue #3fc1cf family vs P/L #43c987/#e0604f as separate Tailwind
  token families); `.github/workflows/ci.yml` (ruff/mypy/pytest +
  lint/typecheck/build). All checks green locally; Actions run pends the
  push.
- **Real data routes (ahead of M2, because the lake exists):**
  `/api/data/coverage` (port of collector/coverage.py + dolthub state,
  both quarantine gates counted, named blind spots, 5-min cache) and
  `/api/data/underlying/{ticker}` (daily closes for chart-teach). Verified
  against the live lake: SPY chains 2020-01-06 → 2026-07-01 (1,071
  sessions), QQQ/IWM 1 session, minute lake 604 frozen sessions,
  recorder heartbeat minutes-fresh. Backend loads R2 creds from
  collector/.env locally (recorder's pattern); refuses with 503 — never
  fakes — when unconfigured.
- **All design screens:** New Analysis (text + chart-teach modes; live
  per-ticker-asymmetric coverage chips), Spec confirmation (editable dial
  tiles, missing-exit question flow), gauntlet progress, Results
  (Verdict Block hero with trust band; fades-oos / survives / refusal
  states; refusal dims everything below "UNBLESSED OUTPUT"), Library
  (mini trust bands, empty state), Data Observatory (all-real telemetry:
  days on record, recorder heartbeat, per-source lanes, quality flags,
  blind spots), Settings. Verdict components use trust tokens only; P/L
  color appears only in trade-log P/L, walk-forward bars, drawdown, MAX DD.
- **Run pipeline honesty:** backend serves explicit 501s for
  parse/backtest/runs (M2–M4 pending) but validates specs at
  /api/backtest already (invalid IR = 422 today, same as post-M2). The
  Next proxy (bearer token server-side) falls back to demo fixtures for
  those routes only; every demo payload carries `demo: true` and the UI
  badges it "demo data — engine lands at M2". Data routes never fall back.
- End-to-end verified in a real browser: canonical strategy → spec →
  gauntlet → verdict; QQQ run → refusal state; Observatory live.

**Deviations from spec, and why:**

1. **Milestone order:** M5's visual layer landed before M2–M4 because the
   owner asked for the design implementation now. Engine, honesty layer,
   and parser remain the next sessions, in BUILD-PLAN order; the demo
   fixtures (frontend/lib/demo.ts) are deleted the day they land.
2. **Demo numbers are the design's illustrative content, labeled** — with
   two honesty edits: refusal copy states the true coverage fact ("QQQ
   record began 2026-07-01") instead of the mock "42 days", and demo runs
   on QQQ/IWM always land in the refusal state because a full verdict on
   days of data would be dishonest even as a placeholder.
3. **Charts are bespoke SVG components** matching the mockups exactly, not
   Recharts (TECH-SPEC §8) — reconsider when real payload shapes land.
4. **Observatory content follows the design brief** (per-source lanes,
   named blind spots) where the dc.html carried placeholder content from
   a dead assumption (AV backfill runways).
5. **Library compare-two** (brief) is absent from the final approved
   design → not built this pass.
6. **Pydantic models forbid unknown keys everywhere** (stricter than the
   JSON Schema's root-only rule) so malformed IR fails loudly.
7. The design's chart-teach input ("show it on the chart") is implemented
   against the real underlying series from the lake; its compiled spec
   maps pins → signal_only entry + drawdown_from_high_pct condition. The
   real parse of pinned examples is M4 scope.

## 2026-07-02 — Full market charts (owner-directed scope addition)

Owner asked for brokerage-grade charts (reference: Robinhood Legend
screenshots): any timeframe, live, candles/line, indicators, all three
tickers. Shipped on `design-implementation`:

- **Backend `/api/data/bars/{ticker}`** (app/data/bars.py): intervals
  1m/2m/3m/5m/15m/30m/1h/4h from the lake's underlying minute bars
  (2024-02 →, 9:30-ET-anchored resampling, extended hours included),
  1D/1W from dailies (1993 →, W-FRI weekly); windows 1D→All, capped at
  2,000 bars; **live tail from Alpaca's IEX feed at request time when
  APCA_* keys are configured** (stock data — not OPRA-gated; without keys
  the payload states its exact freshness). Verified against the live
  lake: 5m/1h/1D/1W for SPY/QQQ/IWM all correct.
- **Indicators server-side** (app/data/indicators.py, per the "backend
  owns all math" rule): SMA, EMA, Wilder RSI, session-anchored VWAP,
  Bollinger, MACD — each with a hand-computed fixture test (11 new tests;
  31 total). Warmup values are NaN/absent, never extrapolated.
- **frontend MarketChart** (components/charts/market-chart.tsx): candles
  (path-batched for 2k bars) or line, crosshair with OHLCV readout +
  price/time chips, range presets (1D 1W 1M 3M YTD 1Y 5Y All), interval
  chips, indicator menu (Volume, SMA 20/50/200, EMA 9/21, VWAP, BB,
  RSI + MACD subpanels), 15 s polling with a live badge when the tail is
  live. Candle/volume up/down uses the P/L pair (market up/down is
  P/L-family data); the trust hue appears only on pins/status.
- **Chart-teach rebuilt on MarketChart**: pins are bar timestamps (works
  at any interval; changing view clears pins, stated in the UI), tickets
  and spec ANCHOR show real bar dates. Verified in-browser: All-window
  weekly since 1993 with SMA 200 + RSI, pinning on 1Y daily, compile →
  spec with anchor/trigger intact.

**Limits stated in-product, per the data evals:** tick intervals are
refused with the honest reason (no tick data exists at $0 —
INTRADAY-OPTIONS-DATA-EVAL); without APCA keys charts say "through <last
close> · nightly lake". Note: the first *scheduled* nightly containing
the minute top-up runs tonight (2026-07-02 21:30 UTC) — July's minute
file lands then; the two Jul-01 runs in Actions were the pre-Alpaca M1
verification dispatches. Deviation: the design's chart area gains a
control bar not in the dc.html mockup (owner-directed); simplicity held
by one indicators menu and chip rows, no config trees. The old sample
-series fallback was removed — an unreachable lake now shows an honest
error, never a synthetic market.

## 2026-07-02 — Fluid chart navigation (owner screen recording replicated)

Owner supplied an 88 s Robinhood Legend recording as the interaction spec
(frames extracted via AVFoundation and studied): grab-drag panning that
tracks the pointer 1:1, inertia on release, cursor-anchored wheel/pinch
zoom, continuous y auto-scale, older data streaming in as you pull back
(their 2m chart pans Jul → Jun 26; daily pans 2025 → 1994), future
whitespace right of the latest candle. Shipped on `design-implementation`:

- **Backend paging:** /api/data/bars gains `before` + `limit`; every page
  is computed with a 300-bar indicator lookback so SMA/RSI/MACD arrive
  warm at page seams (verified: window-edge SMA non-null, page seams
  contiguous for 1d and 5m). `has_more` tells the client when the lake
  is exhausted. GZip middleware for the larger payloads.
- **MarketChart viewport model:** buffer (grows by prepended pages) +
  fractional view {start, span}. Pointer-capture drag, velocity-decay
  inertia, wheel zoom anchored at the cursor frac, horizontal-wheel pan,
  left-edge paging with a 200 ms cooldown, 12k-bar buffer ceiling per
  interval (coarser intervals are the tool for deeper zoom-out), right
  overscan, "→ latest" jump chip, y auto-scale over the visible slice,
  display decimation ≥700 bars (absolute-grid-aligned so candles don't
  shimmer while panning).

Three real bugs found by driving it in the preview browser and fixed:
(1) absolute-position view writes raced page-prepend index shifts —
all hot-path writes are now delta-based; (2) `getBoundingClientRect()
.width` transiently reads 0 in some environments, poisoning px→bars
conversion — replaced with clientWidth + last-known-good caching + a
one-screenful cap per event; (3) React's queued functional updaters get
REPLAYED on rebase under continuous paging, livelocking the render loop —
the viewport now lives in a ref mutated imperatively, with rAF-throttled
plain-state snapshots (setTimeout fallback: backgrounded tabs starve rAF).

Verified end-state in-browser: pan travels exactly the dragged distance
plus glide (Jun 30 → Jun 22, span preserved), zoom out pages bounded then
zooms back in cleanly, pin-click still compiles a chart-taught spec.
Tick-level data remains impossible ($0 sources don't exist — intraday
eval); 1-minute stays the honest floor.

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

## 2026-07-02 — M3: the honesty layer — every backtest now runs the gauntlet

The product's reason to exist. `app/honesty/` lands with five attack
stages (TECH-SPEC §6): OOS 70/30 chronological split, walk-forward on
42-session folds, Monte Carlo circular block bootstrap (block 5, 1,000
seeded resamples), ±20% sensitivity sweep that re-runs the real engine
per neighbor, and deflated Sharpe with a per-family trial counter
persisted in Postgres. Trust is computed by deterministic rules — level
= 1 + core attacks survived, DSR < 0.5 or OOS sign-flip caps at 2, and
thin samples (< 30 trades or single VIX regime) are never blessed
regardless of the numbers. Verdicts are template-first and grounded by
construction; the numeric validator rejects any narrated number absent
from the stats payload (LLM narration auto-activates later via
OPENROUTER_API_KEY, same validator, template fallback). The permanent
go/no-go test now exists: an in-repo 108-combo optimizer tunes a short
put on synthetic zero-edge GBM data (BS-priced chains), finds in-sample
Sharpe 0.68, and `test_overfit_fixture.py` asserts the gauntlet flags
it forever — a green run on that fixture is a failing build. Pipeline:
`POST /api/backtest` → engine → gauntlet (staged run_events drive the
live progress UI) → verdict → payload with real trust band, attack
chips, IS/OOS bars, walk-forward bars, MC fan, and labelled ±20%
sensitivity grid. Canonical SPY short put: full gauntlet in 2.3s
(acceptance < 60s), 232 trades, 3 regimes. UI alongside: one-line
headline, strike/DTE dropdowns (.05Δ–.95Δ, 0–50), per-structure exit
preset sets. Backend 66 tests green, ruff + mypy strict clean.

## 2026-07-02 — LLM narration live, grounded Q&A, verdict unlock at 15 trades, UI polish round

OPENROUTER_API_KEY landed in collector/.env, so two surfaces went live at
once. (1) **Verdict narration**: write_verdict now actually reaches the
LLM — fixed fence-wrapped JSON (extract the outermost {...}), retry on
non-JSON, and hardened the numeric validator's grounding set (list
lengths are legitimate counts, calendar years in report dates are
identifiers, integer-rounded percentages of harvested stats allowed).
The validator earned its keep immediately: it rejected derived numbers
("2.39×" was fine, invented "-55" was not) across three live attempts
before a fully grounded narration shipped — template remains the
fallback, always. (2) **Grounded Q&A**: /api/runs/{id}/ask answers from
a stored stats bundle (engine metrics + honesty report, persisted as
stats_json with an additive micro-migration), same validator, honest
refusal when a number can't be grounded, 501 when no key / no stats;
the Next proxy no longer swallows real runs' ask errors into demo
answers. Owner-set policy change: **minimum trades for a graded verdict
is now 15** (was 30) — MIN_TRADES constant, CLAUDE.md + TECH-SPEC
updated. UI round: ? tooltips explaining every metric tile, honesty
panel, and spec dial in plain English; trade log shows fills only with
skips behind a nested toggle; ticker/structure became dropdowns;
structured custom-exit builder (profit % / stop % / DTE); per-structure
exit preset sets grew 25% profit; compose toggle renamed Describe It /
Show on Chart with proper icons and centered; presets rewritten and
ordered by the user's own run history. Backend 71 tests green, ruff +
mypy strict clean; verified E2E in browser including a grounded answer
to "is this just the 2020 crash?".

## 2026-07-03 — M4: the NL parser — English → spec-or-questions, never guesses

`app/parser/parse.py` + real `POST /api/parse`: OpenRouter structured
output emits either a schema-validated StrategySpec or clarifying
questions (id + question + concrete options); `answers` converge over
multiple turns; description_raw is overwritten server-side with the
user's verbatim text so the model cannot paraphrase the record; failed
validation retries once with the exact pydantic errors, then falls back
to questions — a half-valid spec never escapes. One documented
convention only: unstated tenor with a "close at 21 DTE"-style time
stop uses the 45-DTE cycle (surfaced on the spec screen, where nothing
runs unconfirmed). **Eval harness** (`evals/run_parser_eval.py` + the
12-case set with hand-written ground truths in `evals/parser_cases.json`):
first live run scored 3/8 clear — the model fabricated `time_exit_dte=0`
and over-asked when one exit rule sufficed; after tightening the
contract ("the exit object contains ONLY rules the user stated; one
rule is a complete exit") the harness scores **8/8 clear, 4/4
ambiguous — ACCEPTED** (bar was ≥7/8). Hermetic unit tests cover the
server-side guarantees with the LLM mocked. Frontend: one-at-a-time
clarifying questions ("QUESTION 1 OF 4 — I DON'T GUESS") with option
chips + free text; an unedited parser spec runs verbatim, dial edits
rebuild from the dials; parsed entry conditions render in the trigger
editor; non-delta strikes ("5% below spot", ATM) keep their honest
label in the strike dropdown.

## 2026-07-03 — M5 closeout + grounded recommendations + interactive results

The remaining M5 pieces plus an owner-requested UI round. **Grounded
recommendations**: every results page now carries "WHAT WOULD IMPROVE
IT — COMPUTED FROM THIS RUN" — suggestions derived exclusively from the
run's own gauntlet numbers (the ±20% sweeps genuinely re-ran the
engine: "delta .36Δ beat the specced .30Δ: Sharpe 1.01 → 1.12"), plus
OOS/MC/walk-forward observations when flagged, refusal-aware, capped at
4, with the standing caveat that acting on one is a new trial the
deflated Sharpe will count. **Interactive visuals**: equity chart has a
hover crosshair (date · $ · drawdown, OOS-aware), $-axis and date/split
labels, an OUT-OF-SAMPLE marker; walk-forward bars carry per-fold
tooltips (dates · return · trades); the Monte Carlo fan labels its
bands with terminal dollars; the sensitivity grid shows the actual
swept value in each cell with Sharpe-on-hover and a ring on the
as-specced column. **Layout**: hero centered and scaled up, shell
widened 940→1180px, 12 preset strategies centered and ordered by the
user's own run history, sidebar collapsible (icon rail by default,
labels when expanded, toggle at bottom-left, persisted). Lighthouse
accessibility on Results: **96** (≥90 accepted; sole flag is the
design's muted-ink contrast). Backend 76 tests, ruff, mypy strict,
frontend tsc + lint all green; verified E2E in browser: ambiguous
strategy → 4 questions → spec with trigger → gauntlet → LLM verdict
leading with the walk-forward weakness.

## 2026-07-03 — Live previews, editable costs, and the Verbiage Complexity setting

Owner round three. **Gauntlet previews**: "no previews, no dopamine" is
retired — as each stage finishes, its REAL headline stat streams into a
"LIVE FROM THE GAUNTLET" feed (fills + net equity, unseen-data Sharpe
holding/fading, windows profitable, reshuffle loss rate, plateau/cliff),
stored progressively in a new previews_json column and served on the
running payload; a rotating platform-tips panel fills the quiet moments.
**Editable costs**: commission and slippage live in Settings
(localStorage, clamped — slippage floors at 0.05 because mid fills stay
banned) and are stamped onto EVERY submitted spec client-side, parsed or
dial-built; the spec screen's FILLS tile shows the live values. Verified
E2E: slippage 0.5 → 0.75 changed the same strategy from 232 to 211
fills. **Verbiage Complexity (Institutional | Retail)**: every run now
ships both registers — the LLM narrates twice (retail prompt bans
jargon: "risk-adjusted score" not Sharpe, "reshuffling the trades" not
Monte Carlo) behind the same numeric validator, with a deterministic
retail template fallback; payload carries a retail block (headline,
evidence, breaks, caveat, panel notes, recommendations) and the UI
switches instantly — panel titles ("LUCK TEST — 1,000 RESHUFFLES",
"TRAINING DATA VS UNSEEN DATA"), metric tile names ("WORST DIP",
"RISK SCORE"), gauntlet stage names, and grounded Q&A all follow the
setting. Old stored runs fall back to institutional. Settings page
rebuilt with live system status (engine/parser/narration/Q&A/model/
min-trades from /api/health). **Sizing pass**: market chart 300→430px
tall, verdict headline 25→30px, metric tiles 19→24px, panels/text/
buttons up across compose, spec, gauntlet, results, settings. Backend
76 tests + ruff + mypy strict green; frontend tsc + lint green;
verified in-browser across both registers on a single run.

## 2026-07-03 — Width pass + sidebar defaults + hero cleanup

Owner adjustments: content shell 1380px; composer and preset rows
widened; results and chart mode fill the page; library becomes two wide
columns with larger cards. Sidebar now defaults to OPEN (choice still
persists), labels title-cased (New Analysis, Data Observatory). The
coverage chips row and "day 2 of collection" link are gone from the
hero — coverage lives in the Data Observatory, where it belongs.

## 2026-07-03 — Fluid width: 1800px shell

Owner: wider still. The shell cap moves to 1800px, making every page
effectively fluid on real monitors — the market chart in Show-on-Chart
mode spans ~1430px on a 1728-wide window, library cards ~725px each,
results panels track the full width. Composer 1320px, preset rows
1440px. Operational lesson recorded: tailwind.config.ts edits do NOT
hot-reload — restart the dev server or the old token values keep
being served (the 940→1380px bumps only took effect after this
restart).

## 2026-07-03 — Two bug fixes, −10% width, library verbiage, instant chart

**Bug 1 — trust band overflow:** at level 5 the ±15% band spilled past
the track (75% + 30% = 105%). Clamped in the payload (left ≤ 70%) AND
client-side with CSS min() so already-stored runs render correctly too.
**Bug 2 — degenerate DTE sweep:** ±20% of a 1–2 day tenor rounds back
to the same day, producing five identical "1d" cells and a trivially
"plateau" classification. Short tenors now sweep whole days (1–5 for a
2-DTE spec) with a per-sweep base index so the ringed as-specced column
stays honest; verified across dte 1/2/45/90. **Width:** shell trimmed
10% (1800→1620px), settings page joins the uniform full-shell width.
**Verbiage everywhere:** library cards now carry the retail headline
(quoteRetail in run summaries) and switch with the setting like the
rest of the app. **Chart speed:** the hero prefetches the exact bars
request the chart issues on first mount (60s in-flight cache, failures
never cached) — a warm mode-switch measured 24ms vs ~1s cold.

## 2026-07-03 — Chart expand toggle + uniform chart chrome + sidebar always open

The hero chart now defaults to the Describe It box width (1190px) with
an expand toggle at its top-right: enlarge to full page for serious
charting, shrink back with the same button. All the chrome around the
chart was undersized relative to the canvas — ticker tabs, OHLC
readout, freshness note, window/interval chips, time axis, indicator
menu, price ticks, pin notes and the footer all bumped to a uniform,
readable scale. Sidebar now opens on every load (collapse lasts for
the session only — no persisted state).

## 2026-07-03 — Verbiage-aware tooltips, run back-button, Library nav highlight

Every ? tooltip (metric tiles, equity, honesty panels, trade log,
recommendations, all ten spec dials) now carries both registers and
follows the Verbiage setting — and static UI text switches on the
setting alone, so runs stored before the retail feature still get
retail tooltips/tile names while their verdict text honestly falls
back to institutional. Saved-run pages get a "‹ Library" back button
and keep Library highlighted in the sidebar (/runs/* is a library
entry, not a new analysis). Describe box and hero chart trimmed ~5%
to 1130px, still width-matched.

## 2026-07-03 — Design language: calm/editorial pass (owner-directed)

Owner supplied a reference (Harvey-style legal-AI app: serif display
type, generous whitespace, floating pill composer, quiet inline
actions) and asked for that calm in dark mode. OWNER SIGN-OFF noted:
this consciously evolves beyond the original docs/design mockups.
Shipped: Newsreader serif for display headings (hero, Library,
Settings, Data, gauntlet); hero reworked — S-mark over a serif
rotating headline, composer as a floating 26px-radius card with soft
shadow, mode chips (Describe It / Show on Chart) INSIDE the card
bottom-left, mic + round arrow-submit bottom-right, quiet disclaimer
line beneath; preset cards softened. Sidebar gains a RECENT ANALYSES
section (last 6 runs, live, highlights the open one) mirroring the
reference's history list. Verified E2E: compile via the round submit
still lands on the spec screen.

## 2026-07-03 — Newsreader everywhere (standing owner directive)

Newsreader is now THE app typeface — every word on every page, strictly:
all three Tailwind font tokens (sans/mono/serif) resolve to it, Archivo
and IBM Plex Mono are removed, and SVG chart text (price ticks, hover
chips, panel labels, MC band labels, equity axis) uses the same
variable. Verified by computed style on the verdict headline, meta
lines, chips, and chart <text> nodes. The directive is codified in
CLAUDE.md (Engineering conventions → Typography) and in session memory:
no other font family may ever be introduced.

## 2026-07-03 — Typography settled: serif for headings only (revised directive)

Owner revised the same-day serif-everywhere directive after seeing it:
Newsreader is RESERVED for headings and important moments (page h1s,
hero headline, gauntlet heading, and now the verdict headline); body
returns to Archivo, data returns to IBM Plex Mono (including SVG chart
text). CLAUDE.md typography rule and session memory rewritten to the
three-voice system — no other families, serif never in body copy.

## 2026-07-03 — Sidebar drag-resize

The sidebar edge is now a drag handle: resize freely up to 380px, drop
below 120px and it snaps into the existing 56px icon rail, release
between 120–172px and it settles at the open floor — the same collapse/
open mechanism the toggle uses, and the toggle restores the last
dragged-open width. Implementation is fully imperative (listeners
attached in the pointerdown, settle computed from the release event's
own coordinates) after an effect-based version proved race-prone.
Verification note for the log: the preview harness freezes the CSS
animation clock, so width transitions never advance there — assert on
style.width or disable transitions when testing; real browsers animate
the 150ms ease normally.

## 2026-07-03 — Brand kit integration

Owner delivered the Skeptic brand kit (SKEPT/C wordmark; the S is two
identical hooks under 180° rotation — the same question asked from both
sides). Wired in per the kit's usage rules for our dark surfaces:
white wordmark in the open sidebar, standalone white S-mark when
collapsed and above the hero headline; kit favicon.ico + gray-tile 512
+ apple-touch-icon in metadata; og-image for link previews. First boot
per browser session plays the draw-on animation (the kit's pathLength-
dash SVG, no JS) as a full-screen splash that fades into the app —
gated at module scope after React StrictMode's double-effect consumed
the session flag and stranded the overlay on first attempt. SVG
masters + animation live in frontend/public/brand/.

## 2026-07-03 — Favicon centering audit

Owner spotted the favicon S riding high. Audited every S asset in the
kit programmatically (glyph bbox center vs canvas center): the dark
tile (512/180/32), light tile, and transparent-grayS all carry the S
~10% above center; the gray tile, white circle, s-mark renders,
apple-touch-icon and maskable are true. Rebuilt our dark tiles from
scratch — sampled bg #101014 and the 113px corner radius from the
original, took the glyph from the verified-centered s-mark render,
composited at identical size, dead center (residual ≤0.5px from odd/
even rounding at small sizes). favicon.ico regenerated from the fixed
512. Kit source files in Downloads left untouched — worth regenerating
upstream in kit.py someday.

## 2026-07-03 — Neon transfer quota exhausted: graceful fallback + the fix for the cause

Owner hit "backend unreachable" and asked if Neon's monthly transfer
limit was the cause. Confirmed directly: Neon now refuses connections
with "Your project has exceeded the data transfer quota" — and the
backend used to DIE at startup because init_db connects at boot.
Two fixes. (1) **Graceful degradation:** if the configured DATABASE_URL
is unreachable at startup, the backend logs it, falls back to the local
SQLite file, and /api/health + Settings report "local SQLite fallback —
…quota…" honestly; charts, parser and new runs all keep working (runs
stored during the outage live locally, not in Neon). (2) **The actual
transfer hog:** /api/runs pulled full payload_json (equity series and
all, ~100KB+ each) for up to 50 runs on EVERY listing — and the new
sidebar requests the listing on every navigation. New summary_json
column (~500B) written at run completion and backfilled lazily for old
rows; listings now read only that. Client side, listRuns gets a 30s
TTL cache. Estimated egress per listing drops ~99%.

## 2026-07-03 — Appearance settings: light/dark mode + four accent colors

Settings gains an APPEARANCE panel: Mode (Dark default / Light) and
Accent (Cyan default, Sage, Lavender, Rose — four max per owner). Under
the hood the whole palette moved to CSS variables: every Tailwind color
token now reads a var, hardcoded chart/SVG colors (grids, candles,
crosshair, MC bands, heat cells, OOS shade, overlay bars, shadows,
gradients) were converted, and <html data-theme/data-accent> switches
everything at runtime — an inline pre-hydration script applies the
stored choice before first paint, so no flash. Light mode is the brand
kit's paper palette (#F4F4F5 ground, ink text); each accent carries a
deepened light-mode value so accent text keeps contrast on paper. The
brand marks and boot splash follow the theme (black wordmark/S/draw-on
in light — derived from the white masters since the kit uses one-color
strokes). Color contract intact in every combination: trust hue never
colors P/L and vice versa. Persisted in the same local settings store
as costs/verbiage.

## 2026-07-03 — M6 artifacts: Dockerfile, smoke script, RUNBOOK

Deploy prep (owner merged the design PR and called for M6):
backend/Dockerfile (uv-based, python 3.13-slim, runs uvicorn on
Railway's $PORT) + .dockerignore + railway.json (Dockerfile builder,
/api/health gate, on-failure restarts); scripts/smoke_prod.py walks the
canonical strategy health → parse → backtest → verdict against a prod
URL with the bearer token and fails on any demo flag or timeout;
docs/RUNBOOK.md covers topology, every env var for both platforms,
token rotation, deploys and cold-start behavior, collector operations,
the Neon fallback/quota story, and cost dashboards.

## 2026-07-03 — Prod polish: trade-log bug, speed, gauntlet theater, DeepSeek default

Post-deploy round. **Bug:** the trade log capped the last 250 EVENTS
before splitting fills from skips, so a signal strategy with 1,614
skips showed 4 of its 17 fills — fills and skips now cap separately
(all fills up to 400, last 250 skips). **Speed:** the two verdict
narrations (institutional + retail) now run in PARALLEL; market stores
get an in-process cache (the parquet parse happens once per container,
not once per run); and the backend prewarms the SPY lake in a
background thread at boot, so the first user run on a fresh Railway
container no longer pays the cold R2 pull. Neon-paid explicitly NOT
the fix — the DB never was the bottleneck. **Gauntlet theater:** the
heading now fades through 20 sibling phrases ("Interrogating the
edge", "Hunting for luck in the results"…) every 3s with an animated
ellipsis, and the tips pool grew 8 → 50, played in shuffled order so a
session rarely repeats one. **Model:** default LLM switched
anthropic/claude-sonnet-4.5 → deepseek/deepseek-v4-pro (owner). The
parser eval initially REJECTED DeepSeek (5/8 — over-asked day-of-week,
dropped indicator periods); two new prompt conventions ("weekly with
no day named → monday", "the number attached to an indicator IS its
period") brought it to 8/8+4/4 and 7/8+4/4 across two runs — ACCEPTED.
OPENROUTER_MODEL still overrides per-deployment.

## 2026-07-04 — Dictation overhaul, auto-growing chatbox, retail gauntlet previews

**Dictation** (the big one): new `frontend/lib/dictation.ts` rewrites
every transcript chunk — interim and final — from prose into
strategy-speak. Spoken numbers become digits with full compound support
("twenty one" → 21, "two hundred" → 200, "point oh five" → 0.05),
"percent" fuses onto its number (50%), "five dollars" → $5, tickers and
indicator acronyms are canonicalized whether spoken or spelled out
("the Q's" → QQQ, "I W M" → IWM, "R S I" → RSI), domain mishears are
repaired ("iron condo" → iron condor, "putt" → put, "seller" → sell a),
and everything else is lowercased — no more mid-sentence capitalization.
E2E-verified by driving a fake SpeechRecognition through the real
pipeline: six spoken utterances landed verbatim-correct in the composer.
**Chatbox** now grows a line at a time with its content (27 → 54 → 82px,
capped at 200px then scrolls) — the second line is never hidden again.
**Retail previews**: gauntlet stage previews now ship BOTH voices
({pro, retail} per line, old string-only runs still render); the live
feed header, preview lines and the 50-tip pool all follow the Verbiage
setting — a fresh retail run showed zero jargon ("on data it never saw:
risk-adjusted score 1.76 vs 0.67 — holding ✓"). Backend battery green
(76 tests, mypy strict), tsc/lint green.

## 2026-07-04 (later) — Full trade log, wf-bar fix, in-progress runs in the library

Trade log is now COMPLETE: every fill event and every skip ships in the
payload, uncapped (the old 400/250 caps hid fills on active strategies;
rows are ~100 B and only travel when a run is opened). The walk-forward
"time periods" panel had flat indistinguishable bars — heights were
normalized against ALL folds while only the last 16 display, so one wild
2020 window flattened everything visible; normalization now uses the
displayed window (verified: heights 17.6–52 vs all ~15 before). And
in-progress runs no longer vanish when you navigate away: the library
lists queued/running runs with a pulsing dot + "gauntlet in progress —
stage N of 6" card (polling every 4 s until done), the sidebar's recent
list gets the same dot, and /runs/{id} shows the LIVE gauntlet screen
with previews, polling until the verdict lands and flipping to results
in place. E2E-verified: launched a run, left for the library mid-flight,
watched the card, opened it live, saw it complete. Note: runs stored
before this deploy keep their old capped logs and flat bars — payloads
are frozen at write time.

## 2026-07-04 (bug sweep) — ATM zero-fills, wing selection, parser over-asks, chart/chat parity

Owner-directed bug hunt: automated battery (12 utterances → real parse →
engine) plus browser E2E on both compose modes. Found and fixed:
**ATM (the reported bug).** "at the money" parsed to method "atm" —
shown as an "ATM" value the strike dropdown doesn't hold, and when a
spread's second leg also came back "atm" both legs resolved to the same
strike, so EVERY entry died as duplicate_leg_strikes → zero fills. ATM
now normalizes to .50Δ at the parser (prompt convention + deterministic
post-pass; legacy atm specs draft as an editable .50Δ). Verified E2E:
the dictated ATM strategy now runs 211 fills.
**Wing selection.** width_from_leg picked nearest-by-absolute-distance —
on coarse strike grids the wing could land ON the reference (dead skip;
53 of them in one ATM spread run) or the WRONG SIDE (an inverted
spread). Wings now select only from strikes strictly beyond the
reference; no candidates → honest "no_wing_strike" skip. 3 new tests.
**Parser over-asking.** Two flakes seen in the battery: "what does 21
days refer to" (now a stated convention: exit-clause days ARE
time_exit_dte) and percent-indicator units (0.03 vs 3 — now stated:
delta is the only decimal field). Eval ground truths updated for the
ATM change; harness re-ACCEPTED twice (7/8+4/4, 8/8+4/4).
**Chart/chat parity.** Chart compile invented an exit ("50% profit ·
21 DTE"), delta and a canned 2% trigger — the exact silent-guess the
chat path refuses. Now: exit ships UNSET so the spec screen asks its
one question exactly like the chat path, and the trigger threshold is
honestly derived from the pins (average pullback-from-high at the
pinned entries, ½%-rounded, clamped 1–10%). Verified E2E: pin → spec
screen question → run (214 fills). Uncapped trade log stress-checked:
1,873 rows render in 79 ms.

## 2026-07-04 (review fixes) — all 15 ultrareview findings closed on PR 16

The xhigh code review of this branch surfaced 15 verified findings; all
fixed, several at a deeper layer than the original patch:
**ATM moved into the IR.** The parse-time post-pass (which could crash on
a malformed model reply) is gone — StrikeSelection itself normalizes
method "atm" → delta 0.5 during validation, so EVERY ingress (parser,
POST /api/backtest, stored specs re-validated for a run) gets it, the
sensitivity sweep's delta axis always applies, and spec_to_draft's
legacy branch is deleted. "atm" also left the prompt's schema line.
**Wings got a tolerance and a bound.** Filled width may deviate from the
requested width by at most the width itself — a $5 wing on a $25 grid
skips as wing_width_unavailable instead of silently trading 5× the max
loss; width ≤ 0 is now a 422 at validation, never reinterpreted
per-entry. Call-side and iron-condor wiring gained the tests they lacked.
**ATM stays greeks-free.** The 50Δ selection falls back to
nearest-to-spot when a session's source carries no deltas (yahoo rows
store none) — definitionally the same strike, so ATM strategies keep
filling on those sessions.
**The chart trigger now measures what the engine tests.** Threshold
derives from DAILY closes vs the rolling 20-session high at the pinned
entries (fetched at compile), and drawdown_from_high_pct honors period —
"2% below its 20-day high" is no longer silently evaluated against the
all-time high (this also fixes the chat path's period-carrying specs).
Monotone clamp (no more 0.2%-pins → 2% while 0.4%-pins → 1%), negatives
impossible by construction, buffer-paging no longer changes the result.
**Parser conventions tightened, not loosened.** "or N days" stays a DTE
exit, but exits counted FROM ENTRY ("sell after 10 days") now explicitly
demand a clarifying question; the decimal-fields line no longer
contradicts offset_pct. Eval re-ACCEPTED twice at 8/8 + 4/4.
**Frontend honesty.** exitRules accepts decimals ("12.5% profit" ran as
5% before — verified 12.5 lands in the spec now), the dead canned-2%
fromChart fallback fails loudly instead, and a chart compile clears any
stale parsed-spec refs. 89 backend tests green (8 new), tsc/lint green,
chart E2E re-verified (151 fills, period-20 condition in the stored spec).

## 2026-07-04 — iVolatility backfill pipeline, built BEFORE the trial clock starts

Decision: iVolatility Lab trial for the 20-year EOD backfill (research
compared Massive/Polygon, Databento, iVolatility — see PR/session notes;
iVol is the only one shipping vendor greeks in exactly our chain shape).
Everything is pre-built so trial day one is pure downloading:
`collector/backfill_ivol.py` (probe mode, resumable state, per-day
validation gates, canonical-column normalization verified against the
vendor's published OpenAPI schema on GitHub), chains loader precedence
ivolatility > av > yahoo > dolthub (unit-tested), coverage page reports
the new source. DATA-PIPELINE §8 documents the trial-day runbook.

## 2026-07-06 — D5a: the scale-in ladder primitive (branch claude/d5a-scale-in-primitive)

**The engine primitive, gated so nothing merged moves.** Spec v3 adds
`entry.scale_in` (a `signal_ladder` of rungs — each an existing condition plus
`add_contracts` — a `rearm`, and a required `max_total_contracts`) and a general
`exit.close_at_time` session force-flat ("no overnight", 5-min clock only,
symmetric with entry `time_of_day`). A basket is ONE accumulating position: its
single leg's qty grows per rung and `premium` stays the BLENDED per-share cost,
so the exit math `(premium + liq)/|premium|` reduces to value/cost − 1 on the
whole basket and the existing exit machinery is untouched. Every basket path is
behind `spec.entry.scale_in is not None` — the three pinned daily digests stay
bit-identical, both lookahead canaries green, overfit fixture still ≤ 2.
**Adds are not trades, for free.** The basket emits one terminal `CLOSE` with a
P&L; rung fires are `ADD` events (never in `filled`), so the sample counter
already counts baskets — a ladder can't inflate to 15 "trades". Per-rung fills
(`RunResult.rung_fills`) are recorded for D5b attribution.
**Five hand-computed fixtures.** PT happy path (+$172.00), martingale-ruin
cascade force-flatted at −$496.00 (loss booked in full, not smoothed), re-arm
(no second basket until the signal leaves the zone), cap-clamp (a +10 rung
trimmed to +5 at the cap), and the interlock. The minute canary is extended: a
rung add fills at the bar it is reached, never the next.
**The interlock (D5a → D5c).** A scale-in run is hard-capped at
`insufficient_evidence` — "scale-in safety checks pending (D5c)", the FIRST cap
reason — no matter the gauntlet or the basket count. Proven as one story: a
ladder that blows up is refused with the interlock LEADING over the thin-sample
cap, and a ladder with ≥15 baskets across two vol regimes (NOT sample-capped) is
still refused, while the identical stage numbers with the flag off grade to a
real level. Documented in HONESTY.md; `unlock_conditions` returns None for the
code-pending refusal so the auto-unlock scan never chases it. Single-leg
(long_call/long_put) and fixed_contracts only this phase — validation refuses
the rest with a reason; the `reversal_signal` stop-mode is wired in the schema
and deferred. Backend suite green: 251 passed, 1 skipped (19 new), ruff +
mypy(strict) clean.

## 2026-07-06 — D5b: depth attribution, the crown jewel (branch claude/d5b-depth-attribution)

**Two tied-out views of a ladder's realized P&L.** New honesty stage
`ladder_depth_attribution` (present on every scale-in run): a per-tier table
(baskets grouped by the MAX rung depth they reached — iVol's P&L-by-depth: count,
win rate, total/avg P&L, share of gross profit vs loss) AND a marginal-rung
analysis (P&L attributable to the contracts added AT each depth — are the deep
adds themselves net negative?). Because the whole basket exits at ONE price, a
fill's marginal = `(exit − fill)·qty·100 − 2·commission·qty`, derived from the
basket's realized P&L, so the per-tier totals AND the per-rung marginals each tie
out to the same realized total — tested to the cent (fixture-1 basket: rungs
−12.60 / +41.10 / +143.50 = +172.00) and on a 20-basket run (shallow tier +$2,306
carries all profit, deep tier −$1,880 is 100% of the loss, deep adds net −$1,043).
**Grounded verdict + prominent panel.** The verdict now MUST reference depth when
a ladder ran ("the deepest adds are net −$X — the edge is not in the deep rungs"),
numerically grounded from the stage and riding in the caveats so it surfaces even
while the interlock withholds the verdict; the LLM path gets the same instruction.
New results panel (marginal-rung bars + the tier table) placed right under the
equity chart — P/L red/green lives on this DATA panel, never the verdict (color
rule honored; verified in the browser preview). No approved depth mockup existed,
so the panel follows the existing results-panel conventions (PANEL tokens, Plex
Mono data) — flagged for owner DesignSync. Backend green: 260 passed, 1 skipped
(9 new), ruff + mypy(strict) clean; frontend tsc + lint clean.

## 2026-07-06 — D5c: scale-in martingale defenses, the interlock lifted (branch claude/d5c-scale-in-defenses)

**Two real defenses replace the blanket interlock.** New honesty stage
`scale_in_honesty` computes, per ladder run: (1) a ruin-tail Monte Carlo —
resamples the basket P&L sequence (seeded block bootstrap, starting capital as
the first peak) and HARD-caps when P(resampled max drawdown > 30%) ≥ 10%
(RUIN_DRAW_THRESHOLD / RUIN_TAIL_PROB); (2) deep-rung dependency — subtracts the
deepest rung's recorded marginals (no re-run) and HARD-caps on a sign flip (a
positive edge that goes negative without the deepest, riskiest adds DEPENDS on
them); (3) basket-size concentration — REPORTED (top basket's share of gross
|basket P&L|), never a cap on its own. `compute_trust` drops the D5a
`scale_in_pending` interlock and takes the `ScaleInHonesty` object instead: trips
either hard cap → insufficient_evidence (reason leads); clears both → judged like
any strategy (**the interlock is LIFTED — a clean ladder can now be blessed**).
**Adds are still not trades.** Sample counting uses closed baskets, not per-rung
fills (a lone ladder built from 4 rung fills is still 1 trade, still
sample-capped) — documented + tested. `unlock_conditions` returns None for a
martingale refusal (strategy property, not thin data → auto-unlock never chases
it). Verdict headline (both registers) names the defense that fired, grounded.
**Acceptance met, one story:** a martingale-overfit fixture (17 ruin @ −251.10 + 3
lucky-deep @ +1855.90, 20 baskets / 2 vol regimes, NOT sample-capped) is refused
with BOTH defenses firing — realized +$1,299 flips to −$486 without rung1
(hand-computed), 25% of resampled orderings draw down > 30%; the clean 20-basket
fixture clears both and grades to level 3. D5c is honesty-only (stages / trust /
verdict / HONESTY.md); no engine or payload change. Backend green: 263 passed, 1
skipped (6 new, interlock test renamed → defenses), ruff + mypy(strict) clean.

## 2026-07-06 — D5d: parser offers the ladder, stops simplifying (branch claude/d5d-parser-ladder · OWNER RE-ACCEPT GATE)

**The parser now runs the ladder AS WRITTEN.** parse.py's system prompt gained
entry.scale_in + exit.close_at_time with explicit conventions: a scale-in ladder
("add 2 at RSI 30, 3 at 25, ...") is SUPPORTED — emit it, never flatten to a
single entry, never say it isn't supported; rungs live in scale_in.rungs and
entry.conditions stays EMPTY (the rungs ARE the signal); rearm = the indicator
leaving the zone; a 5-min ladder indicator ⇒ clock 5min; max_total_contracts is
REQUIRED (the ruin cap) — if unstated the parser ASKS, never defaults it (guardrail
#3); "stop adding when it reverses" → stop_adding_on next_rung_not_reached (the only
implemented mode); "flatten by 3:45 / no overnight" → close_at_time 15:45. sizing
stays fixed_contracts. _required_spec_version recomputes to 3 on scale_in/close_at_time
(server-computed, never trusted from the LLM). Eval grader extended to actually check
scale_in (per-rung indicator/period/timeframe/operator/value/add_contracts + the cap)
and close_at_time, so a ladder case can't pass flattened.
**Eval 18 → 22** (14 clear + 8 ambiguous): golden ladder (case 19 = the founder's
4-rung intraday RSI family → full spec), generality ladder (case 20 = 0DTE QQQ), and
two ASK cases (21 = no cap → asks the ruin cap; 22 = no exits → asks). LIVE EVAL
RESULT: **13/14 clear + 8/8 ambiguous → ACCEPTED**; ALL FOUR ladder cases pass. The
one clear miss is case 3 (pre-existing iron-condor, asked about the "$3 wider" wing —
unrelated to the ladder changes, within the 1-miss tolerance, deepseek nondeterminism).
Hermetic unit tests added (no LLM): the ladder flows through parse_strategy and
recomputes to v3; version detection unit-covered. 265 passed, 1 skipped; ruff + mypy
clean.
**KNOWN INTEGRATION FLAG (frontend follow-up, not parser scope):** a parsed ladder
runs correctly on the parse→run path when the pre-run dials are UNTOUCHED (api.ts sends
the full parsed spec); editing a dial rebuilds from draftToSpec, which does not yet
carry scale_in → the ladder would be dropped. draftToSpec + spec_to_draft need
scale_in awareness for the edit path.
**GATED: owner re-ACCEPT required before merge** (same gate as D1c/D2c). PR opened,
NOT merged — awaiting owner acceptance of the eval.

## 2026-07-06 — Unusual Whales collector prebuilt (before subscription)

Owner directive: prebuild a comprehensive UW collector so trial day one is pure
downloading — bank everything available for SPY/QQQ/IWM, figure out engine use
later. Shipped `collector/backfill_unusual_whales.py` + `collector/uw_manifest.py`:
manifest-driven (59 in-scope endpoints distilled from their 190-path OpenAPI spec —
flow, GEX/DEX dealer positioning, market tide, OI structure, IV rank/skew/term
structure, ETF holdings/flows, shorts, OHLC, per-contract history), Bearer auth,
self-throttling off UW's live rate headers with a resumable daily-budget stop,
faithful json_normalize banking to new R2 prefixes (chain lake untouched). A
`probe` mode auto-detects each `date?` endpoint's history behavior (one-call series
vs per-date) since that's the budget-defining unknown untestable without a token.
Helpers (rows_of/_distinct_dates/to_frame/sessions_desc) unit-tested; ruff clean.
DATA-PIPELINE §9 has the trial-day runbook. NOT wired into coverage/engine yet —
that's the deliberate "collect now, use later" phase. UW options depth ≈2022+, so
this complements (never replaces) the iVol 20-yr analytics + the pre-2022 chain gap.

## 2026-07-07 — ENGINE-V4 F0: data spine (PIT readers + resolution ledger)

V4 program approved (masterplan MD: F0 → FX 0DTE intraday engine → F4/F1/F5/
F2-F3/F7/F8). F0 ships the safety plumbing with ZERO engine-behavior change:
- PIT readers for every new lake source: `app/data/uw.py` (24 per-ticker + 4
  market-wide daily families, 21 series families, 1-min contract bars),
  `app/data/massive.py` (OHLCV aggs; contract directory exposed as non-PIT
  reference only), IVS surfaces in `app/data/ivol_analytics.py`. All require
  as_of, raise LookaheadError beyond it, truncate ROWS at intra-session
  moments (UW files carry intraday stamps), return None when absent, and
  never read `captured_at` as observation time. Bounded LRU caches.
- KEY FINDING baked into the design (owner-confirmed): UW 1-min bars are
  side-attributed trade candles with NO NBBO → minute data upgrades the
  decision CLOCK and validates fills; fill quotes stay on the NBBO hierarchy.
- Per-session resolution map: `app/data/resolution.py` derives
  clock_resolution (minute>five_min>none) + quote_resolution by QUALITY
  (ivol_5min>cboe_2min>eod_only>none, D2 amendment 1) — single
  implementation, imported by `collector/ledger.py`, which rebuilds
  `state/resolution_map/ticker={T}.parquet` + `state/source_coverage.json`
  every run (nightly workflow already calls ledger.py — self-improvement:
  new data upgrades eligibility with no redeploy). Live maps banked: SPY
  4,907 sessions (minute 91 · five_min 2,888), QQQ minute already 10 and
  growing, IWM five_min 2,508.
- Coverage payload: additive `resolution_mix` + `new_sources` blocks;
  Observatory gains the resolution-mix timeline strips + new-sources panel.
- Tests: 303 pass (+28): per-source truncation fixtures (hand-computed),
  LookaheadError canaries in the permanent canary file, an intentionally
  lookahead "evil reader" red test, resolution derivation combos incl. the
  real 2026-07-06 edge (minute+recorder, no iVol yet), summary/timeline
  compression, absent-artifact honesty. Daily-clock + seventeen regression
  digests bit-identical; ruff/mypy/eslint/tsc clean.
Ops: UW intraday collector relaunched QQQ→IWM→SPY (SPY complete at 500
contracts); daily budget hit 29,975/30,000 → budget-aware retry loop armed.
QQQ iVol 5-min backfill completes ~2026-07-08 and flows in via the nightly
ledger rebuild automatically.
REVIEW (independent agent, same session): 2 MAJOR + 6 lesser findings, ALL
FIXED — (1) same-day EOD observations (UW series / Massive daily aggs / IVS
fits) were visible at intra-session moments → datetime as_of now EXCLUDES
the as_of session in those readers; (2) tz-naive stamps were localized as
UTC (fail-open) → new app/data/pit.py detects offsets at the VALUE level
and fails closed; (3) session bound now derives from the UTC-normalized
moment, not the caller's local calendar; (4) contracts_reference returns a
copy (cache-poisoning); (5) CI smoke-imports collector/ledger.py so the
cross-project import chain breaks in CI, not at the 2 AM nightly; (6)
Observatory panels null-guard artifact drift; (7) dead branch removed +
family validation in daily_sessions; (8) ledger gathers UW listings once
per run instead of 3×. 310 tests green after fixes; 7 new fixtures pin the
corrected contracts (incl. the reviewer's exotic-offset probe).

## 2026-07-07 — ENGINE-V4 FX.1: intraday PIT loop + per-session resolution

Survey correction first: 0DTE was ALREADY legal at the 5-min clock (D2:
trading-DTE, same-session settle, SliceCoverage refusal) — FX.1 proves it
end-to-end instead of rebuilding it. Shipped (spec v4, additive):
- `backtest.resolution: "5min"|"finest"` (v4 vocabulary, loud on older
  specs; requires the intraday clock; absent ≡ "5min" bit-identically).
- Per-session selection: engine asks the provider once per run; the
  provider reads the F0 resolution map (clock=minute AND bars_1m grid —
  new additive `has_minute_underlying` column, ledger rebuilt). Minute
  sessions step a 1-min bar grid built from bars_1m underlying NBBO
  (stale prior-session prints dropped, regular hours only) with option
  quotes at their real 5-min NBBO stamps; separate bounded LRU, the 5-min
  disk cache untouched.
- Loop is resolution-parametric: nudge scales to the grid; timeframe-5min
  indicator series + session VWAP read ONLY the 5-min underlying frame's
  stamps on minute grids (same artifact/values/bounds as the 5-min grid —
  review finding 1 hardened; bars_1m rows are price-only refinement). RunResult records
  resolution_mode/mix/compressed runs; payload additive
  (resolutionMode/Mix/Runs).
- Tests 331 (+21): masterplan mixed-run fixture (mix recorded, runs
  compressed), quote-less minute bars fill nothing (skip logged, fill
  detail pins WHICH quote), stop on minute grid = same dollars as 5-min,
  indicator-pollution red test, honest degrade (map empty / grid
  unbuildable), absent≡"5min" bit-identity, 0DTE sell-the-winner
  (PT/force-flat, never settles, to the cent), minute-grid canary.
- Real-lake smoke: SPY finest 2026-05-01→07-02 — 43/43 covered sessions
  at the minute grid, 43 fills all ivol_5min NBBO, exits 38 profit_target
  + 5 session_flat, ZERO settlements ("sell winners, don't settle"),
  23.5s, RSS Δ+22MB flat across re-runs, deterministic re-run identical.
Deliberately NOT here (owner-confirmed split): armed entries (FX.2),
latched stops / worse-path (FX.3), verdict disclosure + mixed-resolution
gauntlet (FX.4), parser vocabulary (FX.5 re-ACCEPT).
REVIEW (independent agent, same session): 1 BLOCKER + 2 MAJOR + 5 lesser,
ALL FIXED — (1) BLOCKER: the minute grid sampled indicators from bars_1m
at %5 minutes, a DIFFERENT artifact with different session bounds than the
5-min underlying record (82 rows to 16:15) → minute slices now merge the
5-MIN frame (wins at stamps, carries ALL indicator samples + VWAP volume,
16:00+ tail included) with price-only bars_1m rows between; engine samples
by stamp membership; REAL-LAKE PARITY PROVEN: finest ≡ fixed-5min exactly
(equity+fills+sources) on SPY 2026-05→07; (2) strategy-spec.schema.json
gained v4 + backtest.resolution (+ parity test); (3) minute und frames now
disk-cached beside the 5-min caches (finest gauntlet was ~1,000 R2 round
trips); (4) bars_1m volume dropped entirely (its diff-after-filter hazard
gone — 5-min frame is the only VWAP source); (5) minute eligibility no
longer frozen per-process (map TTL governs; engine snapshots per run);
(6) negatives never cached + compression-extension and store-glue tests
added; (7) results surface now shows the per-session resolution line when
a run carries a mix (guardrail #6; hidden on all existing runs, verified);
(8) seconds-alignment guard. 336 tests green after fixes.

## 2026-07-07 — ENGINE-V4 FX.2: continuous opportunity scanning

spec v4 `entry.intraday_scan every_setup` (absent ≡ once_per_session,
bit-identical; refuses daily clock + scale_in). Owner decisions baked:
episodes not bursts (false→true arms ONE entry; cap-hit consumes, never
queues); re-entry after intraday exits; condition-less strategies cycle on
the position lifecycle (close = re-arm); ARMED orders fill at the next
quoted bar's real NBBO even if the signal faded (one-quoted-bar validity,
gates apply, episode consumed fill-or-skip, both bars in the trade detail,
die at close_at_time/session end). Every skip counted →
RunResult.skip_reasons + payload skipReasons (log stays deduped);
no_quote_this_bar distinct from no_chain_data. Loop refactor keeps the
once_per_session path byte-identical; closed-this-bar detection is
event-based (never O(positions) per bar — OOM guard). Honest disclosure:
scanning edges live at 5-min stamp granularity today (FX.1 indicator
parity); minute-level triggers arrive with FX.3. 347 tests (+11
hand-computed: persistent=1 entry, refire=2, cap consumed, PT re-entry
+53.70, armed fade fill at 3.025 w/ both bars disclosed, armed dies at
flat, unconditional cycle 2.025→PT→1.425, bit-identity, validators,
schema parity).
REVIEW (independent agent, same session): 1 MAJOR + 4 MINOR + 3 NIT, ALL
FIXED — (1) MAJOR: scanning made position count scale with BARS while three
per-bar paths iterated ALL positions ever (incl. pre-existing _check_exits)
→ quadratic on cyclers; introduced the LIVE book (state.live, swept O(open)
per bar; _check_exits/_force_flat/_settle/_unwind/equity/marks all moved) +
MAX_RUN_FILLS=20,000 loud refusal (RunFillCapError, tested via pathological
per-bar cycler); (2) second edge while armed now counted order_in_flight
(one-working-order model disclosed); (3) waiting bars are NOT skips —
no_quote_this_bar counts once per episode only at unfilled death (session
end/flatten); filled armed orders contribute no count; (4) ANY intraday_scan
+ scale_in refused (schema said mutually exclusive); (5) payload/types
comments now truthful about attempt-level vs episode-level counts; (6) armed
no-hunting bound pinned (refusing quote bar consumes the episode, later good
quotes untouched); (7) closed_this_bar gated to its consumer + covered-call
slot note; (8) schema title v3→v4; (9) lifecycle re-arm comment. 351 tests.

## 2026-07-07 — ENGINE-V4 FX.3: latched exits + the intrabar-unknown rule

Survey: most named FX.3 deliverables already existed (PT/stops/theta/
close_at_time/settlement; stop-first priority IS the worse-path tie rule).
What was missing: nothing could TRIGGER at a minute bar (conditions read
the stamp-sampled series). Shipped (all finest-gated; fixed-5min
bit-identical by construction — the live print at a stamp equals the
sampled value, print-less bars fall back):
- Live-price condition side (owner: entries AND exits, one semantic —
  asymmetric visibility would be "lookahead-flavored"): price-vs-SMA/EMA/
  VWAP compare the current bar's real print against the stamp-sampled
  indicator (_live_price_tail; series cadence untouched).
- Latched exits (owner: directional honesty — "a forgotten exit is
  optimism"; entries/exits have opposite risk polarity so the right
  consistency is same honesty-direction, OPPOSITE validity mechanics): a
  condition-exit trigger observed at an unfillable bar latches on the
  Position (exit_latched/latched_bar), completes at the first fillable
  quote, no re-evaluation, no expiry, "triggered HH:MM" disclosed.
- 0DTE default unchanged (owner: force-flat stays opt-in vocabulary;
  parser SUGGESTS at FX.5 — ask, never default, guardrail #3).
- Fixtures (hand-computed): touch at 09:41 latches → fills 09:45 at the
  real quote (−106.30 pinned, fade-proof); latch survives an unfillable
  stamp (no expiry); the SAME touch invisible at 5-min (blind spot
  pinned); FX.2+FX.3 end-to-end (minute dip arms entry, fills at next
  quote, "armed 09:41"); live-price VWAP/SMA units. 360 tests.
REVIEW (independent agent, same session): 1 MAJOR + 2 MINOR + 2 NIT, ALL
FIXED — (1) MAJOR: crosses operators at off-stamp bars paired (pct@S-1,
live), dropping the latest stamp → genuine inter-stamp crosses MISSED (the
exact forgotten-exit class) + resolved crosses re-fired on minute jitter;
fixed via is_indicator_stamp plumbed through BarView/protocol and
_live_price_tail building (latest sampled, live) pairs only at off-stamp
printed bars — bit-identity now trivial (stamp bars take the untouched
pre-FX.3 path); crosses pinned both directions; (2) latch completion at
gap-session EOD documented + trigger note DATED on later-session fills
(latched_day); (3) a latch first fillable at the flatten bar closes under
its own reason, not session_flat (pinned); (4) settlement supersession of
a pending latch disclosed on the settle event (pinned); (5) _FakeBar
gained stamp-awareness + crosses coverage. 363 tests green; reviewer
verified bit-identity off finest incl. 20k randomized IEEE trials of the
scalar recompute, guardrails #1/#2, latch lifecycle, FX.2 interplay.

## 2026-07-07 — ENGINE-V4 FX.4: mixed-resolution gauntlet honesty

The gauntlet now understands what FX.1–FX.3 built. Shipped (inert on every
run without a per-session resolution record — 368 pre-existing tests
untouched): `resolution_split` stage (full vs 5-MIN-ONLY vs minute stats
from recorded returns/fills, no re-run; RunResult carries the in-process
per-session map); HARD CAP on the optimistic sign-flip at real-evidence
floors (≥15 sessions both subsets + ≥MIN_TRADES in the 5-min sub-window —
owner: "cap hard WHEN it fires, only fire when the evidence is thick
enough"; resolution flip = data-VALIDITY finding vs OOS flip = robustness
signal); walk-forward folds carry minute_share (disclosure in the RUN:
caveats name resolution-flavored folds, tooltips show the share; deeper
fold redesign flagged as its own future pass per masterplan 4b);
grounded verdict caveats quant+retail (numbers validated against the
report); receipts name differing mixes as RESOLUTION UPGRADES (4c);
payload additive resolutionSplit; ReceiptBanner upgrade line. 381 tests
(+13 hand-computed: bucket math to the cent, flip cap, floors disarm,
optimistic-direction-only, inert paths, fold shares, caveat grounding,
receipt annotation).
REVIEW (independent agent, same session): 1 BLOCKER + 1 MAJOR + 4 MINOR +
3 NIT, ALL FIXED — (1) BLOCKER: the receipt "resolution upgrade" note
would have fired FALSELY on every production receipt (daily parents carry
no mix; replays always carry five_min → {} != mix always true) while the
genuine case was unreachable; now requires BOTH runs to carry mixes
(silent on ordinary receipts, pinned with production-shape test) +
resolutionMix rides the stats bundle so future finest-parent comparisons
can fire; (2) MAJOR: a resolution-cap-only refusal fell to the sample
headline ("too few trades" on a 900-trade run — a false statement from
the honesty floor); both template voices gained a headline arm naming the
granularity artifact (pinned); (3) None-sharpe format guard in the cap
reason; (4) first-session gap day counted in eod_fallback; (5) trade-
attribution comment rewritten honestly (boundary-straddler pl-vs-sharpe
divergence + HONESTY.md note); (6) retail caveat no longer asserts
temporal ordering the data doesn't guarantee; (7) dead-assertion
precedence fixed; (8) sub-half-percent fold shares no longer render "0%
minute"; (9) retail voice gained the fold caveat. Stats-bundle contract
test updated (+resolutionMix, quotable by grounded Q&A). 383 tests.

## 2026-07-07 — ENGINE-V4 FX.5: the parser unlock (owner re-ACCEPT gate)

spec v4 vocabulary is now parseable end-to-end. Shipped:
- parse.py: intraday_scan mapping (explicit continuous-scanning phrasing
  only; condition-less cycling is COMPLETE as written — the lifecycle is
  the setup, never ask what defines one; cadence is never a required
  question); resolution "finest" from EXPLICIT phrasing only (owner: a
  data policy is never inferred from strategy shape — reproducibility
  over helpfulness); the exit-less 0DTE seller asks OFFERING force-flat/
  PT/settlement (suggest never default); entry time never required;
  _required_spec_version → 4.
- Eval 22 → 28 cases (+ every_setup, once-contrast, finest, exit-less-
  0DTE ask, golden archetype, condition-less cycling); grader checks
  intraday_scan/resolution incl. fabrication; hermetic v4 recompute
  tests. LIVE EVAL (deepseek-v4-pro): first run REJECTED 14/19 (prompt
  under-specified condition-less cycling; over-asked entry timing) →
  prompt tightened → ACCEPTED 18/19 clear + 9/9 ambiguous (1 miss =
  case 25 asked cadence once, nondeterminism within the 1-miss bar).
  "dips below" as crosses_below accepted as correct semantics (case 23
  expectation broadened).
- FRONTEND 0DTE UNLOCK: the dial path's throw + run-block + warn banner
  ("refused until the minute engine milestone" — SHIPPED in FX.1-4) are
  lifted: DTE 0 emits an intraday spec (clock 5min, DTE band 0-2);
  informative note replaces the refusal. Spec screen gains SCANNING and
  RESOLUTION dials (intraday only).
- ROUND-TRIP FIX (closes the D5d follow-up — "negligence-by-adjacency"
  to leave it): draftToSpec(draft, base) preserves parser-only vocabulary
  through dial edits (scale_in + conditions[], intraday_scan, resolution,
  close_at_time, time_of_day, clock); exit label grammar learned
  "flat HH:MM"; client mirrors the server version computation (server
  stays authority); spec_to_draft surfaces intradayScan/resolution +
  force-flat in the exit display.
GATE: PR held OPEN for owner re-ACCEPT; owner swaps in verbatim personal
0DTE prompts as golden cases at the gate.
REVIEW (independent agent, same session): 1 BLOCKER + 3 MAJOR + 4 MINOR +
NITs, ALL FIXED — (1) BLOCKER: the new dials' OFF state (null) couldn't
override the parsed base through ?? — flipping SCANNING to "once/session"
would have silently RUN every_setup while the confirmed screen showed the
opposite (the PR's own corruption class); fixed with undefined-vs-null
semantics; (2) prompt self-contradiction on the cadence ask (explained
the case-25 nondeterministic miss) — scoped: intraday reads a session
cycle without asking, daily still asks (pinned, new case 29); (3) exit
edits no longer silently re-attach close_at_time — it is LABEL-OWNED
("flat 15:45" round-trips; replacement removes it visibly); label-
inexpressible exit fields (delta stops/theta/exit conditions) pass
through from base, matching verbatim runs; (4) preservation coverage
honestly widened: ALL entry conditions survive an unedited trigger
(multi-condition + timeframes — the case-16 RSI+VWAP flagship no longer
loses its VWAP filter to a window pick), edited triggers keep timeframe,
max_concurrent/max_vega pass through, comment/HONESTY claims corrected;
(5) long-tenor "every time" hijack closed (pinned, new case 30);
(6) stale 0DTE-refusal copy scrubbed; (7) 0DTE window estimates price the
5-min clock; (8) 0DTE band aligned {0,0,1} both ingresses. FINAL LIVE
EVAL (30 cases): 20/20 clear + 10/10 ambiguous — ACCEPTED, perfect score
(the scoping resolved the case-25 nondeterminism). PR held for the owner
re-ACCEPT + verbatim golden swap.

## F4 (ENGINE-V4) — vol-surface signals: 25Δ skew + 30v90 term slope (2026-07-07)

WHAT: spec v5 — two IVS-derived indicators usable as entry/exit condition
filters at any clock: skew_25d (IV 25Δput − 25Δcall @30d tenor, VOL
POINTS, linear delta interpolation between bracketing grid rows) and
term_structure_slope (ATM IV 90d − 30d from exact OTM%=0 rows). Owner
decisions: FIXED market-standard tenors (no parameterization on spec —
exotic tenors later as explicit named vocabulary if a real strategy needs
them); iv_surface_point DEFERRED to its own design pass (disclosed, not
dropped); "variance risk premium" phrasing is a parser ALIAS onto
hv_iv_spread_30d — one implementation per formula, duplicates drift.
HOW: derive-once artifact reference/derived/ivs_signals/ticker={T}.parquet
built nightly by collector/derive_ivs_signals.py (incremental watermark
state/ivs_signals_derive.json; the MATH is imported from
app/data/ivs_signals.py — single source, fixture-tested; new surface
sessions flow in with no redeploy). Engine: MarketStore/MarketView
bisect accessors (PIT ≤ as_of), BarView reads the PREVIOUS session at
intraday bars (EOD-fit rule), conditions compare vol points DIRECTLY
(never re-×100 — pinned against the ivx_level ×100 convention).
Fail-closed derivation: missing tenor/bracket → None per signal, never
extrapolated or cross-tenor. spec_version 5 gating both ways (v5 vocab
on v4 spec is loud; probe bumped to 6), JSON schema updated, TS
computeSpecVersion mirror. Parser: explicit skew/term phrasing, vague
"skew is steep" asks for the threshold, unsupported tenors/deltas ask,
VRP alias pinned NOT to lift the version. Coverage + Observatory: per-
ticker derived window with per-signal session counts (a session can
carry skew and honestly lack term). Eval: cases 31-35.
TESTS: 31 new (415 total green) — hand-computed interpolation (6.0 vol
points exact; 1/3-weight rounding 5.3333), exact-node, both unbracketed
directions, cross-tenor refusal, ATM non-pollution, per-signal absence,
loader NaN handling, PIT boundedness both accessors, BarView prev-day,
×100-bug canary, unavailable-is-False, v5 gating + schema parity,
version detection incl. alias, condition-gated e2e (entry fires only on
the qualifying session).
LIVE EVAL (35 cases): 23/23 clear + 12/12 ambiguous — ACCEPTED, perfect
score. Case 34 asks the exact threshold question ("What threshold defines
'really steep' for the 25-delta skew?"); case 35 refuses the 10Δ/60d remap
and offers the supported signals; case 33 pins the VRP alias at v2.
REVIEW (independent agent, clean worktree of the commit): 0 BLOCKER +
3 MAJOR + 3 MINOR + 3 NIT, must-fixes ALL FIXED — (1) MAJOR: the
collector watermark advanced past never-derived sessions (transient R2
read failure = permanent hole; drip-backfilled OLD sessions below the
watermark never derived) → REDESIGNED to set-difference incrementality:
each run derives exactly the listed sessions absent from the artifact,
no state file at all; unreadable sessions write no row and retry next
night, loudly logged (holes heal by construction — self-improvement
thesis); (2) MAJOR: the v5 gate scanned only entry/exit conditions — a
v3 LADDER smuggled skew_25d rungs/rearm past all three mirrors
(spec.py validator, parser _required_spec_version, spec.ts) → all three
fold in scale_in.rungs + rearm, pinned both ways (loud at v3, valid at
v5, parser returns 5); (3) MAJOR: derive_signal_row trusted vendor
dtypes — a string-typed surface would derive an all-None row silently →
pd.to_numeric coercion (same rule as load_ivs_surface) + unrecognized-
shape early return, pinned (string-typed fixture derives identically);
(4) chains.py loads the F4 series in its OWN try/except (a corrupt skew
artifact can no longer zero IVX/HV for a v2 strategy); (5) Observatory
panel gates on any-ticker, not SPY-only (guardrail #6 mid-backfill);
(6) gated-e2e docstring states the real carry-forward semantics;
(7) schema title bumped v5; (8) jsonschema added to dev deps — the four
schema-parity tests (incl. v5) now RUN in CI instead of skipping;
(9) stable interpolation sort comment. Real-lake acceptance: SPY derived
4,905/4,905 sessions with skew present on every one.
OWNER GATE FULFILLED (2026-07-07, same session): golden cases 27/28/36
are now the owner's VERBATIM prompts (typos preserved — the set protects
real phrasing, not tidy archetypes). 27 = the 0DTE put seller (cycling +
finest + flat 15:45 + stop 100% of credit) → parses to spec exactly.
28 = the 1DTE QQQ cycler; "No holding overnight" has no stated time, so
the case is kind spec_or_questions (owner-blessed dual outcome): a spec
with any end-of-session close_at_time passes, and so does asking the
exact-time question — dropping the constraint silently fails. Grader
gained the dual kind + close_at_time list matching. 36 = the personal
RSI scale-in ladder as typed ("by 10 more") → must ask (unstated ruin
cap). First run on the swapped set exposed a REAL pre-existing flake:
case 29 (daily, no tenor, no cadence) fabricated frequency "daily" +
the 45-DTE convention ~1-in-5 runs — the ONE ALLOWED CONVENTION was
over-applying to bare profit targets. Prompt tightened: the convention
applies ONLY when a DTE number appears in the exit itself; cadence rule
gained the case-29 worked negative example. FINAL EVAL (36 cases):
23/23 clear + 13/13 ambiguous ACCEPTED; case-29 probe 5/5 asks (was
4/5). All three verbatim goldens pass.

## F1 (ENGINE-V4) — dealer positioning: GEX/DEX sign + rank (2026-07-07)

WHAT: spec v6 — four UW dealer-positioning indicators as condition
filters: gex_level / dex_level (net gamma / net delta, vendor sign
convention; the sign IS the regime — dealer_gamma_regime is parser sugar
for gex_level > 0, never a duplicate indicator) and gex_rank_1y /
dex_rank_1y (trailing-252 percentile with the D1 ivx_rank ≥126-obs
floor, owner amendment — rank unlocks as UW data accrues). OWNER
DECISIONS: (1) pre-run REFUSAL when a conditioned run's window starts
before the signal's first covered session (prevention beats correction —
no corrupted long-window artifact is ever produced; covered window
offered back; bound surfaced on the composer's window tile);
(2) daily-first semantics — intraday spot_exposures GEX is its own later
chunk (stale-but-true beats fresh-but-leaky); (3) sign + rank vocabulary
only — raw vendor-unit thresholds refused by the parser (opaque units, a
silent upstream rescale would corrupt every threshold spec).
gex_flip_distance DEFERRED after live probing: the 50-strike EOD
snapshot derives no transition on ~35% of sessions and wing-noise
transitions produce absurd values (SPY −69%) — disclosed, not dropped
(F4 iv_surface_point precedent).
HOW: app/data/gex_signals.py loader (coercion, dedupe keep=last,
per-signal NaN skip) reads the nightly-banked reference/uw/
greek_exposure series — NO new collector job (the series is already one
row per session; self-improvement wiring is the existing UW collector).
MarketStore/MarketView/BarView + protocol; conditions dispatch;
check_signal_coverage in run_engine at BOTH clocks (SliceCoverageError,
plain reason); spec v6 gating incl. ladder rungs/rearm; schema + TS
mirror (v6 before v5, max wins); estimate signal_windows block +
window-tile bound note; coverage + Observatory dealer-positioning lane.
TESTS: 28 new (452 total green) — loader coercion/dedupe/missing-columns,
PIT boundedness + history bounding, BarView prev-day, sign semantics
both directions (real SPY +283K / QQQ −124K magnitudes as fixtures),
rank floor boundary 125/126 + rising-series rank-100 hand fixture,
unavailable-is-False all four, v6 gating (incl. v6 rung on v3 ladder),
schema parity, refusal suite (window-before-signal refused with the
covered window; default-full-window refused; covered window runs AND
gates on the qualifying session only; unconditioned spec untouched;
no-data-at-all refused plainly).
LIVE EVAL (40 cases): 26/26 clear + 14/14 ambiguous — ACCEPTED, perfect
score; case 39's raw-unit refusal asks "How would you like to define the
GEX condition?" offering sign/rank.
REVIEW (independent agent, clean worktree): 0 BLOCKER + 1 MAJOR +
5 MINOR + 4 NIT — (1) MAJOR FIXED: a window lying ENTIRELY before the
signal offered an inverted, impossible window ("Run 2025-07-08 →
2024-01-08") — now offers the real covered window with an
"entirely before coverage begins" reason, pinned; (2) FIXED: rank-
condition refusals + the composer note now name the RANK-UNLOCK date
(first + 126 observations) — the offered window must not hide six
structurally unevaluable months, pinned; (3) FIXED: the trailing-
percentile formula existed in FOUR inline copies (iv_percentile, ivx,
gex, dex) — extracted to _trailing_rank(history, min_obs), byte-
identical (floors 20/126 as args), battery green; (4) FIXED: a cold
5-min /estimate no longer blocks on the full daily store build — reads
the single small greek_exposure parquet directly; (5) schema title
bumped v6; (6) FIXED: the 5-min-clock refusal was untested — pinned
with an intraday fixture; (7) window-tile note documented as
DELIBERATELY partial (trigger-dial only; run-time refusal always
guards) + shows the rank-unlock date; (8,9,10) NITs: Observatory
session counts mirror the ivs pattern (noted), BarView test pins
delegation (named), refusal params renamed win_start/win_end.

## F5 (ENGINE-V4) — fill realism: displayed-depth disclosure (2026-07-07)

WHAT: every option-leg fill now compares its quantity against the
traded side's displayed NBBO size from the iVol 5-min record
(bid_size/ask_size, in the lake since 2013, unused until now). OWNER
DECISIONS: disclose first, model later (prices untouched — a price-
impact model must be EARNED via D3d calibration; a hard gate fails the
FX.2 pessimism test); intraday slip unchanged (no volume-proxy scaling
without calibration evidence); Massive cross-check DEFERRED to F7
(CORRECTED same day: the first survey probed the wrong prefix — the
free-tier collector HAS banked QQQ/IWM contract universes + ~5.7K QQQ
aggs under reference/massive/, stalled at ~3.6% by the 5 req/min rate:
~34 days for the full 244K-contract universe; ramp is an ops decision).
HOW: Quote gains bid_size/ask_size (None on EOD rows — +2 slots/quote,
~+70MB on a full store, within the 8GB budget); intraday slice
plumbing + CACHE_SCHEMA_VERSION 4→5 (spread stats
untouched — the #62 lesson; lazy per-session rebuild; NOTE the version
is shared with the FX.1 1-minute underlying frame cache, so those
frames also rebuild once — wasteful but safe, disclosed); _record_leg_fill
gains (action, qty) → counts fills_depth_known/fills_beyond_depth and
returns the trade-log note; all three fill sites (entry legs by side,
ladder adds, quote-priced closes by close-side) thread notes into
OPEN/ADD/CLOSE details; settlements honestly carry nothing;
LiquidityProfile + payload gain depth_known_share/beyond_depth_share
with a caveat note on ANY exceedance; results-view liquidity line shows
the beyond-depth share. NO parser change — no eval gate this chunk.
TESTS: 10 new (465 total green) — within/exact-boundary/beyond
counting, traded-side correctness (thin ask doesn't flag a short
entry; the PT buyback against ask_size 3 does), missing sizes stay
unknown, prices identical thin-vs-deep (the disclosure-only pin),
daily clock carries zero depth (digest guarantee), profile shares +
note, unknown→None not zero.
REVIEW (independent agent, clean worktree): 0 BLOCKER + 1 MAJOR +
2 MINOR + 4 NIT — (1) MAJOR FIXED: the disclosure note's raw counts
("15 of 228") existed only inside a STRING — the grounding harvester
would falsely reject any verdict/Q&A echoing the disclosure's own
numbers past the counting allowance (the WF-fold latent class) →
fills_depth_known/fills_beyond_depth are now numeric LiquidityProfile
fields, pinned (harvest-set test); (2) FIXED: opening-bar rung fills
fold into the basket OPEN event, so their depth notes now travel back
from _fire_rungs — a beyond-depth FIRST rung is named, not just
counted; (3) FIXED: disclosed that CACHE_SCHEMA_VERSION is shared with
the FX.1 1-minute und-frame cache (those rebuild once too — wasteful
but safe); (4) frontend never renders a confusing "0%" (shows "<1%"),
names the denominator, tooltip explains the semantics; (5) action/qty
are now REQUIRED params (no silent depth-unknown default) and the
entry site reuses fills.open_action; (6) negative vendor sizes clamp
to None (garbage would count as depth-known with an automatic
exceedance); (7) denominator named on the surface. Real-lake
acceptance: June→July 0DTE cycling seller, 10 contracts — 249 leg
fills, 228 depth-known (92%), 15 beyond displayed depth (6.6%), trade
log naming e.g. "qty 10 > ask size 1" on a buyback vs displayed 1.
MASSIVE CORRECTION + DECISION (same session): the survey's "zero data"
was a wrong-prefix probe — reference/massive/ holds QQQ/IWM contract
universes (157,310/86,696) + 5,702 QQQ aggs, stalled at 3.6% by the
free tier's 5 req/min (~33 days for the census). Owner chose the FREE
pruned crawl ($0): prioritize ATM-at-expiry contracts overlapping the
iVol short-DTE slice, resumable, census continues behind it. (Paid
alternative was one Options Starter month, verified $29/mo unlimited
calls — declined.)
DELTA REVIEW (independent agent, clean worktree of the PR head — the
two commits added AFTER the first review: 59ea097 review-fixes +
1a0d0e1 pruned crawl; run because the every-PR-reviewed rule covers
commits pushed post-review): MERGE-READY, 0 BLOCKER + 0 MAJOR +
2 MINOR + 4 NIT. Verified: the grounding fix is real (harvest traced),
_fire_rungs' new return breaks no caller, the crawl permutation drops
NOTHING (exact partition), resumability + periodic flush intact, zero
new API calls, deterministic ordering, ±$8 band == the iVol slice
constant, docs math checks. Fixed on the spot: (1) MINOR — one
malformed date row in the underlying parquet crashed the prioritizer
(NaT is not None) instead of falling back to census order →
pd.notna(day); (2) MINOR — "aggregates complete" logged after every
phase SEGMENT (a 4%-done band read as done on a ~34-day crawl) → the
segment log now states banked/total; (3) NIT — the band ETA quoted the
default rate even when --rate overrode it; (4,5) race-artifact dedup:
the duplicated grounding test and the duplicated review paragraph
removed. Deferred with a note: exhausted-retry contracts are appended
to aggs_done and never retried (pre-existing; costlier now that the
ATM band goes first — F7 follow-up).

## F2/F3 (ENGINE-V4) — flow, sentiment & pin structure (2026-07-08)

WHAT: spec v7 — five UW flow/pin indicators (net_premium_level+rank,
market_tide_level+rank [MARKET-WIDE — the first market-scope series],
nope_level+rank, put_call_flow_ratio, max_pain_distance_pct); THREE
masterplan indicators REFUSED on input quality (owner decision:
oi_change_signal = top-50 vendor curation; oi_concentration/pin_risk =
invented conventions; max_pain_distance carries the pin thesis).
NOPE = sign/rank only (vendor implementation ≠ published concept;
monotone-rescale invariance). Max pain = FRONT expiry fixed ("the
convention is the concept"). Reduction semantics PROBED and pinned:
net_prem_ticks rows are per-minute BUCKETS (sum), market_tide is
CUMULATIVE (last row — median row-diff 6.9M << median |row| 361M).
HOW: derive-once nightly artifacts (F4 set-difference pattern):
reference/derived/flow_signals/ticker={T}.parquet + market-wide
market_tide_signals.parquet; math single-sourced in
app/data/flow_signals.py; collector/derive_flow_signals.py +
collect-eod step + CI smoke + Makefile. Store/views/BarView (5 series
incl. market-wide), _trailing_rank(126) reused, refusal registry +
rank-unlock naming reused, spec v7 gating incl. ladders, schema + TS
mirror (v7 before v6), estimate options_flow bound + window-tile note,
coverage + Observatory lane (per-signal counts + market-wide tide).
TESTS: 26 new (497 total in the clean worktree; local counts include
another session's files) — hand-computed reductions (80.0 / 0.6 /
last-stamp NOPE / −0.2390% FORWARD front max pain; expired AND
same-day rows excluded),
cumulative-last-row-wins, per-signal absence, zero-volume never
divides, sign+raw semantics, rank floor 125/126, BarView prev-day,
unavailable×8, v7 gating + schema parity, refusal + covered-window
gating e2e (entries on exactly the tide-positive sessions).
REVIEW (independent agent, clean worktree): 0 BLOCKER + 2 MAJOR +
5 MINOR + 4 NIT — (1) MAJOR FIXED: all-NaN premium/volume columns
fabricated 0.0 through pandas' default sum (min_count=0) — "put/call
ratio below 0.8" would evaluate TRUE on missing data and the zeros
would enter rank histories permanently → .sum(min_count=1) ×4, pinned;
(2) MAJOR FIXED: the eval grader could not match TWO expected
conditions on the same indicator (first-indicator-match returned a
false operator error) — case 43's "within 1% of max pain" pair was
unpassable by a PERFECT parse → _match_condition now consumes matched
candidates, verified in both emission orders; (3) FIXED: collector
TOCTOU double-read could truncate the artifact for a day → single
read threaded through; (5→OWNER DECISION 2026-07-08): front expiry is
now STRICTLY AFTER the session — with daily expirations "≥" referenced
the expiry settling that day (retrospective at the stamp, a ghost at
consumption; forward-by-CALENDAR is PIT-clean, forward-into-DATA stays
forbidden); artifacts re-derived, fixture updated; (8) duplicate-stamp
ties now last-wins (vendor corrections beat stale rows); (9) flow/tide
guards split (chains + estimate); (10) market-wide refusal phrasing
drops the ticker; (7) driving-family + partial-NaN caveats disclosed
in HONESTY.md; (11) test counts corrected. Real-lake acceptance:
91/91 sessions all five signals (bullish-flow 40/91, risk-on tide
51/91); 2024-window refusal fires naming market-wide tide; tide-gated
short put makes 18 gated fills over the covered window.
LIVE EVAL (45 cases, post-grader-fix): 29/29 clear + 16/16 ambiguous —
ACCEPTED, perfect score. Run 1 confirmed the review's grader prediction
exactly (case 43 failed on the pair-matching bug while the PARSE was
correct); run 2 passed 43 but hit an upstream OpenRouter network flake
(case 27) + pre-existing D5d ladder nondeterminism (case 20); run 3
clean. Cases 44/45 refuse raw NOPE and dollar thresholds offering
sign/rank; case 42 pairs the market-wide tide with a raw put/call
ratio; case 43's "within 1% of max pain" compiles to the ANDed pair.

## F7 (ENGINE-V4) — cross-source validation & data confidence (2026-07-08)

WHAT: the honesty layer's sharpest expression — independent vendors
compared nightly wherever they overlap, per-run data confidence, and an
on-demand fill audit. SURVEY REALITY: no two EOD chain sources share a
single session (iVol/AV chains never banked — tariff/undecided; Yahoo
5 sessions; DoltHub 1,115 with none of the others), and DoltHub's
≥11-DTE floor is contract-disjoint from the iVol short-DTE slice — so
F7 v1 was built on the pairs that EXIST: dolthub_vs_alpaca (527
sessions, productionizing the proven one-off methodology),
dolthub_vs_uw (85), yahoo_vs_ivol5m (5, grows nightly),
massive_vs_ivol5m (activates as the ATM-band crawl lands). OWNER
DECISIONS: per-pair rates + audited-share denominators, NO blended
score ("a confidence score whose own confidence is unfounded");
REPORTED-only v1 (thresholds EARNED from the accumulated distribution,
D3d staging — the FX.4 cap had a binary trigger + borrowable floor,
nothing comparable exists here); fill audit ON-DEMAND (two-tier:
ambient checks cheap and automatic, deep audit human-triggered —
"detection automatic, spend on demand").
HOW: app/data/cross_validation.py comparators (single-sourced,
fixture-tested) + collector/derive_cross_validation.py (set-difference;
Massive pair works per SYMBOL with counts accumulating) + nightly step/
CI/Make; stages.data_confidence aggregates the run's own window
(numeric fields — grounding-safe; no-overlap → None, never a fabricated
100%); verdict caveat; payload dataConfidence; results-view line;
Observatory pair lanes; RunResult.fill_log (structured per-leg fills at
all three sites, pid-joined to trade-log bar times);
app/data/fill_audit.py pure core + POST /runs/{id}/audit (re-runs the
spec deterministically under the engine lock, audits vs Alpaca minute
trades, stores audit_json like receipts — verdict never rewritten) +
frontend audit button/line. NO parser change — no eval gate.
TESTS: 17 new (514 total) — comparator hand-fixtures (stale-print
excluded-not-flagged; band edges both sides; expiry-total bands 5%/10%;
day-range checks), confidence window-scoping + honest-absence, fill_log
recording (sell entry + PT buyback), audit within/outside/no_trades/
no_coverage/session-range degradation.
REVIEW (independent agent, clean worktree): 2 BLOCKER + 4 MAJOR +
6 MINOR + 3 NIT — ALL must-fixes FIXED: (1) BLOCKER: the audit re-run
with end=None extended to TODAY'S lake — a June run audited in July
would attribute independent verification to fills it never made → the
re-run pins to the stored honesty report's effective window AND refuses
on fill-count mismatch ("the lake has changed since this run");
(2) BLOCKER: alpaca_modeled fills were audited against the prints they
were PRICED from — self-confirmation counted as independent
verification → excluded, disclosed self_source bucket, pinned;
(3) MAJOR: NaN agreement_rate from checked=0 sessions would 500 the
entire run page (allow_nan=False) → NaN-guarded; (4) MAJOR: CLOSE/ADD
fills were audited in a window around the OPEN's bar — fabricated
disagreement on the flagship 0DTE path → the engine stamps each
fill_log row with ITS OWN bar time (pinned: a 14:10 close audits near
14:10); (5) MAJOR: compare_dolthub_alpaca had no column guards — one
malformed session would brick the nightly derive forever, and NaN
deltas fabricated violations → guarded + excluded; (6) MAJOR: the
audit loaded stores OUTSIDE the engine lock with no memory release —
the exact OOM concurrency class the incident fix serialized → loads
inside the lock + finally _release_memory. Also: in-flight guard
(repeated POSTs 409 while running, 30-min stale takeover); audit
day-cache bounded at 2 projected frames; failed audits DISPLAYED with
a retry path; empty cross-source lines hidden; joined = inner-join
count per the module contract; artifact-load failures logged;
session-range kind pinned observable; massive double-count crash
window documented.
