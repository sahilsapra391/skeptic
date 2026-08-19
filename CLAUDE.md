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

- Backend: `uv run --project backend python -m pytest backend/tests` (runs
  from the repo root; `uv sync --project backend` first on a cold checkout).
  Start the dev server through `.claude/launch.json`, never by hand: the pinned
  entry is the only form that guarantees a local database (see below).
  - Use `python -m pytest`, not bare `uv run pytest`. The `pytest` shim on this
    machine resolves a 3.13 environment while the project pins 3.12, so the
    bare form fails the whole suite with `ModuleNotFoundError: fastapi`. CI
    builds fresh and is unaffected. A documented command that errors gets
    worked around by running a subset, which in a repo this guardrail-heavy is
    how a green-looking partial run happens (V-83).
  - **The rule, not the instances (V-153):** on this machine, invoke Python
    tooling as `python -m <tool>`. Console-script entry points (`pytest`,
    `mypy`, `uvicorn`, and presumably the next one) resolve to a 3.13
    interpreter while the project pins 3.12. The failures do not look alike —
    pytest raises `ModuleNotFoundError: fastapi`, mypy reports 43 phantom
    errors from the wrong stubs, uvicorn fails to spawn at all — so each one
    reads as its own bug until you know the pattern. CI builds fresh and is
    unaffected.
  - **Never verify through a relative `cd` (V-196).** `cd frontend && npm run
    lint` succeeds at nothing when the shell is already in `frontend`, and the
    clean report that follows reads as success on work that never ran. Same
    class as the rule above: the command looks right and so does the output.
    Prefer the forms that need no directory, both checked from the repo root:
    `uv run --project backend python -m pytest backend/tests` and
    `npm --prefix frontend run lint`. Otherwise make the path absolute or print
    `pwd` in the same invocation. The collector's `cd collector && uv run python
    collect.py --mode eod` below is a known instance of the pattern, left alone
    on purpose until that command is next touched: rewriting a documented
    command without running it first is the same mistake pointing the other way
    (V-198).
  - **Credential-shaped debugging asks whether, never what (V-209).** When you
    are tracing a secret, print `bool(os.environ.get(KEY))` or the key's
    presence, never its value or a `repr` of it. A single debug line that asked
    what a token held put it in a session transcript and forced a rotation. The
    same rule covers connection strings: print the host, never the URL.
  - **`load_local_env()` at `backend/app/main.py` gives any local boot
    production's database and auth gate**, because it reads `collector/.env`,
    which holds `DATABASE_URL` and `SKEPTIC_ACCESS_TOKEN`. It uses
    `setdefault`, so a value already present wins: that is why
    `.claude/launch.json`'s pins hold, and why **unsetting cannot work** —
    `env -u` removes the variable and the import puts it back. Pin, never
    clear (V-189, V-209).
  - The V-18 round-trip guard shells out to **node** (22.6+, native TS type
    stripping) to execute the real `frontend/lib/spec.ts`. It is a hard
    dependency: without node the guard fails rather than skipping.
  - **Before freeing a port, confirm the PID is this project's own tooling**
    (`ps -p <pid> -o command`). SpecHawk and other projects share this
    machine, and a port-adjacent casualty in someone else's dev server is the
    cheapest accident here to prevent (V-191).
  - The dev server pins `DATABASE_URL` to local SQLite in
    `.claude/launch.json`. It is a PIN, not a removal: the preview spawner's
    environment carried a production Neon URL from an unidentified source, and
    pinning defends against any origin. Never set
    `SKEPTIC_ALLOW_REMOTE_MIGRATION` to make a dev server start (V-188).
- Frontend: `npm --prefix frontend i`, then the dev server via
  `.claude/launch.json`. Checks: `npm --prefix frontend run lint` and
  `... run typecheck`.
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
5. **Thin samples are never blessed silently.** Below the minimum-trades bar
   (a user setting since 2026-07-14: standard 15, floor 1, never 0) or in a
   single volatility regime, trust is capped at "insufficient evidence" no
   matter how good the numbers look. A bar under 15 lets thin samples grade,
   but every such verdict MUST carry the below-standard-sample disclosure,
   and saved runs re-grade at read time when the viewer's bar differs from
   the bar they were scored at.
6. **Data coverage is honest.** Any surface that shows results also shows the
   data window they were computed on.

## README currency (owner directive 2026-08-05, non-negotiable)

The README is public and is how the project is judged. **Every major change to
the application ships with the README update in the SAME change.** Not a
follow-up, not a TODO.

A change is "major" if it alters any of: what the app does, a guardrail it
promises, the architecture or data flow, a data source, the honesty/gauntlet
stages, the API surface, or how the system is operated and monitored. Refactors
with no behavioural difference, test-only work, typos, and dependency bumps are
not major.

When it is major, update the affected README sections AND any Mermaid diagram
the change contradicts. A diagram that no longer matches the code is worse than
no diagram.

This is enforced, not remembered: `.claude/hooks/readme-currency.sh` runs as a
PreToolUse hook on `git commit` and escalates to a user prompt when application
surface is staged without `README.md`. Do not work around it by staging the
README with a cosmetic edit.

## Engineering conventions

- Python: uv, ruff, mypy (strict on `engine/` and `honesty/`), pydantic v2
  models generated to match `strategy-spec.schema.json` exactly.
- Every honesty-layer statistic ships with a unit test against a
  hand-computed fixture. The deliberately-overfit fixture in
  `backend/tests/fixtures/overfit_strategy.json` must ALWAYS be flagged by
  the gauntlet; treat a green run on it as a failing build.
- Determinism: all stochastic steps (Monte Carlo) take a seed, logged with
  the run. Same spec + same data + same seed = identical ENGINE and gauntlet
  output. The verdict GATE additionally reads the minimum-trades setting
  (guardrail #5) — a view-time policy recorded on the report, never an
  engine input.
- Frontend implements the approved mockups in `docs/design/`; do not restyle
  by taste. P/L colors never appear on verdict components and vice versa.
- **Typography (owner directive 2026-07-03, strict):** three voices, no
  more — Archivo (sans) for body/UI text, IBM Plex Mono for data (numbers,
  chips, chart text), and the Newsreader serif RESERVED for headings and
  important moments (page h1s, the hero headline, the verdict headline).
  Never introduce another font family; never spread the serif into body copy.
- Secrets only via env vars; never commit keys, never log chain data rows.

## Legal / ethical rails

Personal-use research tool. Collected market data is never redistributed or
exposed via public endpoints. Every results surface carries the disclaimer:
research tool, not financial advice. (Owner directive 2026-07-17: the
"backtests overstate live performance" clause was removed from every
surface — liability protection now lives in the legal pages; do NOT re-add
it.) The app never emits buy/sell recommendations for live trading.
