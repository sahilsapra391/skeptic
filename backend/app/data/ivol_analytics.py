"""iVolatility analytics loader: the banked IVX / HV series → engine series.

reference/ivol/ivx/ticker={T}/year={Y}.parquet — per-session rows with a
per-tenor IV index grid; we read the 30d IV Mean (the canonical "IVX").
reference/ivol/hv/... — realized-vol tenors; we read the 30d HV.
Both series run 2005 → now on SPY/QQQ/IWM (22 year-files each) and store
DECIMALS (0.2255 = 22.55% — verified against the lake, 2026-07-05).

These power the spec-v2 market-condition filters (ivx_rank_1y,
ivx_level_30d, hv_iv_spread_30d): the depth the chain-derived
iv_percentile_1y can never have on QQQ/IWM. Missing series are honest
absences — the indicators evaluate False, never a guess.
"""

from __future__ import annotations

from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from typing import Any

import pandas as pd

from app.data import r2
from app.engine.market import LookaheadError

FETCH_WORKERS = 8
IVX_COLUMN = "30d IV Mean"
HV_COLUMN = "30d HV"


def _series_from_years(
    s3: Any, prefix: str, column: str
) -> dict[date, float]:
    keys = r2.list_keys(s3, prefix)
    if not keys:
        return {}

    def one(key: str) -> pd.DataFrame | None:
        df = r2.get_parquet(s3, key)
        if df is None or df.empty or "date" not in df.columns or column not in df.columns:
            return None
        return df[["date", column]]

    with ThreadPoolExecutor(max_workers=FETCH_WORKERS) as pool:
        frames = [f for f in pool.map(one, sorted(keys)) if f is not None]
    if not frames:
        return {}

    combined = pd.concat(frames, ignore_index=True)
    combined[column] = pd.to_numeric(combined[column], errors="coerce")
    combined = combined.dropna(subset=["date", column])
    out: dict[date, float] = {}
    for d, v in zip(pd.to_datetime(combined["date"]).dt.date, combined[column], strict=True):
        out[d] = float(v)  # later year-files win on duplicate dates
    return out


def load_ivx_30d(s3: Any, ticker: str) -> dict[date, float]:
    """date → 30d IV Mean (decimal), full banked history."""
    return _series_from_years(s3, f"reference/ivol/ivx/ticker={ticker}/", IVX_COLUMN)


def load_hv_30d(s3: Any, ticker: str) -> dict[date, float]:
    """date → 30d HV (decimal), full banked history."""
    return _series_from_years(s3, f"reference/ivol/hv/ticker={ticker}/", HV_COLUMN)


# ── IVS surfaces (F0, ENGINE-V4 data spine) ─────────────────────────────────
# reference/ivol/ivs/ticker={T}/date={D}/surface.parquet — one fitted vol
# surface per session (2007+ · ~4,905 sessions/ticker · period × strike ×
# call/put grid, IV and delta as decimals). PIT-bounded here so the F4
# skew/term-structure phase inherits the contract; nothing consumes it yet.

_IVS_NUMERIC = ["period", "strike", "IV", "delta"]
_IVS_CACHE: OrderedDict[str, pd.DataFrame] = OrderedDict()
_IVS_CACHE_MAX = 16  # bounded — post-OOM rule


def load_ivs_surface(
    s3: Any, ticker: str, session: date, as_of: date | datetime
) -> pd.DataFrame | None:
    """The fitted vol surface observed on `session`, or None if not banked.

    Raises LookaheadError when session lies beyond as_of (guardrail #2 —
    a surface is a same-day observation; reading tomorrow's fit is lookahead).
    """
    bound = as_of.date() if isinstance(as_of, datetime) else as_of
    if session > bound:
        raise LookaheadError(f"ivs surface {ticker} {session} requested with as_of {bound}")
    key = f"reference/ivol/ivs/ticker={ticker}/date={session}/surface.parquet"
    if key in _IVS_CACHE:
        _IVS_CACHE.move_to_end(key)
        return _IVS_CACHE[key].copy()
    df = r2.get_parquet(s3, key)
    if df is None or df.empty:
        return None
    out = df.copy()
    for col in _IVS_NUMERIC:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")
    present = [c for c in _IVS_NUMERIC if c in out.columns]
    out = out.dropna(subset=present).reset_index(drop=True)
    if out.empty:
        return None
    _IVS_CACHE[key] = out
    while len(_IVS_CACHE) > _IVS_CACHE_MAX:
        _IVS_CACHE.popitem(last=False)
    return out.copy()


def ivs_sessions(s3: Any, ticker: str) -> list[str]:
    """Sessions with a banked surface (coverage/ledger use — lake listing,
    never called from an engine hot path)."""
    return r2.list_date_prefixes(s3, f"reference/ivol/ivs/ticker={ticker}/")
