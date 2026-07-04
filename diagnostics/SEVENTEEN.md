# SEVENTEEN — the seventeen-fills anomaly

**Status:** root cause proven in code; final data-lake confirmation is a
one-command check Sahil runs against R2 (see §5).
**Owner:** engine / honesty layer.
**Date:** 2026-07-04.

> Observed: many unrelated strategies all produce EXACTLY 17 fills and
> 1,000+ skips. A constant that survives changes in strategy logic lives
> outside the strategies.

It does. The constant is **the number of distinct options-chain date
partitions present in the R2 lake** — nothing in any strategy, and no cap
anywhere in the code.

---

## 1. The proven causal chain

1. **Underlying dailies are backfilled deep; chains are not.** The engine's
   clock is built from `store.sessions`, which comes from the underlying
   daily bars (`underlying/ticker=SPY/daily.parquet`) — years of history.
   Options chains are a *separate* set of per-date partitions
   (`options/source=*/ticker=SPY/date=YYYY-MM-DD/`), and only ~17 of those
   dates currently exist in the lake.

2. **The clock spans the underlying history, not the chain history.**
   [`engine.py:451-456`](../backend/app/engine/engine.py#L451):

   ```python
   first_chain, last_chain = store.chain_dates[0], store.chain_dates[-1]
   req_start = spec.backtest.start or first_chain
   eff_start = max(req_start, first_chain)
   eff_end   = min(req_end, store.sessions[-1])
   clock = [d for d in store.sessions if eff_start <= d <= eff_end]
   ```

   With one old chain date (a backfill seed) and the underlying spanning
   years, `clock` is ~1,500+ sessions long.

3. **Every scheduled session without a chain is a `no_chain_data` SKIP.**
   [`engine.py:146-148`](../backend/app/engine/engine.py#L146):

   ```python
   if not view.has_chain:
       skip("no_chain_data")
       return
   ```

   and `has_chain` is simply membership in the loaded chain set
   ([`market.py:62-63`](../backend/app/engine/market.py#L62)):
   `return self._as_of in self._store.chains`.

4. **Fills are exactly the chain dates the schedule lands on.** A daily
   strategy attempts an entry every session, so it fills on *every* chain
   date and skips every other session. `filled` counts OPEN events
   ([`engine.py:496`](../backend/app/engine/engine.py#L496)).
   ⇒ **daily fills == number of chain dates == 17.** Weekly/monthly land on
   subsets of the same dates, so they fill ≤ 17 but never more.

5. **Therefore 17 is a property of the data lake** (distinct chain-date
   partitions), identical across strategies because it is upstream of them.

## 2. Ruling out the alternatives (the brief's decisive comparison)

Reproduction script: `diagnostics/repro_seventeen.py` — three maximally
different specs against two synthetic lakes.

### (b) A hidden code cap at 17? **Refuted.**

Run the *same* daily strategy against a **dense** lake of 60 chain dates:

| lake | strategy | filled | skip reasons |
|------|----------|-------:|--------------|
| **60 chain dates** | daily short put | **20** | `max_concurrent` ×21, `no_expiration_in_window` ×19 |

20 > 17. If a constant cap lived in the code, 60 dense dates could not
produce 20 fills. The two skip buckets that *do* appear are both
legitimate and strategy/data-driven: `max_concurrent` is the per-spec
`max_concurrent_positions` (=10 here), and `no_expiration_in_window` is the
data tail near the end of the window. Neither is a global constant.

Corroborating code search: no literal `17`, no `max_fills`, no `LIMIT`, no
response slice `[:N]`, and no pagination truncation anywhere in the read
path. `list_date_prefixes`
([`r2.py:73-82`](../backend/app/data/r2.py#L73)) walks **every** page of
`list_objects_v2` — the date listing is complete, never truncated.

### (a) The data lake is the constant? **Confirmed (mechanism).**

Same three specs against a **sparse** lake — 17 chain dates spread across
1,600 sessions:

| lake | strategy | filled | `no_chain_data` skips | all fills ⊆ the 17 chain dates? |
|------|----------|-------:|----------------------:|:---:|
| **17 chain dates** | daily short put   | **17** | 1,488 | ✅ |
| **17 chain dates** | weekly iron condor |    4  |   297 | ✅ |
| **17 chain dates** | monthly covered call | 14 | 1,052 (+269 `max_concurrent`) | ✅ |

The daily strategy reproduces the exact reported symptom: **17 fills,
~1,500 `no_chain_data` skips.** Every strategy's fills are a subset of the
same 17 chain dates. (Weekly is lower because only 4 of the 17 dates are
Mondays; monthly is 14 because 14 fall in distinct months — both track the
schedule∩chain-date intersection, nothing intrinsic to 17.)

### (c) Entry-date generator clamping / walk-forward miscount? **Refuted.**

The generated candidate dates span the full requested window
(2020-01-06 … 2025-10-10 in the sparse run) — the clock is not clamped, and
the skips are attributed to `no_chain_data`, i.e. missing data on generated
dates, not missing dates. Walk-forward is downstream of the fill count and
plays no part.

## 3. Why "multi-year backtest" is a lie the product must stop telling

"Backtested 2020–2025" backed by 17 chain dates means the strategy was
*actually* tested on 17 days. The equity curve interpolates cash across
~1,500 chainless sessions where nothing can trade. This is precisely the
self-deception Skeptic exists to catch (CLAUDE.md guardrail #6, brief §0.0
step 5).

## 4. Fix plan

- **Root cause (data, out of engine scope):** the lake needs more chain
  dates. That is the Alpha Vantage / iVolatility backfill depth problem —
  brief Phase 1 item 5 and Phase 3 Loop C. The engine cannot manufacture
  coverage it does not have; it can only stop overstating it.
- **Honesty consequence (this session, shipped):**
  1. Compute a **coverage** stat (requested window vs. sessions with a
     usable chain) and thread it into `HonestyReport`.
  2. **Cap trust to `insufficient_evidence`** when usable-chain coverage of
     the requested window is materially short (< 50%), mirroring the
     existing thin-sample cap. `app/honesty/trust.py`.
  3. **Disclose** requested-vs-effective window + coverage % in the verdict
     caveats (grounded automatically — every number in the report is in the
     verdict's allowed set). `app/honesty/verdict.py`.
- **Regression protection (this session, shipped):**
  `backend/tests/test_seventeen_regression.py` — a 60-chain-date synthetic
  lake asserts `filled > 17` and `filled == analytically expected`, and a
  sparse lake asserts fills track the chain-date count with the balance
  attributed to `no_chain_data`. Any future hidden cap fails this test.

## 5. The one check that closes this out (Sahil, ~30s)

Confirm the lake really holds ~17 chain dates (vs. some accident of a
particular run). With R2 creds in the environment:

```
uv run --project backend python diagnostics/count_chain_partitions.py SPY
```

It prints, per source, the count of `date=` partitions under
`options/source=*/ticker=SPY/` and the dolthub quarantine size. If the
union is ~17, root cause is confirmed exactly as written above. If it is
materially larger, re-open this file — the count would then point at a
loader-side date filter rather than the lake, and the dense-lake evidence
in §2 still stands as proof there is no fill cap.
