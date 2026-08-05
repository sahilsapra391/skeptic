# Skeptic — Technical Specification
*Consumer: Claude Code. Companion docs: DATA-PIPELINE.md, BUILD-PLAN.md,
strategy-spec.schema.json. Product context: the PRD and PoC in docs/.*

## 1. System overview

```
                    ┌─────────────────────────────────────────┐
                    │  Collector VM (systemd timers)          │
                    │  collect-eod.sh · intraday recorder     │
                    │  (GH Actions = dispatch-only fallback)  │
                    └──────────────┬──────────────────────────┘
                                   │ Parquet writes
                                   ▼
   Vercel                   Cloudflare R2 (data lake)          Neon Postgres
┌───────────┐  HTTPS   ┌───────────▼───────────┐  SQLAlchemy ┌─────────────┐
│ Next.js   │ ───────► │ FastAPI on Railway    │ ──────────► │ app state:  │
│ frontend  │ ◄─────── │  parser │ engine      │             │ runs, specs,│
└───────────┘   JSON   │  honesty│ verdict     │             │ trial count │
                       │  DuckDB over R2       │             └─────────────┘
                       └───────────┬───────────┘
                                   │ Anthropic API (parser + verdict)
                                   ▼
```

- **Frontend:** Next.js 14, app router, TypeScript, Tailwind, shadcn/ui,
  Recharts for charts. Deployed on Vercel. Pure client of the backend API.
- **Backend:** FastAPI, Python 3.12, deployed on Railway. Owns all math, all
  data access, all LLM calls. Frontend never calls Anthropic directly.
- **Data lake:** Cloudflare R2, S3-compatible, Parquet objects. Queried with
  DuckDB (`httpfs` + S3 credentials) directly from the backend; hot partitions
  cached to local disk on the Railway container for speed.
- **App DB:** Neon Postgres (free tier). Tables: `strategies`, `runs`,
  `run_events`, `trial_counter` (per-strategy-family test count for the
  deflated Sharpe correction), `backfill_state`.
- **LLM:** Anthropic API, structured outputs (tool/JSON schema) for the parser;
  a constrained generation + post-hoc numeric validation for the verdict.
- **DECIDED (owner, July 2026): LLM access is via OpenRouter** —
  `OPENROUTER_API_KEY`, OpenAI-compatible endpoint — not a direct Anthropic
  key. Wherever this or the companion docs say "Anthropic API" /
  `ANTHROPIC_API_KEY`, read OpenRouter. Structured-output and
  numeric-validation requirements are unchanged.

## 2. Strategy IR (the load-bearing contract)

`strategy-spec.schema.json` is the single source of truth. Pydantic models in
`backend/app/models/spec.py` are generated/maintained to match it exactly, and
the frontend imports a TS type generated from the same schema. Any change to
the schema is a versioned migration (`spec_version` field), never silent.

Parser output is a discriminated union:
```json
{ "status": "spec",     "spec": { ... } }
{ "status": "questions","questions": [{"field": "...", "question": "...", "options": [...]}] }
```

## 3. Backend API contract

All routes under `/api`, JSON, authenticated by a single bearer token
(`SKEPTIC_ACCESS_TOKEN`) checked by middleware (single-user v1; see §10).

| Route | Method | Purpose |
|---|---|---|
| `/api/parse` | POST | `{text, answers?}` → spec or clarifying questions. `answers` carries responses to prior questions so the loop converges. |
| `/api/backtest` | POST | `{spec, seed?}` → `{run_id}`. Runs synchronously if estimated < 15 s (EOD single run), else enqueues; response includes `status`. |
| `/api/runs/{id}` | GET | Full run: status, stats, honesty payload, verdict, trade log, equity/drawdown series, data window used. |
| `/api/runs` | GET | Library list with compact trust levels. |
| `/api/runs/{id}/ask` | POST | `{question}` → grounded answer. Implementation: LLM plans a re-slice (predefined analysis functions: filter trades by period/regime, worst-N, recompute subset metrics), backend executes, LLM narrates ONLY the returned numbers. Same numeric validator as verdicts. |
| `/api/sweep` | POST | `{spec, param_path, values[]}` → sweep job → sensitivity matrix. Registers `len(values)` trials in `trial_counter`. |
| `/api/data/coverage` | GET | Per ticker/source: date ranges, snapshot counts, backfill frontier, quality flags, collector last-seen. Powers the Data Observatory. |
| `/api/runs/{id}/notebook` | GET | The completed run as an `.ipynb` download (parity Tier 1). Opens with the provenance story (prompt → Q&A → decision grid → mechanics) as markdown, then API-backed cells: headline stats with their window, equity/drawdown, trade log + skips, fill provenance, F7 per-pair agreement, D5b ladder depth (ladder runs), the honesty gauntlet with the F8 sweep-coverage disclosure baked in, the verdict, and the reproduce loop. Never embeds the token — cells read `SKEPTIC_ACCESS_TOKEN`. |
| `/api/runs/{id}/report` | GET | The completed run as a standalone HTML document (owner ask 2026-07-14: a format anyone can open) — the notebook's story rendered statically from the stored run with inline-SVG equity/drawdown, three-voice typography, print-to-PDF CSS, all user text escaped, disclaimer opening and closing. No live calls; never embeds the token or market-data rows. |
| `/api/runs/{id}/reproduce` | POST/GET | Deterministic re-execution proof: same spec + seed, the ORIGINAL effective window pinned, and the recorded per-session resolution map pinned (a replay never silently re-resolves — D3 receipts semantics). POST kicks a background engine re-run (serialized behind the engine lock, like audits); GET returns the stored report: stored-vs-fresh headline stats within tolerance, resolution divergence (disclosed, never papered over), build then/now. Stored like receipts — the run's verdict is never rewritten. |
| `/api/health` | GET | Liveness + R2/DB/Anthropic reachability. |

Async model: a lightweight in-process job runner (FastAPI BackgroundTasks +
`run_events` rows for stage progress). Frontend polls `/api/runs/{id}` (2 s);
stage list drives the gauntlet progress UI. No Celery/Redis in v1; complexity
not justified at this scale.

## 4. Data access layer (`app/data/`)

- One module owns all DuckDB SQL. Canonical chain view unifies sources:
  `read_parquet('s3://skeptic-data/options/source=*/ticker=?/...')` with
  schema normalization per DATA-PIPELINE.md §4.
- **Source precedence:** for the same (ticker, trading_date), Alpha Vantage
  rows are the EOD record; Yahoo rows are redundancy. The view exposes
  `preferred_chain(ticker, date)` implementing this.
- **Point-in-time enforcement:** the engine can only obtain data through
  `MarketView(as_of_date)` objects whose queries are hard-bounded by
  `as_of_date`. There is no "load full history" call available to strategy
  logic. Test: a canary strategy that tries to peek at T+1 must raise.
- Greeks: Alpha Vantage rows carry delta/gamma/theta/vega/rho + IV. Yahoo rows
  carry IV only; compute missing greeks at ingest with Black-Scholes
  (py_vollib_vectorized), rate from FRED 3M T-bill (fetched weekly, cached),
  and mark `greeks_source='computed'`.
- Underlying daily OHLCV: `underlying/` prefix in R2, backfilled decades deep
  from Stooq/yfinance at M1 time. Indicators (EMA, RSI, SMA, realized vol,
  VIX level/percentile) computed by the engine's indicator module with
  strictly trailing windows.

## 5. Backtest engine (`app/engine/`)

Event-driven daily loop. Do not adopt Backtrader/VectorBT; at EOD granularity
with options-native needs, a purpose-built ~1k-line engine is simpler and
safer than bending an equity framework. Design:

- **Clock:** iterate trading days over the intersection of requested window
  and available options coverage (never silently extend beyond coverage; the
  run result records the effective window).
- **Position model:** `Position` = list of `Leg(contract_id, side, qty)` +
  lifecycle state. Multi-leg structures open/close atomically; per-leg fills
  computed independently then summed.
- **Fill model (guardrail #1):** buy fills at `ask + slip*(ask-bid)` beyond...
  correction: buy fills at `bid + (1 - slip) * spread` is wrong; define
  explicitly: `slip ∈ [0,1]` is the fraction of the half-spread conceded from
  mid toward the adverse side. Defaults are EARNED from the D3d tape
  calibration (233M real prints, 2026-07-13 owner decision): buys 0.85,
  sells 0.90 — side-aware, because seller-aggressor prints measurably
  concede more (p50 ~0.90, 17-26% beyond the displayed bid) than buyers
  (~0.85-0.87, ~3% beyond the ask). Buys: `mid + slip*(ask-mid)`. Sells:
  `mid - slip_sell*(mid-bid)`. `slip=1.0` = full adverse quote, `0`
  forbidden by config validation (mid fills banned; the tape vindicates
  this — 0.1% of real prints fill at mid or better). A single user-stated
  slippage number sets BOTH sides at the parser; asymmetry only ever comes
  from the defaults or an explicit two-value request. Commission per
  contract per side (default $0.65). All configurable in `spec.costs`.
- **Entry evaluation:** each day, if schedule matches and all entry conditions
  (indicator expressions over `MarketView`) are true and
  `open_positions < max_concurrent`, select contracts: expiration nearest
  `target_dte` within `[min,max]`, strikes by delta (nearest |delta| in
  chain), offset %, or ATM. If required contract/quote is missing or bid/ask
  invalid (crossed, zero-bid on shorts), the trade is **skipped and logged
  with the reason** (feeds the trade log's "skipped" section).
- **Marking & exits:** positions marked daily at conservative liquidation
  side. Exit triggers evaluated in priority order: stop loss → profit target
  → time exit (DTE) → condition exits → expiration.
- **Expiration/assignment:** cash-settle at intrinsic using underlying close
  on expiration day. Short ITM options: assignment modeled (shares assigned
  then liquidated next open for equity settlement simplicity, cost applied).
  Early assignment approximation: deep-ITM short calls through ex-div dates
  flagged and assigned (ex-div calendar from yfinance). Document this as an
  approximation in the run's methodology notes.
- **Accounting:** cash ledger + open position marks → daily equity series →
  returns → metrics. Metrics module: CAGR, Sharpe, Sortino, max drawdown,
  win rate, profit factor, exposure %, per-trade stats.
- **Trade log:** every candidate evaluated, opened, adjusted, closed, or
  skipped, with timestamps, prices, greeks at entry, and reason codes.

Testing bar (M2 acceptance): fixtures with tiny hand-built chains where P/L is
computed by hand in the fixture file comments; engine must match to the cent.
Include: short put expiring OTM, short put assigned, credit spread hitting
stop, iron condor profit-target exit, skipped trade on zero-bid.

## 6. Honesty layer (`app/honesty/`) — the product

Runs automatically after every backtest, in stages (each stage emits a
`run_event` for UI progress):

1. **IS/OOS split:** chronological 70/30. Report both metric sets + a
   degradation ratio (OOS Sharpe / IS Sharpe). Flag if OOS Sharpe < 50% of IS
   or sign flips.
2. **Walk-forward:** rolling windows, train 6 mo / test 2 mo, step 2 mo
   (auto-shrink proportionally if total window is short; below 3 folds,
   report "walk-forward not meaningful at this history length" instead of
   fake results). Report per-fold OOS metrics + consistency (% profitable
   folds).
3. **Monte Carlo:** stationary block bootstrap on per-trade P/L (block size
   ~5 trades to respect clustering), 1,000 resamples, seeded. Report terminal
   equity 5/50/95th percentiles, max-drawdown distribution, and probability of
   loss over the window.
4. **Sensitivity sweep:** perturb each numeric spec parameter ±20% in 5 steps
   (delta target, DTE target, profit target, stop). Classify optimum:
   **plateau** if median neighbor Sharpe ≥ 70% of peak, else **cliff**.
   Output the matrix for the heatmap.
5. **Multiple-testing correction:** deflated Sharpe ratio (Bailey &
   López de Prado) using `trial_counter` for this strategy family (family key:
   underlying + structure). Every parse-to-run and every sweep value
   increments trials. Report DSR and the plain-English implication.
6. **Regime & sample guardrail (guardrail #5):** compute VIX regime coverage
   (days in VIX <15 / 15–20 / >20 buckets) and trade count. If trades < 15 or
   only one regime bucket represented, `trust_cap = "insufficient_evidence"`.

Output: a single `HonestyReport` pydantic model; this JSON is the ONLY input
to the verdict writer.

## 7. Verdict writer (`app/verdict/`)

- Input: `HonestyReport` + core metrics. Never user text, never raw data.
- Prompt contract: produce `VerdictJSON {headline, trust_level (1–5 or
  "insufficient_evidence"), survives_oos: bool, evidence[], breaks_where[],
  caveats[]}` with the uncomfortable finding first; every number quoted must
  appear in the input payload.
- **Numeric validator:** extract numeric tokens from the verdict text,
  normalize (%, x, $), assert each exists in the payload within rounding
  tolerance. On failure: one retry with the violation named, then fall back to
  a template-rendered verdict from the raw stats (never ship an ungrounded
  sentence).
- Trust level is COMPUTED by deterministic rules in code (from OOS
  degradation, walk-forward consistency, MC loss probability, plateau/cliff,
  DSR, guardrail cap); the LLM narrates the level, it does not choose it.
  This keeps the core promise auditable.

## 8. Frontend (`frontend/`)

- Screens per the approved Claude Design exports in `docs/design/`: New
  Analysis, Spec Confirmation, Results/Verdict, Library, Data Observatory,
  Settings, plus gauntlet-progress and insufficient-data states.
- State: TanStack Query against the API; no client-side math beyond chart
  shaping. Charts: Recharts (equity + drawdown synced, MC fan as banded area,
  sensitivity heatmap as grid cells).
- Spec Card renders the IR with editable fields (hybrid input, PM-approved);
  edits produce a new spec version and re-confirm before run.
- Verdict Block component built once, used at hero/card/row sizes.
- Enforce the color contract in the theme layer: `--pl-pos/--pl-neg` vs
  `--trust-*` token families; lint rule or review check that trust components
  never import P/L tokens.

## 9. Deployment & environments

- **Frontend:** Vercel, env `NEXT_PUBLIC_API_URL`, `SKEPTIC_ACCESS_TOKEN`
  (server-side route handler proxies auth so the token isn't shipped to the
  browser; simplest: Next.js route handlers proxy `/api/*` to Railway adding
  the bearer header).
- **Backend:** Railway service from `backend/Dockerfile`. Env: `DATABASE_URL`,
  R2 credentials, `ANTHROPIC_API_KEY`, `SKEPTIC_ACCESS_TOKEN`,
  `ALPHAVANTAGE_API_KEY` (for coverage introspection only; collection runs in
  Actions). Persistent volume optional (cache only; safe to lose).
- **Zero-dollar fallback** (documented for completeness; PM approved ~$25/mo,
  so Railway is the plan): backend on
  Hugging Face Spaces / Fly free allowance, or run backend locally and expose
  via Cloudflare Tunnel. Railway remains the default because a research tool
  that's asleep when you have an idea doesn't get used.
- **CI:** GitHub Actions: ruff + mypy + pytest (backend), lint + typecheck +
  build (frontend). The overfit-fixture test is in the required set.

## 10. Auth & sharing (v1 posture)

Single user. One bearer token, rotated by redeploy. No accounts, no signup.
Design leaves room for share-a-run read-only links later (runs table already
has a nullable `share_slug`), but v1 ships without it.

## 11. Observability

- Healthchecks.io pings from both workflows (see DATA-PIPELINE.md).
- Backend: structured JSON logs; `run_events` doubles as an audit trail.
- Weekly data-quality job flags: null-quote rates > 20%, spot deviation
  between sources > 0.5%, missing trading days. Flags surface in
  `/api/data/coverage` → Observatory.

## 12. Performance targets

- Single EOD backtest over full available history: < 5 s p50, < 15 s p95.
- Full gauntlet: < 60 s p95 (MC dominates; vectorize with numpy).
- Parse round-trip: < 6 s.
- If targets are missed, cache chain partitions locally on the container and
  memoize indicator series per (ticker, window) before optimizing further.
