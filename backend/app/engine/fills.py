"""Fill model — guardrail #1. Never at mid.

slip ∈ (0, 1] is the fraction of the half-spread conceded from mid toward
the adverse quote (TECH-SPEC §5):
    BUY  fill = mid + slip * (ask − mid)
    SELL fill = mid − slip * (mid − bid)
Commission applies per contract per side, option legs only.
"""

from __future__ import annotations

from app.engine.types import Quote


def mid(q: Quote) -> float | None:
    if q.bid is None or q.ask is None:
        return None
    return (q.bid + q.ask) / 2.0


def quote_problem(q: Quote | None, action: str) -> str | None:
    """Reason code when a quote can't support the given action, else None.
    Per TECH-SPEC §5 skips: crossed markets, zero bid on shorts."""
    if q is None or q.bid is None or q.ask is None:
        return "missing_quote"
    if q.bid > q.ask:
        return "crossed_market"
    if action == "sell" and q.bid <= 0:
        return "zero_bid_short"
    return None


def fill_price(q: Quote, action: str, slip: float) -> float | None:
    """Per-share fill for `action` ("buy" | "sell"); None if unusable."""
    m = mid(q)
    if m is None or q.bid is None or q.ask is None:
        return None
    if action == "buy":
        return m + slip * (q.ask - m)
    return m - slip * (m - q.bid)


def close_action(side: str) -> str:
    """Closing a short leg buys it back; closing a long leg sells it."""
    return "buy" if side == "short" else "sell"


def open_action(side: str) -> str:
    return "sell" if side == "short" else "buy"
