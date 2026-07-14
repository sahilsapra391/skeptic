"""Leg selection: protective wings must land strictly beyond the reference
strike — never ON it, never on the wrong side — and within a width tolerance
(deviation ≤ the requested width) so a coarse grid produces an honest skip,
not a silently wider spread. Also: the 50Δ strike IS the ATM strike, so it
stays selectable on sessions whose source carries no greeks."""

from datetime import date, timedelta

from app.engine.selection import select_expiration, select_legs
from app.engine.types import ContractKey, Quote
from app.models.spec import ExpirationSelection, Leg

EXP = date(2026, 3, 20)


def _chain(
    strikes: list[float], right: str = "put", with_delta: bool = True
) -> dict[ContractKey, Quote]:
    # |delta| grows toward the money: for puts the TOP strike carries the
    # biggest |delta|; for calls the BOTTOM strike does
    ordered = sorted(strikes, reverse=(right == "call"))
    n = max(len(ordered) - 1, 1)
    return {
        ContractKey(expiration=EXP, right=right, strike=s): Quote(
            bid=1.0,
            ask=1.2,
            delta=((0.10 + 0.20 * i / n) * (-1 if right == "put" else 1))
            if with_delta
            else None,
        )
        for i, s in enumerate(ordered)
    }


def _leg(right: str, side: str, sel: dict) -> Leg:
    return Leg.model_validate(
        {"right": right, "side": side, "ratio": 1, "strike_selection": sel}
    )


def _spread_legs(width: float, right: str = "put") -> list[Leg]:
    return [
        _leg(right, "short", {"method": "delta", "value": 0.3}),
        _leg(right, "long", {"method": "width_from_leg", "value": width, "reference_leg": 0}),
    ]


def test_put_wing_lands_below_reference_within_tolerance() -> None:
    # fine grid: the $5-wide wing picks exactly ref-5
    chain = _chain([485.0, 490.0, 495.0, 500.0])
    resolved, reason = select_legs(chain, EXP, _spread_legs(5.0), spot=500.0)
    assert reason is None and resolved is not None
    short, wing = resolved
    assert short.strike == 500.0
    assert wing.strike == 495.0


def test_call_wing_lands_above_reference() -> None:
    # the mirrored branch: call wings go ABOVE the short call
    chain = _chain([500.0, 505.0, 510.0, 515.0], right="call")
    resolved, reason = select_legs(chain, EXP, _spread_legs(5.0, right="call"), spot=500.0)
    assert reason is None and resolved is not None
    short, wing = resolved
    assert short.strike == 500.0
    assert wing.strike == 505.0


def test_coarse_grid_is_an_honest_skip_not_a_wider_spread() -> None:
    # $25 spacing, $5 width: nearest-below sits $20 off target — filling it
    # would trade 5× the specified max loss, so the entry must skip
    chain = _chain([450.0, 475.0, 500.0])
    resolved, reason = select_legs(chain, EXP, _spread_legs(5.0), spot=500.0)
    assert resolved is None
    assert reason == "wing_width_unavailable"


def test_moderate_grid_within_tolerance_fills() -> None:
    # $5 spacing, $4 width: ref-5 deviates $1 ≤ width — acceptable fill
    chain = _chain([490.0, 495.0, 500.0])
    resolved, reason = select_legs(chain, EXP, _spread_legs(4.0), spot=500.0)
    assert reason is None and resolved is not None
    assert resolved[1].strike == 495.0


def test_no_strike_below_reference_is_an_honest_skip() -> None:
    chain = _chain([500.0])
    resolved, reason = select_legs(chain, EXP, _spread_legs(5.0), spot=500.0)
    assert resolved is None
    assert reason == "no_wing_strike"


def test_iron_condor_wings_straddle_both_shorts() -> None:
    chain = {
        **_chain([480.0, 485.0, 490.0], right="put"),
        **_chain([510.0, 515.0, 520.0], right="call"),
    }
    legs = [
        _leg("put", "short", {"method": "delta", "value": 0.3}),
        _leg("put", "long", {"method": "width_from_leg", "value": 5, "reference_leg": 0}),
        _leg("call", "short", {"method": "delta", "value": 0.3}),
        _leg("call", "long", {"method": "width_from_leg", "value": 5, "reference_leg": 2}),
    ]
    resolved, reason = select_legs(chain, EXP, legs, spot=500.0)
    assert reason is None and resolved is not None
    sp, lp, sc, lc = resolved
    assert lp.strike == sp.strike - 5
    assert lc.strike == sc.strike + 5


def test_50_delta_falls_back_to_spot_when_chain_has_no_greeks() -> None:
    # ATM ≡ 50Δ: yahoo-sourced sessions store delta=None on every row — the
    # definitional nearest-to-spot pick keeps those sessions tradable
    chain = _chain([495.0, 500.0, 505.0], with_delta=False)
    legs = [_leg("put", "short", {"method": "delta", "value": 0.5})]
    resolved, reason = select_legs(chain, EXP, legs, spot=501.0)
    assert reason is None and resolved is not None
    assert resolved[0].strike == 500.0


def test_non_50_delta_still_skips_without_greeks() -> None:
    chain = _chain([495.0, 500.0, 505.0], with_delta=False)
    legs = [_leg("put", "short", {"method": "delta", "value": 0.3})]
    resolved, reason = select_legs(chain, EXP, legs, spot=501.0)
    assert resolved is None
    assert reason == "no_delta_data"


# ─────────────────────────── expiration selection ──────────────────────────
# select_expiration: nearest listed expiry to target_dte WITHIN the user's
# [min_dte, max_dte] bounds; earlier expiry wins ties; nothing in bounds is
# an honest None (→ no_expiration_in_window skip), never a bridge past the
# window the user asked for. Chain shapes mirror the Tier-0 audit's real
# deep-history probes (docs/AUDIT.md).

AS_OF = date(2026, 6, 15)  # a Monday


def _exp_chain(dtes: list[int]) -> dict[ContractKey, Quote]:
    """select_expiration reads only the keys — one dummy contract per expiry."""
    return {
        ContractKey(expiration=AS_OF + timedelta(days=d), right="put", strike=100.0):
            Quote(bid=1.0, ask=1.2, delta=-0.30)
        for d in dtes
    }


def _sel(target: int, lo: int, hi: int) -> ExpirationSelection:
    return ExpirationSelection(target_dte=target, min_dte=lo, max_dte=hi)


def test_expiration_nearest_to_target_within_bounds() -> None:
    chain = _exp_chain([30, 44, 52])
    picked = select_expiration(chain, AS_OF, _sel(45, 30, 60))
    assert picked == AS_OF + timedelta(days=44)


def test_expiration_tie_breaks_to_the_earlier_expiry() -> None:
    # 40 and 50 DTE sit 5 days either side of target 45 — earlier wins
    chain = _exp_chain([40, 50])
    picked = select_expiration(chain, AS_OF, _sel(45, 30, 60))
    assert picked == AS_OF + timedelta(days=40)


def test_bounds_exclude_even_the_nearest_expiry() -> None:
    # the audit's dolthub SPY 2021-06-15 shape: 13/27/66 DTE. Target 45
    # within [30, 60] finds nothing — 27 and 66 are both out of bounds and
    # must NOT be bridged to, however near: the user's window is the law.
    chain = _exp_chain([13, 27, 66])
    assert select_expiration(chain, AS_OF, _sel(45, 30, 60)) is None


def test_sparse_monthlies_bridge_inside_the_window_only() -> None:
    # the audit's QQQ 2010-06-15 shape: 4/15/32/67/95 DTE. Target 45 within
    # [30, 60] has exactly one candidate — effective 32, a disclosed
    # deviation bounded by the user's own min/max
    chain = _exp_chain([4, 15, 32, 67, 95])
    picked = select_expiration(chain, AS_OF, _sel(45, 30, 60))
    assert picked == AS_OF + timedelta(days=32)


def test_empty_chain_returns_none() -> None:
    assert select_expiration({}, AS_OF, _sel(45, 30, 60)) is None


def test_trading_day_dte_fn_overrides_calendar() -> None:
    # Friday "1DTE" selects Monday's expiry at the 5-min clock: calendar
    # DTE is 3 (out of a [0, 1] window) but the trading-day counter says 1
    friday = date(2026, 6, 12)
    monday = friday + timedelta(days=3)
    chain = {
        ContractKey(expiration=monday, right="put", strike=100.0):
            Quote(bid=1.0, ask=1.2, delta=-0.30)
    }
    sel = _sel(1, 0, 1)
    assert select_expiration(chain, friday, sel) is None  # calendar default

    def trading_dte(e: date) -> int:
        # weekend-free counter for this two-expiry fixture
        return sum(
            1 for i in range(1, (e - friday).days + 1)
            if (friday + timedelta(days=i)).weekday() < 5
        )

    assert select_expiration(chain, friday, sel, trading_dte) == monday
