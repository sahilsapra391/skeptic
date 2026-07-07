"""IVS-derived vol-surface signals (F4) — skew and term structure.

The fitted surface (reference/ivol/ivs/, 2007+, 13 tenors × OTM%-stepped
strikes with real deltas) is DERIVED ONCE per session into a compact
artifact — reference/derived/ivs_signals/ticker={T}.parquet — by
collector/derive_ivs_signals.py (nightly, incremental). Backtests read
the artifact O(1); computing skew per session at run time would mean
~4,900 surface reads per run (post-OOM rule: no live derivations in the
run path). Self-improvement: new IVS sessions flow in on the next
collector pass, no redeploy.

Conventions (owner decision 2026-07-07 — fixed market standards, never
parameterized on spec):
  skew_25d          IV(25Δ put) − IV(25Δ call) at the 30d tenor, VOL
                    POINTS (×100). 25Δ is linearly interpolated in delta
                    between the bracketing grid rows, calls and puts
                    separately. Positive = puts rich = downside fear.
  term_slope_30_90  ATM IV(90d) − ATM IV(30d), VOL POINTS, from each
                    tenor's exact ATM row (OTM% = 0; no interpolation).
                    Negative = inverted term structure = stress.

Honesty: a session whose surface lacks the needed tenor or bracketing
deltas derives NOTHING for that signal (None — unavailable, never
interpolated across tenors or extrapolated beyond the grid). The
derivation lives HERE (not mirrored in the collector) so the math has
exactly one implementation, fixture-tested in the backend battery.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd

SIGNALS_KEY = "reference/derived/ivs_signals/ticker={ticker}.parquet"
SKEW_TENOR_DAYS = 30
TERM_SHORT_DAYS = 30
TERM_LONG_DAYS = 90
SKEW_DELTA = 0.25  # |delta| of the risk-reversal wings


def _interp_iv_at_delta(rows: pd.DataFrame, target_abs_delta: float) -> float | None:
    """IV at |delta| == target, linearly interpolated between the two
    bracketing grid rows. None when the grid doesn't bracket the target
    (fail closed — never extrapolate beyond the fitted grid)."""
    if rows.empty:
        return None
    pts = rows[["delta", "IV"]].copy()
    pts["abs_delta"] = pts["delta"].abs()
    pts = pts.dropna().sort_values("abs_delta")
    below = pts[pts["abs_delta"] <= target_abs_delta]
    above = pts[pts["abs_delta"] >= target_abs_delta]
    if below.empty or above.empty:
        return None
    lo = below.iloc[-1]
    hi = above.iloc[0]
    if hi["abs_delta"] == lo["abs_delta"]:
        return float(lo["IV"])
    w = (target_abs_delta - lo["abs_delta"]) / (hi["abs_delta"] - lo["abs_delta"])
    return float(lo["IV"] + w * (hi["IV"] - lo["IV"]))


def _atm_iv(surface: pd.DataFrame, tenor: int) -> float | None:
    """The tenor's ATM IV: the OTM% == 0 rows' mean of call+put IV (both
    exist on the fitted grid; averaging removes the tiny C/P fit gap)."""
    t = surface[(surface["period"] == tenor) & (surface["out-of-the-money %"] == 0)]
    if t.empty:
        return None
    ivs = pd.to_numeric(t["IV"], errors="coerce").dropna()
    if ivs.empty:
        return None
    return float(ivs.mean())


def derive_signal_row(surface: pd.DataFrame) -> dict[str, float | None]:
    """One session's surface → the derived signal values (vol points).
    Missing tenors/brackets yield None per signal — honest absence."""
    out: dict[str, float | None] = {
        "skew_25d": None,
        "term_slope_30_90": None,
        "atm_iv_30d": None,
        "atm_iv_90d": None,
    }
    if surface is None or surface.empty:
        return out
    t30 = surface[surface["period"] == SKEW_TENOR_DAYS]
    put_iv = _interp_iv_at_delta(t30[t30["Call/Put"] == "P"], SKEW_DELTA)
    call_iv = _interp_iv_at_delta(t30[t30["Call/Put"] == "C"], SKEW_DELTA)
    if put_iv is not None and call_iv is not None:
        out["skew_25d"] = round((put_iv - call_iv) * 100.0, 4)
    atm30 = _atm_iv(surface, TERM_SHORT_DAYS)
    atm90 = _atm_iv(surface, TERM_LONG_DAYS)
    out["atm_iv_30d"] = None if atm30 is None else round(atm30 * 100.0, 4)
    out["atm_iv_90d"] = None if atm90 is None else round(atm90 * 100.0, 4)
    if atm30 is not None and atm90 is not None:
        out["term_slope_30_90"] = round((atm90 - atm30) * 100.0, 4)
    return out


def load_ivs_signals(s3: Any, ticker: str) -> tuple[dict[date, float], dict[date, float]]:
    """(skew_25d by session, term_slope by session) from the derived
    artifact — vol points. Empty dicts until the collector has derived
    (honest absence: the indicators evaluate False, never a guess)."""
    from app.data import r2  # late import keeps this module collector-importable

    df = r2.get_parquet(s3, SIGNALS_KEY.format(ticker=ticker))
    if df is None or df.empty or "date" not in df.columns:
        return {}, {}
    skew: dict[date, float] = {}
    term: dict[date, float] = {}
    dates = pd.to_datetime(df["date"], errors="coerce")
    n = len(df)
    sk_col = df["skew_25d"] if "skew_25d" in df.columns else pd.Series([None] * n)
    ts_col = (df["term_slope_30_90"] if "term_slope_30_90" in df.columns
              else pd.Series([None] * n))
    for d, sk, ts in zip(dates, sk_col, ts_col, strict=True):
        if pd.isna(d):
            continue
        day = d.date()
        if sk is not None and not pd.isna(sk):
            skew[day] = float(sk)
        if ts is not None and not pd.isna(ts):
            term[day] = float(ts)
    return skew, term
