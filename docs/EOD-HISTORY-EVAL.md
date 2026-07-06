# QQQ/IWM EOD chain history — decision memo (D4 memo 1)

*Written 2026-07-06 (ENGINE-V3 D4 data-ops track). Every number below was
measured against the live lake or the collector's own Actions logs this
week; vendor pricing checked 2026-07-06. This memo asks for ONE owner
decision (§6). It deliberately does NOT decide the iVolatility trial
question — that is framed in §5 and belongs to its own deadline
(~2026-07-10).*

---

## 1. The problem, measured

Per-source EOD chain sessions in the lake (probed 2026-07-05):

| ticker | dolthub | yahoo (0–60 DTE) | alphavantage | ivol 5-min intraday |
|---|---|---|---|---|
| SPY | 1,115 (2020-01-06 → 2026-06-30) | 3 (2026-07-01 →) | 0 | 2,978 (2013-01-03 →) |
| QQQ | 0 | 3 (2026-07-01 →) | 0 | 0 |
| IWM | 0 | 3 (2026-07-01 →) | 0 | 61 (2026-04-07 →) |

- **QQQ/IWM daily backtests are impossible today** (3 sessions each). The
  D3d priorities pass ranks both as structural wants; the honesty layer
  correctly refuses anything asked of them.
- The DoltHub archive that rescued SPY has **no QQQ/IWM at all**
  (docs/DOLTHUB-EVAL.md), carries only ~3 expirations per snapshot
  (~14/~28/44–66 DTE — the "no <11 DTE" gap the D3c receipts work
  measured), and is Mon/Wed/Fri-granular before 2024-09.
- Yahoo accumulates all three tickers forward at 0–60 DTE, full chains,
  free — ~21 sessions/month/ticker. Left alone, QQQ/IWM reach an
  honest multi-year daily lake around **2028**.

## 2. What history would buy, in engine terms

The gauntlet needs ≥15 closed trades, ≥50% window coverage, and ≥2
volatility regimes before it blesses anything. A 2020→now backfill gives
QQQ/IWM ~1,640 sessions spanning the COVID crash, the 2021 melt-up, the
2022 bear, and 2023–25 — multiple VIX regimes, real DSR/walk-forward
folds. A 2008→2019 extension (SPY too: DoltHub starts at 2020) adds the
GFC, 2011, 2015, and Volmageddon 2018. Regime diversity is exactly what
the trust ladder is starved of on these tickers.

## 3. Options and costs

| option | cost | what it delivers | verdict |
|---|---|---|---|
| **A. Alpha Vantage premium, ONE month** | **~$50 once** ($49.99, 75 req/min tier) | `HISTORICAL_OPTIONS`: full chains + IV + greeks, any date ≥ 2008-01-02. QQQ+IWM 2020→now ≈ 3,280 requests ≈ one afternoon; all three tickers to the 2008 floor ≈ 14,000 requests ≈ a weekend of drip | **recommended** |
| B. Yahoo forward only | $0 | honest multi-year QQQ/IWM lake ~2028 | the default if A is declined — nothing breaks, verdicts stay refused |
| C. iVolatility Lab tier | $399/mo | bulk EOD endpoints (currently 403 on the trial tariff; support unanswered) | not an EOD play at this price; see §5 |
| D. iVolatility FTP | "from $500" | deep history incl. 1-min snapshots | luxury path; revisit only if a research need demands pre-2008 or vendor-grade lineage |
| E. Databento CBBO-1m | usage-priced ($125 credits) | minute NBBO 2013+ | wrong shape for EOD chains; candidate for QQQ/IWM *intraday* depth later |

**On the 2026-07-01 "out of budget" decision:** that decision rejected AV
as a *recurring subscription* for the forward record, and it stands —
Yahoo remains the source of record. This memo asks a different question:
one paid month, run the backfill, cancel. The forward pipeline is
untouched either way; the collector's AV leg is already built, dormant,
and premium-detecting (verified firing in the 2026-07-03 Actions log).

**Tier question RESOLVED (live probe, 2026-07-06, our own free key):**
`HISTORICAL_OPTIONS` returns the generic gate — *"You may subscribe to
**any** of the premium plans … to instantly unlock all premium
endpoints"* — while `REALTIME_OPTIONS` (which we don't need) carries an
explicit 600/1200-tier gate. AV's own API therefore confirms the
**$49.99 tier unlocks the historical chains**. The day-1 probe in §4
stays as belt-and-braces.

**Same probe session, for the record:** four options endpoints are FREE
on our existing key — realtime + historical `PUT_CALL_RATIO` (real
values verified back to 2008-06-16) and realtime + historical
`VOLUME_OPEN_INTEREST_RATIO`. Aggregates, not chains — no substitute for
the backfill, but a zero-cost sentiment/regime enrichment candidate for
a future spec-v3 discussion. Free-tier limits observed live: 25
requests/day, ~5/minute.

## 4. Execution plan if approved (one paid month)

1. Subscribe at $49.99; set `ALPHAVANTAGE_API_KEY` repo secret.
2. **Probe first**: one `HISTORICAL_OPTIONS` request (SPY, a 2021 date).
   Gated → step up or cancel; nothing else proceeds.
3. **Quality gate before trust** (the DoltHub precedent): pull ~20 SPY
   dates that overlap DoltHub 2020→now, run the §6-style aggregate
   cross-check (spread sanity, IV/greeks presence, spot-vs-strike
   coherence). The loader precedence (`av` beats `dolthub`) already
   prefers AV rows — that stays ONLY if the cross-check passes;
   otherwise flip precedence for overlapping dates in a reviewed PR.
4. Bump the collector's drip for the month (`AV_DAILY_BUDGET`,
   `AV_PACING_SECONDS` — one reviewed PR; free-tier values restore on
   cancel). Run `--mode backfill`: priority QQQ → IWM 2020→now, then
   all-ticker depth to the 2008 floor with whatever the month allows.
   The frontier state is crash-safe; partial progress is banked.
5. Cancel. Yahoo continues as the forward record. Coverage ledger,
   Observatory, and the priorities pass pick the new depth up
   automatically — that is what D3 was for.

## 5. The iVolatility ~Jul-10 decision, framed (not decided here)

The EOD answer above is **independent** of the iVol trial. What the
trial's end actually stops (facts, current as of 2026-07-05):

- the 5-min NBBO capture: **SPY is banked to 2013-01-03 (2,978 sessions)
  and safe in R2**; IWM freezes at ~61 sessions; QQQ never starts.
- forward 5-min coverage falls to `cboe_minute` (~15-min delayed, real
  quotes, already the designed forward source in the fill hierarchy).
- IVX/HV/IVS forward observations stop → `ivx_rank_1y` and the
  vol-regime honesty inputs go stale for NEW dates (history 2005+ is
  banked).

The $399/mo Lab question is therefore: *forward true-NBBO 5-min + live
vol analytics for three tickers* — a product-quality decision, not a
data-gap emergency. Nothing in this memo needs it answered first.

## 6. The ask

**Approve or decline: one Alpha Vantage premium month (~$50, cancel
after backfill), executed per §4.** Decline is safe: option B is the
status quo and the honesty layer keeps refusing what it cannot support.

> DECIDED (owner, ____-__-__): ______________________________________
