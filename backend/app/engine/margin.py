"""Buying-power reserve for option positions (owner decision 2026-07-15).

A real account cannot open positions it can't fund: debits must be covered
by cash, and short options consume margin. The engine reserves, per open
position, a deterministic broker-style requirement and refuses entries
whose requirement exceeds the buying power left — skipped with the named
reason `insufficient_buying_power`, never silently resized.

The formula is the market-standard broker minimum for uncovered short
options (the "20% rule" — the FINRA/Reg-T-style initial requirement quoted
by CBOE margin manuals and retail brokers alike), chosen because it is a
NAMED market convention with a deterministic closed form, not an invented
constant (docs/HONESTY.md · buying power):

  short put :  max(20% · spot − OTM amount, 10% · strike)   per share
  short call:  max(20% · spot − OTM amount, 10% · spot)     per share

Premium credit is NOT part of the reserve: the credit lands in cash at
fill time and the buying-power check reads post-credit cash, so adding it
here would double-count it.

Spreads: a short leg paired with a long leg of the same right and
expiration reserves the strike width (the pair's max loss); a debit pair
reserves nothing (its max loss is the debit, which the cash check covers).
Both-sided same-expiration sets (iron condor) reserve the WORSE side only —
both sides cannot finish in the money at one expiration. A short call
covered by stock reserves nothing (the shares are the collateral).

RESERVE_MODE is the single revisitable seam: broker requirements vary and
maintenance margin is deliberately NOT modeled (docs/HONESTY.md — the ruin
halt fires at $0, the latest-possible ruin date, never the actual).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from app.engine.types import MULT, ContractKey
from app.models.spec import Leg, Side

# "reg_t_20" (the 20% rule, default) | "cash_secured" (strictest: short puts
# reserve the full strike; naked short calls have no cash-secured form).
# Revisitable in a reviewed session only — never at runtime.
RESERVE_MODE = "reg_t_20"

_BROAD_PCT = 0.20  # the 20% rule's base rate on the underlying
_MIN_PCT = 0.10  # the floor rate (strike for puts, spot for calls)


def short_leg_requirement(
    right: str, strike: float, spot: float, mode: str = RESERVE_MODE
) -> float:
    """Per-share reserve for ONE uncovered short contract (premium excluded —
    see module docstring)."""
    if mode == "cash_secured":
        if right == "put":
            return strike
        return float("inf")  # a naked short call has no cash-secured form
    if right == "put":
        otm = max(0.0, spot - strike)
        floor = _MIN_PCT * strike
    else:
        otm = max(0.0, strike - spot)
        floor = _MIN_PCT * spot
    return max(_BROAD_PCT * spot - otm, floor)


@dataclass
class _Units:
    strike: float
    expiration: date
    qty: int


def position_requirement(
    legs: Sequence[Leg],
    keys: Sequence[ContractKey],
    spot: float,
    contracts: int,
    stock_cover_shares: int = 0,
    mode: str = RESERVE_MODE,
) -> float:
    """Dollar reserve for the whole contract-set × `contracts`.

    Pairing rule (deterministic): within each (right, expiration) group,
    short legs pair against long legs — puts pair highest-strike short with
    highest-strike long, calls lowest with lowest — reserving the credit
    width per paired unit and nothing for debit pairs. Unpaired short units
    reserve the naked requirement. When BOTH rights carry a paired reserve
    at one shared expiration (iron condor), only the worse side is reserved.
    `stock_cover_shares` covers short calls first (covered call → 0).
    """
    shorts: dict[str, list[_Units]] = {"put": [], "call": []}
    longs: dict[str, list[_Units]] = {"put": [], "call": []}
    for leg, key in zip(legs, keys, strict=True):
        bucket = shorts if leg.side is Side.SHORT else longs
        bucket[key.right].append(
            _Units(strike=key.strike, expiration=key.expiration, qty=leg.ratio * contracts)
        )

    # stock covers short calls first (lowest strike = deepest risk first)
    cover = stock_cover_shares // 100
    for row in sorted(shorts["call"], key=lambda u: u.strike):
        if cover <= 0:
            break
        take = min(cover, row.qty)
        row.qty -= take
        cover -= take

    naked_total = 0.0
    paired_by_right = {"put": 0.0, "call": 0.0}
    expirations = {k.expiration for k in keys}

    for right in ("put", "call"):
        # puts: pair high short strikes with high long strikes; calls: low/low
        desc = right == "put"
        s_rows = sorted(shorts[right], key=lambda u: u.strike, reverse=desc)
        l_rows = sorted(longs[right], key=lambda u: u.strike, reverse=desc)
        for s in s_rows:
            for lr in l_rows:
                if s.qty <= 0:
                    break
                if lr.qty <= 0 or lr.expiration != s.expiration:
                    continue
                q = min(s.qty, lr.qty)
                width = (
                    max(0.0, s.strike - lr.strike)
                    if right == "put"
                    else max(0.0, lr.strike - s.strike)
                )
                paired_by_right[right] += width * q * MULT
                s.qty -= q
                lr.qty -= q
            if s.qty > 0:
                naked_total += (
                    short_leg_requirement(right, s.strike, spot, mode) * s.qty * MULT
                )

    if paired_by_right["put"] > 0 and paired_by_right["call"] > 0 and len(expirations) == 1:
        paired_total = max(paired_by_right["put"], paired_by_right["call"])
    else:
        paired_total = paired_by_right["put"] + paired_by_right["call"]
    return paired_total + naked_total
