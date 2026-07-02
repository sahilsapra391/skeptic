# Skeptic — Build Plan for Claude Code
*Run one milestone per session, in order. Do not advance past red acceptance
criteria. Each milestone ends with CI green and a short session note appended
to `docs/BUILD-LOG.md`.*

---

## M0 — Scaffold (small session)

**Goal:** monorepo skeleton, CI, tooling.

**Tasks:** layout per CLAUDE.md; backend with uv + FastAPI + `/api/health`;
pydantic models generated from `docs/strategy-spec.schema.json` (+ a test that
round-trips a valid spec and rejects an invalid one); frontend via
`create-next-app` + Tailwind + shadcn/ui + a placeholder shell; `ci.yml`
running ruff/mypy/pytest and lint/typecheck/build.

**Accept:** CI green; `GET /api/health` 200 locally; spec round-trip test passes.

**Paste-ready prompt:** "Read CLAUDE.md, docs/TECH-SPEC.md and
docs/strategy-spec.schema.json. Execute Milestone M0 from docs/BUILD-PLAN.md
exactly: scaffold the monorepo, pydantic models matching the schema with
round-trip tests, FastAPI health route, Next.js shell, and CI. Stop at the
acceptance criteria and show me the test output."

## M1 — Data pipeline live (run this the same day you create the repo)

**Goal:** history accruing nightly with zero further attention.

**Tasks:** productionize `collector/reference/collector_v2.py` into
`collector/collect.py` per DATA-PIPELINE.md (modes eod/backfill/quality; AV
budget wall; frontier state in R2; NYSE calendar; healthcheck pings);
workflows `collect-eod.yml` (+ catch-up cron) with `workflow_dispatch`;
one-time underlying/VIX deep backfill script; a `coverage.py` script printing
ranges/counts/frontier from the lake.

**Accept (matches DATA-PIPELINE.md §8):** manual dispatch green; R2 shows AV +
Yahoo objects for all 3 tickers, underlying to the 1990s, VIX, moving
frontier; healthcheck pinged; coverage script output pasted into BUILD-LOG.

**Prompt:** "Read docs/DATA-PIPELINE.md and the reference collector. Execute
Milestone M1. Secrets are configured in GitHub Actions. After implementing,
walk me through triggering the workflow manually and verifying R2 contents."

## M1.5 — Alpaca minute-bar options lake (data-only session)

**Goal:** the full 2024-02 → present 1-minute option-bar history for
SPY/QQQ/IWM in R2, accruing nightly. Data only — no engine changes, no
bulk quote pulls.

**Tasks:**

- **Step 0 — verify before pulling (needs `APCA_*` keys):** with live
  calls, confirm (a) `GET /v2/options/contracts?status=inactive` reaches
  expiries back to 2024-02 (fallback universe source: ThetaData free-tier
  contract lists); (b) 1-min bars return for a long-expired contract;
  (c) whether historical option *quotes* are servable on the Basic plan
  (entitlement/feed behavior); (d) pull one probe week per ticker and
  measure real bar density → refined volume/runtime/storage estimate.
  Record all four in BUILD-LOG. Owner then picks: full chains (enable R2
  paid class, ~cents/mo) vs filtered lake (DTE ≤ 90, moneyness ±25%) —
  before any bulk request is issued.
- `collector/alpaca.py` + `--mode alpaca-backfill`: month×ticker frontier
  in `state/alpaca_backfill.json`; per-month contract universe from the
  contracts endpoint; bars pulled 100 symbols/request, `limit=10000`,
  `page_token` pagination, paced under the ~200 req/min account budget
  with backoff; parquet per (ticker, trading_date) per DATA-PIPELINE §4b;
  idempotent overwrites; state written after each completed month.
- `--mode alpaca-eod` appended to the nightly workflow: yesterday's option
  bars + underlying minute bars for all 3 tickers.
- Underlying 1-min equity bars backfilled for the same window →
  `underlying_minute/`.
- `coverage.py` extended: minute-lake date range, session count,
  rows/session, missing sessions vs XNYS.

**Accept:** `options_minute/` parquet spans 2024-02 → yesterday for all 3
tickers with per-session bar counts ≥ the probe-week baseline; frontier
state shows every month done; nightly dispatch green and appends the new
session; coverage output pasted into BUILD-LOG; zero bulk quote requests
anywhere in the code path.

**Prompt:** "Read docs/DATA-PIPELINE.md (both DECIDED blocks + §4b) and
docs/INTRADAY-OPTIONS-DATA-EVAL.md. Execute Milestone M1.5. Run step 0
first and show me the four findings and the refined size estimate before
any bulk pull."

## M2 — Backtest engine core (the correctness milestone; go slow)

**Goal:** trustworthy EOD options engine for the 5 v1 structures.

**Tasks:** `MarketView(as_of)` PIT data layer over DuckDB/R2 with the
lookahead canary test; indicator module (trailing-only); engine loop, fill
model, entries, exits, expiration/assignment, accounting, metrics, full trade
log with skip reasons, all per TECH-SPEC §4–5; **hand-computed fixtures**
(tiny synthetic chains, expected P/L written by hand in fixture comments) for:
short put OTM expiry, short put assigned, credit spread stop, iron condor
profit target, covered call called away, skip on zero-bid; a `POST
/api/backtest` + `GET /api/runs/{id}` happy path storing runs in Neon.

**Accept:** all fixtures match to the cent; lookahead canary raises; a real
run against actual R2 data completes < 15 s and produces a sane trade log.

**Prompt:** "Read docs/TECH-SPEC.md §4–5 and CLAUDE.md guardrails. Execute
Milestone M2. Build fixtures FIRST with hand-computed expected values in
comments, then make the engine pass them. Show me the fixture math for the
assigned short put before implementing it."

## M3 — Honesty layer + verdict (the go/no-go milestone, PoC risk R3)

**Goal:** the gauntlet + grounded verdicts, and proof it catches overfitting.

**Tasks:** stages 1–6 per TECH-SPEC §6 with seeded determinism and
`run_events` progress; deterministic trust-level rules; verdict writer +
numeric validator + template fallback per §7; **the overfit fixture**: a
strategy whose parameters were tuned on the full sample (construct it by
in-repo optimization script against synthetic data with no true edge) saved as
`tests/fixtures/overfit_strategy.json`; a required test asserting the gauntlet
assigns it trust ≤ 2 with OOS degradation flagged. Also the
insufficient-evidence cap test (< 30 trades).

**Accept:** overfit fixture flagged (this test failing = build failing,
forever); insufficient-evidence cap works; verdict validator rejects a
deliberately hallucinated number in a test; full gauntlet < 60 s on real data.

**Prompt:** "Read docs/TECH-SPEC.md §6–7. Execute Milestone M3. Build the
deliberately-overfit fixture first and show me its in-sample vs out-of-sample
stats before wiring the verdict writer."

## M4 — NL parser + clarifying loop (PoC risk R1)

**Goal:** English → spec-or-questions, never silent guesses.

**Tasks:** `POST /api/parse` with Anthropic structured output emitting the
discriminated union; multi-turn `answers` convergence; hard rule: missing
exit rules, strike selection, or underlying ⇒ questions (schema's exit
minProperties helps but the parser must ask, not fabricate); the eval harness
running the 12-case set below with a pass/fail report.

**Eval set (8 clear, 4 ambiguous):**
1. "Sell a 30 delta put on SPY every Monday, close at 50% profit or 21 DTE." → spec
2. "Put credit spread on QQQ, short leg 25 delta, $5 wide, 45 DTE, exit at 50% profit, stop at 2x credit, weekly entries." → spec
3. "Iron condor on IWM, 20 delta wings... shorts at 20 delta, longs $3 wider, 30 DTE, take profit 40%, time exit 10 DTE, enter monthly." → spec
4. "Covered call on SPY, sell the 30 DTE call at 20 delta, roll... exit at expiration, enter monthly." → spec
5. "Buy a 60 DTE ATM call on QQQ when RSI(14) < 30, sell at 100% gain or 20 DTE, one at a time." → spec
6. "Short put on SPY at 5% below spot, 30 DTE, profit target 60%, stop 300%, enter every Friday." → spec
7. "When the 9 EMA is below the 20 EMA on SPY, sell a 25 delta call spread $5 wide, 21 DTE, 50% profit target, stop at 150%, daily signal." → spec
8. "Long put on IWM when price is 3% above its 50 SMA, 45 DTE ATM, exit 21 DTE or 75% profit." → spec
9. "Sell puts on SPY when the market dips." → questions (which delta/strike? which DTE? what exit? what counts as a dip?)
10. "Iron condor every week, manage at 21 days." → questions (underlying? deltas/widths? profit/stop?)
11. "Do the wheel on QQQ." → questions (v1 has no wheel structure; offer nearest templates, ask)
12. "Buy calls when it looks oversold, sell when it recovers." → questions (define oversold, define recovers, DTE, strike)

**Accept:** ≥ 7/8 clear cases produce specs matching hand-written ground truth
(stored beside the eval); 4/4 ambiguous cases produce questions with zero
fabricated parameters; spec always echoes `description_raw` verbatim.

**Prompt:** "Read docs/TECH-SPEC.md §2–3 and the M4 eval set in
docs/BUILD-PLAN.md. Execute Milestone M4. Write the ground-truth specs for
the 8 clear cases first, then build the parser to pass the harness."

## M5 — Frontend

**Goal:** the approved designs, wired end to end.

**Tasks:** implement `docs/design/` exports: New Analysis (with data-coverage
line + template chips), Spec Confirmation (Spec Card + one-at-a-time
questions), Results (Verdict Block hero + honesty panels + trade log +
follow-up composer via `/api/runs/{id}/ask`), Library, Data Observatory (from
`/api/data/coverage`), Settings; gauntlet progress from `run_events`; the
insufficient-data verdict state; token-level enforcement of the P/L-vs-trust
color contract; Next.js route-handler proxy carrying the bearer token.

**Accept:** the canonical strategy (eval case 1) flows New Analysis → Spec →
gauntlet progress → Verdict without dev tools open; Observatory shows live
coverage; Lighthouse accessibility ≥ 90 on Results.

**Prompt:** "Read docs/design/ exports and docs/TECH-SPEC.md §8. Execute
Milestone M5, implementing the approved mockups exactly. Start with the
Verdict Block component at three sizes and show it to me before building
pages around it."

## M6 — Deploy + smoke

**Goal:** live URLs, monitored, operating.

**Tasks:** Railway service (Dockerfile, envs, deploy); Vercel project (proxy
envs); production smoke script hitting parse→backtest→verdict against prod;
Healthchecks tiles verified; `docs/RUNBOOK.md` (rotate token, re-run
collector, read quality flags, cost dashboard links).

**Accept:** canonical strategy end-to-end on the production URL from a phone
browser; collector green for 3 consecutive scheduled nights; RUNBOOK exists.

**Prompt:** "Execute Milestone M6 per docs/BUILD-PLAN.md. I'll provide Railway
and Vercel access; walk me through each deploy step and then run the
production smoke test."

---

## Cross-milestone rules

- The overfit-fixture test (M3) and lookahead canary (M2) are permanent
  required CI checks. If either goes red later, everything stops.
- Every milestone appends: date, what shipped, deviations from spec and why,
  to `docs/BUILD-LOG.md`. Deviations from the guardrails in CLAUDE.md require
  the owner's explicit sign-off in the log.
