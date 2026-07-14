# AUDIT.md — Honest Parity Plan v2, Tier 0

2026-07-14 · Audit-before-building pass over the three v1 items the plan
marked "likely resolved by later fixtures — verify, don't rebuild", plus the
two dependency checks the sequencing needs (provenance Chunk A, iv_zscore).
Method: code + fixture reading, and read-only R2 probes of real deep-history
sessions (aggregates only, no chain rows logged).

## Verdicts

| # | Item | Verdict |
|---|------|---------|
| 1 | Short-leg bid/ask fill direction (D1b + FX fixtures) | **PARITY** |
| 2 | Per-leg capital (D5 basket bookkeeping) | **PARITY** |
| 3 | Expiration bridging + requested-vs-effective DTE, deep-history paths | **PARITY** (one small test gap noted) |
| 4 | Tier 3 pre-check: `iv_zscore` | **GAP** — does not exist |
| 5 | Tier 1 dependency: provenance Chunk A | **NOT STARTED** — Tier 1 stays blocked |

Only true GAPs proceed → the build list out of Tier 0 is exactly one small
chunk (iv_zscore, Tier 3) plus one test-only nicety (see item 3).

## 1 · Short-leg fill direction — PARITY

`app/engine/fills.py` is the single fill model: BUY = mid + slip·(ask − mid),
SELL = mid − slip·(mid − bid); `open_action(short) = "sell"` (toward bid),
`close_action(short) = "buy"` (toward ask). Zero-bid shorts and crossed
markets are skips (`quote_problem`), never fills.

Evidence, all hand-computed:
- `tests/test_fills.py` (12 tests): direction at slip 0.5 and 1.0 (= full
  adverse quote), plus the D3d side-aware base slips (buy 0.85 → 2.185,
  sell 0.90 → 2.01 on a 2.00/2.20 quote).
- `tests/fixtures/engine/fx_credit_spread_stop.py`: short leg entry 2.05
  (toward bid), long leg 1.075 (toward ask); the close reverses sides
  (btc 4.225, stc 1.85) — asserted to the cent through the full sim.
- Marking is liquidation-side with the same model (`common.py` conventions),
  so triggers, marks, and exit fills can never disagree on direction.

## 2 · Per-leg capital — PARITY

- Every leg's open fill is logged individually (`state.fill_log`: action,
  qty, price, expiration, source) with per-leg cash deltas and commission
  per contract per side; `OpenLeg` carries entry price/qty; position
  `cash_flow` accumulates and realized P/L is attached at finalize.
- Sizing risk is defined-risk per contract-set (`_risk_per_contract` by
  structure; long structures = debit; short put = strike − premium, i.e.
  cash-secured). There is deliberately no margin model — capital is cash
  accounting; this is a design stance, not missing bookkeeping.
- D5b is the iVol ladder-depth table and better: per-tier P&L AND
  marginal-rung attribution, hand-computed on fixture 1
  (−12.60 / +41.10 / +143.50 = +172.00) and tied out against the trade log
  on a 20-basket run (`tests/test_scale_in_depth.py`).

## 3 · Expiration bridging + requested-vs-effective DTE — PARITY

Mechanics (`app/engine/selection.py::select_expiration`): nearest listed
expiration to `target_dte` **within the user's [min_dte, max_dte] bounds**;
earlier expiry wins ties; no candidate in bounds → `no_expiration_in_window`
skip. The engine never bridges outside the user's own window, so the
requested-vs-effective deviation is bounded by the spec itself. Disclosure is
per-trade: every OPEN event names the effective expiration date, every fill
in `fill_log` carries it, and skips are counted (`skipReasons`) and listed in
the uncapped trade log.

Verified on the real lake (read-only probes, 2026-07-14):

| Session | Expirations (DTE) | 45-target [30,60] | 0–1 DTE request |
|---|---|---|---|
| QQQ 2010-06-15 | 4, 15, 32, 67, 95, 186, 221 | → effective **32** | honest skip |
| SPY 2015-06-16 | 10, 24, 31, 38, 45, 66, 186, 198 | → effective **45** | honest skip |
| IWM 2018-06-15 | 0, 7, 21, 42, 98, 189 | → effective **42** | fills (0 DTE listed) |
| QQQ 2009-10-12 | 250 only (14 rows) | honest skip | honest skip |
| SPY 2012-07-09 | 250 only (27 rows) | honest skip | honest skip |

Facts the plan should absorb (corrections, not gaps):
- **IWM deep history starts 2017-10-09, not 2009-10-12.** Only QQQ goes back
  to 2009-10-12 (4,201 sessions). SPY: ivolatility 2012-07-09+ (2,399),
  dolthub 2020-01-06+ (1,115).
- The earliest sessions of each ivolatility backfill are thin vendor
  artifacts (single ~250-DTE expiration); usable depth for short-tenor
  strategies starts later. Coverage honesty (guardrail #6) already surfaces
  this per run.
- Deep-history greeks are partial (delta null: QQQ 2010 ~12.5%, IWM 2018
  ~19.1%) — delta selection correctly restricts to quoted rows.
- dolthub rows carry NO open interest/volume (100% null) → liquidity floors
  never gate on those sessions (unknown is disclosed, never punished);
  ivolatility rows carry both (0% null) → gates active. Behavior differs by
  era/source exactly as the D1b rule intends.

Small gap worth a test-only follow-up: `select_expiration` had no direct
unit test (nearest-to-target, earlier-tie-break, bounds exclusion,
sparse-monthlies chain) — the FX fixtures exercise it only through rich
chains. Closed in the same PR as this audit: a dedicated block in
`tests/test_selection.py`, chain shapes taken from the probes above.

## 4 · iv_zscore — GAP (the one build item)

No `zscore`/`z_score` token existed anywhere in backend, schema, or
frontend at audit time. Closed in the same PR as this audit:
`ivx_zscore_1y` (spec v8) — the 30d IVX standardized within the trailing
252 observations, the σ-unit sibling of `ivx_rank_1y` with the same
126-obs floor, raw thresholds legal, PARSER RE-ACCEPT gate carried by
the PR.

## 5 · Provenance Chunk A — in flight, not merged

No prompt/Q&A/decision-grid snapshot exists at run creation on main;
`provenance` in code is F8's `data_provenance` (signal splicing), a
different thing. Chunk A is being built in a parallel session (worktree
`provenance-chunk-a`, branch `claude/provenance-chunk-a`). Tier 1
(notebook export) stays blocked on it per the plan's sequencing.

## Per-source chain coverage (probe, 2026-07-14)

| Source | SPY | QQQ | IWM |
|---|---|---|---|
| ivolatility | 2,399 dates, 2012-07-09+ | 4,201 dates, 2009-10-12+ | 2,192 dates, 2017-10-09+ |
| dolthub | 1,115 dates, 2020-01-06+ | 0 | 0 |
| cboe_eod | 7 dates, 2026-07-02+ | 7 | 7 |
| yahoo | 9 dates, 2026-07-01+ | 9 | 9 |
| alphavantage | 0 | 0 | 0 |
