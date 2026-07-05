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

## Conventions the numbers depend on (fixed, tested)

- Exit priority at every clock: stop_loss → delta_stop → profit_target →
  theta_harvest → time_exit → condition exits (dual-clock fixture).
- Entries fill at the DECISION bar; exits evaluate from the NEXT bar.
- DTE is calendar days at clock="daily", TRADING days at clock="5min".
- 5-min indicators read a fixed 1,200-bar lookback
  (`INTRADAY_LOOKBACK_BARS`, 3× the schema's max period) — the charting
  convention, and what keeps a full-history run O(n).
- VWAP is session-anchored, last × per-bar volume (the vendor's cumulative
  volume column is diffed at load); the record carries no intra-bar H/L.
- Daily-history indicators at an intraday bar read ≤ the PREVIOUS session's
  close — today's daily close does not exist at 10:15.
