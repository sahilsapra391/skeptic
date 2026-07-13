"""Fill-model calibration from the UW option tape (D3d — measured, never
asserted; REPORTED, never scored).

The engine's fill model (app/engine/fills.py, guardrail #1) concedes a
CONFIGURED fraction of the half-spread from mid toward the adverse quote:
    BUY  fill = mid + slip · (ask − mid)
    SELL fill = mid − slip · (mid − bid)
The tape gives the same quantity MEASURED: every print carries its NBBO at
execution and its aggressor side, so each print implies the slip a real
aggressor actually paid:
    ask-side print:  slip = (price − mid) / (ask − mid)
    bid-side print:  slip = (mid − price) / (mid − bid)
slip ≤ 0 is mid-or-better (price improvement); slip = 1 is exactly the
touch; slip > 1 printed beyond the displayed quote (the F5 displayed-depth
story, now with a number).

Artifact (reference/derived/fill_calibration/ticker={T}.parquet): one row
per (date, side, size_bucket) holding n, the slip HISTOGRAM in fixed bins,
and the session median. Histograms SUM across sessions, so pooled
quantiles are estimated from merged bins — per-session medians never
averaged (that would be wrong math). Excluded prints are counted, never
silently dropped: multi-leg packages legitimately print outside the leg
NBBO, mid/no-side prints carry no aggressor, locked or crossed quotes
have no half-spread to measure against.

This module is measurement + disclosure only. The engine's configured
slip is untouched; any change to live fill pricing is a separate
owner-gated decision that cites these numbers.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

FILL_CAL_KEY = "reference/derived/fill_calibration/ticker={ticker}.parquet"

# fixed slip-histogram bin edges; bins are (edge, next-edge], first bin is
# (−inf, 0] (mid-or-better), last is (1.5, +inf). Changing these invalidates
# every banked row — treat as frozen once derived.
SLIP_EDGES = (0.0, 0.25, 0.5, 0.75, 1.0, 1.5)
BIN_COLUMNS = ("b_le0", "b_025", "b_050", "b_075", "b_100", "b_150", "b_gt150")

# print-size buckets (contracts); label is the artifact key
SIZE_BUCKETS = ((1, 1, "1"), (2, 10, "2-10"), (11, 50, "11-50"),
                (51, 250, "51-250"), (251, None, "251+"))

_NUMERIC = ("price", "size", "nbbo_bid", "nbbo_ask", "ask_vol", "bid_vol",
            "mid_vol", "no_side_vol", "multi_vol", "stock_multi_vol")


def _bucket_label(size: float) -> str:
    for lo, hi, label in SIZE_BUCKETS:
        if size >= lo and (hi is None or size <= hi):
            return label
    return SIZE_BUCKETS[0][2]  # size < 1 cannot occur on a real print


def _bin_counts(slips: pd.Series) -> list[int]:
    edges = [-float("inf"), *SLIP_EDGES, float("inf")]
    counts = pd.cut(slips, bins=edges).value_counts(sort=False)
    return [int(c) for c in counts]


def calibrate_session(prints: pd.DataFrame) -> list[dict[str, Any]] | None:
    """Reduce one session's tape prints to (side, size_bucket) histogram
    rows. Returns None on an unrecognized shape (honest absence — the
    derive leaves no row and retries when the input heals)."""
    need = {"price", "size", "nbbo_bid", "nbbo_ask", "ask_vol", "bid_vol"}
    if prints is None or prints.empty or not need.issubset(prints.columns):
        return None
    t = prints.copy()
    for col in _NUMERIC:
        t[col] = (pd.to_numeric(t[col], errors="coerce")
                  if col in t.columns else 0.0)
    t = t.dropna(subset=["price", "size", "nbbo_bid", "nbbo_ask"])
    if t.empty:
        return None

    n_all = int(len(t))
    multi = (t["multi_vol"] > 0) | (t["stock_multi_vol"] > 0)
    n_multi = int(multi.sum())
    t = t[~multi]
    # a locked (bid == ask) or crossed (bid > ask) quote has no half-spread
    # to measure a concession against; zero/negative bids are not a market
    bad_quote = ~((t["nbbo_bid"] > 0) & (t["nbbo_ask"] > t["nbbo_bid"]))
    n_bad_quote = int(bad_quote.sum())
    t = t[~bad_quote]

    is_ask = t["ask_vol"] > 0
    is_bid = ~is_ask & (t["bid_vol"] > 0)
    n_mid = int((~is_ask & ~is_bid & (t["mid_vol"] > 0)).sum())
    n_no_side = int(len(t) - is_ask.sum() - is_bid.sum() - n_mid)

    m = (t["nbbo_bid"] + t["nbbo_ask"]) / 2
    half = t["nbbo_ask"] - m
    slip = pd.Series(float("nan"), index=t.index, dtype="float64")
    slip[is_ask] = (t["price"] - m)[is_ask] / half[is_ask]
    slip[is_bid] = (m - t["price"])[is_bid] / half[is_bid]

    context = {"tape_prints": n_all, "n_multi": n_multi,
               "n_bad_quote": n_bad_quote, "n_mid_side": n_mid,
               "n_no_side": n_no_side}
    rows: list[dict[str, Any]] = []
    sided = t[is_ask | is_bid].assign(
        _slip=slip[is_ask | is_bid],
        _side=pd.Series("ask", index=t.index).where(is_ask, "bid"),
        _bucket=t["size"].map(_bucket_label),
    )
    for (side, bucket), grp in sided.groupby(["_side", "_bucket"]):
        bins = _bin_counts(grp["_slip"])
        rows.append({
            "side": side, "size_bucket": bucket, "n": int(len(grp)),
            **dict(zip(BIN_COLUMNS, bins, strict=True)),
            "slip_median": round(float(grp["_slip"].median()), 4),
            **context,
        })
    return rows or None


def _quantile_from_bins(bins: list[int], q: float) -> float | None:
    """Quantile ESTIMATE from merged histogram bins, linear within a bin.
    The open end bins clamp to their finite edge (≤0 → 0.0, >1.5 → 1.5) —
    documented as an estimate, never presented as an exact statistic."""
    n = sum(bins)
    if n == 0:
        return None
    edges = [(None, 0.0), (0.0, 0.25), (0.25, 0.5), (0.5, 0.75),
             (0.75, 1.0), (1.0, 1.5), (1.5, None)]
    target = q * n
    cum = 0
    for count, (lo, hi) in zip(bins, edges, strict=True):
        if count == 0:
            continue
        if cum + count >= target:
            if lo is None:
                return 0.0
            if hi is None:
                return 1.5
            frac = (target - cum) / count
            return round(lo + frac * (hi - lo), 4)
        cum += count
    return 1.5


def pooled_summary(df: pd.DataFrame) -> dict[str, Any] | None:
    """Merge every banked session into per-(side, bucket) pooled stats.
    Bin counts sum exactly; quantiles are bin estimates; shares are exact."""
    if df is None or df.empty or "side" not in df.columns:
        return None
    dates = sorted(df["date"].astype(str).unique())
    out: dict[str, Any] = {
        "sessions": len(dates), "first": dates[0], "last": dates[-1],
        "buckets": [],
    }
    for (side, bucket), grp in df.groupby(["side", "size_bucket"]):
        bins = [int(grp[c].sum()) for c in BIN_COLUMNS]
        n = int(grp["n"].sum())
        if n == 0:
            continue
        out["buckets"].append({
            "side": side, "size_bucket": bucket, "prints": n,
            "slip_p50_est": _quantile_from_bins(bins, 0.5),
            "slip_p75_est": _quantile_from_bins(bins, 0.75),
            "slip_p90_est": _quantile_from_bins(bins, 0.9),
            "share_mid_or_better": round(bins[0] / n, 4),
            "share_beyond_touch": round((bins[5] + bins[6]) / n, 4),
        })
    ctx_cols = ("tape_prints", "n_multi", "n_bad_quote", "n_mid_side",
                "n_no_side")
    if set(ctx_cols).issubset(df.columns):
        per_date = df.drop_duplicates(subset=["date"])
        out["excluded"] = {c: int(per_date[c].sum()) for c in ctx_cols[1:]}
        out["tape_prints"] = int(per_date["tape_prints"].sum())
    return out


def load_fill_calibration(s3: Any, ticker: str) -> pd.DataFrame | None:
    from app.data import r2

    return r2.get_parquet(s3, FILL_CAL_KEY.format(ticker=ticker))
