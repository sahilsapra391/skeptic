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
  from the TRUE closing mid? The model intends each side's slippage default (buys 0.85 / sells 0.90 since 2026-07-13, D3d-earned);
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
  Completion edge cases (review-hardened, all disclosed): a latch carried
  OVERNIGHT or into a GAP session (no intraday slice) completes at the
  first real quotes available there — including the daily EOD-chain
  fallback D2 already allows for gap-session exits — and the trigger note
  is DATED when the fill lands on a later session ("triggered 2026-07-01
  09:41"). A latch first fillable at the close_at_time bar closes under
  its OWN reason, never misattributed to session_flat. A latch swallowed
  by same-session expiry is named on the settlement event ("pending
  condition_exit (triggered 09:41) superseded by settlement") — never
  silently dropped.
- **Crosses are stamp-anchored (review-hardened).** At an off-stamp
  minute bar, crosses_above/below evaluate (latest SAMPLED value → live
  print): a genuine inter-stamp cross fires exactly once, and a cross
  already resolved AT a stamp never re-fires on minute jitter.
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

## FX.4 (ENGINE-V4): mixed-resolution honesty — the gauntlet learns the mix

A finest-mode run can mix bar resolutions session by session. The rules
(owner decisions 2026-07-07):

- **The resolution split.** Every mixed run recomputes its headline on the
  5-MIN-ONLY sub-window from recorded per-session returns and closed
  trades — cheap, no re-run (stage `resolution_split`). The verdict
  caveats disclose the mix with grounded numbers ("minute grid for N
  sessions (first → last), 5-minute for M before that"), in both quant
  and retail voices.
- **A resolution sign-flip is a data-VALIDITY finding, not a robustness
  signal.** If the full-run edge is positive but the 5-min-only sub-window
  is negative, the edge appears only on the recent minute slice and
  reverses at the resolution the deep history was tested at — a
  granularity mirage until proven otherwise → HARD CAP to
  insufficient_evidence, refused, never weakly blessed. Contrast the OOS
  sign-flip: both windows there are measured at EQUAL resolution, so a
  flip is a robustness signal and earns a low level instead. Different
  findings, categorically different messages.
- **The cap fires only on real evidence.** Judged only when both subsets
  hold ≥ 15 sessions AND the 5-min subset holds ≥ MIN_TRADES (15) closed
  trades — the SAME evidentiary bar any main result must clear (it would
  be incoherent to refuse a main result under 15 trades yet let a
  9-trade sub-window deliver a verdict-flipping judgment). Below the
  floors: "too thin to cross-check", a disclosed caveat — never a
  noise-cap, never a silent pass. Only the OPTIMISTIC direction caps; a
  negative full run blesses nothing to protect.
- **Walk-forward folds disclose their minute share IN the run.** Each fold
  carries `minute_share`; minute-flavored folds are named in the caveats
  ("fold differences there can be resolution, not regime") and on the
  fold tooltips. The deeper fold-construction redesign (windows that never
  straddle the resolution boundary) is a FLAGGED future design pass — a
  substantial change to a pinned honesty stage that deserves its own
  reviewed chunk; disclosed-not-solved until then. Monte Carlo is
  unchanged: it resamples recorded per-session returns, and attribution
  belongs to the split stage.
- **Resolution upgrades are named, never silent.** When a receipt compares
  two runs that BOTH carry per-session resolution mixes and the mixes
  differ, the comparison carries "resolution changed between runs: minute
  sessions A → B … — differences may be a resolution upgrade, not a
  market change" (the FX.1 record powering the D3 receipts loop). A
  daily parent carries no mix — ordinary daily-vs-5min receipts stay
  silent (review-hardened: the note must never fire a false explanation).
- **Bucket `pl` vs `sharpe` can diverge for boundary-straddlers.** A
  position opened on a 5-min session and closed on a minute session
  accrues its P&L across BOTH subsets' marks, while its closed-trade
  count and realized `pl` land on the close day. The flip test is
  mark-to-market and internally consistent; the `trades` floor is an
  evidence count, not a P&L attribution.
- **High trade counts are never trust.** 0DTE scanning runs produce many
  trades; the regime/DSR/coverage machinery judges them unchanged — count
  clears floors, it never substitutes for regimes or robustness.

## FX.5 (ENGINE-V4): the parser unlock — vocabulary, never inference

spec v4 vocabulary is now parseable, under the standing rule that
intelligence lives in the parser's QUESTIONS, never in silent defaults:

- **`entry.intraday_scan "every_setup"`** maps from explicit continuous-
  scanning phrasing ("take every setup", "every time RSI dips", "re-enter
  after I take profit", "keep selling all day"); once-per-session phrasing
  leaves the field ABSENT. Never combined with a ladder — conflicting
  phrasing asks.
- **`backtest.resolution "finest"`** maps ONLY from explicit resolution
  language ("use the finest data", "minute-level where you have it").
  Resolution is a DATA POLICY, never inferred from strategy shape: a plain
  0DTE prompt gets no resolution field — the same spec must mean the same
  thing across time (owner decision 2026-07-07; reproducibility over
  helpfulness).
- **The exit-less 0DTE seller asks, offering the choices**: a force-flat
  time, a profit target, or hold-to-settlement (ITM = assignment, OTM =
  worthless). Suggest, never default (guardrail #3).
- **The dial round-trip preserves parser-only vocabulary.** Editing a
  pre-run dial rebuilds the spec WITH the parsed spec as base: ladders,
  intraday_scan, resolution, close_at_time and time_of_day all survive a
  dial edit (closing the D5d follow-up where an unrelated dial edit
  silently DROPPED the whole ladder — silent strategy corruption, the
  wrong-answer-no-error class). The client mirrors the server's
  vocabulary→version computation; the server remains the authority.
- **The 0DTE dial refusal is lifted**: DTE 0 on the spec screen now emits
  an intraday spec (clock 5min, DTE band 0–2, the intraday slice) instead
  of blocking the run — the minute engine milestone it was waiting on has
  shipped (FX.1–FX.4).

## F4 (ENGINE-V4): vol-surface signals — derive once, never at run time

Two spec-v5 indicators read the fitted IVS surface (2007+), through a
derived artifact rather than live surface reads:

- **`skew_25d`** — IV(25Δ put) − IV(25Δ call) at the 30d tenor, VOL
  POINTS. 25Δ is linearly interpolated in |delta| between the bracketing
  grid rows, calls and puts separately. Positive = puts rich.
- **`term_structure_slope`** — ATM IV(90d) − ATM IV(30d), vol points,
  from each tenor's exact ATM row (OTM% = 0). Negative = inverted.

Conventions are FIXED market standards (owner decision 2026-07-07): the
vocabulary maps to one unambiguous meaning; other tenors/deltas are not
parameterizable on spec — the parser asks. A raw `iv_surface_point`
accessor was considered and DEFERRED for its own design pass: it reopens
exactly that parameterization and would put surface reads in the run
path. "Variance risk premium" phrasing is an alias onto the existing
`hv_iv_spread_30d` (the same IV-minus-realized formula) — one
implementation per formula, because duplicates drift and a drift here is
a silent inconsistency in the product's own numbers.

Honesty rules the numbers depend on:

- **Derive once, nightly.** `collector/derive_ivs_signals.py` walks new
  surface sessions past a watermark and appends one row per session to
  `reference/derived/ivs_signals/ticker={T}.parquet`. The MATH lives in
  the backend (`app/data/ivs_signals.py`, imported by the collector) —
  one implementation, fixture-tested. Runs read the artifact O(1); no
  surface reads in the run path (post-OOM rule). New sessions flow in on
  the next collector pass — no redeploy (self-improvement thesis).
- **Fail closed at the grid edge.** A session whose surface lacks the
  tenor or the bracketing deltas derives NOTHING for that signal — never
  extrapolated, never interpolated across tenors. Missing rows make the
  condition unevaluable (False) that day, exactly like a thin IVX rank.
- **EOD fits obey the intraday boundary.** At intraday bars both signals
  read the PREVIOUS session's surface — today's fit doesn't exist at
  10:15 (guardrail #2), same rule as IVX/HV.
- **Vol points, stored once.** The artifact stores ×100 values; the
  condition compares them DIRECTLY ("skew above 5" → 5). A re-×100 here
  is the drift class the derive-once design kills; a fixture pins it.
- **Coverage is disclosed per signal.** The Observatory shows the derived
  window with separate skew/term session counts — a session can carry one
  signal and honestly lack the other (guardrail #6).

## F1 (ENGINE-V4): dealer positioning — sign and rank, never vendor units

Four spec-v6 indicators read Unusual Whales' daily EOD dealer
greek-exposure series (banked nightly; ~250 sessions from 2025-07-08,
deepening every night with no redeploy):

- **`gex_level`** — net dealer gamma (call_gamma + put_gamma, the
  vendor's sign convention). Positive = dealers long gamma. The sign IS
  the regime: "trade only when dealers are long gamma" compiles to
  `gex_level > 0` — there is no separate dealer_gamma_regime indicator,
  because a duplicate of a sign test is a drift surface.
- **`dex_level`** — net dealer delta, same convention.
- **`gex_rank_1y` / `dex_rank_1y`** — percentile of the current value
  within the trailing 252 observations, exactly the ivx_rank
  construction INCLUDING its floor (owner amendment): below 126 trailing
  observations the rank is unevaluable that day — False, never a
  thin-window percentile. The rank unlocks as the UW window accrues past
  the floor; no code change involved.

Honesty rules the numbers depend on:

- **Vendor units are opaque — vocabulary is sign + rank only.** The raw
  magnitudes (~10⁵–10⁶ today) are vendor-defined and could be rescaled
  upstream without notice; a raw threshold ("GEX above 5 billion") would
  then change meaning with no error — the silent-spec-corruption class.
  The parser refuses raw-unit thresholds and offers the sign form or a
  percentile instead. Comparisons against 0 and percentile ranks survive
  any rescale.
- **Pre-run signal-coverage refusal (owner decision 2026-07-07).** A run
  conditioned on this family whose window starts before the signal's
  first covered session is REFUSED before simulating, with the covered
  window offered back. The uncovered stretch would sit in forced flat
  cash — Sharpe over zero-variance years, diluted drawdowns, a long
  window as costume (the SEVENTEEN class). Prevention beats correction:
  the problem is detectable from spec + store alone, so no corrupted
  artifact is ever produced. Unconditioned specs are untouched, and the
  composer shows the bound on the window tile before submitting.
- **EOD observations obey the intraday boundary.** At intraday bars both
  signals read the PREVIOUS session (guardrail #2) — intraday
  spot_exposures cadence is a deliberately separate, later chunk
  (stale-but-true beats fresh-but-leaky).
- **gex_flip_distance was surveyed and DEFERRED, not shipped degraded.**
  The banked 50-strike EOD snapshot is structurally the wrong input for
  locating a zero-gamma point: ~35% of sessions have no sign transition
  at all, and wing-noise transitions produce absurd values (−69% of spot)
  that LOOK like dealer positioning while being illiquid-strike
  artifacts — honest to the artifact, dishonest to the concept. It earns
  its own design pass when deeper strike profiles or a vendor zero-gamma
  endpoint exist (paid tier).
- **Coverage is disclosed per signal** — the Observatory shows the
  banked window with per-signal session counts, and the pre-run refusal
  quotes the same first session it displays.

## F5 (ENGINE-V4): displayed depth — disclosed, never modeled (yet)

The iVol 5-min record carries the displayed NBBO size (contracts, both
sides) on every quoted bar. As of F5 every option-leg fill compares its
quantity against the traded side's displayed size (buy → ask, sell →
bid):

- **Prices are untouched.** Fills keep FX.2 semantics — real NBBO plus
  the configured slippage fraction. A fill larger than displayed depth
  is COUNTED (`fills_beyond_depth` / `fills_depth_known`), named in the
  trade log ("qty 20 > ask size 3"), and reported by the liquidity
  profile with a verdict caveat when nonzero. REPORTED, never scored —
  the session-regime-split rule.
- **Why not a model or a gate (owner decision 2026-07-07):** we hold L1
  depth only. A book-walk model on one level is invented structure; a
  hard gate refuses fills reality would likely give (hidden size,
  sweeps — the FX.2 "pessimistic in a way reality wouldn't be" test).
  A price-impact model must be EARNED the way the fill model itself
  was: through the D3d weekly calibration loop, with the asymmetric
  evidence bar for optimism-increasing changes.
- **Unknown stays unknown.** EOD chains and modeled/recorder quotes
  without sizes carry None — depth-unknown fills are excluded from the
  denominators, never guessed. Daily-clock output is bit-identical
  (pinned digests).
- **Settlements carry no depth** — cash settlement has no NBBO, so a
  settlement close honestly records nothing.
- **Massive cross-check DEFERRED to F7.** Correction (same day): the
  collector (collector/backfill_massive.py, free tier, 5 req/min) HAS
  banked contract universes for QQQ (157,310) and IWM (86,696) plus
  ~5.7K QQQ per-contract daily aggregates — the backfill stalled at
  ~3.6% because the full 2-year universe needs ~34 days at the free
  rate. Ramp strategy (prune/sample/paid month) is an owner ops
  decision. The masterplan's guardrail stands either way: Massive is
  OHLCV only, never a fill source.

## F2/F3 (ENGINE-V4): flow, sentiment & pin — five shipped, three refused

Five spec-v7 indicators from UW's per-session families (2026-02-24+,
nightly), reduced once per session by collector/derive_flow_signals.py
(set-difference incremental, self-healing):

- **`net_premium_level`** — session Σ net call premium − Σ net put
  premium (the tick rows are per-minute buckets — probe-pinned). Vendor
  DOLLARS → sign/rank vocabulary only.
- **`market_tide_level`** — the market-wide cumulative tide's session
  total (probe-pinned cumulative: last row, never the sum). The first
  MARKET-WIDE indicator: one series for all tickers. Sign/rank only.
- **`nope_level`** — the vendor's NOPE at the last session stamp.
  Dimensionless and published, but what we ingest is the VENDOR'S
  IMPLEMENTATION — sign/rank only (owner decision: sign and rank are
  invariant to monotone rescaling; raw thresholds are not, and ~91
  sessions cannot even verify scale stability — "precision theater").
- **`put_call_flow_ratio`** — session Σput/Σcall volume. Unit-free
  classic; raw thresholds legal ("above 1" → 1).
- **`max_pain_distance_pct`** — (front-expiry max pain − close)/close ×
  100, signed, where front = the nearest expiry STRICTLY AFTER the
  session. With daily expirations, "≥" would reference the expiry
  settling that day — retrospective at the EOD stamp and a ghost by the
  time next session's bars consume it (review finding, owner decision
  2026-07-08). The forward reference is by CALENDAR (tomorrow's expiry
  DATE, known today; computed from today's OI at today's close) — not
  forward-looking into DATA, which stays forbidden. Other tenors ask.
  Unit-free %; raw thresholds legal.

Refused, with reasons (owner decision — input quality per indicator,
never thematic family):

- **`oi_change_signal`** — the banked family is a vendor TOP-50 movers
  list, not a census: any aggregate measures vendor curation, not
  market-wide OI dynamics (the gex_flip class: looks like the concept,
  is the artifact). Waits for census-shaped data.
- **`oi_concentration` / `pin_risk`** — no market-standard formula
  exists; shipping one means WE author a definition and present it
  under a name traders think they understand — the invented-convention
  sin at formula scale. max_pain_distance_pct carries the pin thesis
  with vendor-defined input; these earn a design pass only when a real
  strategy needs them AND a definition can be defended from evidence.

Same coverage honesty as F1: pre-run refusal before the signal's first
covered session; *_rank_1y floors at 126 trailing observations (unlock
as UW data accrues); EOD series read the previous session at intraday
bars; unavailable ⇒ False, never a guess. Two derivation caveats,
disclosed: net_prem_ticks is the DRIVING family — a session whose nope
or max_pain lands only AFTER the row is written keeps None for those
columns (rows are never re-derived), so "self-healing" is exact for the
driving family and best-effort for the riders; and partial-NaN sessions
enter the sums as the non-NaN subset (all-NaN columns yield None — the
fabricated-zero path is pinned closed — but a half-null session is
presented as its readable half).

## F7 (ENGINE-V4): cross-source validation — rates with denominators

Independent vendors are compared nightly wherever they overlap
(collector/derive_cross_validation.py, set-difference incremental; the
comparator math is single-sourced in app/data/cross_validation.py):

- **dolthub_vs_alpaca** (SPY, ~527 sessions) — EOD closing quotes vs
  minute TRADES, the proven 2026-07-01 methodology: only near-close
  prints are checked (a stale deep-ITM print legitimately disagrees by
  delta × the move), each print delta-adjusted to the close, the
  session's capture spot self-calibrated from its own high-|delta|
  contracts.
- **dolthub_vs_uw** (SPY) — chain volume/OI per expiry vs UW's
  aggregates (OI band 5%, volume 10% — capture cutoffs differ). NOTE
  (real-lake 2026-07-08): the DoltHub archive carries the volume/OI
  columns but never POPULATES them (all-NaN across every session), so
  this pair currently derives checked=0 — honest absence, not a bug; it
  lights up automatically if a volume-bearing EOD source ever overlaps
  the UW window.
- **yahoo_vs_ivol5m** — EOD quote mids vs the last 5-min NBBO (grows
  nightly with the Yahoo capture).
- **massive_vs_ivol5m** (QQQ/IWM) — the daily vendor close must sit
  inside the day's quoted NBBO range (the F5-deferred cross-check;
  activates as the ATM-band crawl lands; Massive is never a fill source).
- **recorder_vs_uw_tape** (SPY/QQQ/IWM, tape-banked sessions only) —
  every UW full-tape print vs the recorder's displayed quote valid at
  that moment. The recorder's CBOE quotes lag the feed's publish stamp
  by the OPRA delayed-data standard — MEASURED at 15 minutes, not
  assumed (probe 2026-07-09: lag sweep on the 2026-07-08 SPY overlap,
  agreement 0.93 at 15 min vs ≤0.74 at every other lag) — so prints are
  sliced by `source_ts − 15 min` into fixed 60 s windows; a print on a
  two-sided contract must sit inside [bid − tol, ask + tol] (the
  standard band). Extras carry the violation DIRECTION (`below_bid` /
  `beyond_ask` — the displayed-quote calibration raw material for the
  D3d fill-model staging) and `tape_trades`, so a session where the
  recorder missed minutes shows how much tape it never saw (the
  2026-07-09 gap day is visibly partial, never silently complete).

Rules the numbers depend on (owner decisions 2026-07-08):

- **Per-pair agreement rates travel with their audited-share
  denominators — there is NO blended score.** Weights across
  incommensurable pairs would be an invented convention wearing a
  number, and this number's whole job is to be trusted. If a headline
  ever ships, it will be the MINIMUM pair rate with its pair named — a
  fact, not a blend.
- **Reported, never scored (v1).** No trust consequence until the
  accumulated distribution EARNS thresholds (the D3d staging; the one
  approved hard cap — FX.4's sign flip — had a binary falsification
  trigger and a borrowable floor; nothing like that exists here yet).
- **The per-run block** aggregates only the sessions inside the run's
  own window; pairs with no overlap are omitted — a run with nothing
  audited says nothing, never a fabricated 100%.
- **The fill audit is on-demand** (the replay-receipt mechanics): it
  re-runs the spec deterministically over the ORIGINAL effective window
  (an open-ended window would extend to today's lake and describe fills
  the run never made — review blocker, refused with a fill-count check
  when in-window lake drift changes the regeneration) and checks every
  regenerated option-leg fill against Alpaca minute trades. Independence
  has ONE exception, handled: `alpaca_modeled` fills were PRICED from
  those very prints — they are never audited (self-confirmation is not
  verification) and count in a disclosed `self_source` bucket. Per
  fill: within the traded range band (max($0.05, 2%)) in a ±15-minute
  window of the fill bar, degrading to the session range when no bar
  time exists (disclosed by kind). `no_trades` is honest absence, never
  counted against the run. The stored audit never rewrites the verdict.

## F8 (ENGINE-V4): the sensitivity sweep reaches the signal thresholds

The anti-overfitting sweep perturbed strike/DTE/profit-target/stop-loss
since M3 — but never the ENTRY-CONDITION thresholds. A strategy overfit
to exactly "RSI < 30" or "25Δ skew > 5" sailed through untested. F8
closes that: the sweep now perturbs entry-condition thresholds ±20% in
5 steps, re-running the real engine at each, classified plateau/cliff
like every other parameter. Because recommendations, the sensitivity
grid, and the verdict caveats all consume the sweep params generically,
weaving the SWEEP is the whole weave — a fragile signal threshold now
drags the verdict to "cliff", and a better neighbor becomes a grounded
recommendation.

Rules (owner decisions 2026-07-08):

- **Sign tests are skipped, disclosed.** A condition whose threshold is
  exactly 0 (gex_level > 0, market_tide > 0, term_structure_slope < 0,
  price-below-VWAP) is a REGIME SIGN test — the sign IS the signal,
  there is no "20% more than zero", and the vendor units are the ones
  we deliberately refused to let users state. Perturbing them would be
  the invented-convention sin committed by the honesty tool itself. The
  report names them ("gex_level is a sign test, not a swept threshold")
  so absence is never misread as a free pass. The RANK forms
  (gex_rank_1y etc.) ARE real 0-100 thresholds and sweep normally once
  they clear the 126-observation floor.
- **Small thresholds sweep an absolute family-scale grid (2026-07-14,
  PR #97 review follow-up).** ±20% of a small threshold on a wide
  natural scale probes almost nothing — "ivx_zscore_1y > 0.3" would
  sweep a 0.12σ band of a ±3σ scale, and five near-identical Sharpes
  read as a FALSE PLATEAU: the classifier blessing exactly the fragile
  threshold the sweep exists to catch. When the multiplicative step
  (10% of |threshold| per cell) falls under the indicator family's
  floor, the sweep switches to an absolute grid of ±2 floor-steps
  around the specced value, shifted up by whole steps at a bounded
  family's lower edge (the dte whole-day guard's pattern — the specced
  value always stays ON the grid). ONE rule grounds every floor:
  floor = 10% of the family's stated reference magnitude, so a small
  threshold is probed exactly as widely as a reference-scale one
  already is — never finer. References: 0-100 ranks/oscillators → 20
  (the bottom-quintile edge); z-scores → 2.5σ (outer edge of the ±3σ
  usable band); vol points → 5 (this doc's own "skew > 5" example);
  percent-of-price → 2.5%; drawdown → 10%; VIX-style levels → 20 (the
  regime line regime_sample already draws); flow ratio → 1.0 (parity).
  SMA/EMA keep pure ±20% (percent-of-price IS the scale of an absolute
  price level), and the vendor-unit *_level families get NO invented
  floor — their raw thresholds are parser-refused, and fabricating an
  absolute step for units we refused to let users state would be the
  invented-convention sin ourselves. The multiple-testing tax is
  untouched: same 5 cells, same classifier, identical engine-run
  count, and the sweep never re-centers on a better neighbor — a
  better neighbor stays a recommendation that re-enters the gauntlet
  as a NEW trial. Disclosed per condition in conditions_note
  ("skew_25d > 0.5 swept -0.5…1.5 — absolute family-scale steps …"):
  the operator + specced value attribute the grid when one indicator
  appears twice (the max-pain band pair), and every numeral is the
  specced value or a grid ENDPOINT, so verdict grounding (guardrail
  #4) always finds them in the report. A threshold AT or BELOW a
  bounded family's lower edge (e.g. a negative RSI — schema-legal,
  scale-nonsensical) keeps the pre-floor multiplicative sweep: the
  floor was grounded on the family's scale, and shifting a grid past
  the specced value would take the as-specced cell off the grid
  (review finding, regression-tested).
- **Small deltas sweep absolute 0.025Δ steps (2026-07-14, the PR #99
  review's deferred finding).** The same under-probing class on the
  STRIKE grid: ±20% of a 0.05Δ strike selection sweeps 0.04…0.06 in
  0.005Δ cells, and on a discrete chain a cell that small usually
  cannot change the selected contract — Black-Scholes puts one
  strike's worth of delta at |dΔ/dK| = φ(d1)/(K·σ√T), which at the 5Δ
  wing of an SPY-scale chain is ≈0.4–2 delta points per $1 of strike
  spacing across 45→1 DTE (and 5× that on a $5 grid) — so adjacent
  cells resolve to the SAME strike, five near-identical Sharpes read
  as a false plateau, and the sweep blesses exactly the fragile
  lottery-ticket archetype (5Δ tail selling) it exists to catch. At
  the sweep's own 0.03 probe floor the collapse was literal: base 0.03
  clamped three cells identical. Below 0.25Δ the sweep now steps an
  absolute 0.025Δ grid (±2 steps around the specced delta, shifted up
  whole steps off the 0.03 edge — the dte/_condition_grid pattern, the
  specced value always ON the grid at a recorded base_index). The
  floor obeys the SAME grounding rule as the condition floors — 10% of
  the family's reference magnitude, delta's reference being the 25Δ
  wing (the convention this repo already encodes as skew_25d) — and
  the strike-granularity arithmetic above independently lands on the
  same number: 0.025Δ per cell clears "at least one strike per cell"
  at every realistic chain geometry, from fine $1 grids at weekly
  tenors to $5 grids at monthlies. Deltas at or above 0.25 keep pure
  ±20% (their multiplicative step already meets the floor, clamps
  byte-identical); a base below the 0.03 probe floor is outside the
  scale the floor was grounded on and keeps the pre-floor clamped
  path — DISCLOSED, never silent: its cells collapse onto the floor,
  and delta_note says so ("swept cells clamp at 0.03, so smaller
  strikes were not probed"). The multiple-testing tax is untouched:
  same 5 cells, same classifier, no re-centering. (The engine-RUN
  count can rise by up to 2 on small deltas: the old clamped grid's
  duplicate cells deduped into fewer runs, and that dedup WAS the
  under-probing being fixed.) Disclosed in Sensitivity.delta_note
  ("delta 0.05 swept 0.05…0.15 — absolute delta-point steps …"),
  riding both verdict registers; every numeral is the on-grid specced
  value, a grid endpoint, or the probe floor (itself a grid cell on
  the below-floor path), so verdict grounding (guardrail #4) always
  finds them in the report's sweep values.
- **Capped at the first 3 entry conditions.** Each swept condition adds
  5 engine re-runs on the serialized, OOM-sensitive engine; 3 covers
  the overfit surface of nearly every real spec (secondary filters
  included — that's where curve-fitting hides), and the rest are
  disclosed ("N further conditions not swept (cost cap)"). A structural
  bound from a stated fact, not an invented threshold.
- **Entry conditions only in v1.** Exit-condition and scale-in rung
  thresholds are deferred-with-disclosure: rung perturbation entangles
  with the D5 basket cap logic, and reopening that at the finish line
  would bloat the last chunk. They earn their own pass if a real
  strategy demonstrates the need.

Bit-identity holds: the sweep is a gauntlet stage, not the engine run —
the daily-clock regression digests (which pin the RUN) are unchanged.

## The evidence bar is the user's dial; the disclosure is not (2026-07-14)

The minimum-trades floor for a graded verdict (guardrail #5) became a user
setting: **default 15, floor 1, never 0** (`Settings → Evidence bar`,
`BacktestRequest.min_trades`, clamped 1–10,000 both ends). Three rules keep
it honest:

- **The gate moves, the honesty doesn't.** A bar under 15 lets thin samples
  grade, but `payload._below_standard_note` rides EVERY graded verdict on a
  sub-15 sample, in both registers, appended at payload-build time so the
  LLM narration can never drop it. The single-volatility-regime cap is not
  configurable — it never was about trade count.
- **Saved runs re-grade at read time, both directions.** `GET
  /api/runs/{id}?min_trades=N` re-decides ONLY the evidence gate from the
  stored honesty report (`regrade_sample` + `rejudge_resolution` +
  `compute_trust` — the same functions the gauntlet ran, so the rule cannot
  fork): a 13-trade refusal unlocks when the viewer's bar drops to 1; a
  20-trade "graded" re-caps when the bar rises to 300. The re-graded view is
  template-narrated (the stored LLM words argued a different grade), carries
  a re-grade caveat + UI chip, and never mutates the stored row. When the
  gate outcome is unchanged the stored payload — LLM narration included —
  passes through untouched.
- **Automatic re-runs inherit their parent's bar.** Auto-unlock and receipt
  runs score at the bar the parent was refused at (`_inherit_min_trades`),
  so an unlock promise can't move its own goalposts; `UnlockConditions.
  trades.needs` records the run's own bar for the nightly scan.

The 5-min sub-window's evidentiary floor in the mixed-resolution defense
follows the same bar (the "same bar as any main result" coherence rule
above), recomputable at read time from the stored buckets.

## The verdict narration is an upgrade, not a gate (2026-07-14)

The "honest verdict" stage used to block run completion on the LLM
narration — two OpenRouter calls with up to 3 × 45 s validated retries, i.e.
2–5 minutes of a finished gauntlet staring at the user. Now:

- `_run_and_store` ships the run `done` with the **deterministic template
  verdicts** (grounded by construction, same numbers) the moment the
  gauntlet ends; `payload.narrationPending` is true only when a narration
  key exists.
- `_narrate_and_patch` runs AFTER the engine lock releases (pure network
  I/O never holds the next queued run hostage), calls the same validated
  `write_verdicts`, and swaps ONLY the wording surfaces
  (`apply_verdict_text`) plus the library-card quotes. Numbers, trust
  geometry, and every data panel are byte-identical before and after.
- Any narration failure clears the flag and leaves the template standing —
  the same fallback contract as before, minus the wait.
- `perf.verdict_s` now records the BLOCKING verdict cost the user actually
  waits on (the pre-run time estimates stay honest); the measured narration
  time lands separately as `perf.narration_s`.

## Buying power and the ruin halt — capital is real (2026-07-15)

Before this change the engine's ledger was a single unchecked cash float:
entries never asked whether the account could fund them, a short-put
assignment debited `shares × strike` unconditionally, and the equity curve
ran to −$37,818 on a $21,000 account — fabricated trades of exactly the
class guardrail #1 exists to prevent (a fill the real world would refuse).
Two rules fix it, both engine-level so every consumer (gauntlet, sweeps,
walk-forward, exports) inherits them:

- **The buying-power gate.** An entry (or scale-in rung) that cannot be
  funded is SKIPPED with the named reason `insufficient_buying_power` —
  counted in `skip_reasons`, visible in the trade log, never resized.
  Debits must be covered by post-fill cash. Uncovered short options
  reserve the market-standard broker minimum (the "20% rule",
  FINRA/Reg-T-style initial requirement; CBOE margin manuals):
  `max(20%·spot − OTM, 10%·strike)` per share for puts,
  `max(20%·spot − OTM, 10%·spot)` for calls. Paired legs (spreads)
  reserve the strike width; an iron condor reserves the worse side only;
  a covered call's shares are its collateral. Premium credit is counted
  once, in cash — never inside the reserve. The formula is a **named,
  revisitable constant** (`app/engine/margin.py · RESERVE_MODE =
  "reg_t_20"`): broker requirements vary; changing the mode (e.g. to
  `cash_secured`) is a reviewed-session decision. The reserve is held
  from fill to FULL close (conservative on partially settled multi-leg
  positions), then released.
- **The ruin halt.** The first session whose end-of-session equity is
  ≤ $0 halts the simulation right there: a `HALT` event, every open
  position closed in the log at its mark (reason `ruin_halt`, cash
  untouched — the appended equity already is the mark), and the run
  carries `ruined / ruin_date / ruin_equity`. Everything downstream
  treats the truncation honestly: the coverage stage measures the window
  to the halt (`halted_at_ruin` — a ruin-shortened window is never
  blamed on data), Monte Carlo paths are **absorbed at zero** (a
  reshuffled path that crosses $0 ends there; `p_ruin` reports the share
  that die; drawdowns cap at 100%), the metrics drawdown caps at 100%,
  and trust hard-caps at the lowest band with the wipeout named — or, on
  an already-refused run, the refusal carries the ruin reason first.

**What is deliberately NOT modeled, and disclosed:** a real margin account
is margin-called and liquidated BEFORE equity reaches zero. Maintenance
thresholds are broker-specific and would be an invented constant, so the
halt fires at exactly $0 — which makes the reported ruin date the **latest
possible**, not the actual. A real account would likely have been
liquidated earlier. The verdict's ruin caveat says so in both registers
(the same disclose-what-isn't-modeled rule as modeled quotes and displayed
depth).

**The funding disclosure.** When a material share of otherwise-eligible
entries were skipped for buying power (`FUNDING_MATERIAL_SHARE = 0.20` of
fills + funding skips, minimum `FUNDING_MATERIAL_MIN = 3`, reviewed
thresholds like every constant in `stages.py`), the verdict must say so: a
strategy that only "works" if you could fund 3× your account isn't working
for the user running it. On ladders, unaffordable rungs are attributed in
the depth table (`LadderRung.unaffordable_baskets`) — a deep add the
account couldn't fund reads as *unaffordable*, never as merely
unprofitable. Buying power is reality's cap on martingale ladders;
`max_total_contracts` is only the user's.
