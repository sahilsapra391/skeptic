"""Fill model — guardrail #1. Never at mid.

slip ∈ (0, 1] is the fraction of the half-spread conceded from mid toward
the adverse quote (TECH-SPEC §5):
    BUY  fill = mid + slip * (ask − mid)
    SELL fill = mid − slip * (mid − bid)
Commission applies per contract per side, option legs only.

Liquidity awareness (D1b). Two mechanisms, both hand-computable:

  * Entry gates (`liquidity_gate`): a quote whose spread exceeds
    max_spread_pct of mid, or whose KNOWN open interest / volume sits below
    the configured floors, is not silently filled at fantasy prices —
    the entry skips with a reason, or fills at the full adverse quote in
    stress mode. Exits are never gated: a real position can always pay the
    quoted price to get out.
  * OI-scaled slippage (`effective_slip`): when open interest is known and
    thin, the slip fraction scales linearly from `base` (at oi ≥ 10×floor)
    to ~1.0 (at the floor):
        p = max(0, 1 − oi / (10 · min_open_interest))
        slip_eff = base + (1 − base) · p
    Unknown OI is NEVER penalized — it is counted and disclosed in the
    run's liquidity profile instead (fixtures and greek-less sources stay
    at base slip). Applied identically to entries, exit triggers, exit
    fills and marks, so open and close always use the same fill model.
"""

from __future__ import annotations

from app.engine.types import Quote
from app.models.spec import Costs


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


def spread_pct(q: Quote) -> float | None:
    """(ask − bid) / mid as a FRACTION of mid; None when not assessable
    (missing side or mid ≤ 0)."""
    m = mid(q)
    if m is None or m <= 0 or q.bid is None or q.ask is None:
        return None
    return (q.ask - q.bid) / m


def effective_slip(base: float, q: Quote, min_open_interest: int) -> float:
    """OI-scaled slip in [base, 1.0] per the module docstring. Unknown OI or
    a disabled floor (min_open_interest = 0) → base, unchanged."""
    if min_open_interest <= 0 or q.open_interest is None:
        return base
    p = max(0.0, 1.0 - q.open_interest / (10.0 * min_open_interest))
    return min(base + (1.0 - base) * p, 1.0)


def liquidity_gate(q: Quote, costs: Costs) -> str | None:
    """ENTRY-ONLY liquidity gate: reason code when the quote fails a floor,
    else None. Unknown OI/volume never gates (disclosed, not punished)."""
    sp = spread_pct(q)
    if sp is not None and sp > costs.max_spread_pct / 100.0:
        return "illiquid_spread"
    if (
        costs.min_open_interest > 0
        and q.open_interest is not None
        and q.open_interest < costs.min_open_interest
    ):
        return "illiquid_oi"
    if costs.min_volume > 0 and q.volume is not None and q.volume < costs.min_volume:
        return "illiquid_volume"
    return None
