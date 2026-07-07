"""Shared point-in-time primitives for the F0 data-spine readers.

Two rules every new-source reader inherits (established once, here):

* The session bound of a datetime `as_of` derives from the UTC-NORMALIZED
  moment, never the caller's local calendar date — an exotic-offset caller
  must not mark a live session "strictly before the bound" and see its
  whole file.
* Row stamps with no timezone reference FAIL CLOSED. A naive wall-clock
  string could mean ET or UTC; guessing can hide up to a session of
  lookahead, so unknowable stamps are treated as unobservable rather than
  localized by assumption.
"""

from __future__ import annotations

from datetime import date, datetime

import pandas as pd


def as_of_parts(as_of: date | datetime) -> tuple[date, pd.Timestamp | None]:
    """(session bound, intra-session moment).

    A bare date means the whole session is visible (end-of-day view); a
    datetime bounds rows within its UTC-normalized session. Naive datetimes
    are taken as UTC (documented; all engine clocks pass tz-aware)."""
    if isinstance(as_of, datetime):
        ts = pd.Timestamp(as_of)
        ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
        return ts.date(), ts
    return as_of, None


# an explicit UTC marker or numeric offset at the end of the stamp:
# "…Z", "…+00:00", "…-04:00", "…+0000"
_OFFSET_RE = r"(?:Z|[+-]\d{2}:?\d{2})\s*$"


def stamps_utc(series: pd.Series) -> pd.Series | None:
    """Row observation stamps as UTC, or None when the column carries no
    timezone reference (caller fails closed).

    tz-awareness is detected at the VALUE level (an explicit Z/offset in the
    string), not from pandas parse dtypes — pandas collapses mixed-offset
    columns unpredictably. Offset-less rows become NaT and drop out of any
    ≤-moment comparison (fail closed per row)."""
    if pd.api.types.is_datetime64_any_dtype(series):
        if getattr(series.dtype, "tz", None) is None:
            return None  # parquet naive datetimes: unknowable wall clock
        return pd.Series(series.dt.tz_convert("UTC"))
    try:
        as_str = series.astype("string")
        has_offset = as_str.str.contains(_OFFSET_RE, na=False, regex=True)
        if not bool(has_offset.any()):
            return None  # no stamp in the column names its timezone
        return pd.to_datetime(
            series.where(has_offset), errors="coerce", utc=True, format="mixed"
        )
    except (ValueError, TypeError):
        return None
