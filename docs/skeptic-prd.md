# Skeptic — Product Requirements Document

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
*Working title, rename freely.*

**Author:** Sahil Sapra
**Type:** Passion / indie project
**Status:** Draft v0.1
**Last updated:** July 2026

---

## 1. Overview

Skeptic is an agentic options-research copilot for retail index-options traders. A user describes a strategy in plain English. Skeptic compiles it into an executable spec, backtests it on options data, and then does the thing every other tool skips: it stress-tests the result against overfitting and reports an honest verdict, not a flattering equity curve.

The category is crowded. Natural-language backtesting is now a commodity (TrendSpider, QuantConnect's Mia, TradrLab, TradeAlgo). Retail options backtesting has an entrenched leader (Option Alpha, 11,000+ users, focused squarely on 0DTE/1DTE on SPY/QQQ/IWM/SPX). Skeptic does not try to out-feature them. It competes on a single sharp axis they all under-serve: **intellectual honesty about whether a strategy actually works.**

This is explicitly a passion project. Success is defined as a tool the author and a small number of like-minded traders genuinely rely on, plus a credible portfolio artifact. It is not scoped as a venture-scale company, because the moat is thin and the data economics are hostile.

## 2. Problem statement

Retail options traders keep losing money to a predictable failure mode:

1. They have a plausible thesis (e.g. "SPY mean-reverts, so fade extremes with short-dated options").
2. They backtest it once, in-sample, tune the parameters until the curve looks great, and mistake curve-fitting for edge.
3. They trade it live. It breaks, because it was noise.

Existing tools are complicit. They optimize for making the backtest look good and impressive, not for telling the user the uncomfortable truth. Even the tools that gesture at rigor (walk-forward, Monte Carlo) bury it behind menus most retail users never touch and cannot interpret.

The gap is not "can I backtest options." It is "will something stop me from fooling myself, and explain the statistics in language I actually understand."

## 3. Vision and positioning

**Positioning statement:** For the technical-but-self-taught options trader who keeps getting burned by backtests that looked great and failed live, Skeptic is a research copilot that acts as an honest adversary. Unlike commodity backtesters that flatter your strategy, Skeptic actively tries to prove your edge is fake, and tells you plainly when it is.

**One-liner:** The backtester that argues with you.

**What it is not:** A signal service. A trade-idea generator. An autotrading bot. A "get rich" tool. It generates no recommendations to buy or sell anything. It is a research instrument.

## 4. Target users

**Primary persona: "The Burned Tinkerer."**
Technical enough to describe a strategy precisely and read a chart. Trades index options (SPY/QQQ/IWM/SPX), often short-dated. Has a spreadsheet or a Python script and a graveyard of strategies that looked great and lost money. Wants rigor but is not a statistician and will not manually run walk-forward analysis. This is the author and people like the author.

**Secondary persona: "The Premium Seller."**
Sells options premium (short puts, spreads, condors) on indices. Cares about tail risk, drawdown, and whether a high win rate is hiding a catastrophic loss profile. Deeply served by a tool that surfaces the downside honestly.

**Explicit anti-persona:** Someone looking for buy/sell signals or a strategy handed to them. Skeptic will disappoint them on purpose.

## 5. Goals and non-goals

**Goals (v1):**
- Turn a plain-English options strategy into a correct, inspectable spec, or ask a clarifying question when it cannot.
- Backtest EOD options strategies on SPY/QQQ/IWM with realistic fills and costs.
- Automatically run the honesty suite (in-sample vs out-of-sample, walk-forward, Monte Carlo, parameter sensitivity) with zero extra clicks.
- Deliver a plain-English verdict grounded in those numbers, including an explicit "this is probably overfit / not enough data" when true.
- Run entirely on the author's own free/self-collected data.

**Non-goals (v1, and mostly forever):**
- Intraday, 0DTE, and 1DTE support. Deferred until enough intraday data is self-collected. This is a real limitation given the author's own trading style and is called out honestly in-product.
- Live trading, broker integration, autotrading.
- Trade recommendations or signals.
- Supporting arbitrary tickers or exotic instruments.
- Being a business with billing, teams, and SLAs.

## 6. Success metrics

Because this is a passion project, metrics are about usefulness and honesty, not revenue.

- **North star:** The author reaches for Skeptic instead of a raw backtest when evaluating a new idea. Binary, self-assessed, honest.
- **Honesty precision:** On a held-out set of deliberately overfit strategies, the verdict flags them as suspect at least 80% of the time.
- **Parse reliability:** 80%+ of clear NL strategies compile to a correct spec with no silent misreads; 100% of ambiguous ones trigger a question.
- **Trust delta:** In blind comparison, a smart trader trusts the Skeptic verdict more than the equity curve alone. Qualitative, but the whole product hinges on it.
- **If shared:** small number of active users who return week over week. Retention over count.

## 7. Core user flows

**Flow A: Evaluate a new strategy.**
1. User types a strategy in English.
2. Skeptic shows the compiled spec and asks any clarifying questions.
3. User confirms.
4. Skeptic runs the backtest and the full honesty suite.
5. Skeptic returns: equity curve, core metrics, per-trade log, and an honest verdict ("This survived out-of-sample and walk-forward. The edge is concentrated in high-IV regimes and disappears in calm markets. Sample is still thin, treat as suggestive, not proven.").

**Flow B: Interrogate a result.**
1. User asks follow-up questions in chat ("what happens if I widen the spread?", "show me the worst month", "is this just 2020?").
2. Skeptic re-runs the relevant slice and answers, grounded in data.

**Flow C: Compare variations.**
1. User asks to sweep a parameter (e.g. delta from 15 to 40).
2. Skeptic runs the sweep and shows a sensitivity view, flagging whether the "best" setting is a cliff (overfit) or a plateau (robust).

## 8. Functional requirements

### 8.1 Natural-language parser
- FR-1: Accept a free-text strategy description and emit a validated structured spec (entry rules, exit rules, position construction, sizing, underlying, universe).
- FR-2: Always display the compiled spec to the user before running anything.
- FR-3: On ambiguity or missing required fields, ask a specific clarifying question rather than assuming a default. Never silently guess an entry price, delta, or exit.
- FR-4: Support the v1 template family: short put, put/call credit spread, iron condor, covered call, long call/put.

### 8.2 Data layer
- FR-5: Ingest self-collected EOD option chains (Parquet) for SPY/QQQ/IWM.
- FR-6: Ingest free underlying price history (daily) going back decades.
- FR-7: Enforce point-in-time correctness: a backtest on date T may only use data available at or before T. No lookahead.
- FR-8: Surface data coverage honestly (e.g. "options data available from 2026-07-01 to today; underlying from 1993"). Never imply more history than exists.

### 8.3 Backtest engine
- FR-9: Event-driven EOD simulation supporting multi-leg positions.
- FR-10: Model fills at bid/ask plus a configurable slippage assumption. Never fill at mid by default.
- FR-11: Model commissions and fees.
- FR-12: Handle expiration, exercise, and assignment, including early-assignment risk for short options.
- FR-13: Track greeks and position value over the life of each trade.
- FR-14: Produce a complete per-trade log (entry/exit time, price, legs, P/L, and why a candidate trade was skipped).

### 8.4 Honesty / validation layer (the differentiator)
- FR-15: Automatically split data into in-sample and out-of-sample and report both, by default, with no user action.
- FR-16: Run walk-forward analysis across rolling windows.
- FR-17: Run Monte Carlo resampling (trade order and/or bootstrap) to produce a distribution of outcomes, not a single curve.
- FR-18: Run a parameter-sensitivity sweep and classify the optimum as a robust plateau or a fragile cliff.
- FR-19: Apply a multiple-testing correction (e.g. deflated Sharpe ratio) when the user has tried many variations, and warn about data-mining bias.
- FR-20: Refuse to bless a result when the sample is too small or spans too few market regimes. Say so explicitly.

### 8.5 Verdict writer
- FR-21: Generate a plain-English verdict strictly grounded in the computed statistics. No number appears in the verdict that the engine did not produce.
- FR-22: The verdict must state, in order: does it survive OOS, where the edge concentrates, where it breaks, and how much to trust it given the sample. Lead with the uncomfortable part.
- FR-23: Include a standing, unmissable disclaimer: not financial advice, past performance does not predict future results, backtests overstate real-world results.

### 8.6 Interface
- FR-24: Chat-style interaction plus visual output (equity curve, drawdown, payoff diagram, trade log, sensitivity view).
- FR-25: v1 may be a notebook, CLI, or bare Streamlit. A Next.js/Vercel front end is a later nicety, not a requirement.

## 9. Non-functional requirements

- NFR-1 (Cost): Runs on free and self-collected data. No paid data dependency in v1. Total monthly cost approximately the LLM API bill only.
- NFR-2 (Honesty by default): Rigor is not a menu item. The honesty suite runs automatically. A user should have to opt out of rigor, never opt in.
- NFR-3 (Data-quality transparency): Every result carries the caveat that it was computed on approximate, self-collected data. The tool never presents backtest output as ground truth.
- NFR-4 (Performance): A single EOD backtest over the available window returns in seconds, not minutes, so iteration is fast.
- NFR-5 (Privacy / legal): Personal use only. Self-collected data is never redistributed. Respect source ToS. Clear non-advice framing everywhere.
- NFR-6 (Reproducibility): Same spec plus same data yields the same result. Seeds are fixed and logged.

## 10. Data strategy

This is the crux, so it gets its own section.

**The hard truth:** Deep, granular historical options data (20 to 30 years, intraday) is not available for free and is expensive even to license. Any promise of "30 years of options backtests" is false. Skeptic is honest about this in-product.

**The free/DIY plan:**
- **Underlying prices:** Free, instant, 30+ years. yfinance or Stooq. Covers the underlying-signal half completely.
- **Option chains:** Self-collect from now. A daily EOD collector snapshots SPY/QQQ/IWM chains (within a sane DTE window) to Parquet. Every day adds a day of history you own.
- **Backfill (bonus):** Optionally seed a few years from a free community-crowdsourced EOD options archive. Verify coverage. Never redistribute.
- **Free rate-limited history:** Alpha Vantage options endpoints on the free key (about 25 requests/day) for slow backfill of a couple tickers.

**Phasing tied to data maturity:**
- **Phase 0 (now):** Collect EOD daily. Build and validate the pipeline and honesty layer on whatever exists plus backfill. Conclusions are about the machinery, not strategy edge.
- **Phase 1 (months in):** Enough EOD history across at least one volatility regime shift to draw cautious strategy conclusions.
- **Phase 2 (later, optional):** Add an intraday collector (snapshots every few minutes during market hours) to eventually support the author's own 1DTE/0DTE style. Expect Yahoo reliability and rate limits to be the main obstacle, and consider a paid intraday source if the project earns the investment.

**Standing honesty rule baked into the product:** a strategy is never blessed on a sample too short or too regime-poor to support the claim. This guardrail is built before any strategy conclusions are trusted.

## 11. MVP scope and roadmap

**MVP (Phase 0 to 1):**
- SPY/QQQ/IWM, EOD only.
- 3 to 5 strategy templates.
- NL parser with clarifying loop.
- Options-aware EOD backtest engine with realistic fills.
- Full honesty suite plus verdict writer.
- Bare interface (notebook/CLI/Streamlit).
- Self-collected + free data only.

**Post-MVP, in rough priority:**
1. Better interface (Next.js/Vercel chat + charts).
2. More templates and free-form strategy support.
3. Intraday collection and eventual short-DTE support.
4. Saved strategies, portfolio-level combination of multiple strategies.
5. Optional paid data source for depth and accuracy, if warranted.

**Deliberately deferred, maybe permanently:** live trading, broker links, autotrading, signals, multi-user infra, billing.

## 12. Competitive context (why this shape and not another)

- **Option Alpha** owns the retail 0DTE/1DTE index-options backtesting niche with automation and a large user base. Skeptic does not fight there in v1 (no intraday, no automation). It fights on honesty.
- **TrendSpider / QuantConnect Mia / TradrLab / TradeAlgo** offer NL backtesting broadly. NL is therefore not a differentiator, only a convenient interface.
- **ORATS / getVolatility / OptionMetrics** are data-and-analytics heavyweights. Skeptic is not a data provider and will never compete on data depth.

The only defensible axis for a solo builder is the honesty layer plus a genuinely good explanation of the statistics. If a competitor ships that convincingly, Skeptic's edge is gone. That is an accepted risk for a passion project.

## 13. Open questions and honest risks

- **Data quality:** Is self-collected free EOD data clean enough to backtest even simple strategies believably? Unknown until the PoC runs. If not, v1 needs paid data, which changes the cost profile.
- **Regime coverage:** How long until self-collected data spans enough regimes to say anything real? Likely a year or more. The product must not overstate conclusions in the meantime.
- **The author's own use case:** v1 does not support 1DTE, which is what the author actually trades. Honest tension. v1 proves the concept on adjacent strategies; the author's style waits for intraday data.
- **Verdict trust:** Will a good trader actually trust an LLM-written verdict? If the verdict is not clearly grounded and clearly honest, they will not, and the product fails. This is the single biggest product risk.
- **Scope discipline:** The temptation to add live trading and become "Option Alpha but worse" is real and fatal. The non-goals are load-bearing.

## 14. Disclaimer (product and document)

Skeptic is a research tool. It does not provide financial, investment, or trading advice, and produces no recommendations to buy or sell any security. Backtests are computed on approximate, self-collected data and systematically overstate real-world results. Past performance does not predict future results. The author is not a financial advisor.
