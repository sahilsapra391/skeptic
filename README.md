# Skeptic

An options-research copilot that argues with you. You describe a strategy in
plain English, it backtests it on real EOD options data, then it spends most of
its effort trying to prove the result is noise.

Single-user research tool. Not financial advice, and it never emits buy or sell
recommendations.

## Why I built it this way

Most backtesters are optimists. They will happily show you a 3.2 Sharpe on 11
trades in one volatility regime and let you draw your own conclusion. The
backtest is the easy part. The honest part is the product.

So the interesting code here is not the engine. It is everything that tries to
tear the engine's answer down.

## The honesty layer

Every run goes through a gauntlet before it is allowed to say anything:
out-of-sample split, walk-forward, Monte Carlo, parameter sensitivity, and a
deflated Sharpe ratio that accounts for how many variations were tried.

A deliberately overfit strategy lives in the test fixtures. If the gauntlet ever
passes it, the build fails. That test exists so the honesty layer cannot quietly
rot.

## Guardrails I refuse to break

These are enforced in code and in tests, not in a style guide.

**Never fill at mid.** Buys fill toward the ask, sells toward the bid, plus a
configured slippage fraction of the spread. Commission always applies. Mid fills
are the single easiest way to manufacture a strategy that only works in a
spreadsheet.

**Point-in-time correctness.** A simulation at date T may read only data
observed on or before T. There are tests whose only job is to prove no lookahead
exists.

**The verdict is grounded.** Every number in the plain-English verdict must
exist in the computed stats payload. A numeric validator rejects the text
otherwise. The verdict model receives only computed statistics, never the raw
user prompt, so it cannot be talked into a conclusion.

**Thin samples are never blessed quietly.** Below the minimum-trades bar, or
inside a single volatility regime, trust is capped at "insufficient evidence" no
matter how good the numbers look. Saved runs re-grade at read time if the
viewer's bar differs from the one they were scored at.

**No silent guesses.** If the strategy description is missing or ambiguous on
entry, strike, or exit, the parser asks a clarifying question. It never
defaults.

**Determinism.** Same spec, same data, same seed produces identical engine and
gauntlet output. Every stochastic step takes a logged seed.

## Architecture

```
frontend/    Next.js 14 (App Router, TypeScript, Tailwind, shadcn/ui) -> Vercel
backend/     FastAPI on Python 3.12 -> Railway
  parser/    natural language -> validated strategy spec
  engine/    EOD options backtest
  honesty/   OOS, walk-forward, Monte Carlo, sensitivity, DSR
  verdict/   stats -> grounded English, with a numeric validator
collector/   market data pipeline, runs on an always-on VM
```

Data lives in Cloudflare R2 and is queried with DuckDB.

## The part that taught me the most

The data collector runs unattended every night, and getting that right was
harder than the engine.

It started on GitHub Actions. In July a billing block refused to start the
nightly job for a full trading week, and every run died in under five seconds.
The job never got far enough to report a failure, so nothing alerted. A week of
missing data, discovered by noticing a dashboard had gone quiet.

It now runs on an always-on VM under systemd timers, with a dead-man's switch, a
hook that fires on abnormal death (timeout, OOM, reboot) because a killed
process cannot report on itself, and a cross-host lease so a manual run cannot
collide with the scheduled one.

The lesson underneath all of it: a job that fails loudly is fine. A job that
fails silently is the actual enemy. Most of the operational code here exists to
convert the second kind into the first.

## Tests

1,099 passing tests (986 backend, 113 collector). Every honesty-layer statistic
is tested against a hand-computed fixture rather than a golden file, so a wrong
number cannot be blessed by regenerating a snapshot.

## Status

Working and in daily use by me. Not accepting contributions, and not licensed
for anyone else's use. See LICENSE.
