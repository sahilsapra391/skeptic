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
         "hv_inhouse_vs_ivol", "ivs_cboe_vs_ivol", "positioning_cboe_vs_uw")

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
    mid = (traded["bid"] + traded["ask"]) / 2
    tol = pd.concat([pd.Series(ABS_TOL, index=mid.index), mid * REL_TOL],
                    axis=1).max(axis=1)
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
    tol = pd.concat([pd.Series(ABS_TOL, index=two.index), mid_ref * REL_TOL],
                    axis=1).max(axis=1)
    ok = (mid_ref - mid_liv).abs() <= tol
    return _record(len(j), len(two), int(ok.sum()))


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
    rng = iv.groupby("occ_symbol").agg(lo=("bid", "min"),
                                       hi=("ask", "max")).reset_index()
    m = massive_day.copy()
    m["c"] = pd.to_numeric(m["c"], errors="coerce")
    j = m.merge(rng, on="occ_symbol", how="inner")
    checked = j[j["c"].notna() & j["lo"].notna() & j["hi"].notna()]
    if checked.empty:
        return _record(len(j), 0, 0)
    tol = pd.concat([pd.Series(ABS_TOL, index=checked.index),
                     (checked["lo"] + checked["hi"]) / 2 * REL_TOL],
                    axis=1).max(axis=1)
    ok = ((checked["c"] >= checked["lo"] - tol)
          & (checked["c"] <= checked["hi"] + tol))
    return _record(len(j), len(checked), int(ok.sum()))


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
