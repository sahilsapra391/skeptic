# DoltHub community options archive — backfill evaluation

*Evaluated 2026-07-01 against `docs/DATA-PIPELINE.md` §4 (canonical schema) and
§6 (quality thresholds), per the DECIDED block of 2026-07-01 (Yahoo-forward
record, DoltHub as backfill candidate). Read-only evaluation over the DoltHub
SQL API; nothing was imported to R2 and no data files are committed.*

**Dataset:** `post-no-preference/options` on dolthub.com
(tables `option_chain`, `volatility_history`), evaluated at commit
`7459qoplv92chull8mc0n7dds8sbpmar` (2026-07-01, "volatility_history
2026-06-30 update").

---

## Verdict: GO — scoped to SPY, 2020-01-06 → 2026-06-30

Adopt as the **SPY-only historical backfill**, subject to the ingest
conditions in §7. The data is real, clean, and honest about what it is:
EOD quotes with IV and greeks that pass every §6 threshold with huge
headroom and track known market regimes convincingly.

It is **not** a general chain archive. QQQ and IWM are absent entirely,
snapshots are Mon/Wed/Fri-only before 2024-09, and each snapshot carries
only ~3 expirations (~14 / ~28 / one of 44–66 DTE) with strikes ~±30% of
spot. Backtests on this history are therefore **checkpoint-marked, not
daily-marked** — a limitation the coverage layer and every verdict must
disclose (guardrail #6), not a data-quality defect.

QQQ/IWM options history remains impossible for $0: it begins 2026-07-01
with our own Yahoo record, full stop.

---

## 1. Provenance, freshness, license

- Maintainer `post-no-preference` (post.no.preference@protonmail.com);
  automated commits every collection morning ~06:34 UTC titled
  "option_chain YYYY-MM-DD update" — data is stamped to the prior trading
  session. Current through 2026-06-30 (yesterday) at evaluation time.
- Cadence history observed in the data: one stray snapshot 2019-05-10;
  Mon/Wed/Fri from 2020-01-06; daily from ~2024-09.
- **License: CC BY-SA 4.0** (repo `LICENSE.md`). Compatible with
  personal-use research. Attribution note belongs in our internal
  data-sources documentation. Redistribution is already forbidden
  project-wide; ShareAlike therefore never triggers.
- The database spans a large symbol universe (full clone impractical and
  unnecessary — see §6 ingest sketch; the SPY slice is ~166k rows total).

## 2. Schema (as found)

```
option_chain                             volatility_history
  date          date      PK              date        date    PK
  act_symbol    varchar   PK              act_symbol  varchar PK
  expiration    date      PK              hv_current / hv_week_ago / …
  strike        decimal(7,2) PK           iv_current / iv_week_ago / …
  call_put      varchar   PK  Call|Put    (+ 1y highs/lows with dates)
  bid, ask      decimal(7,2) nullable
  vol           decimal(5,4)  = IV (fraction, e.g. 0.2748)
  delta,gamma,theta,vega,rho decimal(5,4)
```

Not present anywhere: **last price, volume, open interest, underlying
spot, quote timestamp**. `volatility_history` is a per-(date,symbol)
IV/HV summary — useful as a weekly-quality cross-check input, not needed
for the backfill itself.

Operational note: the public SQL API times out (~50 s) on any full-table
scan. Every query must pin `date` (PK prefix) — point lookups and
`date IN (...)` lists return in ~1–4 s. This constrains ingest design but
costs nothing (§6).

## 3. Coverage vs the XNYS calendar

Probed **every weekday 2019-01-01 → 2026-06-30** (1,956 dates; 1,883 XNYS
sessions) for all three tickers.

| ticker | snapshot sessions | range | notes |
|---|---|---|---|
| SPY | 1,116 valid (+53 holiday phantoms, +1 stray 2019 date) | 2020-01-06 → 2026-06-30 | ~166k rows total |
| QQQ | **0** | — | absent from the archive |
| IWM | **0** | — | absent from the archive |

**Eras (SPY):**

| era | sessions with data | coverage |
|---|---|---|
| M/W/F era 2020-01 → 2024-08 | 670 of 693 XNYS Mon/Wed/Fri sessions | 96.7% of M/W/F = **57.1% of all sessions** |
| daily era 2024-09 → 2026-06 | 445 of 457 XNYS sessions | 97.4% (ramp month Sep-2024 ragged; ~100% during 2025–2026) |

**Gaps.** M/W/F era misses 29 expected M/W/F sessions
(2020: 5, 2021: 8, 2022: 4, 2023: 7, 2024: 5); the only multi-session
outage is **2024-07-31 → 2024-08-09 (5 sessions), which spans the
2024-08-05 VIX-spike week — a known blind spot** worth a named flag in
coverage. Daily era misses 12 sessions (2024: 09-03, 09-05, 09-10, 10-28,
11-12; 2025: 08-26, 09-10; 2026: 02-19, 02-20, 05-01, 05-13, 05-14).

**Phantom sessions.** 53 snapshots are dated on NYSE holidays (MLK, Good
Friday, Memorial, July 4th, Labor, Thanksgiving, Christmas, New Year,
Juneteenth, and the 2025-01-09 national day of mourning). Verified
byte-identical duplicates of the prior session (2026-06-19 ≡ 2026-06-18,
212/212 rows) — the scraper runs on its own schedule and re-stamps stale
quotes. **Ingest must drop non-XNYS dates.**

## 4. What each snapshot contains (chain structure)

The archive is a *filtered* chain, per snapshot:

- **Expirations: 3 slots (4 on some 2026 dates)** — the expiration nearest
  ~14 DTE, nearest ~28 DTE, and one in the **44–66 DTE** band (usually the
  next monthly; the occupant of the third slot varies snapshot-to-snapshot).
  Across all 1,116 sessions: min DTE never below 10, max never above 66.
  No weeklies below ~14 DTE, no LEAPS, nothing beyond 66 DTE.
- **Consequence — quoting is checkpoint-based per contract.** Verified on
  exp 2022-08-19 (monthly): quoted continuously 65→44 DTE (third slot),
  then only at ~28 DTE and ~14 DTE, then never again before expiry.
  Weeklies (e.g. 2022-07-13) appear **only** near the 28- and 14-DTE marks.
  There are no marks between checkpoints and none inside ~10 DTE.
- **Strikes:** ~70%–130% of spot (2020 slightly narrower, ~80–120%);
  ~20–35 strikes per expiration, steps ~$1–5 near ATM widening to $10–35
  at the wings. Rows/session: ~62 (2020) → ~130–160 (2021-25) → ~212 (2026).

## 5. Quality vs DATA-PIPELINE §6

Aggregates computed on **every** SPY snapshot session (1,169 incl.
phantoms), not a sample:

| year | sessions | rows/sess | dead-quote % (flag >20%/sess) | crossed % (flag >1%/sess) | null IV % | null greeks % |
|---|---|---|---|---|---|---|
| 2020 | 151 | 131 | 0.00 | 0.000 | 0.00 | 0.00 |
| 2021 | 151 | 145 | 0.16 | 0.000 | 0.00 | 0.00 |
| 2022 | 151 | 147 | 0.00 | 0.000 | 0.00 | 0.00 |
| 2023 | 151 | 136 | 0.00 | 0.000 | 0.00 | 0.00 |
| 2024 | 182 | 114 | 0.00 | 0.000 | 0.00 | 0.00 |
| 2025 | 258 | 134 | 0.10 | 0.000 | 0.00 | 0.00 |
| 2026 | 124 | 210 | 0.00 | 0.000 | 0.00 | 0.00 |

- **Dead quotes** (null/zero bid AND ask): 2 sessions of 1,169 breach the
  20% flag — 2021-03-03 (26.1%) and 2025-03-26 (23.1%). Isolated scrape
  glitches; flag-and-keep or drop those two sessions at ingest.
- **Crossed markets** (bid > ask): **zero rows in the entire 165,874-row
  SPY history.** No session comes near the 1% flag.
- IV and all five greeks are populated on every row.

**Plausibility (row-level, 12 sessions / 1,646 rows spanning 2020-01 →
2026-06):** zero greeks sign/range violations (call Δ∈[0,1], put Δ∈[−1,0],
γ,ν ≥ 0); ATM |Δ| ≈ 0.46–0.58 everywhere; put-call parity residuals of the
implied forward within **±0.7%** (mostly ±0.3%) using era T-bill rates and
1.4% dividend yield; ATM call spreads $0.02–0.66 (widest in the 2020
crash, as it should be).

| regime spot-check | ATM IV found | expectation | |
|---|---|---|---|
| 2020-03-18 (crash, VIX ~85) | 0.84 / 0.74 / 0.69 by exp | extreme, backwardated | ✓ |
| 2020-03-20 (SPY 228.80) | 0.71 / 0.60 / 0.57 | extreme, easing | ✓ |
| 2022-06-17 (bear low) | ~0.28 | elevated | ✓ |
| 2023-06-16 / 2024-06-21 (calm) | ~0.11 / ~0.10–0.12 | low teens | ✓ |
| 2025-06-20 | ~0.16–0.17 | mid-teens | ✓ |

## 6. Transform to the canonical schema (§4)

| canonical | from | notes |
|---|---|---|
| ticker | `act_symbol` | 'SPY' only |
| trading_date | `date` | **after** XNYS filter (drops the 53 phantoms) |
| snapshot_ts | derived | XNYS session close (UTC) for `date`; vendor capture time unknown — EOD stamp, documented as such |
| expiration / strike | `expiration` / `strike` | decimal → float |
| dte | computed | `expiration − trading_date` |
| right | `call_put` | `Call`/`Put` → `call`/`put` |
| bid / ask | `bid` / `ask` | as-is; nullable |
| last, volume, open_interest | — | **NULL — not in source.** No OI/volume liquidity filters on backfill history |
| iv | `vol` | fraction, verified against regimes |
| delta…rho | vendor columns | pass sanity; keep |
| greeks_source | constant | **`vendor`** — the handoff assumption that greeks would need computing is wrong for this source; recomputing would discard vendor info and add rate/div assumptions. Weekly quality job should BS-recompute from `vol` on a sample and flag drift |
| spot | join | from our `underlying/ticker=SPY/daily.parquet` (1993→) on trading_date — same pattern BUILD-LOG records for AV chains; refuse rows that fail the join |
| source | constant | `dolthub` |

**Ingest mechanics.** No dolt clone needed: batched SQL-API pulls
(`date IN (…5 dates…)` × full rows, ~1k rows/response ≈ 225 requests at
~1/s) fetch the whole 166k-row slice in ~15 min, one-shot. New collector
subcommand `--mode dolthub-backfill`: pull → drop non-XNYS dates → drop
(or flag) the 2 dead-quote-breach sessions → duplicate-guard (drop any
session byte-identical to its predecessor) → normalize per the table
above → parquet → `options/source=dolthub/ticker=SPY/date=…/chain.parquet`.
Idempotent by date key; record the dolt commit hash + row counts in
`state/dolthub_backfill.json`. Bounded at 2026-06-30: the forward record
is the Yahoo leg (DECIDED block), so the sources never contend. Query-time
precedence extends to `alphavantage > yahoo > dolthub`. Size: ~3–6 MB
parquet total — noise inside the R2 free tier.

## 7. Conditions attached to the GO

1. **Ingest filters:** XNYS-calendar filter (phantoms), duplicate-guard,
   dead-quote-breach sessions flagged in coverage metadata.
2. **Spot joined from our own dailies;** no row lands without spot.
3. **greeks_source='vendor'** + weekly sampled Black-Scholes cross-check.
4. **Reproducibility:** pin and log the dolt commit hash with the ingest run.
5. **Honest coverage (guardrail #6):** the coverage surface must show
   dolthub sessions as their own source with era granularity (M/W/F vs
   daily), and any verdict computed on pre-2024-09 history must carry a
   "checkpoint marks (M/W/F EOD), not daily marks" methodology note.
6. **Engine/parser gating:** strategies whose rules need marks the grid
   cannot provide — option-price stop-losses between checkpoints,
   manage-at-21-DTE exits (nearest marks: ~28/~14), anything <10 DTE or
   >66 DTE, strikes beyond ±30% of spot — must be refused or trust-capped
   for the dolthub era, never silently approximated (guardrails #3/#5).
7. **Legal rails:** personal use; no redistribution; attribution note;
   data to R2 only, never git.

**What this history supports well:** 28-ish/14-ish DTE entry cycles (CSPs,
covered calls, verticals, iron condors), 44–66 DTE entries managed at the
28/14 checkpoints or held to expiration (settlement from underlying
closes), IV-regime-conditioned entries — across COVID crash, 2021 melt-up,
2022 bear, 2023–24 bull, 2025 vol events: a genuinely multi-regime SPY
sample (guardrail #5 satisfiable). **What it cannot support:** QQQ/IWM
(nothing exists before 2026-07-01 at $0), daily-marked equity curves
pre-2024-09, sub-10-DTE styles (already out of scope per §7 of
DATA-PIPELINE), long-dated (>66 DTE) structures.

## 8. If adopted (owner checklist)

- [ ] Amend DATA-PIPELINE.md: DECIDED block → resolved; add `dolthub` to
      §1 sources, §3 layout, §4 precedence; fold §7 conditions into §6.
- [ ] Implement `collector --mode dolthub-backfill` (one-shot) + tests.
- [ ] Extend `/api/data/coverage` + Observatory with per-source eras and
      the 2024-08-05 blind-spot flag.
- [ ] BUILD-LOG entry on execution.

---

## Appendix: methodology & reproduction

Evaluation ran entirely over the public SQL API
(`https://www.dolthub.com/api/v1alpha1/post-no-preference/options/master?q=…`),
read-only, from scratch scripts (not committed; queries below reproduce
everything). Full-table scans exceed the API's ~50 s deadline, so all
queries pin `date` values (PK prefix seeks, ~1–4 s per 15-date batch).

- **Coverage sweep:** every weekday 2019-01-01→2026-06-30 (1,956 dates,
  131 batched queries), per-(date,symbol) aggregates for SPY/QQQ/IWM:
  ```sql
  SELECT date, act_symbol, COUNT(*) n,
    SUM(CASE WHEN (bid IS NULL OR bid=0) AND (ask IS NULL OR ask=0)
        THEN 1 ELSE 0 END) dead,
    SUM(CASE WHEN bid IS NOT NULL AND ask IS NOT NULL AND bid > ask
        THEN 1 ELSE 0 END) crossed,
    SUM(CASE WHEN vol IS NULL THEN 1 ELSE 0 END) null_iv,
    COUNT(DISTINCT expiration) n_exp, MIN(expiration) min_exp,
    MAX(expiration) max_exp, MIN(strike) min_k, MAX(strike) max_k
  FROM option_chain
  WHERE date IN ('2024-01-05', …) AND act_symbol IN ('SPY','QQQ','IWM')
  GROUP BY date, act_symbol;
  ```
  Calendar reference: `exchange_calendars` XNYS sessions; SPY closes for
  strike-band/parity work from yfinance (matches our own dailies source).
- **Row-level sample:** full chains for 2020-01-17, 2020-03-18/20,
  2021-03-10, 2022-06-15/17, 2022-09-30, 2023-06-16, 2024-06-21,
  2025-06-20, 2026-06-18/19/30 (split by `call_put` per pull).
- **Checkpoint-quoting proof:** presence of `expiration='2022-08-19'`
  (monthly) and `'2022-07-13'` (weekly) across all snapshot dates
  2022-06-13→2022-08-19.
- **Phantom proof:** row-set equality of 2026-06-19 (Juneteenth, market
  closed) vs 2026-06-18.
- **Parity check:** implied forward `F = K + (C_mid − P_mid)·e^{rT}` vs
  `S·e^{(r−q)T}`, era 3M T-bill rates, q=1.4%.

*Not financial advice; backtests overstate live performance. This
evaluation authorizes nothing by itself — ingest happens only after the
owner accepts the conditions in §7.*
