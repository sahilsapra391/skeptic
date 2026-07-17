# Copy deck — every string on the page

Provenance flags: **[V]** verbatim from app/recordings · **[PRD]** verbatim from PRD/brief · **[NEW]** written for the page, voice-matched · **[FIX]** fixture content, replaced by pinned run payload · **[VERIFY]** owner confirms before launch.

## Nav
- Sign in **[PRD]**
- Get 5 free backtests **[PRD]**

## Hero
- H1: Pitch me a trade. I'll play the skeptic. **[V]**
- Sub: Describe an options strategy in plain English. Skeptic interviews you and runs it on real intraday options data. **[NEW — owner-edited]**
- Composer placeholders (rotate 3.6s, pause on typing) **[V app presets]**:
  1. sell a 30-delta put on SPY every week, close at 50% profit or 21 days…
  2. iron condor on SPY at 16 delta, 45 DTE, exit at 21 DTE or 2x credit stop…
  3. buy a 10-delta SPY put, 45 DTE, sell at +200% or hold to expiry…
  4. covered call on SPY, sell the 30-delta monthly, roll at 21 DTE…
- Idle line: your first backtest is free — no account, no card **[NEW]**
- Submitted (anon): human check — Turnstile · queued honestly — {n} runs ahead of you · trial run: daily clock · ≤3-year window **[PRD F]**
- Past anon budget: trials are busy — create a free account **[PRD F]**
- Preset chips: Weekly income put / short put · Calm-market condor / iron condor · Crash-insurance put / long put **[V]**
- Chart affordance: or pin examples on a chart — chart-teach → **[NEW]**
- Standing line: Research tool, not financial advice. **[V, shortened per owner]**
- Scroll cue: HOW IT ARGUES ↓ **[NEW]**

## Section 2 — How it argues
- Kicker: HOW IT ARGUES · H2: Three moves. No guesses. **[NEW]**
- 01 — YOU PITCH · english, or a chart **[NEW]**
  - "You showed me winners — that's what eyes do. I'll test every look-alike since 1993, losers included." **[V, REC 2 @ 0:12; "1993" → VERIFY]**
- 02 — IT INTERVIEWS · ambiguity earns a question **[NEW]**
  - "Here's what I heard — every dial is adjustable." **[V]**
  - QUESTION 1 OF 1 — I DON'T GUESS **[V pattern]**
  - Q: Two exits could apply at 21 days — take whichever hits first, or profit target only? **[V]**
  - Options: whichever hits first / profit target only / time exit only **[V]**
  - Answered state: ✓ spec compiled — 10 dials, every one adjustable · window: last 1 year · 220 sessions · ~3s **[V]** · ↺ ask me again
- 03 — IT ATTACKS ITSELF · then the verdict **[NEW]**
  - Stages **[V, REC 1 @ 0:27]**: Backtest — real bid/ask, never mid · Test on data it never saw — last 30% kept hidden · Test each time period — rolling ~2-month windows · Reshuffle the trades 1,000× — how much was luck? · Nudge the settings — does it survive small changes? · The honest verdict — grounded in the numbers above
  - Footnote: live numbers stream while it runs — never a loading bar **[V pattern]**

## Section 3 — The verdict
- Kicker: MOST BACKTESTERS TELL YOU WHAT YOU WANT TO HEAR **[brief]**
- H2: Refusal is a feature. **[NEW]**
- Body: Nine fills in a year isn't evidence — it's anecdotes. Skeptic ships the numbers, withholds the blessing, and shows exactly what unlocks a verdict. **[NEW]**
- Refusal block **[FIX — owner pins run id]**:
  - VERDICT — THE HONEST READ / VERDICT WITHHELD **[V pattern]**
  - H: Nine trades isn't evidence. This strategy barely fired in the window you picked. **[NEW]**
  - Body: The spec is valid. The engine ran it. But the entry fired 9 times in a 1-year window — below the 15-trade bar. Numbers shown, blessing withheld. **[NEW, matches app refusal register]**
  - TWO HONEST WAYS TO A VERDICT: re-run on last 3 years · 711 sessions · ~11s **[V]** / re-run on all available · 3,480 sessions · ~55s **[V]** / edit the spec — make the entry fire more often **[NEW]**
  - Meta: QQQ 0DTE fade · 9 trades in the last-1-year window · ran Jul 12 '26 **[FIX]** · read the full run →
- THE RANGE — RECENT VERDICTS:
  - 16Δ SPY condor 45 DTE — "Survived every attack. Small edge, honestly earned." **[FIX]**
  - SPY .30Δ short put — "84% of profit came from 13 lucky days — the rest is noise." **[V, REAL RUN, REC 1 @ 1:05 — owner supplies run id]**
  - QQQ 0DTE fade — "Not enough evidence to grade. Unblessed by design." **[FIX]**
  - Footer: refused, damned, or blessed — whatever the evidence supports. the library sorts by trust, not by return. **[V pattern]**

## Section 4 — Receipts
- Kicker: RECEIPTS · right: every fallback disclosed · gaps stay gaps, never interpolated **[V]**
- H2: The data earns the verdicts. **[NEW]**
- Tiles: 3,480 SESSIONS ON TAP **[V]** · 0DTE MINUTE-LEVEL FILLS **[brief]** · bid/ask NEVER MID (slip 0.85/0.9 · $0.65/ct) **[V]** · 2009 CHAINS SINCE **[VERIFY]** · 379 DAYS COLLECTING **[live counter at build]**
- Chips: SPY chains Jan '20 → now · QQQ/IWM chains Jul '26 → now · minute bars Feb '24 → now **[app home — render live]**
- Line: nightly lake · through Jul 15, 19:10 **[V]** · asymmetric on purpose →

## Section 5 — Coming next
- Kicker: WHERE THIS GOES — COMING SOON · right: ● autonomous session — replayed
- H2: Coming next: it takes the trade itself. **[NEW]**
- Body: Enters, manages, exits — and narrates every call as it makes it. Still refuses what the evidence can't support. **[NEW]**
- Chart header: SPY · 5m · demo feed · fills from the stored run
- Signal cards **[numbers V from stored demo run trade log]**:
  - ENTER — SELL THE 30Δ PUT: Premium is rich. Case built, order placed — on its own. · credit $2.31 · Mar 4
  - CLOSE — TARGET HIT: 50% profit. Bought back; the edge is spent. · +$115 · held 14d
  - STAND ASIDE: Spread is 31% of mid — a fill here would be fantasy. · skipped · Apr 8
- Reasoning rail (loop order): Mar 4 · scanning the tape — hunting rich premium at 30Δ / Mar 4 · SOLD 1× SPY 512P @ 2.31 (bid 2.29 / ask 2.35) / why · target 50% · time exit 21 DTE · theta does the work / Mar 18 · BOUGHT BACK @ 1.12 — target hit · +$115 · held 14d / Apr 8 · next entry looks juicy — but the spread is 31% of mid. standing aside. / edge spent. every call logged + replayable — no human touched this trade.
- Under panel: it entered, managed, and exited on its own — reasoning logged, replayable · no dates, no waitlist theater

## Section 6 — Pricing
- Kicker: PRICING · H2: That's the whole price list. **[PRD]**
- 1 / free backtest, right now / no account, no card — the composer up top is live
- 5 / free with an account / your anonymous run comes with you — first result kept **[PRD claim flow]**
- $10 → 50 / one-time, fifty more / no subscription, no tiers, no sales call
- Honesty: "Not enough evidence" refunds the credit — you only spend on a graded verdict. **[OWNER OVERRIDE Jul 16 — reverses PRD E, which charged for refusals; L2 credit semantics must change to match]**
- same if a run fails on our end — the credit comes back on its own **[PRD E]**
- CTAs: Run your free backtest ↑ (scrolls to composer) · Create an account — 5 free

## Footer
- The backtester that argues with you. Pitch a trade; get the honest read — refusals included. **[NEW]**
- day 379 of collection → **[live counter]**
- PRODUCT: How it argues / The verdict block / Data coverage / Pricing — $10, once
- LEGAL: Terms of Service / Privacy policy / Refund policy **[owner supplies text — launch blockers, PRD H]**
- Theme row: market hours follows the clock — light 8am–6pm ET, dark after the close · control: light / dark / market hours (default market hours)
- © 2026 Skeptic · skeptic.fyi · every number on this page is computed — none decorative
- Removed per owner (Jul 16): the standing disclaimer. PRD H marks it load-bearing on landing — open decision.

## Post-run conversion (results screen, not on landing)
- Keep this run + get 5 free backtests — create an account. **[PRD]**
