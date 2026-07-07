"""Massive lake readers — PIT-bounded access to the banked Massive prefixes.

F0 (ENGINE-V4 data spine). Massive Options Basic contributes daily OHLCV
aggregates for QQQ/IWM option contracts: a COVERAGE and VOLUME CROSS-CHECK.
It carries no bid/ask, so it is NEVER a fill source (owner decision, ENGINE-V4
masterplan §F5; guardrail #1 — fills quote from real NBBO only).

Layouts (written by collector/backfill_massive.py):
  reference/massive/contracts/ticker={T}.parquet            contract directory
  reference/massive/option_agg/ticker={T}/symbol={S}.parquet daily OHLCV rows

PIT: `option_agg` rows carry a session date and are truncated at as_of, with
LookaheadError on requests beyond it — same contract as MarketView. The
contract directory carries no listing timestamps, so it CANNOT answer "which
contracts existed at date T"; `contracts_reference` is exposed for coverage
counting only and is documented as non-point-in-time reference metadata.
Absent data → None (honest `unavailable`).
"""

from __future__ import annotations

from collections import OrderedDict
from datetime import date, datetime
from typing import Any

import pandas as pd

from app.data import r2
from app.data.pit import as_of_parts
from app.engine.market import LookaheadError

_FRAME_CACHE: OrderedDict[str, pd.DataFrame] = OrderedDict()
_FRAME_CACHE_MAX = 32  # bounded — post-OOM rule


def _cached_frame(s3: Any, key: str) -> pd.DataFrame | None:
    if key in _FRAME_CACHE:
        _FRAME_CACHE.move_to_end(key)
        return _FRAME_CACHE[key]
    df = r2.get_parquet(s3, key)
    if df is None:
        return None
    _FRAME_CACHE[key] = df
    while len(_FRAME_CACHE) > _FRAME_CACHE_MAX:
        _FRAME_CACHE.popitem(last=False)
    return df


def option_agg(
    s3: Any,
    ticker: str,
    occ_symbol: str,
    as_of: date | datetime,
    session: date | None = None,
) -> pd.DataFrame | None:
    """Daily OHLCV aggregate rows for one contract, dated at or before as_of.

    occ_symbol uses Massive's own key (e.g. "O:QQQ240708C00408000" — the
    collector banks files under that symbol verbatim). Volume/OHLC only:
    cross-check data, never a fill price.

    `session` narrows to one session and raises LookaheadError when that
    session lies beyond as_of (an explicit future request — guardrail #2);
    without it, rows are truncated at as_of and a contract with nothing
    visible yet is an honest None, not an error.

    Rows are DAILY aggregates — end-of-day observations — so a datetime
    as_of excludes the as_of session itself: today's daily OHLCV does not
    exist mid-session (docs/HONESTY.md)."""
    bound, moment = as_of_parts(as_of)
    if session is not None and session > bound:
        raise LookaheadError(
            f"massive option_agg {occ_symbol} at {session} requested with as_of {bound}"
        )
    key = f"reference/massive/option_agg/ticker={ticker}/symbol={occ_symbol}.parquet"
    df = _cached_frame(s3, key)
    if df is None or df.empty or "date" not in df.columns:
        return None
    stamps = pd.to_datetime(df["date"], errors="coerce")
    dates = stamps.dt.date
    visible = dates < bound if moment is not None else dates <= bound
    keep = stamps.notna() & visible
    if session is not None:
        keep &= dates == session
    out = df.loc[keep]
    return out.reset_index(drop=True).copy() if not out.empty else None


def contracts_reference(s3: Any, ticker: str) -> pd.DataFrame | None:
    """The banked contract directory — REFERENCE METADATA, not point-in-time.

    Massive's contract list carries no listing timestamps, so it cannot say
    which contracts existed at a past date. Coverage counting only; simulation
    code must never derive contract existence from it."""
    df = _cached_frame(s3, f"reference/massive/contracts/ticker={ticker}.parquet")
    return df.copy() if df is not None else None  # never hand out the cache


def agg_symbols(s3: Any, ticker: str) -> list[str]:
    """Symbols with banked aggregates (coverage/ledger use — lake listing,
    never called from an engine hot path)."""
    prefix = f"reference/massive/option_agg/ticker={ticker}/"
    out = []
    for k in r2.list_keys(s3, prefix):
        name = k.rsplit("symbol=", 1)[-1]
        if name.endswith(".parquet"):
            out.append(name[: -len(".parquet")])
    return sorted(out)
