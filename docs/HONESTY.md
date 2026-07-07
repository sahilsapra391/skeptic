# HONESTY.md — how the gauntlet scales across clocks

Written at D2d (2026-07-05), per the ENGINE-V3 brief: "walk-forward and
Monte Carlo parameters rescaled for higher fill counts (document the
scaling rules in HONESTY.md)." Every rule here is a REVIEWED decision —
none of it changes at runtime (standing guardrail).

## The one decision everything else follows from

**Equity is marked once per session close at every clock** (owner decision,
2026-07-05). Positions trade at 5-minute bars; the equity SERIES stays
daily. Consequence: every statistic defined on the equity curve keeps its
exact daily semantics at the 5-minute clock — nothing is silently rescaled.

| Stage | Input | 5-min scaling |
|---|---|---|
| Sharpe / Sortino / CAGR | daily equity returns | **unchanged** (√252 annualization still correct — the series is daily) |
| IS/OOS split (70/30) | daily equity + closed trades | **unchanged** |
| Walk-forward (~42-session folds) | daily equity | **unchanged**; folds simply contain more trades, which makes fold returns LESS noisy, not differently defined |
| Monte Carlo (block=5 on per-trade P/L) | closed trades | **unchanged**: blocks are TRADE-level; a 0/1DTE run's larger trade count (~2,000 vs ~90) means resampling is better-conditioned, and clustering within a week is still captured by 5-trade blocks (≈ a week of 1DTE trades) |
| DSR | daily equity returns + trial count | **unchanged** |
| Regime guard (VIX buckets, MIN_TRADES=15) | daily dates + trades | **unchanged** |
| Coverage guard | sessions with usable quotes / requested | **unchanged** — at 5min "usable quotes" means an intraday slice for the session |

The thing that DOES scale is cost, and it is bounded by measurement, not
hope:

## The sensitivity sweep at the 5-minute clock

Measured (D2b benchmark, Apple Silicon dev box; Railway is slower):

```
full-history 5-min run (2,252 sessions, no conditions):   136 s
same with intraday conditions (after the D2c O(n²) fix):  ~57 s / 627 sessions
±20% sweep = ~20 engine re-runs on full history:          45–70 min
```

45–70 minutes of synchronous sweep is not a viable gauntlet on the
production box. Decision (owner amendment 6, decided on these numbers):

**At clock="5min", every sweep cell — including the base cell — re-runs on
the trailing 252 covered sessions of the requested window**
(`SENS_5MIN_WINDOW_SESSIONS` in `app/honesty/stages.py`). Rationale:

- parameter fragility is a LOCAL property of the strategy's neighborhood;
  a cliff does not need nine years to reveal itself;
- all cells share the window, so plateau/cliff comparisons are exact;
- the headline verdict still comes from the FULL-history run — only the
  ±20% probes are windowed;
- the window is disclosed in the report (`sensitivity.window_note`) and in
  the verdict caveats. Cost: ~20 × ~23 s ≈ 8 min dev, ~20–25 min Railway.

Revisit trigger: if D3's calibration infrastructure adds an async job
runner, the sweep can move back to full history as a background pass.

## The entry-time nudge (5-min clock only)

"An edge that only exists at exactly one minute of the day is noise"
(brief). The sweep gains a fifth row, `entry_time`, shifting the session's
entry WINDOW by −30/−15/0/+15/+30 minutes and re-running (same subsample
window as the parameter sweep):

- positive shifts delay entries past the session start / `time_of_day`;
- negative shifts require a `time_of_day` gate to move earlier — signal-
  driven entries cannot honestly enter BEFORE their signal, so those cells
  are None, never fabricated;
- classification is the same plateau/cliff rule as every parameter, and a
  cliff here makes the sensitivity verdict a cliff (it IS a parameter —
  the one nobody admits to tuning).

## The session-regime split (reported, never scored)

Entries bucket by their bar time — open (09:30–10:29), mid (10:30–14:59),
close (15:00+) ET — with per-bucket trades, wins, and P/L
(`report.session_split`). A strategy whose whole edge is one hour of the
day should have to say so out loud. It does not move the trust level.

## Modeled quotes (fill source `alpaca_modeled`)

QQQ/IWM intraday (and any session without a quote record) is served by
trade prints + a modeled spread: mid = the contract's last print within 5
minutes (stale prints never masquerade as quotes), half-spread = the
ticker's OWN median EOD spread fraction over the slice band.

- Modeled fills ALWAYS pay the full adverse modeled price (stress
  slippage), entry and exit alike.
- Every modeled fill is counted (`fill_sources`) and the verdict carries a
  caveat with the exact share.
- **There is deliberately NO trust cap on the modeled share in D2.** The
  owner excluded synthetic EOD-interpolated quotes outright; for
  bar-modeled quotes a cap threshold would itself be a guess today. D3's
  calibration loop (daily fills vs intraday fills on overlapping windows)
  is designed to EARN that threshold from evidence; until then the share
  is disclosed, loudly, and the reader judges.

## Auto re-runs and the trial counter (D3b)

The deflated Sharpe counts TRIALS — every human attempt at a strategy
family is a bite at the multiple-testing apple. An AUTOMATIC re-run of a
refused verdict (the auto-unlock queue) is the SAME spec on more data: no
new parameter choice was made, so it does NOT bump the family's trial
counter — it inherits the parent run's count (owner decision, 2026-07-05).
Human-initiated runs always bump, including a human clicking re-run on the
same spec. Auto re-runs are capped per night (`AUTO_RERUNS_PER_NIGHT`) and
every one is labeled `origin=auto_unlock` in the runs table and
"re-ran automatically" in the Library — provenance is never blurred.

## Verdict receipts (D3c)

Every eligible daily verdict eventually faces its own 5-minute replay
("the daily backtest promised X; the 5-min replay says Y"). Rules:

- Eligible = a user-initiated daily run whose WHOLE tenor band fits the
  intraday slice (max_dte ≤ 2) — both clocks must trade the same
  contracts, or the receipt is an apples-to-oranges comparison dressed up
  as like-for-like. The nightly drain replays ALL eligible runs, but
  OFF-PEAK (≈02:00 ET) and SERIALIZED — one at a time, polled to
  completion, fixed delay between submissions — so it can never collide
  with live work. An on-demand "replay at 5-min" button does the same for
  one run, immediately.
- Replays are `origin=receipt` runs: like auto-unlocks they do NOT bump
  the family trial counter (a mechanical replay of the same spec is not a
  new try).
- "Disagrees" = the replay Sharpe lands more than RECEIPT_WORSE_DELTA
  (0.25) below the daily promise, or flips sign. A disagreeing receipt is
  displayed PROMINENTLY and lowers the SHOWN confidence — but the stored
  verdict's trust level is NEVER rewritten (owner amendment): the original
  remains the honest record of what the daily engine concluded from its
  data. Reporting treatment only, never retroactive scoring.
- Lake reality (measured 2026-07-05): the historical EOD chains (dolthub,
  2020→now) carry NO expirations under ~11 DTE, so an eligible daily run
  fills zero trades against today's history — its receipt is one-sided
  ("the daily engine had no data; the 5-min record says Y") and one-sided
  receipts never accuse (`worse` stays false when either side is unknown).
  The Yahoo EOD capture (0–60 DTE, running since 2026-07-01) closes this
  gap mechanically: as short-dated daily history accumulates, the same
  drain starts producing real two-sided receipts, no code change needed.

## Fill-model calibration (D3d)

The daily fill model's parameters change ONLY through reviewed PRs — the
weekly calibration pass (`scripts/calibrate_fill_model.py`) produces the
evidence and, when the measured divergence clears the documented bar,
opens a PROPOSAL PR editing the real defaults (`app/models/spec.py` + the
schema — owner amendment 2, no config indirection). Nothing changes until
a human merges it.

- Measurement: for every contract-date in BOTH the EOD chain record and
  the intraday slice's closing NBBO (ivol_5min sessions, last ≥15:45 ET
  bar), how many TRUE half-spreads does the daily model's fill concede
  from the TRUE closing mid? The model intends f (= the slippage default);
  stale or wide EOD marks push the measured median off f.
- Asymmetric evidence bar (owner amendment 3): a CONSERVATIVE correction
  (daily fills measured cheaper than reality → raise the default) opens at
  the base bar (n ≥ 500 contract-day sides, divergence ≥ 0.25). An
  OPTIMISM-INCREASING correction (daily fills measured more punitive →
  lower the default) needs n ≥ 2,000 and divergence ≥ 0.50, and the PR
  title carries the `OPTIMISM-INCREASING:` prefix — never a silent nudge
  toward rosier numbers.
- Evidence: `docs/calibration/YYYY-MM-DD.md` (in the proposal PR) and
  aggregate stats in `state/calibration_latest.json`. Distribution stats
  only — chain rows never leave the lake.
- Lake reality: contract overlap begins with the Yahoo 0–60 DTE capture
  (2026-07-01) — see the receipts section above. Until n clears the floor
  the weekly doc honestly says "insufficient evidence, no change."

## Scale-in martingale defenses (D5c — the interlock, lifted)

A scale-in ladder that adds size into a losing position is a MARTINGALE —
the most ruin-prone structure in retail options, and exactly the thing
Skeptic exists not to be fooled by. D5a shipped the primitive behind a blanket
INTERLOCK (every ladder hard-capped at `insufficient_evidence`, "scale-in
safety checks pending (D5c)") so a blessable-but-undefended martingale was
structurally impossible in the interim. D5c REPLACES that interlock with two
real, strategy-specific defenses — each a HARD cap in `compute_trust` (a
`scale_in` object the gauntlet passes, the same mechanism as every other cap).
A ladder that trips either is refused; one that clears both is now judged like
any strategy and CAN be blessed.

- **Ruin-tail Monte Carlo.** Baskets add into losers, so the danger is a fat
  drawdown tail even when the average is fine. `scale_in_honesty` resamples the
  basket P&L sequence (seeded block bootstrap, same as the main MC) and, with
  the account's starting capital as the first peak, measures the max-drawdown
  distribution. It HARD-caps when `P(resampled max drawdown > 30%) ≥ 10%`
  (`RUIN_DRAW_THRESHOLD` / `RUIN_TAIL_PROB`, reviewed constants). One lucky deep
  win in a sea of ruinous baskets → most resampled orderings draw down hard →
  refused.
- **Deep-rung dependency.** Removing the deepest rung's fills (cheap — subtract
  their recorded marginals, no re-run) and recomputing the total: if a POSITIVE
  edge flips negative without the deepest, riskiest adds, the edge DEPENDS on
  them — a martingale sign-flip → HARD cap. When the deepest rung merely moves
  the total materially without flipping the sign, that is REPORTED, not capped.
- **Basket-size concentration.** One deep-basket day dominating the P&L is the
  martingale tell. A per-basket concentration (share of gross |basket P&L| from
  the top basket) is REPORTED (never a cap on its own — the D1d posture); the
  session-level concentration check already accounts for basket size on real
  runs, since an intraday basket closes same-session.
- **Adds are not trades.** A basket is ONE position that emits one terminal
  `CLOSE` with a P&L; rung adds are `ADD` events (never counted in `filled`).
  So the sample counter counts BASKETS, not fills — a ladder cannot inflate its
  way to 15 "trades" by adding more rungs (a lone ladder built from four rung
  fills is still one closed trade, and still sample-capped).
- **Not a data unlock.** A martingale refusal is a strategy property, not thin
  data, so `unlock_conditions` returns None for it — the D3b auto-unlock scan
  must not re-run and re-refuse it forever.

The tests prove one story end to end: a martingale-overfit ladder (one lucky
deep reversal in a sea of ruinous ones, ≥15 baskets across two vol regimes so
it is NOT sample-capped) is refused with BOTH defenses firing (realized
+$1,299 flips to −$486 without the deepest rung; 25% of resampled orderings
draw down > 30%), while a clean ladder that clears both grades to a real level.

## Ladder depth attribution (D5b)

The point of running a martingale honestly is to make its risk VISIBLE, the
way iVolatility's P&L-by-ladder-depth table did. `ladder_depth_attribution`
(present on every ladder run) reports two tied-out views of the realized P&L:

- **Per-tier table:** baskets grouped by the MAX rung depth they reached
  (their basket-size tier) — count, win rate, total/avg P&L, and share of
  gross profit vs gross loss. This is iVol's table: which depth made or lost.
- **Marginal-rung analysis:** the P&L attributable to the contracts added AT
  each rung depth — the question that kills or saves a martingale (are the
  deep, riskiest adds themselves net negative?). Because the whole basket
  exits at ONE price, a fill's marginal P&L is
  `(exit − fill_price)·qty·MULT − 2·commission·qty`, and a basket's marginals
  sum exactly to its realized P&L. So the per-tier totals AND the per-rung
  marginals each tie out to the same `realized_total` (tested to the cent).
- **In the verdict + panel:** the verdict MUST reference depth whenever a
  ladder ran ("the deepest adds are net −$X — the edge is not in the deep
  rungs"), grounded from the stage numbers; it rides in the caveats so it
  surfaces even while the interlock withholds the verdict. The results panel
  shows the marginal-rung bars + the tier table. P/L red/green is allowed
  there (it is a DATA panel), never on the trust/verdict surfaces.

## Conventions the numbers depend on (fixed, tested)

- Exit priority at every clock: stop_loss → delta_stop → profit_target →
  theta_harvest → time_exit → condition exits (dual-clock fixture).
- `exit.close_at_time` (5-min clock only) force-flats every open position at
  the first bar ≥ its ET time — "no overnight", symmetric with entry
  `time_of_day`; it overrides the priority order at/after its bar.
- A scale-in basket accumulates into ONE blended-cost position; its exits use
  the SAME profit_pct math as any position (blended premium reduces it to
  value/cost − 1). Rung adds fill at the CURRENT bar's ask (canary-guarded).
- Entries fill at the DECISION bar; exits evaluate from the NEXT bar.
- DTE is calendar days at clock="daily", TRADING days at clock="5min".
- 5-min indicators read a fixed 1,200-bar lookback
  (`INTRADAY_LOOKBACK_BARS`, 3× the schema's max period) — the charting
  convention, and what keeps a full-history run O(n).
- VWAP is session-anchored, last × per-bar volume (the vendor's cumulative
  volume column is diffed at load); the record carries no intra-bar H/L.
- Daily-history indicators at an intraday bar read ≤ the PREVIOUS session's
  close — today's daily close does not exist at 10:15.

## F0 (ENGINE-V4): new-source point-in-time + the resolution ledger

The V4 data spine adds Unusual Whales, Massive, and iVolatility-IVS readers
(app/data/uw.py, massive.py, ivol_analytics.py) and a per-session resolution
map (app/data/resolution.py, built by collector/ledger.py). Rules:

- **Row-level truncation.** UW per-session files carry intraday timestamps
  (market_tide 5-min buckets, spot_exposures ~minute rows). A reader at
  as_of 10:35 returns only rows stamped ≤ 10:35 — filtering by file date
  alone is NOT point-in-time for intraday data. Rows with no recognizable
  observation timestamp are visible at a date-level view and honestly EMPTY
  at an intra-session moment (a same-day observation cannot be assumed to
  exist mid-session).
- **Session-level observations are end-of-day facts.** UW series rows,
  Massive daily aggregates and IVS surface fits for session D do not exist
  at 10:35 on D: a datetime as_of EXCLUDES the as_of session in those
  readers (same rule as "today's daily close does not exist at 10:15").
  A bare-date as_of is the end-of-day view and includes it.
- **Timezone honesty (fail closed).** A stamp with no timezone reference
  (naive wall clock) could mean ET or UTC; localizing it by assumption can
  hide most of a session of lookahead. Offset-less stamps are treated as
  unobservable at intra-session moments — dropped, never guessed
  (app/data/pit.py, value-level offset detection). The session bound of a
  datetime as_of derives from its UTC-normalized moment, never the
  caller's local calendar date.
- **`captured_at` is metadata, never observation time.** Rows predating our
  capture start carry the vendor's own historical timestamps; we trust them
  as observation time the same way we trust dolthub/iVol dating, and say so
  here rather than pretending we verified the vendor's clock.
- **Absent → unavailable.** Every reader returns None when the lake has
  nothing; no zeros, no interpolation, no guesses. Requests beyond as_of
  raise LookaheadError (canary-tested per source, plus a deliberately
  lookahead "evil reader" test that proves the assertions bite).
- **Clock vs quote (owner decision 2026-07-07).** UW 1-min bars are
  side-attributed TRADE CANDLES with no NBBO. Minute data therefore upgrades
  the DECISION CLOCK (when the engine may look) and validates fills; the
  fill price always comes from the NBBO hierarchy. The resolution map
  records both facts independently per session:
  `clock_resolution` (minute > five_min > none) and `quote_resolution`
  by QUALITY precedence (ivol_5min > cboe_2min > eod_only > none — real
  NBBO outranks the finer-but-delayed recorder, D2 amendment 1).
- **The map is rebuilt, never hand-pinned.** collector/ledger.py rebuilds
  state/resolution_map/ticker={T}.parquet after every collection run
  (nightly workflow + manual), so newly-banked sessions and finer data
  upgrade eligibility automatically; the backend only reads the artifact
  (TTL-cached, O(1) per-session lookups — never a live lake probe in a hot
  path). A re-run whose result changes because a session's resolution
  improved must be explained as a RESOLUTION UPGRADE (wired in FX.4 via the
  D3 receipts loop).
- **Massive is never a fill source.** OHLCV aggregates only (no bid/ask):
  coverage and volume cross-checks. Its contract directory carries no
  listing timestamps and is exposed as non-point-in-time REFERENCE metadata
  only — simulation code must never derive contract existence from it.

## FX.1 (ENGINE-V4): per-session resolution — the finest honest clock

spec v4 adds `backtest.resolution: "finest"` (intraday clock only; absent ≡
"5min" exactly, bit-identical). Rules:

- **Resolution is selected PER SESSION** from the collector-built resolution
  map (an O(1) artifact lookup at run start — never a live lake probe): the
  minute grid where UW minute data AND the bars_1m 1-min underlying exist,
  else the 5-min grid. The chosen resolution of every covered session is
  recorded on the run (`resolution_mix` + compressed `resolution_runs`) —
  the receipts loop must explain a re-run that changed because minute data
  newly arrived as a RESOLUTION UPGRADE, never a silent shift.
- **Minute bars refine when the engine LOOKS, never what it can fill.**
  Option quotes stay the 5-min NBBO stamps; a minute bar between stamps
  serves no chain and can fill nothing (skip `no_chain_data`, logged). True
  minute-level quotes remain the paid-tape upgrade path.
- **timeframe-"5min" indicators mean ONE thing at every session.** On a
  minute grid the rolling indicator series samples ONLY the 5-min
  underlying frame's stamps — the same artifact, values, and session
  bounds (incl. its 16:00+ tail) the 5-min grid reads; bars_1m rows are
  PRICE-ONLY refinement between stamps and never enter the series or move
  session VWAP (review-hardened; fixture- and store-glue-pinned). A
  stamp-only strategy therefore produces IDENTICAL results at both
  resolutions — behavioral differences arrive only with FX.2 (armed
  entries) and FX.3 (latched stops).
- **Honest degrade.** Map pending / grid unbuildable → the session runs
  5-min and is RECORDED as five_min; the next nightly ledger rebuild
  upgrades eligibility automatically. bars_1m rows carrying stale
  prior-session prints (the vendor repeats the last print) are dropped —
  a Friday print is not a Monday price.
- **Reachability.** "finest" is engine/API vocabulary only until FX.5's
  parser unlock (owner re-ACCEPT gate) and FX.4's verdict disclosure; no
  user-facing surface can produce a finest run before its disclosure
  exists.

## FX.2 (ENGINE-V4): continuous opportunity scanning — maximum honest fills

spec v4 adds `entry.intraday_scan: "every_setup"` (intraday clock only;
absent ≡ "once_per_session" ≡ the D2 one-entry-per-session behavior,
bit-identical; mutually exclusive with the scale-in ladder). Rules:

- **A setup is an EPISODE, not a persistent state.** Entry conditions
  transitioning false→true arm exactly one entry; a signal that stays true
  for an hour is ONE opportunity, never a burst to the concurrency cap.
  Distinct episodes each fill, bounded by max_concurrent_positions and
  capital. An episode that fires at the cap is consumed (skip counted) —
  never queued or re-armed on the same dip. Intraday exits free their slot
  for later episodes (same-session round trips).
- **Condition-less strategies cycle on the position lifecycle.** With no
  signal to arm on, the position CLOSING is the re-arm trigger (plus the
  window start) — the always-in-the-market premium seller re-enters at the
  next quoted bar after each exit, one position lifecycle at a time.
- **Armed orders (owner decision 2026-07-07).** A signal that fires at a
  bar with no usable chain (a quote-gap stamp, or a minute bar between
  NBBO stamps) ARMS an order that fills at the NEXT quoted bar's real
  NBBO — even if the signal faded meanwhile, because a submitted order
  cannot be recalled when RSI ticks back before the fill prints. Honesty
  bounds: ONE-quoted-bar validity (the order never hunts across bars);
  liquidity gates still apply at the fill bar; the episode is consumed
  fill-or-skip; both bars are disclosed in the trade detail
  ("… · armed 09:40 · 09:45"); armed orders die at close_at_time and at
  the session end (no overnight orders). Skipping faded signals outright
  was rejected as dishonest in the OTHER direction — it systematically
  drops fills a live trader would have gotten.
- **Signal granularity is the indicator's, disclosed.** timeframe-"5min"
  indicators are sampled at 5-min stamps (FX.1 parity rule), so scanning
  edges occur at stamp granularity today; minute-bar-level triggers arrive
  with FX.3's price-touch exits and any future minute-native conditions —
  more data or finer grids never silently change where a signal can fire.
- **Every skip is counted, at the right granularity.** The run carries a
  skip-reason distribution (`skip_reasons`). EPISODE-level reasons count
  once per setup: `max_concurrent` (fired at the cap, consumed),
  `order_in_flight` (a fresh edge arrived while an order was armed — one
  working order at a time, so the second setup is a REAL missed
  opportunity, disclosed never absorbed), `no_quote_this_bar` (an armed
  order died unfilled at the session end or the flatten bar). WAITING at
  quote-less bars is not a skip — a filled armed order contributes no
  count. ATTEMPT-level reasons (conditions_not_met, illiquid_*,
  zero_bid_short, …) count per attempted bar, as they always have. The
  trade log stays deduped per session; the counts do not.
- **Bounded by construction (OOM-guard directive).** Scanning makes
  position count scale with bars, so every per-bar path iterates the LIVE
  book (open positions, swept O(open) per bar), never the full history;
  and a run that opens more than MAX_RUN_FILLS (20,000) positions is
  REFUSED loudly mid-run with a plain reason — never silently truncated,
  never an unbounded payload.

## FX.3 (ENGINE-V4): the intrabar-unknown rule + latched exits

Moves inside a bar are unobservable at EVERY resolution — finer data
shrinks the blind spot, never removes it. How the engine resolves that,
now formalized (owner decisions 2026-07-07):

- **The live-price side (finest mode; entries AND exits — one semantic).**
  On a minute grid, price-vs-indicator conditions compare the CURRENT
  bar's real underlying print against the indicator; the indicator SERIES
  stays stamp-sampled (the live price refines WHEN a touch is observable,
  never the indicator's defined cadence — minute jitter cannot manufacture
  RSI-type signals that don't exist at the indicator's resolution). On
  5-min grids this is bit-identical by construction: the current print at
  a stamp IS the sampled value. Rejecting the same touch for entries while
  honoring it for exits would be incoherent — the same bar's price cannot
  be real enough for a stop but not for an entry.
- **Latched exits (directional honesty).** An exit-condition trigger
  OBSERVED at a bar that cannot fill the close (a quote-less minute bar,
  or a quote-gap stamp) LATCHES on the position: the close completes at
  the first quoted bar that can fill it, WITHOUT re-evaluation and with
  no expiry — a seen touch counts, exactly as a real stop works (once
  triggered you're out at the next liquidity; no un-triggering on a
  bounce). Trigger and fill bars are both disclosed in the trade detail
  ("· triggered 09:41 · 09:45"), so the gap and any worse fill are
  visible. Gated to finest mode: fixed-5min runs keep their pre-FX.3
  retry-and-re-evaluate behavior bit-identically.
- **Why entries and exits latch DIFFERENTLY (the governing principle).**
  Entries have one-quoted-bar validity and can be liquidity-skipped;
  exits persist until fillable. Not a contradiction: entries and exits
  have opposite RISK POLARITY. A missed entry is neutral (not trading is
  safe); a missed exit is optimism (the dropped exit would have closed a
  position that keeps accruing outcomes you chose not to see). The right
  consistency is the same DIRECTION of honesty, which requires opposite
  validity mechanics.
- **Profit targets confirm at observed quotes, always.** PT (and stop_
  loss/delta/theta) evaluate from the position's real option quotes —
  profit can never be claimed from an underlying-only bar; there is no
  quote to prove it (guardrail #1's exit-side mirror).
- **The gap-bar tie.** Point-quote records make the classic OHLC
  "both stop and target inside one bar" ambiguity structurally absent —
  evaluation happens only at observed quote points, and the canonical
  priority (stop → delta_stop → PT → …, D2 amendment 3) resolves any
  residual same-quote tie toward the WORSE outcome.
- **The blind spot is pinned, not hidden.** The same minute touch that
  latches an exit on the minute grid is fixture-proven INVISIBLE at the
  5-min grid — the difference between resolutions is a documented,
  testable fact, feeding FX.4's mixed-resolution honesty.
