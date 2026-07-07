"""Per-session resolution map — which clock and which quote source each
session honestly supports (F0, ENGINE-V4 data spine).

Owner decision (masterplan §2 + clock-vs-quote split, 2026-07-07): the engine
uses the finest HONEST resolution available PER SESSION. Two independent
facts are recorded for every session, because they come from different data:

  clock_resolution — the finest DECISION clock (when the engine may look):
      minute    UW 1-min trade candles exist for the session
      five_min  iVol 5-min NBBO bars or CBOE recorder snapshots exist
      none      EOD data only (daily clock)
    UW minute candles carry NO NBBO — they upgrade the clock and validate
    fills, they are never a fill price (guardrail #1).

  quote_resolution — the best FILL-GRADE quote source, by QUALITY precedence
  (D2 owner amendment 1: real NBBO outranks the delayed recorder even though
  the recorder is finer-grained):
      ivol_5min  true NBBO + greeks (iVolatility 5-min)
      cboe_2min  CBOE recorder chain snapshots (~2 min, ~15-min delayed)
      eod_only   EOD chain only
      none       nothing quotable that session

The map is BUILT by collector/ledger.py after every collection run (nightly +
manual) and only READ here, through a small TTL cache — per-session selection
must be an O(1) artifact lookup, never a live lake probe in a hot path
(post-OOM rule). Self-improvement thesis: as collectors bank new sessions or
finer data, the next ledger rebuild upgrades the map and every later run
picks it up automatically — no code change, no redeploy. A re-run whose
result changed because a session's resolution improved is explained as a
RESOLUTION UPGRADE by the D3 receipts loop (FX.4 wires that in).

Derivation lives HERE (not mirrored in the collector) so the honesty-critical
mapping has exactly one implementation, fixture-tested in the backend battery;
collector/ledger.py imports it by path.
"""

from __future__ import annotations

import time
from collections.abc import Iterable, Mapping
from typing import Any

import pandas as pd

CLOCK_MINUTE = "minute"
CLOCK_FIVE_MIN = "five_min"
CLOCK_NONE = "none"

QUOTE_IVOL_5MIN = "ivol_5min"
QUOTE_CBOE_2MIN = "cboe_2min"
QUOTE_EOD_ONLY = "eod_only"
QUOTE_NONE = "none"

RESOLUTION_MAP_KEY = "state/resolution_map/ticker={ticker}.parquet"

MAP_COLUMNS = [
    "session", "clock_resolution", "quote_resolution",
    "minute_contract_count", "has_eod_chain", "uw_families_present",
    "ivs_present",
]


def derive_resolution_rows(
    minute_contracts_by_session: Mapping[str, int],
    ivol_5min_sessions: Iterable[str],
    recorder_sessions: Iterable[str],
    eod_sessions: Iterable[str],
    ivs_sessions: Iterable[str],
    uw_families_by_session: Mapping[str, int] | None = None,
) -> list[dict[str, Any]]:
    """One row per session (union of every source), ISO-date keyed.

    minute_contracts_by_session: session → distinct contracts with UW 1-min
    bars (0-contract sessions are treated as no minute data).
    uw_families_by_session: session → count of UW per-session signal families
    banked for that date (coverage context, not part of the resolution pick).
    """
    minute = {d: int(n) for d, n in minute_contracts_by_session.items() if int(n) > 0}
    ivol = set(ivol_5min_sessions)
    recorder = set(recorder_sessions)
    eod = set(eod_sessions)
    ivs = set(ivs_sessions)
    families = dict(uw_families_by_session or {})

    rows: list[dict[str, Any]] = []
    for session in sorted(set(minute) | ivol | recorder | eod | ivs | set(families)):
        if session in minute:
            clock = CLOCK_MINUTE
        elif session in ivol or session in recorder:
            clock = CLOCK_FIVE_MIN
        else:
            clock = CLOCK_NONE

        if session in ivol:
            quote = QUOTE_IVOL_5MIN
        elif session in recorder:
            quote = QUOTE_CBOE_2MIN
        elif session in eod:
            quote = QUOTE_EOD_ONLY
        else:
            quote = QUOTE_NONE

        rows.append({
            "session": session,
            "clock_resolution": clock,
            "quote_resolution": quote,
            "minute_contract_count": minute.get(session, 0),
            "has_eod_chain": session in eod,
            "uw_families_present": int(families.get(session, 0)),
            "ivs_present": session in ivs,
        })
    return rows


def timeline_runs(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Compress per-session rows into consecutive (clock, quote) runs for the
    Observatory strip — payload stays small at multi-thousand sessions."""
    runs: list[dict[str, Any]] = []
    for row in rows:
        key = (row["clock_resolution"], row["quote_resolution"])
        if runs and (runs[-1]["clock"], runs[-1]["quote"]) == key:
            runs[-1]["last"] = row["session"]
            runs[-1]["sessions"] += 1
        else:
            runs.append({
                "first": row["session"], "last": row["session"], "sessions": 1,
                "clock": row["clock_resolution"], "quote": row["quote_resolution"],
            })
    return runs


# ── backend read side (TTL-cached artifact lookups) ─────────────────────────

_CACHE: dict[str, tuple[float, pd.DataFrame | None]] = {}
CACHE_SECONDS = 300.0
_CACHE_MAX = 8  # SPY/QQQ/IWM today; bounded regardless


def _load_map(s3: Any, ticker: str) -> pd.DataFrame | None:
    """The banked per-session map for one ticker, or None until the ledger
    has built it (honest absence — surfaces show 'map pending')."""
    now = time.time()
    hit = _CACHE.get(ticker)
    if hit is not None and now - hit[0] < CACHE_SECONDS:
        return hit[1]
    from app.data import r2  # late import keeps this module collector-importable

    df = r2.get_parquet(s3, RESOLUTION_MAP_KEY.format(ticker=ticker))
    if df is not None and (df.empty or "session" not in df.columns):
        df = None
    if df is not None:
        df = df.sort_values("session").reset_index(drop=True)
    _CACHE[ticker] = (now, df)
    while len(_CACHE) > _CACHE_MAX:
        _CACHE.pop(next(iter(_CACHE)))
    return df


def summary(s3: Any, ticker: str) -> dict[str, Any] | None:
    """Coverage-payload block: counts per resolution + compressed timeline.
    None until the collector has built the map (never fabricated)."""
    df = _load_map(s3, ticker)
    if df is None:
        return None
    rows = [{str(k): v for k, v in rec.items()} for rec in df.to_dict("records")]
    clock_counts = df["clock_resolution"].value_counts().to_dict()
    quote_counts = df["quote_resolution"].value_counts().to_dict()

    def _window(mask: pd.Series) -> dict[str, Any] | None:
        sel = df.loc[mask, "session"]
        if sel.empty:
            return None
        return {"first": str(sel.iloc[0]), "last": str(sel.iloc[-1]),
                "sessions": int(len(sel))}

    return {
        "sessions": int(len(df)),
        "clock": {str(k): int(v) for k, v in clock_counts.items()},
        "quote": {str(k): int(v) for k, v in quote_counts.items()},
        "minute_window": _window(df["clock_resolution"] == CLOCK_MINUTE),
        "five_min_window": _window(df["clock_resolution"] == CLOCK_FIVE_MIN),
        "timeline": timeline_runs(rows),
    }


def clear_cache() -> None:
    """Test hook."""
    _CACHE.clear()
