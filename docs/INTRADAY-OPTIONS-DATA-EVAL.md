# Intraday (1-minute) options history for QQQ/IWM — acquisition evaluation

*Evaluated 2026-07-01. Question from the owner: minute-by-minute options
pricing for QQQ and IWM, 5–10 years back (10 preferred, 5 worst-case),
free via API if possible, otherwise scraped free. Companion to
docs/DOLTHUB-EVAL.md (EOD backfill; that archive is EOD + SPY-only, so it
contributes nothing here).*

---

## Verdict

> **DECIDED (owner, 2026-07-02): Path A step 1 adopted for all three
> tickers.** Alpaca minute-bar backfill 2024-02 → present for SPY/QQQ/IWM
> + nightly accrual, quotes lazy-only. Spec: DATA-PIPELINE.md (second
> DECIDED block, §4b); implementation milestone: BUILD-PLAN.md M1.5.
> B-lite (OptionsDX QQQ), B-full (ThetaData) and C (QuantConnect) remain
> open options, undecided.

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
- **Owner-suggested sources (addendum, same day):** two of three pan out.
  **OptionsDX** sells QQQ option chains at **5/15/30-min + EOD
  granularity, 2012–2023**, full fields (bid/ask/last, IV, greeks,
  underlying), $0–$20 per year×granularity variant — tens of dollars
  one-time for the whole QQQ intraday history, splicing into Alpaca's
  2024-02→ minute data almost seamlessly. **No IWM in their catalog.**
  **QuantConnect**'s free tier includes cloud backtesting *and* a research
  node against AlgoSeek US equity options — **minute resolution,
  quotes+trades+OI, 4,000 symbols incl. QQQ/IWM, from 2012-01** —
  use-in-platform only (bulk export is paid and license-restricted).
  **Kaggle** has nothing minute-level for QQQ/IWM options: the best hits
  are EOD QQQ/SPY chain re-dumps (2020–2022) of murky provenance, which
  the record lake can't trust anyway.

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
| **OptionsDX** | free account; some year×granularity variants priced $0 | QQQ: **5/15/30-min + EOD chains, 2012–2023** (monthly CSVs) | bid/ask/last + IV + greeks + underlying | ~$0–20 per year×granularity; whole QQQ intraday history ≈ tens of dollars one-time | **best cheap ownable QQQ intraday**; catalog = SPY/SPX/VIX/QQQ/TSLA/AAPL/UVXY/SLV/NVDA/BTC — **no IWM**; 5-min floor, not 1-min |
| **QuantConnect** | free tier: cloud backtest node + research node, all datasets at minute–daily | AlgoSeek US equity options: **minute, 2012-01 →**, 4,000 symbols incl. QQQ+IWM | quotes + trades + OI (greeks via universe dataset) | $0 in-platform; bulk download paid + license-restricted; live deploy paid | **deepest free-to-USE minute source for both tickers** — but the data can't be exported into our lake; it's a venue, not a source |
| Polygon (now "Massive") | EOD-oriented free tier, 5 req/min (site is JS-walled; verify at signup) | paid tiers to $199/mo | aggregates/quotes | subscription | structurally unusable free: per-contract endpoints × ~10k contracts × 5 req/min |
| FirstRateData | — | **options are EOD-only** (their 1-min granularity is stocks/ETFs) | EOD chains | ~$99/yr updates | eliminated for minute data |
| CBOE DataShop | — | custom historical orders to 2000s | official everything | cart-quoted, typically the expensive route | overkill |
| IBKR / Schwab / Tradier | account APIs | **no expired-contract history at all**; TOS thinkBack/OnDemand is in-platform replay, not exportable | — | — | dead end |
| Kaggle / HuggingFace / GitHub / DoltHub | $0 | no minute options chains for QQQ/IWM (searched twice, incl. per-dataset check); closest: **EOD** QQQ & SPY chain dumps 2020–2022, SPY-only "intraday options" one-offs | EOD chains | — | dead end for this ask; the EOD dumps are unlicensed re-dumps of vendor data — provenance fails the record-lake bar, and OptionsDX sells the same thing clean for ~$20 |

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

**Path B — paid one-time bootstrap (owner decision), two rungs:**

- **B-lite, ~tens of dollars, QQQ only:** OptionsDX QQQ chains at 5-min
  (or 15/30-min/EOD) granularity, 2012–2023, $0–$20 per year×granularity,
  monthly CSVs with bid/ask/IV/greeks/underlying. Spliced with Alpaca
  (2024-02→) this yields a near-continuous QQQ intraday history
  2012→present. Floor is 5-minute, not 1-minute, and **IWM does not
  exist in their catalog.** Single-user license; no redistribution —
  compatible with our rails.
- **B-full, $40–80 once, both tickers at true 1-min:** one month of
  ThetaData Value ($40 → 1-min quotes to 2020-01, 6.5y) or Standard
  ($80 → 2016-01, 10.5y), bulk-download QQQ/IWM (+SPY while we're
  there), cancel. Read their ToS on post-cancellation data retention
  *before* paying. Alternative: Databento — preflight the exact cost of
  `cbbo-1m`/`ohlcv-1m` for parent symbols `QQQ.OPT`+`IWM.OPT` 2016→2026;
  $125 signup credit offsets; decide on the number, not a guess.

**Path C — QuantConnect as a free research venue (not a data source):**
free tier runs cloud backtests and research notebooks against AlgoSeek
minute-resolution options (quotes/trades/OI) for QQQ **and IWM** back to
2012-01. This answers intraday research questions *today* at $0 — but the
data stays on their platform (export is paid and license-restricted), so
it cannot fill the R2 lake or feed Skeptic's own engine/honesty layer.
Useful as a pre-purchase sanity lane: if an intraday edge doesn't show up
in QC, don't buy history to chase it.

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

Adopt **Path A now** — $0, starts the clock, and is the only way the free
constraint and the minute constraint coexist. **B-lite (OptionsDX QQQ,
~tens of dollars one-time) is the standout value** the owner should
seriously consider: it makes QQQ intraday history 2012→present a solved
problem at 5-min granularity for less than one month of any subscription.
IWM history remains gated behind B-full ($40–80 once) — or behind Path C
for research-without-ownership. Do not take any ongoing subscription: the
$25/mo budget rule already killed a cheaper one.

## 7. Actions (pending owner)

- [x] ~~Decide adoption~~ → **DECIDED 2026-07-02: Alpaca for all three
      tickers (see verdict block); M1.5 added to BUILD-PLAN.**
- [ ] Create free Alpaca account + **paper API key pair** → repo secrets
      `APCA_API_KEY_ID` / `APCA_API_SECRET_KEY`; quote (not just bar)
      depth is verified at M1.5 step 0.
- [ ] Cart-quote OptionsDX QQQ: exact total for 5-min × 2012–2023 (some
      variants $0) and confirm license text; decide B-lite.
- [ ] Decide B-full for IWM (+1-min QQQ): no / ThetaData $40 / ThetaData
      $80 / Databento (after cost preflight). If ThetaData: read ToS
      retention clause first.
- [ ] Optional Path C: free QuantConnect account; reproduce one candidate
      intraday strategy on QC minute data before buying any history.
- [ ] Choose the minute-lake filter (DTE/moneyness/cadence) against the
      R2 budget before any collector starts.
- [ ] Then: collector `--mode alpaca-backfill` + `--mode intraday-snap`
      (CBOE JSON; host = Mac/Oracle VM), R2 layout
      `options/source=alpaca|cboe_intraday|optionsdx/…`, DATA-PIPELINE.md
      amendments.

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
[OptionsDX shop](https://www.optionsdx.com/shop/) ·
[OptionsDX QQQ product](https://www.optionsdx.com/product/qqq-option-chains/) ·
[QuantConnect AlgoSeek US equity options](https://www.quantconnect.com/docs/v2/writing-algorithms/datasets/algoseek/us-equity-options) ·
[QuantConnect pricing](https://www.quantconnect.com/pricing/) ·
[Kaggle QQQ EOD chains 2020–2022](https://www.kaggle.com/datasets/kylegraupe/qqq-daily-option-chains-q1-2020-to-q4-2022) ·
CBOE delayed-quote endpoint and Yahoo v8 chart API probed directly (see §3).
Not financial advice; backtests overstate live performance.*
