"""Leg selection: protective wings must land strictly beyond the reference
strike — never ON it (dead duplicate-strike skip on coarse grids) and never
on the wrong side (an inverted spread the user didn't ask for)."""

from datetime import date

from app.engine.selection import select_legs
from app.engine.types import ContractKey, Quote
from app.models.spec import Leg

EXP = date(2026, 3, 20)


def _put_chain(strikes: list[float]) -> dict[ContractKey, Quote]:
    # put deltas grow toward the money: the highest strike carries the
    # biggest |delta|, so a 0.30-delta short resolves to the TOP strike
    ordered = sorted(strikes)
    n = max(len(ordered) - 1, 1)
    return {
        ContractKey(expiration=EXP, right="put", strike=s): Quote(
            bid=1.0, ask=1.2, delta=-(0.10 + 0.20 * i / n)
        )
        for i, s in enumerate(ordered)
    }


def _spread_legs(width: float) -> list[Leg]:
    return [
        Leg.model_validate(
            {"right": "put", "side": "short", "ratio": 1,
             "strike_selection": {"method": "delta", "value": 0.3}}
        ),
        Leg.model_validate(
            {"right": "put", "side": "long", "ratio": 1,
             "strike_selection": {"method": "width_from_leg", "value": width,
                                  "reference_leg": 0}}
        ),
    ]


def test_wing_lands_below_reference_on_coarse_grid() -> None:
    # $25 spacing, $5 width: nearest-by-absolute-distance would pick the
    # reference itself (5 < 20 away) — the wing must land BELOW instead
    chain = _put_chain([450.0, 475.0, 500.0])
    resolved, reason = select_legs(chain, EXP, _spread_legs(width=5.0), spot=500.0)
    assert reason is None and resolved is not None
    short, wing = resolved
    assert short.strike == 500.0
    assert wing.strike == 475.0


def test_no_strike_below_reference_is_an_honest_skip() -> None:
    # the short resolves to the only strike; nothing sits below it — a wing
    # on the wrong side would invert the spread, so this must be a skip
    chain = _put_chain([500.0])
    resolved, reason = select_legs(chain, EXP, _spread_legs(width=5.0), spot=500.0)
    assert resolved is None
    assert reason == "no_wing_strike"


def test_wing_prefers_nearest_below_target() -> None:
    # fine grid: the $5-wide wing picks exactly ref-5, not merely "below"
    chain = _put_chain([485.0, 490.0, 495.0, 500.0])
    resolved, reason = select_legs(chain, EXP, _spread_legs(width=5.0), spot=500.0)
    assert reason is None and resolved is not None
    short, wing = resolved
    assert short.strike == 500.0
    assert wing.strike == 495.0
