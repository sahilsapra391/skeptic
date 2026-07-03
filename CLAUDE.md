# CLAUDE.md — Skeptic

Standing instructions for every Claude Code session in this repo. Read
`docs/TECH-SPEC.md`, `docs/DATA-PIPELINE.md`, and `docs/BUILD-PLAN.md` before
non-trivial work. The strategy IR contract is `docs/strategy-spec.schema.json`.

## What this project is

An agentic options-research copilot. NL strategy in → validated spec →
EOD options backtest → automatic anti-overfitting gauntlet → grounded
plain-English verdict. Single-user passion project. The honesty layer is the
product; the backtest is table stakes.

## Monorepo layout

```
skeptic/
  frontend/            Next.js 14 (app router, TS, Tailwind, shadcn/ui) → Vercel
  backend/             FastAPI (Python 3.12) → Railway
    app/api/           routes
    app/parser/        NL → spec (Anthropic structured output)
    app/engine/        EOD options backtest engine
    app/honesty/       OOS, walk-forward, Monte Carlo, sensitivity, DSR
    app/verdict/       stats → grounded verdict (LLM + numeric validator)
    app/data/          R2/DuckDB access layer, coverage
    tests/             pytest; engine fixtures are hand-computed
  collector/           pipeline scripts (see docs/DATA-PIPELINE.md)
  .github/workflows/   collect-eod.yml, backfill-drip.yml, ci.yml
  docs/                specs + approved design exports (docs/design/)
```

## Commands

- Backend: `cd backend && uv sync && uv run pytest` · dev: `uv run uvicorn app.main:app --reload`
- Frontend: `cd frontend && npm i && npm run dev` · checks: `npm run lint && npm run typecheck`
- Collector local run: `cd collector && uv run python collect.py --mode eod`
- CI must be green before any milestone is called done.

## Non-negotiable engine guardrails

These encode the product's integrity. Violating them silently is the worst
class of bug in this codebase.

1. **Never fill at mid.** Buys fill toward ask, sells toward bid, plus the
   configured slippage fraction of the spread. Commission always applied.
2. **Point-in-time correctness.** A simulation at date T may read only data
   with snapshot/observation dates ≤ T. No lookahead, ever. Tests must prove it.
3. **No silent parser guesses.** Missing or ambiguous strategy fields produce
   clarifying questions, never defaults for entry/strike/exit parameters.
4. **The verdict is grounded.** Every numeric token in verdict text must exist
   in the stats payload; the numeric validator rejects otherwise. The verdict
   LLM call receives ONLY computed stats, never raw user text.
5. **Thin samples are never blessed.** Below minimum trades (15) or single
   volatility regime, trust level is capped at "insufficient evidence" no
   matter how good the numbers look.
6. **Data coverage is honest.** Any surface that shows results also shows the
   data window they were computed on.

## Engineering conventions

- Python: uv, ruff, mypy (strict on `engine/` and `honesty/`), pydantic v2
  models generated to match `strategy-spec.schema.json` exactly.
- Every honesty-layer statistic ships with a unit test against a
  hand-computed fixture. The deliberately-overfit fixture in
  `backend/tests/fixtures/overfit_strategy.json` must ALWAYS be flagged by
  the gauntlet; treat a green run on it as a failing build.
- Determinism: all stochastic steps (Monte Carlo) take a seed, logged with
  the run. Same spec + same data + same seed = identical output.
- Frontend implements the approved mockups in `docs/design/`; do not restyle
  by taste. P/L colors never appear on verdict components and vice versa.
- Secrets only via env vars; never commit keys, never log chain data rows.

## Legal / ethical rails

Personal-use research tool. Collected market data is never redistributed or
exposed via public endpoints. Every results surface carries the disclaimer:
not financial advice; backtests overstate live performance. The app never
emits buy/sell recommendations for live trading.
