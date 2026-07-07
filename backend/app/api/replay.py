"""Verdict receipts (ENGINE-V3 D3c): a daily verdict has to survive its
own 5-minute replay.

Shared by the on-demand endpoint (POST /api/runs/{id}/replay) and the
nightly receipt drain — one eligibility rule, one spec mutation, one
receipt shape.

Owner amendment 4 governs everything downstream: a worse replay displays
PROMINENTLY and lowers the SHOWN confidence, but the stored verdict's
trust level is NEVER silently rewritten — the original remains the record
of what the daily engine honestly concluded from its data.
"""

from __future__ import annotations

import json
from typing import Any

# A replay "disagrees" when the 5-min Sharpe lands this far below the
# daily promise (absolute), or flips sign. Reviewed constant — reporting
# treatment only, never scoring (docs/HONESTY.md).
RECEIPT_WORSE_DELTA = 0.25

# The intraday record is the short-DTE slice (0–2 trading-DTE, ATM±$8);
# a spec's WHOLE tenor band must fit inside it. Requiring only min_dte
# would admit wide bands (min 1, target 11, max 30) whose daily run trades
# 11–16 DTE while the replay silently trades ≤2 — an apples-to-oranges
# comparison dressed up as a like-for-like receipt.
REPLAY_MAX_DTE = 2


def replay_eligible_spec(spec: dict[str, Any]) -> bool:
    """Static half of eligibility (the coverage half is checked at submit
    time by the engine's own slice refusal): a daily-clock spec whose
    ENTIRE tenor band fits the intraday slice, so both clocks trade the
    same contracts."""
    clock = (spec.get("backtest") or {}).get("clock", "daily")
    if clock != "daily":
        return False
    sel = (spec.get("position") or {}).get("expiration_selection") or {}
    return int(sel.get("max_dte", 99)) <= REPLAY_MAX_DTE


def build_replay_spec(spec: dict[str, Any]) -> dict[str, Any]:
    """The SAME strategy at the 5-minute clock. DTE bounds pass through
    as-is: at short tenors calendar and trading DTE coincide up to
    weekends, and the engine's trading-DTE selection handles the rest."""
    replay: dict[str, Any] = json.loads(json.dumps(spec))  # deep copy
    replay.setdefault("backtest", {})["clock"] = "5min"
    replay["spec_version"] = 2
    return replay


def build_receipt(replay_run_id: str, daily_stats: dict[str, Any],
                  replay_stats: dict[str, Any], replay_payload: dict[str, Any],
                  created_at: str) -> dict[str, Any]:
    """The receipt stored on the ORIGINAL run: both promises, side by side,
    with the replay's fill provenance riding along."""
    daily_sharpe = (daily_stats.get("metrics") or {}).get("sharpe")
    replay_sharpe = (replay_stats.get("metrics") or {}).get("sharpe")
    worse = False
    if daily_sharpe is not None and replay_sharpe is not None:
        worse = (replay_sharpe < daily_sharpe - RECEIPT_WORSE_DELTA) or (
            daily_sharpe > 0 > replay_sharpe
        )
    # FX.4 (masterplan defense 4c): when the two runs carry per-session
    # resolution records that DIFFER, the receipt names the change as a
    # RESOLUTION UPGRADE — a re-run that improved because minute data newly
    # arrived is explained, never a silent shift.
    # Annotate ONLY when BOTH runs carry a resolution mix and they differ
    # (review finding: a daily parent has no mix — comparing {} against the
    # replay's would fire a FALSE "upgrade" note on every receipt; the
    # honest condition is two resolution-carrying runs whose mixes moved).
    daily_mix = daily_stats.get("resolutionMix") or {}
    replay_mix = replay_payload.get("resolutionMix") or {}
    resolution_upgrade = None
    if daily_mix and replay_mix and daily_mix != replay_mix:
        resolution_upgrade = (
            f"resolution changed between runs: minute sessions "
            f"{daily_mix.get('minute', 0)} → {replay_mix.get('minute', 0)}, "
            f"5-min {daily_mix.get('five_min', 0)} → "
            f"{replay_mix.get('five_min', 0)} — differences may be a "
            "resolution upgrade, not a market change"
        )
    return {
        "replay_run_id": replay_run_id,
        "created_at": created_at,
        "daily_sharpe": daily_sharpe,
        "five_min_sharpe": replay_sharpe,
        "daily_return": (daily_stats.get("metrics") or {}).get("total_return"),
        "five_min_return": (replay_stats.get("metrics") or {}).get("total_return"),
        "fill_sources": replay_payload.get("fillSources") or {},
        "worse": worse,
        "resolution_upgrade": resolution_upgrade,
    }
