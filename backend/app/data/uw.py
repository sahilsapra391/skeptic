"""Unusual Whales lake readers — PIT-bounded access to the banked UW prefixes.

F0 (ENGINE-V4 data spine): these readers establish the point-in-time bounds
and `unavailable` semantics that every UW signal phase (F1 dealer positioning,
F2 flow, F3 OI/pin) will inherit. Nothing in the engine consumes them yet —
they ship with tests and coverage surfacing only (zero engine-behavior change).

Layouts (written by collector/backfill_unusual_whales.py):
  reference/uw/{family}/ticker={T}.parquet        series (one obs per date)
  uw/{family}/ticker={T}/date={D}/rows.parquet    per-session, intraday rows
  uw/{family}/date={D}/rows.parquet               per-session, market-wide
  uw/option_intraday/ticker={T}/symbol={S}/date={D}/bars.parquet  1-min candles

PIT rules established here (guardrail #2, inherited by all later phases):
  * `as_of` is REQUIRED everywhere. A request for a session beyond it raises
    LookaheadError — same contract as MarketView.
  * ROW-LEVEL truncation: per-session files carry intraday timestamps, so a
    reader at 10:35 returns only rows stamped at or before 10:35 — file-date
    filtering alone is not point-in-time for intraday data.
  * `captured_at` / `created_at` are collector metadata, never observation
    time. Rows predating our capture carry UW's own historical timestamps,
    trusted as observation time and disclosed in docs/HONESTY.md.
  * Absent data → None (honest `unavailable`), never a guess or a zero.
  * UW 1-min bars are side-attributed TRADE CANDLES — no NBBO bid/ask.
    They inform the decision clock and fill validation, NEVER a fill price
    (guardrail #1: fills quote from real NBBO only).
"""

from __future__ import annotations

from collections import OrderedDict
from datetime import date, datetime
from typing import Any

import pandas as pd

from app.data import r2
from app.data.pit import as_of_parts, stamps_utc
from app.engine.market import LookaheadError

# ── family registry (probe-verified against the lake, 2026-07-07) ──────────
# Per-session families under uw/{family}/ticker={T}/date={D}/rows.parquet.
TICKER_DATE_FAMILIES = frozenset({
    "atm_chains", "darkpool", "etf_tide", "expiry_breakdown",
    "flow_per_strike", "flow_per_strike_intraday", "greek_exposure_expiry",
    "greek_exposure_strike", "greek_exposure_strike_expiry", "greek_flow",
    "greek_flow_expiry", "interpolated_iv", "lit_flow", "max_pain",
    "net_prem_ticks", "nope", "oi_change", "oi_per_expiry", "oi_per_strike",
    "option_historic", "option_volume_profile", "spot_exposures",
    "spot_exposures_by_expiry", "spot_exposures_expiry_strike",
    "spot_exposures_strike", "stock_price_levels",
    "stock_volume_price_levels", "vol_term_structure", "volume_oi_expiry",
})
# Market-wide per-session families under uw/{family}/date={D}/rows.parquet.
MARKET_DATE_FAMILIES = frozenset({
    "market_oi_change", "market_tide", "market_top_net_impact",
    "net_flow_expiry",
})
# Series families under reference/uw/{family}/ticker={T}.parquet.
SERIES_FAMILIES = frozenset({
    "etf_exposure", "etf_holdings", "etf_inoutflow", "flow_alerts",
    "flow_per_expiry", "flow_recent", "greek_exposure", "greeks",
    "hist_risk_reversal_skew", "institution_ownership", "iv_rank",
    "option_contracts", "options_volume", "seasonality_monthly",
    "seasonality_year_month", "shorts_data", "shorts_ftds",
    "shorts_interest_float", "shorts_volumes_by_exchange",
    "vol_variance_risk_premium", "volatility_realized",
})

# Observation-time column, searched in order. `captured_at`/`created_at` are
# deliberately excluded — collector metadata is never observation time.
_TIME_COLUMNS = ("timestamp", "time", "start_time", "executed_at", "tape_time")
_DATE_COLUMNS = ("date", "trading_date", "report_date", "settlement_date")

_FRAME_CACHE: OrderedDict[str, pd.DataFrame] = OrderedDict()
_FRAME_CACHE_MAX = 64  # bounded — post-OOM rule: every cache has a ceiling


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


def _time_column(df: pd.DataFrame) -> str | None:
    for col in _TIME_COLUMNS:
        if col in df.columns:
            return col
    return None


def _truncate_rows(
    df: pd.DataFrame, session: date, session_bound: date, moment: pd.Timestamp | None
) -> pd.DataFrame:
    """Row-level PIT: keep rows observed at or before as_of. Files from
    sessions strictly before the bound are fully visible; the as_of session
    itself is truncated at the intra-session moment when one is given."""
    if moment is None or session < session_bound:
        return df
    col = _time_column(df)
    if col is None:
        # session-level rows only (no intraday stamps): the whole file is a
        # same-day observation — visible only at a date-level view, so an
        # intra-session moment honestly sees nothing rather than guessing.
        return df.iloc[0:0]
    stamps = stamps_utc(df[col])
    if stamps is None:
        # stamps carry no timezone reference — FAIL CLOSED rather than
        # localize by assumption (a naive ET stamp read as UTC would leak
        # up to a session of lookahead)
        return df.iloc[0:0]
    return df.loc[stamps.notna() & (stamps <= moment)].reset_index(drop=True)


def daily_rows(
    s3: Any,
    family: str,
    ticker: str | None,
    session: date,
    as_of: date | datetime,
) -> pd.DataFrame | None:
    """Rows for one per-session family file, PIT-truncated at as_of.

    ticker=None reads the market-wide layout. Returns None when the lake has
    nothing for that (family, session) — honest `unavailable`.
    """
    if ticker is None:
        if family not in MARKET_DATE_FAMILIES:
            raise ValueError(f"unknown market-wide UW family: {family}")
        key = f"uw/{family}/date={session}/rows.parquet"
    else:
        if family not in TICKER_DATE_FAMILIES:
            raise ValueError(f"unknown per-ticker UW family: {family}")
        key = f"uw/{family}/ticker={ticker}/date={session}/rows.parquet"
    session_bound, moment = as_of_parts(as_of)
    if session > session_bound:
        raise LookaheadError(f"uw/{family} at {session} requested with as_of {as_of}")
    df = _cached_frame(s3, key)
    if df is None or df.empty:
        return None
    out = _truncate_rows(df, session, session_bound, moment)
    return out.copy() if not out.empty else None


def series(
    s3: Any, family: str, ticker: str, as_of: date | datetime
) -> pd.DataFrame | None:
    """A reference series truncated to observations dated at or before as_of.

    Series rows are session-level (typically end-of-day) observations, so a
    datetime as_of EXCLUDES the as_of session itself — today's daily
    observation does not exist mid-session (docs/HONESTY.md). Rows without a
    recognizable date column cannot be bounded and are dropped (never
    returned unbounded)."""
    if family not in SERIES_FAMILIES:
        raise ValueError(f"unknown UW series family: {family}")
    session_bound, moment = as_of_parts(as_of)
    df = _cached_frame(s3, f"reference/uw/{family}/ticker={ticker}.parquet")
    if df is None or df.empty:
        return None
    col = next((c for c in _DATE_COLUMNS if c in df.columns), None)
    if col is None:
        col = _time_column(df)
    if col is None:
        return None  # unboundable rows are unavailable, not unbounded
    stamps = pd.to_datetime(df[col], errors="coerce", utc=True, format="mixed")
    dates = stamps.dt.date
    visible = dates < session_bound if moment is not None else dates <= session_bound
    out = df.loc[stamps.notna() & visible]
    return out.reset_index(drop=True).copy() if not out.empty else None


def minute_bars(
    s3: Any, ticker: str, occ_symbol: str, session: date, as_of: date | datetime
) -> pd.DataFrame | None:
    """UW 1-min per-contract candles for one session, PIT-truncated.

    These are side-attributed TRADE CANDLES (no NBBO) — decision-clock and
    validation data only, never a fill source (guardrail #1)."""
    session_bound, moment = as_of_parts(as_of)
    if session > session_bound:
        raise LookaheadError(
            f"uw/option_intraday {occ_symbol} at {session} requested with as_of {as_of}"
        )
    key = f"uw/option_intraday/ticker={ticker}/symbol={occ_symbol}/date={session}/bars.parquet"
    df = _cached_frame(s3, key)
    if df is None or df.empty:
        return None
    out = _truncate_rows(df, session, session_bound, moment)
    if out.empty:
        return None
    out = out.copy()
    col = _time_column(out)
    if col is not None:
        stamps = pd.to_datetime(out[col], errors="coerce", utc=True, format="mixed")
        out = out.assign(_minute_ts=stamps).sort_values("_minute_ts")
        out = out.drop(columns=["_minute_ts"]).reset_index(drop=True)
    return out


def daily_sessions(s3: Any, family: str, ticker: str | None) -> list[str]:
    """Sessions banked for a per-session family (coverage/ledger use — this
    is a lake listing, never called from an engine hot path)."""
    if ticker is None:
        if family not in MARKET_DATE_FAMILIES:
            raise ValueError(f"unknown market-wide UW family: {family}")
        return r2.list_date_prefixes(s3, f"uw/{family}/")
    if family not in TICKER_DATE_FAMILIES:
        raise ValueError(f"unknown per-ticker UW family: {family}")
    return r2.list_date_prefixes(s3, f"uw/{family}/ticker={ticker}/")
