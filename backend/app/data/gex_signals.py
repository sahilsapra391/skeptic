"""Dealer-positioning signals (ENGINE-V4 F1) — net GEX / net DEX.

Source: reference/uw/greek_exposure/ticker={T}.parquet — Unusual Whales'
daily EOD dealer greek-exposure aggregates, banked nightly by the UW
collector (~250 sessions from 2025-07-08; the window deepens every night
with no redeploy — self-improvement thesis, no derivation artifact
needed because the vendor series is already one row per session).

Signals (VENDOR UNITS — meaningful in SIGN and RANK only, never as raw
thresholds; the parser refuses raw-unit values because a silent upstream
rescale would corrupt every threshold spec with no error):
  net_gex   call_gamma + put_gamma. The vendor's sign convention embeds
            the standard dealer-positioning assumption (puts arrive
            negative); positive = dealers long gamma (moves dampened),
            negative = short gamma (moves amplified).
  net_dex   call_delta + put_delta, same convention: net dealer delta.

Coverage honesty (owner decision 2026-07-07): specs conditioned on these
indicators REFUSE pre-run when the backtest window starts before the
signal's first covered session — a multi-year run with one covered year
is corrupted stats wearing a long window, not a thin sample (prevention
beats correction; the D2 slice-refusal precedent).

gex_flip_distance was surveyed and DEFERRED to its own design pass: the
banked 50-strike EOD snapshot is structurally the wrong input for
locating a zero-gamma point (~35% of sessions have no sign transition;
wing-noise transitions produce absurd values like −69% of spot).
Disclosed, not dropped — it needs deeper strike profiles or a vendor
zero-gamma endpoint (paid tier).
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd

GEX_SERIES_KEY = "reference/uw/greek_exposure/ticker={ticker}.parquet"


def load_dealer_exposure(
    s3: Any, ticker: str
) -> tuple[dict[date, float], dict[date, float]]:
    """(net_gex by session, net_dex by session) from the banked UW series
    — vendor units. Empty dicts until the collector has banked the family
    (honest absence: the indicators evaluate False, never a guess)."""
    from app.data import r2  # late import keeps module import light

    df = r2.get_parquet(s3, GEX_SERIES_KEY.format(ticker=ticker))
    if df is None or df.empty or "date" not in df.columns:
        return {}, {}
    needed = {"call_gamma", "put_gamma", "call_delta", "put_delta"}
    if not needed.issubset(df.columns):
        return {}, {}  # unrecognized series shape — honest absence
    # vendor JSON dtypes are untrusted (same rule as every reader): coerce
    df = df.copy()
    for col in needed:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    stamps = pd.to_datetime(df["date"], errors="coerce")
    df = df.loc[stamps.notna()]
    df["_day"] = stamps.loc[stamps.notna()].dt.date
    # vendor re-captures can duplicate a session — last write wins
    df = df.drop_duplicates(subset=["_day"], keep="last")
    gex: dict[date, float] = {}
    dex: dict[date, float] = {}
    for day, cg, pg, cd, pdl in zip(df["_day"], df["call_gamma"],
                                    df["put_gamma"], df["call_delta"],
                                    df["put_delta"], strict=True):
        if not (pd.isna(cg) or pd.isna(pg)):
            gex[day] = float(cg) + float(pg)
        if not (pd.isna(cd) or pd.isna(pdl)):
            dex[day] = float(cd) + float(pdl)
    return gex, dex
