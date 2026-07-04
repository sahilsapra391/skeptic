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
