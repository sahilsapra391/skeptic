"""Cross-source validation (ENGINE-V4 F7) — pair comparators + loaders.

Every comparator reduces one session's overlap between two INDEPENDENT
sources to a uniform record:

    {"joined": n, "checked": n, "within_band": n, "agreement_rate": r}

joined   contracts/rows present in BOTH sources (structural agreement —
         parsing, strike scaling, date attribution);
checked  the subset where a price/size comparison is honest (e.g. traded
         near the close, two-sided quotes);
within_band  checked rows whose values agree within the documented
         tolerance; agreement_rate = within_band / checked.

Owner decisions (2026-07-08): REPORTED, never scored — per-pair rates
travel with their audited-share denominators, no blended single score
(weights across incommensurable pairs would be an invented convention
wearing a number); trust-caps wait until accumulated history EARNS the
thresholds (the D3d staging). The nightly artifact is derived by
collector/derive_cross_validation.py (set-difference incremental) into
reference/derived/cross_validation/pair={pair}/ticker={T}.parquet.

Tolerances are REVIEWED CONSTANTS in the spirit of the 2026-07-01
one-off validator (whose near-close + delta-adjustment methodology the
dolthub_vs_alpaca comparator lifts verbatim): a comparison band is a
reporting convention, never scoring.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, cast

import pandas as pd

PAIR_KEY = "reference/derived/cross_validation/pair={pair}/ticker={ticker}.parquet"

# price bands (one-off validator, diagnosed on real data 2026-07-01):
# $0.05 beyond the quoted spread, or 2% of mid — whichever is larger
ABS_TOL = 0.05
REL_TOL = 0.02
# size bands: OI is a settled end-of-day figure (tight); volume captures
# differ by cutoff moment across vendors (looser). Reviewed constants.
OI_REL_TOL = 0.05
VOLUME_REL_TOL = 0.10

PAIRS = ("dolthub_vs_alpaca", "dolthub_vs_uw", "yahoo_vs_ivol5m",
         "massive_vs_ivol5m",
         # forward-record continuations vs their frozen vendor series (the
         # 2026-07-08 no-subscription decision): the overlap MEASURES each
         # convention seam instead of asserting it
         "hv_inhouse_vs_ivol", "ivs_cboe_vs_ivol", "positioning_cboe_vs_uw",
         # recorder displayed quotes vs the UW full option tape (the
         # 2026-07-09 trial-endgame bank): real prints checked against the
         # delayed NBBO record the engine quotes intraday fills from
         "recorder_vs_uw_tape",
         # the in-house forward flow family (Alpaca×recorder classification)
         # vs the frozen UW flow artifact on their overlap — the numbers a
         # future unfreeze/substitution decision must cite
         "flow_inhouse_vs_uw")

# The CBOE delayed feed's quotes lag its own publish stamp by the OPRA
# delayed-data standard. MEASURED, not assumed (probe 2026-07-09: SPY
# 2026-07-08 tape NBBO vs 4 recorder snaps, lag sweep 0/5/10/14/15/16/20
# min → agreement 0.93 at 15 min, 0.74 at 16, ≤0.43 elsewhere). A snap's
# quotes are therefore valid around source_ts − 15 min, and the tape join
# slices trades by that shifted moment — never capture time.
RECORDER_DELAY_MIN = 15
# One snap's quote validity window: the recorder loop is per-minute, so a
# snap covers the 60 s starting at its shifted moment. Gaps in the record
# (missed minutes) leave prints uncovered — honest absence, they are never
# joined against a stale quote.
RECORDER_SNAP_WINDOW_SEC = 60

# In-house continuation bands (reporting conventions, reviewed constants):
# vol-point quantities compare a FITTED surface against a raw-chain
# interpolation — 1.5 vol points; HV is the same statistic on the same
# closes (tight: 0.01 abs / 5% rel); PCR compares chain volume to flow
# volume (loose: 25%); max-pain distance is the same formula on the same
# OI (0.5 pp); GEX/DEX conventions share only their SIGN.
INHOUSE_VOLPT_TOL = 1.5
HV_ABS_TOL = 0.01
HV_REL_TOL = 0.05
PCR_REL_TOL = 0.25
MPD_ABS_TOL = 0.5


def _record(joined: int, checked: int, within: int,
            **extra: Any) -> dict[str, Any]:
    out: dict[str, Any] = {
        "joined": int(joined),
        "checked": int(checked),
        "within_band": int(within),
        "agreement_rate": round(within / checked, 4) if checked else None,
    }
    out.update(extra)
    return out


def band_tol(mid: pd.Series) -> pd.Series:
    """THE reviewed price band, single-sourced: max(ABS_TOL, REL_TOL × mid)
    per row. Every price comparator uses this — a tolerance tweak lands
    everywhere at once or the cross-pair rates stop being comparable."""
    return (mid * REL_TOL).clip(lower=ABS_TOL)


def merge_records(parts: list[dict[str, Any]], *sum_extras: str,
                  **extra: Any) -> dict[str, Any]:
    """Fold per-window comparator records into one session record. Sums
    joined/checked/within_band plus any named integer extras, then rebuilds
    the record through _record so the agreement_rate convention (round-4,
    checked==0 → None) has exactly one implementation."""
    joined = sum(int(p.get("joined") or 0) for p in parts)
    checked = sum(int(p.get("checked") or 0) for p in parts)
    within = sum(int(p.get("within_band") or 0) for p in parts)
    sums = {k: sum(int(p.get(k) or 0) for p in parts) for k in sum_extras}
    return _record(joined, checked, within, **sums, **extra)


def recorder_tape_window(
    ts: pd.Series, source_ts: Any, not_before: Any = None
) -> tuple[int, int, Any]:
    """Slice bounds [lo, hi) of the tape prints a recorder snap may judge,
    plus the window's end moment for the caller to thread as the next
    snap's `not_before`. The snap's quotes are valid RECORDER_DELAY_MIN
    behind its feed stamp (measured constant above), for
    RECORDER_SNAP_WINDOW_SEC. Clamping `start` to `not_before` keeps
    windows disjoint when the feed stalls and consecutive snaps repeat a
    source_ts — the same print is never judged twice (a stalled feed
    would otherwise over-weight one minute's prints up to ~14×).
    `ts` must be sorted and on the same clock as `source_ts` (UTC)."""
    src = pd.Timestamp(source_ts)
    if src.tzinfo is None:
        src = src.tz_localize("UTC")
    start = src - timedelta(minutes=RECORDER_DELAY_MIN)
    end = start + timedelta(seconds=RECORDER_SNAP_WINDOW_SEC)
    if not_before is not None:
        nb = pd.Timestamp(not_before)
        if nb > start:
            start = nb
    if start >= end:
        return 0, 0, end
    lo = int(ts.searchsorted(start, side="left"))
    hi = int(ts.searchsorted(end, side="left"))
    return lo, hi, end


def compare_dolthub_alpaca(
    eod: pd.DataFrame, bars: pd.DataFrame, spots: pd.Series
) -> dict[str, Any] | None:
    """DoltHub EOD closing quotes vs Alpaca minute TRADES (SPY overlap).
    Methodology lifted from the one-off validator: only contracts whose
    last trade printed ≥ 15:45 ET are price-checked (a stale deep-ITM
    print legitimately disagrees with the close by delta × the move);
    each print is delta-adjusted to the close via vendor delta and the
    underlying minute series; the session's effective capture spot is
    self-calibrated from its own high-|delta| contracts (±$3 clamp)."""
    if eod is None or eod.empty or bars is None or bars.empty:
        return None
    if spots is None or spots.empty:
        return None
    # one malformed session must not poison the nightly derive forever
    # (review #5) — unrecognized shape is honest absence, like every pair
    if not {"expiration", "right", "strike", "bid", "ask", "delta",
            "spot"}.issubset(eod.columns):
        return None
    if not {"expiration", "right", "strike", "minute_ts",
            "close"}.issubset(bars.columns):
        return None
    bars = bars.assign(
        _et=pd.to_datetime(bars["minute_ts"]).dt.tz_convert("America/New_York"))
    last = (bars.sort_values("minute_ts")
            .groupby(["expiration", "right", "strike"])
            .agg(last_trade=("close", "last"), last_et=("_et", "last"))
            .reset_index())
    last = last[last["last_et"] >= (last["last_et"].dt.normalize()
                                    + pd.Timedelta(hours=15, minutes=45))]
    last["expiration"] = last["expiration"].astype(str)
    eod = eod.copy()
    eod["expiration"] = eod["expiration"].astype(str)
    j = eod.merge(last, on=["expiration", "right", "strike"], how="left")
    two_sided = j[j["bid"].notna() & j["ask"].notna() & (j["bid"] > 0)]
    # joined = present in BOTH sources (the module contract, review #12);
    # a NaN vendor delta cannot be adjusted — excluded from checked,
    # never fabricated into a violation (review #5)
    joined = int(two_sided["last_trade"].notna().sum())
    traded = two_sided[two_sided["last_trade"].notna()
                       & pd.to_numeric(two_sided["delta"],
                                       errors="coerce").notna()].copy()
    if len(two_sided) == 0:
        return None
    if len(traded) == 0:
        return _record(joined, 0, 0, capture_offset=0.0)
    spot_close = float(eod["spot"].iloc[0])
    def _spot_at(ts: Any) -> float:
        v = spots.asof(ts)
        return float(cast("float", v)) if not pd.isna(v) else spot_close

    spot_at_print = traded["last_et"].map(_spot_at)
    traded["cmp_price"] = (traded["last_trade"]
                           + traded["delta"] * (spot_close - spot_at_print))
    hd = traded[traded["delta"].abs() >= 0.5]
    offset = 0.0
    if len(hd) >= 5:
        offset = float(((hd["bid"] + hd["ask"]) / 2 - hd["cmp_price"])
                       .div(hd["delta"]).median())
        offset = max(-3.0, min(3.0, offset))
    traded["cmp_price"] = traded["cmp_price"] + traded["delta"] * offset
    tol = band_tol((traded["bid"] + traded["ask"]) / 2)
    inside = ((traded["cmp_price"] >= traded["bid"] - tol)
              & (traded["cmp_price"] <= traded["ask"] + tol))
    return _record(joined, len(traded), int(inside.sum()),
                   capture_offset=round(offset, 3))


def compare_dolthub_uw(
    chain: pd.DataFrame, voe: pd.DataFrame
) -> dict[str, Any] | None:
    """DoltHub chain volume/OI summed per expiry vs UW volume_oi_expiry.
    A row agrees when BOTH totals sit within their relative bands (OI
    tight at 5%, volume looser at 10% — capture cutoffs differ)."""
    if chain is None or chain.empty or voe is None or voe.empty:
        return None
    need_c = {"expiration", "volume", "open_interest"}
    need_v = {"expires", "volume", "oi"}
    if not need_c.issubset(chain.columns) or not need_v.issubset(voe.columns):
        return None
    c = chain.copy()
    c["_exp"] = pd.to_datetime(c["expiration"], errors="coerce").dt.date.astype(str)
    c["_vol"] = pd.to_numeric(c["volume"], errors="coerce")
    c["_oi"] = pd.to_numeric(c["open_interest"], errors="coerce")
    agg = c.groupby("_exp").agg(
        c_vol=("_vol", lambda x: x.sum(min_count=1)),
        c_oi=("_oi", lambda x: x.sum(min_count=1)),
    ).reset_index()
    v = voe.copy()
    v["_exp"] = pd.to_datetime(v["expires"], errors="coerce").dt.date.astype(str)
    v["u_vol"] = pd.to_numeric(v["volume"], errors="coerce")
    v["u_oi"] = pd.to_numeric(v["oi"], errors="coerce")
    j = agg.merge(v[["_exp", "u_vol", "u_oi"]], on="_exp", how="inner")
    checked = j[j["c_vol"].notna() & j["c_oi"].notna()
                & j["u_vol"].notna() & j["u_oi"].notna()]
    if checked.empty:
        return _record(len(j), 0, 0)

    def _within(a: pd.Series, b: pd.Series, rel: float) -> pd.Series:
        denom = pd.concat([a.abs(), b.abs()], axis=1).max(axis=1)
        return (a - b).abs() <= denom * rel + 1e-9

    ok = (_within(checked["c_oi"], checked["u_oi"], OI_REL_TOL)
          & _within(checked["c_vol"], checked["u_vol"], VOLUME_REL_TOL))
    return _record(len(j), len(checked), int(ok.sum()))


def compare_quote_close(
    ref: pd.DataFrame, live_last: pd.DataFrame
) -> dict[str, Any] | None:
    """Generic EOD-quote vs last-intraday-NBBO comparison, used for
    yahoo_vs_ivol5m: join on (expiration, right, strike); the two mids
    agree within max(ABS_TOL, REL_TOL × mid)."""
    if ref is None or ref.empty or live_last is None or live_last.empty:
        return None
    keys = ["expiration", "right", "strike"]
    if not set(keys + ["bid", "ask"]).issubset(ref.columns):
        return None
    if not set(keys + ["bid", "ask"]).issubset(live_last.columns):
        return None
    a = ref.copy()
    b = live_last.copy()
    for df in (a, b):
        df["expiration"] = df["expiration"].astype(str)
        df["strike"] = pd.to_numeric(df["strike"], errors="coerce")
    j = a.merge(b, on=keys, suffixes=("_ref", "_liv"), how="inner")
    two = j[j["bid_ref"].notna() & j["ask_ref"].notna()
            & j["bid_liv"].notna() & j["ask_liv"].notna()
            & (j["bid_ref"] > 0) & (j["bid_liv"] > 0)]
    if two.empty:
        return _record(len(j), 0, 0)
    mid_ref = (two["bid_ref"] + two["ask_ref"]) / 2
    mid_liv = (two["bid_liv"] + two["ask_liv"]) / 2
    ok = (mid_ref - mid_liv).abs() <= band_tol(mid_ref)
    return _record(len(j), len(two), int(ok.sum()))


def _norm_occ(col: pd.Series) -> pd.Series:
    """Canonical OCC key: strip the "O:" prefix and any root padding so
    Massive ("O:QQQ260702C00705000") and iVol ("QQQ   260702C00705000")
    join. Unambiguous for our 3-char roots."""
    return col.astype(str).str.replace("O:", "", regex=False).str.replace(
        " ", "", regex=False)


def compare_massive_ivol5m(
    massive_day: pd.DataFrame, ivol_day: pd.DataFrame
) -> dict[str, Any] | None:
    """Massive daily per-contract close (TRADE) vs the iVol 5-min NBBO
    day range: the vendor close should sit inside [day-min bid − tol,
    day-max ask + tol] — a trade can't honestly print outside the day's
    quoted band. Massive is NEVER a fill source (guardrail #1); this is
    the F5-deferred coverage/volume cross-check landing at F7."""
    if massive_day is None or massive_day.empty:
        return None
    if ivol_day is None or ivol_day.empty:
        return None
    need_m = {"occ_symbol", "c"}
    if not need_m.issubset(massive_day.columns):
        return None
    if not {"occ_symbol", "bid", "ask"}.issubset(ivol_day.columns):
        return None
    iv = ivol_day.copy()
    iv["bid"] = pd.to_numeric(iv["bid"], errors="coerce")
    iv["ask"] = pd.to_numeric(iv["ask"], errors="coerce")
    # the two vendors format the OCC symbol differently — Massive prefixes
    # "O:", iVol pads the root to six chars — so the raw join finds NOTHING
    # (real-lake acceptance 2026-07-08). Normalize both to the canonical
    # {root}{YYMMDD}{C/P}{strike8} before joining (all tickers are 3-char
    # roots, so stripping "O:" and whitespace is unambiguous).
    iv["_occ"] = _norm_occ(iv["occ_symbol"])
    rng = iv.groupby("_occ").agg(lo=("bid", "min"),
                                 hi=("ask", "max")).reset_index()
    m = massive_day.copy()
    m["c"] = pd.to_numeric(m["c"], errors="coerce")
    m["_occ"] = _norm_occ(m["occ_symbol"])
    j = m.merge(rng, on="_occ", how="inner")
    checked = j[j["c"].notna() & j["lo"].notna() & j["hi"].notna()]
    if checked.empty:
        return _record(len(j), 0, 0)
    tol = band_tol((checked["lo"] + checked["hi"]) / 2)
    ok = ((checked["c"] >= checked["lo"] - tol)
          & (checked["c"] <= checked["hi"] + tol))
    return _record(len(j), len(checked), int(ok.sum()))


def compare_recorder_tape_window(
    snap: pd.DataFrame, trades: pd.DataFrame
) -> dict[str, Any] | None:
    """One recorder snapshot vs the UW tape prints inside its validity
    window (the runner slices trades by source_ts − RECORDER_DELAY_MIN):
    a print on a contract the snap quotes two-sided must sit inside
    [bid − tol, ask + tol], tol = max(ABS_TOL, REL_TOL × mid) — the
    reviewed band. The quote is delayed top-of-book and a print inside
    the next 60 s may legitimately chase a fast move, so the band is a
    reporting convention, never scoring. Extras `below_bid`/`beyond_ask`
    accumulate the DIRECTION of every violation — the displayed-quote
    calibration signal (F5 disclosure → D3d staging), reported only.
    Prints on contracts the snap doesn't list join zero (honest absence);
    an unrecognized shape is None, like every pair."""
    if snap is None or snap.empty or trades is None or trades.empty:
        return None
    if not {"expiration", "right", "strike", "bid", "ask"}.issubset(snap.columns):
        return None
    if not {"expiry", "option_type", "strike", "price"}.issubset(trades.columns):
        return None
    keys = ["_exp", "_right", "_strike"]
    q = snap.copy()
    q["_exp"] = q["expiration"].astype(str).str[:10]
    q["_right"] = q["right"].astype(str).str[:1].str.lower()
    q["_strike"] = pd.to_numeric(q["strike"], errors="coerce")
    q["bid"] = pd.to_numeric(q["bid"], errors="coerce")
    q["ask"] = pd.to_numeric(q["ask"], errors="coerce")
    # a snap lists each contract once; a malformed duplicate would fan the
    # merge out and double-count prints — keep the first, defensively
    q = (q.dropna(subset=["_strike"])
         .drop_duplicates(subset=keys, keep="first"))[keys + ["bid", "ask"]]
    t = trades.copy()
    t["_exp"] = t["expiry"].astype(str).str[:10]
    t["_right"] = t["option_type"].astype(str).str[:1].str.lower()
    t["_strike"] = pd.to_numeric(t["strike"], errors="coerce")
    t["_price"] = pd.to_numeric(t["price"], errors="coerce")
    t = t.dropna(subset=["_strike", "_price"])
    if t.empty:
        return None
    j = t.merge(q, on=keys, how="inner")
    # a crossed quote (bid > ask — the delayed feed does serve them) is
    # not an honest two-sided market: excluded from checked, or one print
    # could land in below_bid AND beyond_ask and the direction extras
    # would stop reconciling with checked
    two = j[(j["bid"] > 0) & j["ask"].notna() & (j["ask"] >= j["bid"])]
    if two.empty:
        return _record(len(j), 0, 0, below_bid=0, beyond_ask=0)
    tol = band_tol((two["bid"] + two["ask"]) / 2)
    below = two["_price"] < two["bid"] - tol
    beyond = two["_price"] > two["ask"] + tol
    within = ~(below | beyond)
    return _record(len(j), len(two), int(within.sum()),
                   below_bid=int(below.sum()), beyond_ask=int(beyond.sum()))


def compare_signal_values(
    fields: list[tuple[float | None, float | None, str, float, float]],
) -> dict[str, Any] | None:
    """One session's signal-vs-signal comparison. Each field is
    (ours, theirs, mode, abs_tol, rel_tol); mode "band" agrees within
    max(abs_tol, rel_tol·|theirs|), mode "sign" agrees on sign (an exact
    zero on either side is joined but unCHECKED — sign(0) is not a
    claim). None when nothing joined — that session has no overlap."""
    joined = checked = within = 0
    for ours, theirs, mode, abs_tol, rel_tol in fields:
        if ours is None or theirs is None:
            continue
        if pd.isna(ours) or pd.isna(theirs):
            continue
        joined += 1
        if mode == "sign":
            if ours == 0 or theirs == 0:
                continue
            checked += 1
            if (ours > 0) == (theirs > 0):
                within += 1
        else:
            checked += 1
            tol = max(abs_tol, rel_tol * abs(theirs))
            if abs(ours - theirs) <= tol:
                within += 1
    if joined == 0:
        return None
    return _record(joined, checked, within)


def load_pair_summary(
    s3: Any, pair: str, ticker: str
) -> dict[str, dict[str, Any]]:
    """{iso-date: record} from a pair artifact — empty until derived."""
    from app.data import r2

    df = r2.get_parquet(s3, PAIR_KEY.format(pair=pair, ticker=ticker))
    if df is None or df.empty or "date" not in df.columns:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for row in df.to_dict("records"):
        d = str(row.get("date"))
        if d and d != "nan":
            out[d] = {str(k): v for k, v in row.items()}
    return out
