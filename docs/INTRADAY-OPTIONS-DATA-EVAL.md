# Intraday (1-minute) options history for QQQ/IWM — acquisition evaluation

*Evaluated 2026-07-01. Question from the owner: minute-by-minute options
pricing for QQQ and IWM, 5–10 years back (10 preferred, 5 worst-case),
free via API if possible, otherwise scraped free. Companion to
docs/DOLTHUB-EVAL.md (EOD backfill; that archive is EOD + SPY-only, so it
contributes nothing here).*

---

## Verdict

**Free + 5–10 years + minute granularity does not exist. Not from any
API, not by scraping.** US options quote history is OPRA-licensed vendor
data; nobody gives away deep minute history, and the past cannot be
scraped because no free surface displays it (verified empirically below —
Yahoo deletes contracts from its API at expiration).

What is actually attainable, verified today:

- **Free, straight API:** Alpaca's free plan serves historical option
  **1-min bars and quotes back to 2024-02** (their collection start) for
  any US-listed underlying incl. QQQ/IWM. That is ~2.4 years today and
  grows forward — the best free answer that exists, well short of 5.
- **Free, forward-only:** we can start *collecting* minute quotes today
  at $0 (CBOE's delayed-quote JSON returns the full QQQ chain — 10,606
  contracts with bid/ask/IV/greeks/OI — in one unauthenticated request;
  tested). History then accrues from now, like the Yahoo EOD leg.
- **The actual 5–10 year ask is cheap-but-paid:** ThetaData sells 1-min
  option quote history back to **2020-01 at $40/mo** (Value) and tick
  back to **2016-01 at $80/mo** (Standard). A single month, used to bulk
  download QQQ/IWM history once, is the cheapest realistic acquisition
  (~$40–80 one-time; their ToS on retaining data after cancellation needs
  reading before buying). Databento prices OPRA by usage back to 2013-04
  with a cost-preflight API and $125 signup credit.

Ongoing subscriptions all violate the locked ~$25/mo budget
(README-START-HERE Decisions #3 — the same rule that killed AV premium).
A one-time bootstrap month is an owner decision, not something this
evaluation can authorize.

## 1. Why there is no free path

OPRA is the consolidated tape for US listed options; vendors pay
exchange/OPRA fees and license the redistribution. The data is also
enormous — QQQ alone carries **10,606 live contracts today** (counted on
CBOE's feed), and a minute-quote history for one ETF chain runs to
billions of rows per decade. Free tiers therefore top out at EOD
granularity, shallow lookbacks, or delayed snapshots of *now*. Every
"free historical options data" trail ends at one of those three.

## 2. Provider matrix (checked 2026-07-01)

| provider | free tier gives | minute history depth | data type | 5–10y cost | verdict for this ask |
|---|---|---|---|---|---|
| **Alpaca** | options history API, free key | **2024-02 → now (~2.4y)** | 1-min bars + trades + quotes (recency >15 min on free feed — irrelevant for backtests) | $0 | best free option; adopt |
| **ThetaData** | EOD chains 2023-06 → now, 20 req/min | Value $40/mo: **1-min quotes+OHLC+OI to 2020-01**; Standard $80/mo: tick to 2016-01; Pro $160/mo: 2012-06 | quotes (NBBO) — exactly what the fill model needs | $40–80 one-time-ish (single month) or ongoing | cheapest route to the real ask; owner decision |
| **Databento** | $125 signup credit | OPRA.PILLAR: 1-min trade bars + 1-min consolidated BBO (`cbbo-1m`) **to 2013-04** (full MBP quotes only 2023-03→) | trades + sampled BBO | usage-priced; exact cost preflightable via their metadata API *before* paying | precise pay-per-pull alternative; get the quote first |
| Polygon (now "Massive") | EOD-oriented free tier, 5 req/min (site is JS-walled; verify at signup) | paid tiers to $199/mo | aggregates/quotes | subscription | structurally unusable free: per-contract endpoints × ~10k contracts × 5 req/min |
| FirstRateData | — | **options are EOD-only** (their 1-min granularity is stocks/ETFs) | EOD chains | ~$99/yr updates | eliminated for minute data |
| CBOE DataShop | — | custom historical orders to 2000s | official everything | cart-quoted, typically the expensive route | overkill |
| IBKR / Schwab / Tradier | account APIs | **no expired-contract history at all**; TOS thinkBack/OnDemand is in-platform replay, not exportable | — | — | dead end |
| Kaggle / HuggingFace / GitHub / DoltHub | $0 | no credible minute options chains exist (searched today; hits are all equity minute bars) | — | — | dead end; OPRA re-dumps would be license-violating and untrustworthy anyway |

## 3. Scraping assessment — what was actually tested

- **Yahoo per-contract chart API** (the only free surface with any
  intraday option bars): a live ATM QQQ call returned 1-min **trade**
  bars for a trailing ~week (6,162 minute slots, 1,027 filled — options
  trade sparsely); a contract that expired five days ago returns
  **HTTP 404**. Conclusion: the past is unscrapeable, and even forward
  harvesting yields trade prices without bid/ask — which cannot feed the
  engine's fill model (guardrail #1: fills come from bid/ask, never
  mid/last). Supplementary at best.
- **CBOE delayed-quote JSON** (`cdn.cboe.com/.../options/QQQ.json`): full
  chain, bid/ask/IV/all greeks/OI/volume, one request, ~15-min delay, no
  auth. Snapshot of *now* only — but as a forward collector it is
  strictly richer than the current yfinance leg. Tested and confirmed.
- **Broker platforms** (TOS thinkBack etc.): not pursued — scraping
  authenticated platforms breaches their terms, and none expose expired
  contract history programmatically anyway.

## 4. The two honest paths

**Path A — $0, adopt now (recommended default):**

1. **Alpaca backfill**: free key → pull 1-min bars + quotes for
   QQQ/IWM (+SPY) from 2024-02 → `options/source=alpaca/…` in R2.
2. **Forward minute collector** on the CBOE JSON (full chain incl.
   greeks/OI every minute or five). Infra reality: a 390-min/day loop is
   ~8,200 runner-minutes/month — private-repo GitHub Actions free tier is
   2,000/mo, so this leg runs on the home Mac (launchd) or a free Oracle
   VM, the same fallback DATA-PIPELINE §5 already names for Yahoo
   throttling. GH Actions stays the EOD scheduler only.
3. **ThetaData free tier** as an EOD bonus: QQQ/IWM EOD chains back to
   2023-06, which partially plugs the QQQ/IWM EOD hole DOLTHUB-EVAL left
   open (that archive is SPY-only).

Yield: minute history 2024-02→ (2.4y today, 5y in 2029-02), minute
*quotes* from switch-on day forward, QQQ/IWM EOD depth to 2023-06.

**Path B — paid one-time bootstrap (owner decision):** one month of
ThetaData Value ($40 → 1-min quotes to 2020-01, 6.5y) or Standard
($80 → 2016-01, 10.5y), bulk-download QQQ/IWM (+SPY while we're there),
cancel. Read their ToS on post-cancellation data retention *before*
paying. Alternative: Databento — preflight the exact cost of
`cbbo-1m`/`ohlcv-1m` for parent symbols `QQQ.OPT`+`IWM.OPT` 2016→2026;
$125 signup credit offsets; decide on the number, not a guess.

**Never:** scraping authenticated broker platforms, or ingesting
redistributed OPRA dumps. Both fail the project's legal rails, and dumps
fail integrity (unverifiable provenance) — the honesty layer cannot sit
on stolen, unauditable quotes.

## 5. Storage and engine impact (either path)

- Full-chain minute quotes ≈ 6M rows/day for the pair (QQQ ~10.6k + IWM
  ~5k contracts × 390 min) → ~7–15 GB/yr compressed → **busts the 10 GB
  R2 free tier inside a year.** Ingest must filter (e.g. DTE ≤ 60,
  moneyness ±20% → ~1–2 GB/yr) and/or sample at 5-min cadence. Decide the
  filter *before* the collector starts, or we pay migration tax later.
- Minute backtests need quote-based fills; trade-bar-only sources
  (Yahoo harvest, Databento `ohlcv-1m` alone) cannot satisfy guardrail
  #1. Point-in-time rules are unchanged: `snapshot_ts` = true capture
  time; session boundaries/half-days via the XNYS schedule.
- 0DTE/1DTE research (the owner's live style, currently refused per
  DATA-PIPELINE §7) becomes *possible* exactly where minute data exists:
  2024-02→ free, 2020→ / 2016→ paid. The refusal message can eventually
  say "insufficient intraday coverage before YYYY-MM" instead of a flat no.

## 6. Recommendation

Adopt **Path A now** — it is $0, starts the clock, and is the only way
the free constraint and the minute constraint coexist. Hold **Path B**
as a single-purchase decision for the owner: $40–80 once buys the 5–10
year history outright if the wait is unacceptable. Do not take any
subscription: the $25/mo budget rule already killed a cheaper one.

## 7. Actions (pending owner)

- [ ] Create free Alpaca account + API key → repo secret; verify quote
      (not just bar) depth to 2024-02 on the free feed.
- [ ] Decide Path B: no / ThetaData $40 / ThetaData $80 / Databento
      (after cost preflight). If ThetaData: read ToS retention clause first.
- [ ] Choose the minute-lake filter (DTE/moneyness/cadence) against the
      R2 budget before any collector starts.
- [ ] Then: collector `--mode alpaca-backfill` + `--mode intraday-snap`
      (CBOE JSON; host = Mac/Oracle VM), R2 layout
      `options/source=alpaca|cboe_intraday/…`, DATA-PIPELINE.md amendments.

---

*Sources checked 2026-07-01:
[ThetaData pricing](https://www.thetadata.net/pricing) ·
[ThetaData subscription grid](https://docs.thetadata.us/Articles/Getting-Started/Subscriptions.html) ·
[Alpaca historical option data](https://docs.alpaca.markets/us/docs/historical-option-data) ·
[Alpaca option bars API](https://docs.alpaca.markets/us/reference/optionbars) ·
[Databento OPRA dataset](https://databento.com/datasets/OPRA.PILLAR) ·
[Databento options](https://databento.com/options) ·
[Polygon/Massive pricing](https://massive.com/pricing?product=options) ·
[FirstRateData bundles](https://firstratedata.com/cb/5/complete-us-stocks-index-etf-futures-options) ·
CBOE delayed-quote endpoint and Yahoo v8 chart API probed directly (see §3).
Not financial advice; backtests overstate live performance.*
