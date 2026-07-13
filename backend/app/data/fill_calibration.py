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

Schema truth (review 2026-07-13, verified against the live lake): the
tape's ask_vol/bid_vol/mid_vol columns are CUMULATIVE contract-day
counters, useless per print — the per-print aggressor side is the
{ask_side}/{bid_side}/{mid_side}/{no_side} token in `tags`, which
partitions every session's prints exactly. There is NO per-print
multi-leg marker anywhere in the capture (report_flags carries only
sweep/cross/floor codes), so **package legs are INCLUDED and disclosed**:
a multi-leg print can legitimately execute outside its leg's NBBO, and
those prints land in the beyond-touch tail rather than being silently
(and wrongly) excluded.

Artifact (reference/derived/fill_calibration/ticker={T}.parquet): one row
per (date, side, size_bucket) holding n, the slip HISTOGRAM in fixed bins,
and the session median. Histograms SUM across sessions, so pooled
quantiles are estimated from merged bins — per-session medians never
averaged (that would be wrong math). Every excluded print is counted:
mid/no-side prints carry no aggressor, locked or crossed quotes have no
half-spread to measure against, unparseable rows get their own counter. A
session where nothing is measurable still writes a context-only row, so
its exclusion counts bank and the frozen tape is never re-read for it.

This module is measurement + disclosure only. The engine's configured
slip is untouched; any change to live fill pricing is a separate
owner-gated decision that cites these numbers.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

FILL_CAL_KEY = "reference/derived/fill_calibration/ticker={ticker}.parquet"

# fixed slip-histogram bin edges; bins are (edge, next-edge], first bin is
# (−inf, 0] (mid-or-better), last is (1.5, +inf). Columns are named by their
# UPPER edge. Changing these invalidates every banked row — frozen once
# derived.
SLIP_EDGES = (0.0, 0.25, 0.5, 0.75, 1.0, 1.5)
BIN_COLUMNS = ("b_le0", "b_025", "b_050", "b_075", "b_100", "b_150", "b_gt150")

# print-size buckets (contracts); label is the artifact key
SIZE_BUCKETS = ((1, 1, "1"), (2, 10, "2-10"), (11, 50, "11-50"),
                (51, 250, "51-250"), (251, None, "251+"))

_PRICE_COLUMNS = ("price", "size", "nbbo_bid", "nbbo_ask")
REQUIRED_COLUMNS = (*_PRICE_COLUMNS, "tags")

_ARTIFACT_SHAPE = frozenset({"date", "side", "size_bucket", "n",
                             *BIN_COLUMNS})


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
    rows plus, always, the session's exclusion accounting. Returns None
    only on an unrecognized shape (missing required columns / empty) —
    a readable session ALWAYS banks at least a context-only row."""
    if prints is None or prints.empty \
            or not set(REQUIRED_COLUMNS).issubset(prints.columns):
        return None
    t = prints.copy()
    for col in _PRICE_COLUMNS:
        t[col] = pd.to_numeric(t[col], errors="coerce")
    n_raw = int(len(t))
    parse_ok = t[list(_PRICE_COLUMNS)].notna().all(axis=1)
    n_unparseable = int((~parse_ok).sum())
    t = t[parse_ok]

    # a locked (bid == ask) or crossed (bid > ask) quote has no half-spread
    # to measure a concession against; zero/negative bids are not a market
    good_quote = (t["nbbo_bid"] > 0) & (t["nbbo_ask"] > t["nbbo_bid"])
    n_bad_quote = int((~good_quote).sum())
    t = t[good_quote]

    # per-print aggressor side: the tags token (the *_vol columns are
    # cumulative contract-day counters — see module docstring)
    tags = t["tags"].astype(str)
    is_ask = tags.str.contains("ask_side", regex=False)
    is_bid = ~is_ask & tags.str.contains("bid_side", regex=False)
    n_mid = int((~is_ask & ~is_bid
                 & tags.str.contains("mid_side", regex=False)).sum())
    n_no_side = int(len(t) - is_ask.sum() - is_bid.sum() - n_mid)

    m = (t["nbbo_bid"] + t["nbbo_ask"]) / 2
    half = t["nbbo_ask"] - m
    slip = pd.Series(float("nan"), index=t.index, dtype="float64")
    slip[is_ask] = (t["price"] - m)[is_ask] / half[is_ask]
    slip[is_bid] = (m - t["price"])[is_bid] / half[is_bid]

    context = {"tape_prints": n_raw, "n_unparseable": n_unparseable,
               "n_bad_quote": n_bad_quote, "n_mid_side": n_mid,
               "n_no_side": n_no_side}
    rows: list[dict[str, Any]] = []
    # build the working columns on the FULL frame, then filter once —
    # .assign of full-index Series onto an empty filtered frame resurrects
    # rows via index alignment (found by the nothing-measurable fixture)
    work = t.assign(
        _slip=slip,
        _side=pd.Series("ask", index=t.index).where(is_ask, "bid"),
        _bucket=t["size"].map(_bucket_label),
    )
    sided = work[is_ask | is_bid]
    for (side, bucket), grp in sided.groupby(["_side", "_bucket"]):
        bins = _bin_counts(grp["_slip"])
        rows.append({
            "side": side, "size_bucket": bucket, "n": int(len(grp)),
            **dict(zip(BIN_COLUMNS, bins, strict=True)),
            "slip_median": round(float(grp["_slip"].median()), 4),
            **context,
        })
    if not rows:
        # nothing measurable — bank the accounting anyway so the frozen
        # tape session is never re-read and its exclusions are disclosed
        rows.append({"side": "none", "size_bucket": "none", "n": 0,
                     **dict.fromkeys(BIN_COLUMNS, 0),
                     "slip_median": None, **context})
    return rows


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


def pooled_summary(df: pd.DataFrame | None) -> dict[str, Any] | None:
    """Merge every banked session into per-(side, bucket) pooled stats.
    Bin counts sum exactly; quantiles are bin estimates; shares are exact.
    None on an unrecognized artifact shape (honest absence, never a 500)."""
    if df is None or df.empty or not _ARTIFACT_SHAPE.issubset(df.columns):
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
            continue  # context-only rows carry no measurements
        out["buckets"].append({
            "side": side, "size_bucket": bucket, "prints": n,
            "slip_p50_est": _quantile_from_bins(bins, 0.5),
            "slip_p75_est": _quantile_from_bins(bins, 0.75),
            "slip_p90_est": _quantile_from_bins(bins, 0.9),
            "share_mid_or_better": round(bins[0] / n, 4),
            "share_beyond_touch": round((bins[5] + bins[6]) / n, 4),
        })
    ctx_cols = ("tape_prints", "n_unparseable", "n_bad_quote",
                "n_mid_side", "n_no_side")
    if set(ctx_cols).issubset(df.columns):
        per_date = df.drop_duplicates(subset=["date"])
        out["excluded"] = {c: int(per_date[c].sum()) for c in ctx_cols[1:]}
        out["tape_prints"] = int(per_date["tape_prints"].sum())
    return out


def load_fill_calibration(s3: Any, ticker: str) -> pd.DataFrame | None:
    from app.data import r2

    return r2.get_parquet(s3, FILL_CAL_KEY.format(ticker=ticker))
