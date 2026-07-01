# Skeptic — Build Handoff Package
**Read this file first. It tells you exactly what to do, in what order.**

This package turns the Skeptic PoC + PRD into a deployed web app with a live,
always-on data pipeline. It is split for two consumers:

| File | Who consumes it |
|---|---|
| `claude-design-brief.md` | **Claude Design** (paste the brief, iterate on mockups) |
| `claude-code/CLAUDE.md` | **Claude Code** (lives at repo root, always in context) |
| `claude-code/TECH-SPEC.md` | Claude Code (architecture, APIs, algorithms) |
| `claude-code/DATA-PIPELINE.md` | Claude Code (collector, backfill, storage) |
| `claude-code/BUILD-PLAN.md` | Claude Code (milestones M0–M6, paste-ready prompts) |
| `claude-code/strategy-spec.schema.json` | Both (the strategy IR contract) |
| `claude-code/reference/collector_v2.py` | Claude Code (reference implementation) |

---

## Phase 1 — Accounts and keys (~45 min, do once, do first)

Everything downstream needs these secrets. Collect them all now.

1. **GitHub repo.** Create a **private** repo named `skeptic`. Private matters:
   the data lake credentials and your collected data references live here, and
   Yahoo-sourced data must never be publicly redistributed.
2. **Cloudflare R2** (you already have a Cloudflare account).
   - Create bucket `skeptic-data` (region: automatic).
   - R2 → Manage API Tokens → Create token with Object Read & Write on that bucket.
   - Save: `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET=skeptic-data`.
3. **Alpha Vantage free key.** alphavantage.co → claim free API key (25 req/day).
   Save as `ALPHAVANTAGE_API_KEY`. This is your EOD source of record AND your
   free historical backfill engine (see DATA-PIPELINE.md).
4. **Anthropic API key.** console.anthropic.com → key for the NL parser and
   verdict writer. Save as `ANTHROPIC_API_KEY`. Budget expectation: single-user
   usage is a few dollars/month.
5. **Neon Postgres.** neon.tech → free tier project `skeptic` → save the
   connection string as `DATABASE_URL`.
6. **Healthchecks.io** (free). Create one check named `skeptic-eod-collect`,
   schedule = weekdays. Save the ping URL as `HEALTHCHECK_URL`. This is how you
   find out the collector silently died, which it eventually will.
7. **Backend host: Railway (decided).** Budget approved to ~$25/mo; expected
   spend is ~$5 (hobby plan) plus a few dollars of Anthropic API. Create the
   Railway account now; the service itself deploys at Milestone M6. Headroom
   covers a larger instance if the Monte Carlo stage needs speed.

## Phase 2 — Start the data pipeline TODAY (~30 min)

Do this before any app work. Every day of delay is a day of history you never
get back, and the pipeline is independent of the app.

1. Push this package's `claude-code/` contents into the repo (`docs/` +
   `CLAUDE.md` at root, `reference/` under `collector/`).
2. Add all Phase 1 secrets as GitHub Actions repository secrets.
3. Open **Claude Code** in the repo and run **Milestone M1 only** from
   BUILD-PLAN.md (the pipeline milestone). It will productionize
   `collector_v2.py`, write the GitHub Actions workflows, and wire R2 +
   healthchecks.
4. Manually trigger the workflow once (Actions tab → Run workflow). Verify:
   - Parquet objects appear in R2 under `options/source=alphavantage/...` and
     `options/source=yahoo/...`
   - Healthchecks shows a ping.
5. Confirm the nightly schedule is enabled. **From this moment your historical
   database is growing and the backfill drip is walking backward through
   history at ~22 trading days per day.** The app can now be built at leisure.

## Phase 3 — Design (Claude Design, ~1–2 sessions)

1. Paste the entire `claude-design-brief.md` into Claude Design.
2. Iterate until the five core screens feel right. The brief flags three open
   product decisions; resolve them with me (Claude) in chat first if you
   haven't already answered the questions at the end of this handoff.
3. Export/screenshot the approved mockups into the repo under
   `docs/design/` so Claude Code builds to the approved visuals, not to taste.

## Phase 4 — Build (Claude Code, milestones M0–M6)

Run BUILD-PLAN.md milestones **in order**, one per session. Each has explicit
acceptance criteria; do not advance until they pass. Order and rationale:

- **M0** Repo scaffold, CI, monorepo layout (fast)
- **M1** Data pipeline (already done in Phase 2; verify)
- **M2** Backtest engine core + tests with hand-computed fixtures (the correctness milestone; slowest, most important)
- **M3** Honesty layer + verdict writer, including the deliberately-overfit fixture (this is PoC risk R3, the go/no-go)
- **M4** NL parser + clarifying loop + the 12-prompt eval set (PoC risk R1)
- **M5** Frontend implementing the approved designs
- **M6** Deploy (Vercel + Railway + smoke tests) and cutover

## Phase 5 — Verify and operate (~1 evening)

1. End-to-end smoke: type the canonical test strategy ("Sell a 30 delta put on
   SPY every Monday, close at 50% profit or 21 DTE") → spec card → backtest →
   verdict renders with honesty panels.
2. Run the R3 acceptance test: the overfit fixture strategy must get flagged.
3. Check the Data Observatory screen shows real coverage numbers.
4. Weekly ritual (5 min): glance at Healthchecks, glance at R2 object counts,
   glance at backfill progress in the Observatory.

## Decisions (resolved by the PM, July 2026)

1. **Results hierarchy: verdict-first.** Verdict Block leads; charts below.
2. **Input paradigm: hybrid.** Chat primary + editable Spec Card fields.
3. **Budget: up to ~$25/mo.** Backend on Railway; expected spend ~$5–10
   all-in. Note: this budget does NOT stretch to paid options data; the
   free/self-collected pipeline stays the plan.

These are locked in the design brief and tech spec. Changing one later means
editing the corresponding DECIDED block, not re-litigating in a prompt.

## Standing rules (carried over from the PRD, non-negotiable)

- Never fill at mid. Never imply more data history than exists. Never bless a
  thin sample. Rigor is opt-out, not opt-in.
- Personal use only. Never redistribute collected data. Not financial advice,
  everywhere, always.
