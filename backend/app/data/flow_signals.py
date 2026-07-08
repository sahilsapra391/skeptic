"""Flow / sentiment / pin signals (ENGINE-V4 F2/F3) — derive once, nightly.

Five spec-v7 indicators from UW's per-session families (91 sessions from
2026-02-24, deepening nightly), reduced to one EOD row per session by
collector/derive_flow_signals.py into:
  reference/derived/flow_signals/ticker={T}.parquet
      net_premium · put_call_ratio · nope_eod · max_pain_dist_pct
  reference/derived/market_tide_signals.parquet   (market-wide)
      market_tide

Reduction conventions (probed 2026-07-07, pinned in fixtures):
  net_premium        Σ net_call_premium − Σ net_put_premium over the
                     session's net_prem_ticks rows — the rows are
                     per-minute BUCKETS (probe: last call_volume 10,366
                     vs session sum 7.57M), so the session total is the
                     SUM. Dollars (vendor units) → sign/rank vocabulary.
  put_call_ratio     Σ put_volume / Σ call_volume (same rows) —
                     dimensionless classic; raw thresholds legal.
  nope_eod           the LAST stamp's `nope` (vendor-computed per stamp).
                     Dimensionless but a VENDOR IMPLEMENTATION of the
                     published metric → sign/rank only (owner decision
                     2026-07-08: invariant to monotone rescaling; ~91
                     sessions can't even verify scale stability).
  max_pain_dist_pct  (front max_pain − close)/close × 100, where front =
                     the nearest expiry ≥ the session (owner decision:
                     pin dynamics ARE a front-expiry phenomenon — the
                     convention is the concept). Unit-free % → raw
                     thresholds legal.
  market_tide        the LAST row's net_call_premium − net_put_premium of
                     the market-wide cumulative tide series (probe:
                     median row-to-row diff 6.9M << median |row| 361M ⇒
                     cumulative; the last row is the session total).
                     Dollars → sign/rank vocabulary. MARKET-WIDE: one
                     series for all tickers.

Honesty: a session whose family file is missing or empty derives None
for that family's signals — never a guess. Coverage-capped like F1:
specs conditioned on these refuse windows starting before the signal's
first covered session, and the *_rank_1y forms stay unevaluable below
126 trailing observations (unlock as UW data accrues).
"""

from __future__ import annotations

from datetime import date
from typing import Any, cast

import pandas as pd

FLOW_KEY = "reference/derived/flow_signals/ticker={ticker}.parquet"
TIDE_KEY = "reference/derived/market_tide_signals.parquet"


def _num(df: pd.DataFrame, col: str) -> pd.Series:
    return pd.to_numeric(df[col], errors="coerce")


def derive_flow_row(
    net_prem: pd.DataFrame | None,
    nope: pd.DataFrame | None,
    max_pain: pd.DataFrame | None,
    session: str,
) -> dict[str, float | None]:
    """One session's per-ticker families → the derived EOD signal values.
    Missing/empty/unrecognized inputs yield None per signal."""
    out: dict[str, float | None] = {
        "net_premium": None,
        "put_call_ratio": None,
        "nope_eod": None,
        "max_pain_dist_pct": None,
    }
    if net_prem is not None and not net_prem.empty and {
        "net_call_premium", "net_put_premium", "call_volume", "put_volume",
    }.issubset(net_prem.columns):
        ncp = _num(net_prem, "net_call_premium").sum()
        npp = _num(net_prem, "net_put_premium").sum()
        if pd.notna(ncp) and pd.notna(npp):
            out["net_premium"] = round(float(ncp - npp), 2)
        cv = _num(net_prem, "call_volume").sum()
        pv = _num(net_prem, "put_volume").sum()
        if pd.notna(cv) and pd.notna(pv) and cv > 0:
            out["put_call_ratio"] = round(float(pv / cv), 4)
    if nope is not None and not nope.empty and {
        "timestamp", "nope",
    }.issubset(nope.columns):
        stamps = pd.to_datetime(nope["timestamp"], errors="coerce", utc=True)
        vals = _num(nope, "nope")
        ok = stamps.notna() & vals.notna()
        if ok.any():
            last_idx = stamps[ok].idxmax()
            out["nope_eod"] = round(float(vals[last_idx]), 4)
    if max_pain is not None and not max_pain.empty and {
        "expiry", "max_pain", "close",
    }.issubset(max_pain.columns):
        mp = max_pain.copy()
        mp["_exp"] = pd.to_datetime(mp["expiry"], errors="coerce").dt.date
        mp["_mp"] = _num(mp, "max_pain")
        mp["_close"] = _num(mp, "close")
        day = date.fromisoformat(session)
        front = mp.loc[mp["_exp"].notna() & (mp["_exp"] >= day)
                       & mp["_mp"].notna() & mp["_close"].notna()]
        if not front.empty:
            i = front["_exp"].idxmin()
            mp_v = float(cast("float", front.at[i, "_mp"]))
            close_v = float(cast("float", front.at[i, "_close"]))
            if close_v > 0:
                out["max_pain_dist_pct"] = round(
                    (mp_v - close_v) / close_v * 100.0, 4)
    return out


def derive_tide_row(tide: pd.DataFrame | None) -> dict[str, float | None]:
    """The market-wide cumulative tide's session total: LAST row's
    net_call_premium − net_put_premium (probe-pinned cumulative)."""
    out: dict[str, float | None] = {"market_tide": None}
    if tide is None or tide.empty or not {
        "timestamp", "net_call_premium", "net_put_premium",
    }.issubset(tide.columns):
        return out
    stamps = pd.to_datetime(tide["timestamp"], errors="coerce", utc=True)
    ncp = _num(tide, "net_call_premium")
    npp = _num(tide, "net_put_premium")
    ok = stamps.notna() & ncp.notna() & npp.notna()
    if not ok.any():
        return out
    last_idx = stamps[ok].idxmax()
    out["market_tide"] = round(float(ncp[last_idx] - npp[last_idx]), 2)
    return out


def _series_from(df: pd.DataFrame | None, col: str) -> dict[date, float]:
    if df is None or df.empty or "date" not in df.columns or col not in df.columns:
        return {}
    out: dict[date, float] = {}
    stamps = pd.to_datetime(df["date"], errors="coerce")
    vals = pd.to_numeric(df[col], errors="coerce")
    for d, v in zip(stamps, vals, strict=True):
        if pd.notna(d) and pd.notna(v):
            out[d.date()] = float(v)
    return out


def load_flow_signals(
    s3: Any, ticker: str
) -> tuple[dict[date, float], dict[date, float], dict[date, float], dict[date, float]]:
    """(net_premium, put_call_ratio, nope_eod, max_pain_dist_pct) by
    session from the derived artifact — empty dicts until the collector
    has derived (honest absence; indicators evaluate False)."""
    from app.data import r2  # late import keeps module collector-importable

    df = r2.get_parquet(s3, FLOW_KEY.format(ticker=ticker))
    return (_series_from(df, "net_premium"), _series_from(df, "put_call_ratio"),
            _series_from(df, "nope_eod"), _series_from(df, "max_pain_dist_pct"))


def load_market_tide(s3: Any) -> dict[date, float]:
    """Market-wide tide by session — ONE series regardless of ticker."""
    from app.data import r2

    return _series_from(r2.get_parquet(s3, TIDE_KEY), "market_tide")
