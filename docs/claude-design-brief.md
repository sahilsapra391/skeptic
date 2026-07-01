# Skeptic — Design Brief for Claude Design
*Paste this entire document into Claude Design as the opening prompt.*

## What you are designing

**Skeptic** is a research instrument for retail options traders. A trader
describes a strategy in plain English. Skeptic compiles it, backtests it on
real options data, then does what no competitor does: it attacks its own
result. It runs out-of-sample splits, walk-forward, Monte Carlo, and
sensitivity sweeps automatically, and delivers an honest plain-English verdict
about whether the edge is real or curve-fit noise.

**The product's entire identity is adversarial honesty.** Every competing tool
is designed to make backtests look good. Skeptic is designed to make them
tell the truth. The design must embody this: it should feel like a
scientific instrument built by someone who has lost money to a pretty equity
curve, not like a trading app chasing dopamine.

**Subject world to draw from:** quant research desks, lab notebooks,
statistical reports, aviation-style instrument panels. Precision, evidence,
falsification. NOT: crypto dashboards, Robinhood confetti, WallStreetBets
energy, "AI magic sparkle" aesthetics.

## The user

One primary persona: **the Burned Tinkerer.** Technical, self-taught, trades
index options (SPY/QQQ/IWM), has a graveyard of strategies that backtested
beautifully and failed live. Skeptical of tools that flatter him. He will
trust this product exactly as much as it is willing to disappoint him.
Desktop-first (research happens at a desk); mobile is read-only review of
past results, not a first-class creation surface.

## Brand voice in the interface

- Verdicts lead with the uncomfortable part. "This edge disappears
  out-of-sample" comes before any praise.
- Plain verbs, sentence case, no exclamation marks, no hype. "Run analysis,"
  not "Discover your edge! 🚀"
- The product says "I don't have enough data to answer that" proudly, as a
  feature. Data limitations are surfaced, never hidden.
- Numbers are sacred: every number shown traces to a computation. Nothing
  decorative is dressed up as data.

## The signature element (spend your boldness here)

**The Verdict Block.** One distinctive, ownable component that renders the
honest verdict. Requirements:

- Contains: a one-line headline (the uncomfortable truth first), a
  **trust level** on a 5-step scale (from "statistical noise" to "survived
  every attack"), evidence bullets, "where it breaks" bullets, and standing
  caveats.
- The trust scale must NOT read as a score or a grade to gamify. Consider the
  visual language of certainty/uncertainty: confidence intervals, gauge under
  tension, a seal that is only partially stamped. Make one opinionated choice.
- **Critical color rule:** the verdict must never borrow P/L colors. Green/red
  belong to profit/loss data only. The verdict needs its own hue family so
  "trustworthy" is never confused with "profitable." A strategy can be
  profitable in-sample AND untrustworthy; the design must hold both at once.

## Color and type direction (constraints, not a spec)

- Data-dense product: choose a palette that keeps charts legible for hours.
  Dark-mode-first is acceptable and probably right for a research tool, but
  earn it; don't default to near-black + acid green (that is the template
  answer).
- P/L semantics: conventional green/red, reserved exclusively for P/L.
- Verdict semantics: a separate hue family (your choice) for trust levels.
- Typography: numbers do heavy lifting. Use a face with tabular figures for
  all data; give display type real character so the product doesn't read as a
  generic admin dashboard. Monospace or semi-mono for the strategy spec and
  trade logs (they are code-like artifacts).

## Screens to design (five core + two supporting)

### 1. New Analysis (home)
The entry point. A focused composer where the user describes a strategy in
English. Include: template chips for the five v1 structures (short put,
credit spread, iron condor, covered call, long call/put) that pre-fill
example phrasing; a visible, honest data-coverage line ("Options data:
2024-01 → today · Underlying: 1993 → today") so expectations are set before
anything runs.
**DECIDED (PM): hybrid input.** Chat is primary; the compiled parameters
render as editable fields, so correcting one number never requires
re-prompting. Design the chat composer and the editable Spec Card as one
continuous flow, not two separate modes.

### 2. Spec Confirmation
After parsing, the compiled strategy renders as a **Spec Card**: every
parameter the engine will use, in a scannable, code-adjacent layout, with the
user's original words alongside. If the parser needs clarification, questions
appear here as focused prompts, one at a time. Primary action: "Run the
gauntlet" (or your better verb). Nothing ever runs on an unconfirmed spec;
the design should make confirmation feel like signing off on a lab protocol.

### 3. Results / Verdict (the hero screen, spend the most effort here)
Contents: the Verdict Block, core metrics (CAGR, Sharpe, Sortino, max
drawdown, win rate, profit factor), equity curve with drawdown subchart,
**honesty panels** (in-sample vs out-of-sample comparison, walk-forward
window results, Monte Carlo outcome fan showing 5th/50th/95th percentiles,
parameter-sensitivity heatmap labeled plateau vs cliff), per-trade log
(expandable table, includes skipped trades and why), and a persistent
follow-up composer ("ask about this result...") for interrogation.
**DECIDED (PM): verdict-first.** The Verdict Block owns the top of the page;
the equity curve sits below the fold of attention. This inverts every
competitor and is the brand; design it so the inversion feels intentional,
not withholding (a compact P/L sparkline inside the metrics row may
acknowledge the curve exists without letting it lead).

### 4. Strategy Library
Saved analyses. Each entry shows name, underlying, structure, date run, and a
compact trust-level indicator (the signature element, miniaturized). Support
compare-two side by side. Empty state matters: first-run users land here with
nothing; the empty state should teach the product's philosophy in one line
and route to New Analysis.

### 5. Data Observatory
The honesty dashboard, and a screen no competitor has. Shows: collection
streak (days captured), coverage timeline per ticker per source, backfill
progress bar walking backward through history ("backfilled to 2019-03-14, ~4
months to reach 2008"), last collector run status, data-quality flags (bid/ask
null rates, stale quotes). This screen turns a data limitation into visible,
growing progress. Design it like mission telemetry, worth caring about.

### Supporting screens
- **Run-in-progress state:** the gauntlet sequence (backtest → OOS →
  walk-forward → Monte Carlo → sensitivity → verdict) shown as staged
  progress. This is a legitimate place for restrained motion: the product
  visibly attacking the strategy stage by stage.
- **Settings:** API status, cost assumptions (commission, slippage defaults),
  data source health, the standing disclaimer.

## States that must be designed, not improvised

- **Insufficient data verdict:** the most on-brand state in the product. When
  the sample is too thin, the Verdict Block refuses to bless: design the
  refusal state explicitly (it should feel principled, not broken).
- Empty library, first-ever run, parser needs clarification, backtest error
  (with the exact failing parameter), collector-down warning banner in the
  Observatory.
- Loading: staged gauntlet progress, never a generic spinner.

## Anti-patterns (hard no's)

- No confetti, streaks, badges, rockets, or celebratory motion on profitable
  results. A profitable backtest is a hypothesis, not a win.
- No green-tinted overall theming that makes the whole product feel "up."
- No dark-pattern prominence for good results over bad ones; the layout must
  give a damning verdict the same visual weight as a favorable one.
- No decorative AI iconography (sparkles, magic wands). The LLM is a
  statistician here, not a genie.
- Standing footer disclaimer on every results surface: research tool, not
  financial advice, backtests overstate live results.

## Deliverables requested from you (Claude Design)

1. A token system: palette (named hex values), type pairing (display / body /
   data-mono), spacing scale, and the verdict hue family.
2. Hi-fi mockups of the five core screens, desktop-first, including the
   insufficient-data verdict state and the run-in-progress gauntlet state.
3. The Verdict Block as a standalone component spec at three sizes (hero,
   card, library-row).
4. One aesthetic risk, taken deliberately, justified in a sentence.

Where this brief leaves an axis free, make an opinionated choice specific to
this subject (quant research instrument) rather than a generic dashboard
default, and say what you chose and why.
