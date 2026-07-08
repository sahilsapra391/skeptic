"""In-house signal derivations — the forward record after the vendor feeds
froze (owner decision 2026-07-08: no iVolatility or Unusual Whales
subscription; compute what the lake can honestly support, freeze what it
can't).

Inputs are OUR OWN lake objects only:
  * the cboe_eod close chain (per-contract IV/delta/gamma/OI/volume, all
    expirations, quotes ~15-min delayed — a property of the source), and
  * the underlying dailies (for HV and the max-pain close).

Two artifacts, derived nightly by collector/derive_inhouse_signals.py:
  reference/derived/inhouse_signals/ticker={T}.parquet
      date · skew_25d · term_slope_30_90 · atm_iv_30d · atm_iv_90d
      (vol points, same column convention as the vendor ivs_signals
      artifact) · net_gex · net_dex · put_call_ratio · max_pain_dist_pct
  reference/derived/hv_inhouse/ticker={T}.parquet
      date · hv_30d (decimal) — full history, overwritten nightly

CONVENTIONS (each is a fixed, hand-computable market standard; fixtures in
tests/test_inhouse_signals.py pin every one):
  hv_30d            std of the trailing 30 daily log returns, ddof=1,
                    annualized √252. Pinned against the vendor series on
                    the full 5,408-session overlap (probe 2026-07-08:
                    MAE 0.0002) — the forward continuation is measured,
                    not asserted.
  atm_iv_τ          per expiration: mean of call+put IV at the strike
                    nearest spot (iv ≤ 0 is the feed's null, dropped);
                    across expirations: linear in TOTAL VARIANCE (iv²·t,
                    calendar days) between the bracketing expirations,
                    exact tenor short-circuits. Fail closed: no bracket →
                    None, never extrapolated.
  skew_25d          IV(25Δ put) − IV(25Δ call) at the 30d tenor, VOL
                    POINTS. 25Δ linearly interpolated in |delta| between
                    bracketing strikes per expiration (|delta| ≥ 0.999
                    rows are pin noise, dropped); tenor interpolation as
                    above. Positive = puts rich, same sign as the vendor
                    fit.
  term_slope_30_90  atm_iv_90d − atm_iv_30d, vol points.
  net_gex           Σ gamma·OI·100·spot²·0.01, calls positive, puts
                    NEGATIVE (the standard dealers-long-calls/short-puts
                    assumption — the same one the UW series embeds).
                    Dollars of gamma per 1% move. A NEW CONVENTION, never
                    a continuation of UW's opaque vendor units: sign
                    vocabulary splices, rank windows never cross the
                    seam (see splice_forward / MarketView histories).
  net_dex           Σ delta·OI·100·spot (puts carry their negative delta).
  put_call_ratio    Σ put volume / Σ call volume over the chain's session
                    volume — the classic chain PCR. UW's flow-volume PCR
                    is a close cousin; the overlap agreement is reported
                    by the F7 pair, never assumed.
  max_pain_dist_pct (front max_pain − close)/close × 100, front = nearest
                    expiry STRICTLY AFTER the session (the F2/F3 owner
                    convention), max pain = the listed strike minimizing
                    total intrinsic payout over that expiry's OI. Ties
                    break toward the strike nearest the close, then lower.

Honesty: every reduction returns None when its inputs are missing or
unbracketed — absence, never a guess. The derivation lives HERE (not
mirrored in the collector) so the math has exactly one implementation,
fixture-tested in the backend battery.
"""

from __future__ import annotations

import math
from datetime import date
from typing import Any, cast

import numpy as np
import pandas as pd

# shared kernels/loaders — ONE implementation per honest primitive (review
# finding: forked copies of interpolation or series-reading math make the
# continuation diverge from the vendor-era derivation it splices onto)
from app.data.flow_signals import _series_from
from app.data.ivs_signals import _interp_iv_at_delta

CHAIN_SIGNALS_KEY = "reference/derived/inhouse_signals/ticker={ticker}.parquet"
HV_KEY = "reference/derived/hv_inhouse/ticker={ticker}.parquet"

HV_WINDOW = 30  # trailing daily log returns (probe-pinned vs vendor)
SKEW_DELTA = 0.25
SKEW_TENOR_DAYS = 30
TERM_SHORT_DAYS = 30
TERM_LONG_DAYS = 90
DEEP_ITM_DELTA = 0.999  # |delta| at/above this is pin noise, not a wing
CONTRACT_MULT = 100


# ---------------------------------------------------------------- HV (dailies)

def hv30_frame(daily: pd.DataFrame) -> pd.DataFrame:
    """date · hv_30d (decimal) from an underlying daily frame (date/close).
    Rows without a full trailing window are dropped, never padded."""
    if daily is None or daily.empty or not {"date", "close"}.issubset(daily.columns):
        return pd.DataFrame(columns=["date", "hv_30d"])
    df = daily[["date", "close"]].copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df = df.dropna().sort_values("date").drop_duplicates("date", keep="last")
    logret = pd.Series(np.log(df["close"] / df["close"].shift(1)), index=df.index)
    hv = logret.rolling(HV_WINDOW).std(ddof=1) * math.sqrt(252)
    out = pd.DataFrame({"date": df["date"].dt.date.astype(str), "hv_30d": hv})
    return out.dropna(subset=["hv_30d"]).reset_index(drop=True)


# ------------------------------------------------------- chain-derived signals

def _prep(chain: pd.DataFrame) -> pd.DataFrame | None:
    """Coerce the untrusted chain frame; iv ≤ 0 is the feed's null."""
    needed = {"expiration", "right", "strike"}
    if chain is None or chain.empty or not needed.issubset(chain.columns):
        return None
    df = chain.copy()
    for col in ("strike", "iv", "delta", "gamma", "volume", "open_interest", "spot"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        else:
            df[col] = np.nan
    df["iv"] = df["iv"].where(df["iv"] > 0)
    df["right"] = df["right"].astype(str).str.lower()
    exp = pd.to_datetime(df["expiration"], errors="coerce")
    df = df.loc[exp.notna() & df["strike"].notna()].copy()
    df["_exp"] = exp.loc[exp.notna()].dt.date
    return None if df.empty else df


def _spot(df: pd.DataFrame) -> float | None:
    s = df["spot"].dropna()
    return float(s.median()) if len(s) else None


def _atm_iv_of_expiry(rows: pd.DataFrame, spot: float) -> float | None:
    """Mean call+put IV at the strike nearest spot (whichever sides carry
    a real IV there)."""
    with_iv = rows.dropna(subset=["iv"])
    if with_iv.empty:
        return None
    nearest = min(with_iv["strike"].unique(), key=lambda k: (abs(k - spot), k))
    ivs = with_iv.loc[with_iv["strike"] == nearest, "iv"]
    return float(ivs.mean()) if len(ivs) else None


def _iv_at_delta(rows: pd.DataFrame, target_abs_delta: float) -> float | None:
    """IV at |delta| == target for ONE expiry and ONE right: drop the
    feed's nulls and the deep-ITM pin rows, then run the SHARED
    ivs_signals interpolation kernel over the quoted grid."""
    pts = rows.dropna(subset=["iv", "delta"])
    pts = pts[pts["delta"].abs() < DEEP_ITM_DELTA]
    if pts.empty:
        return None
    return _interp_iv_at_delta(pts, target_abs_delta, iv_col="iv")


def _tenor_interp(points: list[tuple[int, float | None]], tau: int) -> float | None:
    """IV at calendar tenor `tau` from per-expiration (dte, iv) points —
    linear in TOTAL VARIANCE (iv²·t) between the bracketing expirations.
    Exact tenor wins; dte < 1 never brackets (zero total variance would
    swallow the short leg's information). Fail closed on no bracket."""
    usable = [(t, v) for t, v in points if t >= 1 and v is not None]
    if not usable:
        return None
    exact = [v for t, v in usable if t == tau]
    if exact:
        return exact[0]
    lows = [(t, v) for t, v in usable if t < tau]
    highs = [(t, v) for t, v in usable if t > tau]
    if not lows or not highs:
        return None
    t_lo, iv_lo = max(lows)
    t_hi, iv_hi = min(highs)
    v_lo = iv_lo * iv_lo * t_lo
    v_hi = iv_hi * iv_hi * t_hi
    w = (tau - t_lo) / (t_hi - t_lo)
    var_tau = v_lo + w * (v_hi - v_lo)
    if var_tau <= 0:
        return None
    return math.sqrt(var_tau / tau)


def derive_chain_signal_row(
    chain: pd.DataFrame, session: str, close: float | None
) -> dict[str, float | None]:
    """One cboe_eod chain → the in-house signal values. Missing inputs
    yield None per signal — honest absence, never a guess."""
    out: dict[str, float | None] = {
        "skew_25d": None, "term_slope_30_90": None,
        "atm_iv_30d": None, "atm_iv_90d": None,
        "net_gex": None, "net_dex": None,
        "put_call_ratio": None, "max_pain_dist_pct": None,
    }
    df = _prep(chain)
    if df is None:
        return out
    day = date.fromisoformat(session)
    spot = _spot(df)

    if spot is not None:
        # groupby keys are the coerced `_exp` dates; cast for strict mypy
        by_exp = {cast(date, e): g for e, g in df.groupby("_exp")
                  if cast(date, e) >= day}

        def dte(e: date) -> int:
            return (e - day).days

        # ONE walk per expiry builds every surface point (review finding:
        # parallel comprehensions over the same dict drift apart — the next
        # wing inherits this loop, not a fourth copy)
        atm_points: list[tuple[int, float | None]] = []
        put_pts: list[tuple[int, float | None]] = []
        call_pts: list[tuple[int, float | None]] = []
        for e, g in by_exp.items():
            t = dte(e)
            atm_points.append((t, _atm_iv_of_expiry(g, spot)))
            put_pts.append((t, _iv_at_delta(g[g["right"] == "put"], SKEW_DELTA)))
            call_pts.append((t, _iv_at_delta(g[g["right"] == "call"], SKEW_DELTA)))
        atm30 = _tenor_interp(atm_points, TERM_SHORT_DAYS)
        atm90 = _tenor_interp(atm_points, TERM_LONG_DAYS)
        out["atm_iv_30d"] = None if atm30 is None else round(atm30 * 100.0, 4)
        out["atm_iv_90d"] = None if atm90 is None else round(atm90 * 100.0, 4)
        if atm30 is not None and atm90 is not None:
            out["term_slope_30_90"] = round((atm90 - atm30) * 100.0, 4)
        put25 = _tenor_interp(put_pts, SKEW_TENOR_DAYS)
        call25 = _tenor_interp(call_pts, SKEW_TENOR_DAYS)
        if put25 is not None and call25 is not None:
            out["skew_25d"] = round((put25 - call25) * 100.0, 4)

        # dealer positioning — both sides must carry gamma·OI rows, or the
        # sum is a one-legged lie
        pos = df.dropna(subset=["gamma", "open_interest"])
        calls, puts = pos[pos["right"] == "call"], pos[pos["right"] == "put"]
        if len(calls) and len(puts):
            scale = CONTRACT_MULT * spot * spot * 0.01
            out["net_gex"] = round(float(
                (calls["gamma"] * calls["open_interest"]).sum() * scale
                - (puts["gamma"] * puts["open_interest"]).sum() * scale), 2)
        dpos = df.dropna(subset=["delta", "open_interest"])
        dcalls, dputs = dpos[dpos["right"] == "call"], dpos[dpos["right"] == "put"]
        if len(dcalls) and len(dputs):
            out["net_dex"] = round(float(
                (dpos["delta"] * dpos["open_interest"]).sum()
                * CONTRACT_MULT * spot), 2)

    cv = df.loc[df["right"] == "call", "volume"].sum(min_count=1)
    pv = df.loc[df["right"] == "put", "volume"].sum(min_count=1)
    if pd.notna(cv) and pd.notna(pv) and cv > 0:
        out["put_call_ratio"] = round(float(pv / cv), 4)

    if close is not None and close > 0:
        mp = _max_pain(df, day, close)
        if mp is not None:
            out["max_pain_dist_pct"] = round((mp - close) / close * 100.0, 4)
    return out


def _max_pain(df: pd.DataFrame, day: date, close: float) -> float | None:
    """Max-pain strike of the front expiry STRICTLY AFTER `day` — the
    listed strike minimizing total intrinsic payout over that expiry's OI.
    Ties break toward the strike nearest the close, then lower."""
    # FRONT = the nearest LISTED expiry strictly after the session (the
    # F2/F3 owner convention this series splices onto). If that expiry
    # carries no positive OI — a freshly listed weekly before OI settles —
    # the signal is honestly None: shifting to a LATER expiry would bank a
    # value under the wrong pin date (review finding).
    fronts = sorted(e for e in df["_exp"].unique() if e > day)
    if not fronts:
        return None
    front_rows = df[df["_exp"] == fronts[0]]
    exp_rows = front_rows.dropna(subset=["open_interest"])
    exp_rows = exp_rows[exp_rows["open_interest"] > 0]
    if exp_rows.empty:
        return None
    # candidate settles are EVERY listed strike of the front expiry (a
    # zero-OI strike is a legal pin); OI only weights the payout
    strikes = sorted(front_rows["strike"].unique())
    calls = exp_rows[exp_rows["right"] == "call"]
    puts = exp_rows[exp_rows["right"] == "put"]

    def payout(settle: float) -> float:
        c = (np.maximum(settle - calls["strike"], 0.0) * calls["open_interest"]).sum()
        p = (np.maximum(puts["strike"] - settle, 0.0) * puts["open_interest"]).sum()
        return float(c + p)

    return float(min(strikes, key=lambda k: (payout(k), abs(k - close), k)))


# ---------------------------------------------------------------- splice rule

def splice_forward(
    vendor: dict[date, float], inhouse: dict[date, float]
) -> tuple[dict[date, float], date | None]:
    """The forward-continuation rule: the vendor series wins every session
    it has; in-house values extend it STRICTLY FORWARD of the vendor's
    last observation. Returns (merged, splice date = first in-house
    session used, None when nothing spliced). History is never rewritten —
    a re-run over a pre-splice window is bit-identical."""
    if not inhouse:
        return vendor, None
    if not vendor:
        return dict(inhouse), min(inhouse)
    last = max(vendor)
    forward = {d: v for d, v in inhouse.items() if d > last}
    if not forward:
        return vendor, None
    return {**vendor, **forward}, min(forward)


# -------------------------------------------------------------------- loaders

def load_chain_signals(s3: Any, ticker: str) -> dict[str, dict[date, float]]:
    """Every chain-derived series by session from the derived artifact —
    empty dicts until the collector has derived (honest absence)."""
    from app.data import r2  # late import keeps module collector-importable

    df = r2.get_parquet(s3, CHAIN_SIGNALS_KEY.format(ticker=ticker))
    return {col: _series_from(df, col) for col in (
        "skew_25d", "term_slope_30_90", "atm_iv_30d", "atm_iv_90d",
        "net_gex", "net_dex", "put_call_ratio", "max_pain_dist_pct",
    )}


def load_hv_inhouse(s3: Any, ticker: str) -> dict[date, float]:
    from app.data import r2

    return _series_from(r2.get_parquet(s3, HV_KEY.format(ticker=ticker)), "hv_30d")
