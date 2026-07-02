# Skeptic — Proof of Concept

> **Status addendum (2026-07-02) — read this first.** Since this document
> was written: Alpha Vantage's options endpoints went premium and were
> dropped (the $0 data rule held); the EOD record is the nightly Yahoo
> snapshot; SPY EOD chains 2020-01 → 2026-06 are backfilled from the DoltHub
> community archive (docs/DOLTHUB-EVAL.md); 1-minute option bars for
> SPY/QQQ/IWM since 2024-02 come from Alpaca's free API (BUILD-PLAN M1.5);
> a local agent records full-chain quote snapshots every session minute
> going forward (docs/INTRADAY-OPTIONS-DATA-EVAL.md). QQQ/IWM EOD history
> begins 2026-07-01 — no free source reaches earlier. UI direction is
> locked: chat-led and radically simple (see the DECIDED block in
> claude-design-brief.md; Option Alpha-style complexity is the named
> anti-example). Where this document assumes Alpha Vantage, a 25/day
> request budget, or backfill-to-2008, read it through these decisions.
*Working title, rename freely. An agentic options-research copilot that refuses to let you fool yourself.*

**Author:** Sahil Sapra
**Status:** Draft / passion project
**Last updated:** July 2026

---

## 1. The one-line thesis

Every retail options backtester happily lets a trader run one in-sample backtest, see a pretty equity curve, and go blow up their account. Skeptic takes a strategy described in plain English, backtests it on options data, and then acts as an adversarial statistician: it automatically runs out-of-sample, walk-forward, and Monte Carlo checks, corrects for multiple testing, and tells you in plain English whether the "edge" is real or curve-fit noise, and where it breaks.

The backtest is table stakes. The honesty layer is the product.

## 2. What this PoC is actually proving

A PoC exists to de-risk the assumptions most likely to kill the project. It is not a product. For Skeptic, the three riskiest beliefs, in order:

| # | Risky belief | How the PoC tests it | Pass condition |
|---|---|---|---|
| R1 | An LLM can reliably turn a natural-language options strategy into a correct, executable strategy spec, and ask for clarification when the description is ambiguous. | Feed 15 to 20 hand-written strategy descriptions (mix of clear and deliberately vague) through the NL parser. Compare the generated spec to a human-authored ground-truth spec. | 80%+ of clear descriptions compile to a correct spec with zero silent misreads. Every vague description triggers a clarifying question instead of guessing. |
| R2 | A from-scratch, self-collected EOD options dataset plus free underlying data is good enough to produce a directionally believable backtest for simple strategies. | Collect EOD chains on SPY/QQQ/IWM for 6+ weeks, backtest a known strategy (e.g. a 30-delta short put on SPY), sanity-check the P/L shape against public results. | Backtest P/L is in the right ballpark and moves in the right direction on known trades. Not "accurate," just "not obviously broken." |
| R3 | The honesty layer produces a verdict a smart trader trusts more than a raw equity curve, and it actually catches overfitting. | Deliberately overfit a strategy (tune parameters on the full dataset), then run it through the validation layer. | The layer flags it as likely overfit and explains why, in language a non-statistician understands. |

If R1 and R3 pass, the project is worth continuing even if R2 needs better data later. If R1 fails, the whole natural-language premise is wrong and you pivot to a form-based builder. If R3 fails, you have just built yet another commodity backtester and should stop.

## 3. Explicit non-goals for the PoC

The fastest way to never ship a PoC is to build the product. Skeptic's PoC deliberately does **not** include:

- Intraday or 0DTE/1DTE support. Your own trading style needs intraday data you have not collected yet. Prove the concept on EOD strategies first.
- Live trading, broker integration, or automation. Research only.
- A polished UI. A notebook or a bare Streamlit/CLI is fine.
- Arbitrary strategy support. Three to five templates only (see scope).
- Accurate historical data. Directionally-believable is the bar.
- Multi-user, auth, billing, or anything cloud-hosted. Runs on your laptop.

## 4. Scope: the smallest thing that proves the thesis

**Underlyings:** SPY, QQQ, IWM only. Deep liquidity, tight spreads, the friendliest possible case for cheap data.

**Strategy templates (pick 3 to 5):**
- Short put (cash-secured), single leg, delta- or strike-selected
- Vertical spread (put credit / call credit)
- Iron condor
- Covered call
- Long call/put (directional)

**Data:** End-of-day chains, self-collected from now, plus free underlying price history going back decades. No intraday.

**Pipeline to build:**
1. NL description in, structured strategy spec out (LLM + JSON schema + clarifying-question loop).
2. Event-driven EOD backtest engine with realistic-ish fills (trade at bid/ask with a slippage assumption, not mid), commissions, expiration and assignment handling, multi-leg support.
3. Metrics: CAGR, Sharpe, Sortino, max drawdown, win rate, profit factor, exposure, per-trade log.
4. Honesty layer: automatic in-sample vs out-of-sample split, walk-forward, Monte Carlo resampling of trade order, parameter-sensitivity sweep, and an LLM-generated plain-English verdict grounded in those numbers.

## 5. Technical approach

**Stack (bias toward what you already run):**
- Language: Python for the engine and data (this is where the ecosystem is), your Next.js/Vercel front end later.
- Data storage: partitioned Parquet on disk via pyarrow, queried with DuckDB. No database server needed for a PoC. Options data is large; columnar + partitioned by date/ticker keeps it sane.
- Backtest core: start from an existing event-driven library (Backtrader, VectorBT, or NautilusTrader) for the plumbing, but write the options-specific handling (multi-leg positions, greeks, expiration, assignment, fills at bid/ask) yourself. Most libraries treat options as an afterthought.
- LLM layer: Claude or GPT with structured output / function calling to emit the strategy spec as validated JSON. Elicitation loop for ambiguity (you already build this pattern into your skills).
- Stats: numpy/scipy/pandas for the validation math. Deflated Sharpe ratio and a simple multiple-testing correction for the honesty layer.

**Architecture sketch:**

```
NL description
    -> LLM parser (JSON schema + clarifying questions)
    -> Strategy Spec (validated intermediate representation)
    -> Backtest Engine (EOD, event-driven, options-aware)
        <- Options data (self-collected Parquet, DuckDB)
        <- Underlying prices (free, yfinance/Stooq)
    -> Raw results + trade log
    -> Honesty Layer (OOS + walk-forward + Monte Carlo + sensitivity)
    -> LLM verdict writer (grounded in the stats)
    -> Report: metrics, equity curve, and the honest read
```

## 6. Data plan for the PoC

- **Day 0:** Stand up the EOD collector (provided script). Cron it to run once daily after market close. Snapshot SPY/QQQ/IWM chains within a sane DTE window (default 60 days) to Parquet.
- **Day 0:** Backfill 20+ years of underlying prices for SPY/QQQ/IWM for free. This is instant and covers the whole "underlying signal" half.
- **Ongoing:** Optionally hunt for a few years of free community-crowdsourced EOD options history to backfill. Treat as a bonus, verify coverage, never redistribute.
- **Honest limit:** Six to eight weeks of self-collected data proves the pipeline and the honesty layer. It is far too short to conclude any strategy has edge. That conclusion waits for either years of collection or a backfilled historical set.

## 7. Success criteria (how you know the PoC passed)

- R1, R2, R3 pass conditions above are met.
- You can type "sell a 30 delta put on SPY every week, close at 50% profit or 21 days" and get back a compiled spec, a backtest, and an honest verdict, end to end, without hand-editing the spec.
- The honesty layer catches at least one deliberately overfit strategy and explains why in language your non-quant friends understand.
- You personally trust the verdict more than you trust a raw equity curve. If you do not, the product has no reason to exist.

## 8. Key risks and how the PoC handles them

| Risk | Severity | Mitigation in PoC |
|---|---|---|
| Free options data is too noisy to backtest at all | High | Bar is "directionally believable," not accurate. If even that fails, the answer is "this needs paid data," which is a real and useful finding. |
| Fill modeling is wrong and makes everything look profitable | High | Trade at bid/ask plus explicit slippage, never mid. Model commissions. Compare to a "perfect fill" run to see the gap. |
| LLM silently misreads a strategy and backtests the wrong thing | High | Always show the user the compiled spec before running. Ambiguity forces a question, never a guess. |
| Scope creep into intraday/live/UI | Medium | Non-goals section is load-bearing. Say no. |
| You conclude a strategy "works" off 6 weeks of data | Medium | The honesty layer itself must refuse to bless a result with too little data or too few regimes. Build that guardrail first. |

## 9. Rough effort estimate

Assuming evenings-and-weekends solo, reusing an existing backtest library:

- Collector + data plumbing: a weekend.
- NL parser + spec schema + clarifying loop: a weekend (you have done this shape of thing before).
- EOD options backtest engine with 3 templates: the real work, one to two weeks of evenings.
- Honesty layer (OOS, walk-forward, Monte Carlo, sensitivity, verdict writer): one week of evenings, and this is where you should spend your best energy.
- Glue + a bare interface: a few evenings.

Call it four to six weeks of part-time work to a credible PoC, with the caveat that meaningful strategy conclusions wait on data you are still collecting.

## 10. Decision after PoC

- **All three pass:** continue to the MVP in the PRD. Consider it a portfolio/indie project, not a company.
- **R1 fails:** drop natural language, ship a form-based builder, revisit NL later.
- **R3 fails:** stop. The market does not need another backtester and the honesty layer was the only defensible thing.
- **R2 fails only:** continue, but budget for a paid EOD data source (ORATS tier) before drawing any strategy conclusions.
