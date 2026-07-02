"""Contract selection: expiration nearest target DTE within bounds, strikes
by delta / offset % / ATM / width-from-leg (TECH-SPEC §5). Anything the
chain can't provide is a skip with a reason, never an approximation."""

from __future__ import annotations

from datetime import date

from app.engine.types import ContractKey, Quote
from app.models.spec import ExpirationSelection, Leg, StrikeMethod


def select_expiration(
    chain: dict[ContractKey, Quote], as_of: date, sel: ExpirationSelection
) -> date | None:
    candidates: set[date] = set()
    for key in chain:
        dte = (key.expiration - as_of).days
        if sel.min_dte <= dte <= sel.max_dte:
            candidates.add(key.expiration)
    if not candidates:
        return None
    # nearest to target; earlier expiration wins ties
    return min(candidates, key=lambda e: (abs((e - as_of).days - sel.target_dte), e))


def _strikes_for(
    chain: dict[ContractKey, Quote], expiration: date, right: str
) -> list[ContractKey]:
    return [k for k in chain if k.expiration == expiration and k.right == right]


def select_legs(
    chain: dict[ContractKey, Quote],
    expiration: date,
    legs: list[Leg],
    spot: float,
) -> tuple[list[ContractKey] | None, str | None]:
    """Resolve every leg to a ContractKey, or (None, skip_reason)."""
    resolved: list[ContractKey] = []
    for leg in legs:
        pool = _strikes_for(chain, expiration, leg.right.value)
        if not pool:
            return None, "no_strike_candidates"
        sel = leg.strike_selection
        method = sel.method
        if method is StrikeMethod.DELTA:
            # accept 0.30 or 30 (whole-number deltas normalized)
            target = sel.value / 100.0 if sel.value > 1 else sel.value
            quoted = [k for k in pool if chain[k].delta is not None]
            if not quoted:
                return None, "no_delta_data"
            pick = min(quoted, key=lambda k: (abs(abs(chain[k].delta or 0.0) - target), k.strike))
        elif method is StrikeMethod.OFFSET_PCT:
            target_strike = spot * (1 + sel.value)
            pick = min(pool, key=lambda k: (abs(k.strike - target_strike), k.strike))
        elif method is StrikeMethod.ATM:
            pick = min(pool, key=lambda k: (abs(k.strike - spot), k.strike))
        elif method is StrikeMethod.WIDTH_FROM_LEG:
            ref_index = sel.reference_leg
            if ref_index is None or ref_index >= len(resolved):
                return None, "bad_reference_leg"
            ref = resolved[ref_index]
            # protective wings: puts sit BELOW the reference, calls ABOVE
            if leg.right.value == "put":
                target_strike = ref.strike - sel.value
            else:
                target_strike = ref.strike + sel.value
            pick = min(pool, key=lambda k: (abs(k.strike - target_strike), k.strike))
        else:  # pragma: no cover — enum is exhaustive
            return None, "unknown_strike_method"
        resolved.append(pick)
    if len({(k.expiration, k.right, k.strike) for k in resolved}) != len(resolved):
        return None, "duplicate_leg_strikes"
    return resolved, None
