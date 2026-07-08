"""On-demand fill audit (ENGINE-V4 F7) — a run's fills vs an
INDEPENDENT vendor's trade record.

Owner decisions (2026-07-08): on-demand only (the replay-receipt
mechanics — deep verification you escalate to, never an ambient stage
on the serialized engine); the audit re-runs the spec deterministically
(same spec + data + seed ⇒ identical fills) and checks each regenerated
option-leg fill against Alpaca minute TRADE bars — a vendor no fill
price ever came from (independence is the point; guardrail #1's fill
sources are DoltHub/iVol/CBOE/modeled, never Alpaca).

The check per fill: trades for the same contract within ±AUDIT_WINDOW_MIN
of the fill bar (whole session when the fill carries no bar time — daily
clock); the fill price must sit inside [min(low) − tol, max(high) + tol]
of those prints, tol = max(ABS_TOL, REL_TOL × fill price) — the same
reviewed band as the nightly cross-validation. Verdicts per fill:
  within        the fill price sits inside the traded range band
  outside       it doesn't — an example worth eyeballing (disclosed rows)
  no_trades     the contract printed nothing in the window — honest
                absence, NEVER counted against the run
  no_coverage   the session has no Alpaca record at all
The stored audit NEVER rewrites the run's verdict (receipt precedent).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

import pandas as pd

from app.data.cross_validation import ABS_TOL, REL_TOL

AUDIT_WINDOW_MIN = 15  # minutes around the fill bar (reviewed constant)
MAX_EXAMPLES = 10


def _fill_moment(day: str, bar_time: str | None) -> datetime | None:
    if not bar_time:
        return None
    try:
        h, m = bar_time.split(":")
        d = datetime.fromisoformat(day)
        return d.replace(hour=int(h), minute=int(m))
    except ValueError:
        return None


def audit_fills(
    fill_log: list[dict[str, Any]],
    bar_times: dict[int, str],
    load_day: Callable[[str], pd.DataFrame | None],
) -> dict[str, Any]:
    """Audit structured fills against per-session Alpaca frames.

    `bar_times` maps position id → the OPEN event's bar time (exits use
    their own event times only via the same map when present — a missing
    time degrades to a whole-session range check, disclosed by kind).
    `load_day` returns the session's Alpaca bars (columns: expiration,
    right, strike, minute_ts, low, high) or None."""
    counts = {"audited": 0, "within": 0, "outside": 0,
              "no_trades": 0, "no_coverage": 0}
    examples: list[dict[str, Any]] = []
    day_cache: dict[str, pd.DataFrame | None] = {}
    for fill in fill_log:
        day = str(fill["day"])
        if day not in day_cache:
            if len(day_cache) > 30:  # bound (OOM guard)
                day_cache.pop(next(iter(day_cache)))
            df = load_day(day)
            if df is not None and not df.empty:
                df = df.assign(
                    _et=pd.to_datetime(df["minute_ts"]).dt.tz_convert(
                        "America/New_York").dt.tz_localize(None),
                    _exp=lambda x: x["expiration"].astype(str),
                    _strike=lambda x: pd.to_numeric(x["strike"],
                                                    errors="coerce"),
                )
            day_cache[day] = df
        df = day_cache[day]
        if df is None or df.empty:
            counts["no_coverage"] += 1
            continue
        rows = df[(df["_exp"] == str(fill["expiration"]))
                  & (df["right"] == fill["right"])
                  & (df["_strike"] == float(fill["strike"]))]
        moment = _fill_moment(day, bar_times.get(int(fill["pid"])))
        if moment is not None and not rows.empty:
            lo_t = moment - timedelta(minutes=AUDIT_WINDOW_MIN)
            hi_t = moment + timedelta(minutes=AUDIT_WINDOW_MIN)
            near = rows[(rows["_et"] >= lo_t) & (rows["_et"] <= hi_t)]
            kind = "bar_window"
            if near.empty:
                near = rows  # degrade to the whole session, disclosed
                kind = "session_range"
        else:
            near = rows
            kind = "session_range"
        if near.empty:
            counts["no_trades"] += 1
            continue
        lo = float(pd.to_numeric(near["low"], errors="coerce").min())
        hi = float(pd.to_numeric(near["high"], errors="coerce").max())
        price = float(fill["price"])
        tol = max(ABS_TOL, price * REL_TOL)
        counts["audited"] += 1
        if lo - tol <= price <= hi + tol:
            counts["within"] += 1
        else:
            counts["outside"] += 1
            if len(examples) < MAX_EXAMPLES:
                examples.append({
                    "day": day, "action": fill["action"],
                    "contract": f"{fill['expiration']} {fill['right']} "
                                f"{fill['strike']:g}",
                    "fill_price": round(price, 4),
                    "traded_low": round(lo, 4), "traded_high": round(hi, 4),
                    "kind": kind, "source": fill.get("source"),
                })
    rate = (round(counts["within"] / counts["audited"], 4)
            if counts["audited"] else None)
    return {**counts, "agreement_rate": rate, "examples": examples,
            "vendor": "alpaca_minute_trades",
            "tolerance": f"max(${ABS_TOL}, {REL_TOL:.0%} of fill price)",
            "window_minutes": AUDIT_WINDOW_MIN}
