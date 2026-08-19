# Launch checklist

Gates that must close before this app serves anyone other than its owner.

**This file is new, and why matters.** A rotation gate needed to land "beside the
Stripe and legal gates" and there was no checklist to land it beside: the launch
phases exist only as annotations scattered through the code (`launch L1`, `launch L1b`,
`launch L3`, `launch L5`) and as prose in `CLAUDE.md`'s legal rails. So this list was
assembled from those annotations by grep, not transcribed from an existing plan. It is
a starting point that names its own provenance, not an authority.

Nothing here is a backlog item. Each line is a condition on serving the public, and an
unclosed line is a reason not to.

## Secrets

- [ ] **Rotate `SKEPTIC_ACCESS_TOKEN` and the production Neon `DATABASE_URL`.**
      Both were exposed during the variant-runs phase — in a session transcript and in
      local shell history, neither committed. The owner decided on 2026-08-19 not to
      rotate at the time, accepting the risk while the database held only their own
      data. **That acceptance expires here**: the moment the database holds a user who
      is not the owner, the exposure stops being the owner's own risk to take. Full
      record, including what was exposed and how, is in the variant-runs brief's
      security entry. `rotated-on` and `old-value-verified-dead` are recorded there as
      NOT DONE and stay that way until someone witnesses them.
- [ ] Confirm no secret reaches a log line. `collector/.env` is untracked and
      gitignored (`.gitignore:23`); the app loads it into any local process via
      `load_local_env()` at `backend/app/main.py`, which is documented at that call
      site and is why a local boot inherits production credentials by default.

## Accounts and billing

- [ ] **Stripe top-ups (launch L3)** — `backend/app/api/billing_routes.py`. Live keys,
      webhook signature verification, and the L2 refund-once partial unique index
      exercised against real events rather than fixtures.
- [ ] **Accounts (launch L1 / L1b)** — `backend/app/api/me.py`,
      `backend/app/api/auth_routes.py`. Self-rolled argon2id + DB sessions, every route
      rate-limited. Email verification is optional until a mail sender is configured;
      decide whether it stays optional for the public.
- [ ] **Admin surface (launch L5)** — `backend/app/api/admin_routes.py` is owner-only
      and gated on a derived flag. Confirm the gate holds for a non-owner account, not
      just for an absent one.

## Legal and honesty

- [ ] Every results surface carries the research-tool disclaimer and the data window it
      was computed on (guardrail #6). The "backtests overstate live performance" clause
      is deliberately absent from product surfaces and lives in the legal pages
      (owner directive 2026-07-17) — confirm the legal pages exist and say it.
- [ ] The app emits no buy/sell recommendation for live trading.
- [ ] Collected market data is never redistributed or exposed by a public endpoint.

## Data and operations

- [ ] Coverage honesty: every surface showing results shows its data window.
- [ ] The overfit fixture `backend/tests/fixtures/overfit_strategy.json` is still
      flagged by the gauntlet. A green run on it is a failing build.
- [ ] `RUNBOOK.md` reflects the deployed topology, including that the image sets
      `SKEPTIC_ALLOW_REMOTE_MIGRATION` and why.
