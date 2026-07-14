"""Strike-granularity floor for the delta sweep — hand-computed.

The PR #99 review's deferred finding: ±20% of a small strike-selection
delta probes almost nothing on a DISCRETE strike grid — 0.05Δ sweeps
0.04…0.06 in 0.005Δ cells while one strike at typical chain spacing is
worth more delta than a cell, so adjacent cells resolve to the same
contract and five near-identical Sharpes bless the fragile
lottery-ticket archetype as a false plateau. At the probe floor the
collapse was literal: base 0.03 clamped three cells to identical
values. Below 0.25Δ the sweep now steps an absolute 0.025Δ grid
(_DELTA_STEP_FLOOR — 10% of the 25Δ-wing reference, the same grounding
rule as _COND_FAMILY_FLOORS), shifted up whole steps off the 0.03
edge, disclosed in Sensitivity.delta_note. Same 5 cells, same
classifier, no re-centering: the multiple-testing arithmetic is
untouched. Every expected grid below is hand-computed.
"""

from __future__ import annotations

import copy
import math
from datetime import date, timedelta

from app.engine.market import build_fixture_store
from app.honesty.stages import (
    _DELTA_STEP_FLOOR,
    _DELTA_SWEEP_MAX,
    _DELTA_SWEEP_MIN,
    _delta_grid,
    _mutations,
    sensitivity,
)
from app.honesty.verdict import (
    grounding_set,
    retail_template_verdict,
    template_verdict,
    validate_numbers,
)
from app.models.spec import StrategySpec
from tests.test_spec_roundtrip import CANONICAL


def _delta_spec(value: float) -> StrategySpec:
    doc = copy.deepcopy(CANONICAL)
    doc["position"]["legs"][0]["strike_selection"] = {
        "method": "delta", "value": value}
    return StrategySpec.model_validate(doc)


class TestDeltaGrid:
    def test_far_otm_lottery_ticket_sweeps_absolute_grid(self) -> None:
        # THE motivating case: base 0.05 → old grid 0.04…0.06 (0.005Δ
        # cells, often the same strike). Floored: 0.05 + [-2..2]·0.025 =
        # [0, 0.025, 0.05, 0.075, 0.1] → min 0 < 0.03 → shift up
        # ceil((0.03-0)/0.025)=2 steps (+0.05) → [0.05, 0.075, 0.1,
        # 0.125, 0.15], specced value ON the grid at index 0
        values, base_index, floored = _delta_grid(0.05)
        assert values == [0.05, 0.075, 0.1, 0.125, 0.15]
        assert base_index == 0 and values[base_index] == 0.05
        assert floored
        assert len(values) == 5  # never more cells — the tax is unchanged

    def test_probe_floor_base_no_longer_collapses(self) -> None:
        # the literal clamp collapse the old code produced at base 0.03:
        # [0.03, 0.03, 0.03, 0.033, 0.036] — three identical cells.
        # Floored: 0.03 + [-2..2]·0.025 → min -0.02 → shift 2 (+0.05) →
        # [0.03, 0.055, 0.08, 0.105, 0.13] — five DISTINCT cells
        values, base_index, floored = _delta_grid(0.03)
        assert values == [0.03, 0.055, 0.08, 0.105, 0.13]
        assert base_index == 0 and values[base_index] == 0.03
        assert floored
        assert len(set(values)) == 5

    def test_one_step_shift(self) -> None:
        # base 0.055: raw min 0.005 < 0.03 → shift exactly 1 (+0.025) →
        # [0.03, 0.055, 0.08, 0.105, 0.13], base at index 1
        values, base_index, floored = _delta_grid(0.055)
        assert values == [0.03, 0.055, 0.08, 0.105, 0.13]
        assert base_index == 1 and values[base_index] == 0.055
        assert floored

    def test_in_range_small_delta_no_shift(self) -> None:
        # base 0.10: raw min 0.05 ≥ 0.03 → symmetric grid, base center
        values, base_index, floored = _delta_grid(0.10)
        assert values == [0.05, 0.075, 0.1, 0.125, 0.15]
        assert base_index == 2 and values[base_index] == 0.10
        assert floored

    def test_overfit_fixture_delta_grid(self) -> None:
        # the canary's 0.15Δ short put — pinned so a change to ITS sweep
        # is a conscious decision, never a drive-by
        values, base_index, floored = _delta_grid(0.15)
        assert values == [0.1, 0.125, 0.15, 0.175, 0.2]
        assert base_index == 2
        assert floored

    def test_boundary_delta_stays_multiplicative(self) -> None:
        # base 0.25 → 10%·0.25 = 0.025 == floor exactly → multiplicative
        # (the floor binds on STRICTLY smaller steps, mirroring
        # _condition_grid): [0.2, 0.225, 0.25, 0.275, 0.3]
        values, base_index, floored = _delta_grid(0.25)
        assert values == [0.2, 0.225, 0.25, 0.275, 0.3]
        assert base_index == 2
        assert not floored

    def test_reference_scale_delta_unchanged(self) -> None:
        # base 0.30 (the CANONICAL spec): byte-identical to the pre-floor
        # sweep — floors bind only where ±20% was degenerate
        values, base_index, floored = _delta_grid(0.30)
        assert values == [0.24, 0.27, 0.3, 0.33, 0.36]
        assert base_index == 2
        assert not floored

    def test_deep_itm_top_clamp_unchanged(self) -> None:
        # base 0.90: multiplicative with the pre-existing 0.95 cap —
        # [0.72, 0.81, 0.9, 0.95, 0.95] (duplicate top cells are reused
        # by the sweep's seen-cache, never re-run)
        values, base_index, floored = _delta_grid(0.90)
        assert values == [0.72, 0.81, 0.9, 0.95, 0.95]
        assert base_index == 2
        assert not floored

    def test_below_probe_floor_keeps_prefloor_path(self) -> None:
        # base 0.02 sits below the sweep's own probe floor — outside the
        # scale the floor was grounded on (_condition_grid's lower-edge
        # posture): the pre-floor clamped path, degenerate as before
        values, base_index, floored = _delta_grid(0.02)
        assert values == [0.03, 0.03, 0.03, 0.03, 0.03]
        assert base_index == 2
        assert not floored

    def test_specced_value_always_on_grid(self) -> None:
        # the invariant the #99 review blocker was about: base_index in
        # range and values[base_index] == the (4dp-rounded) specced value
        for base in (0.03, 0.04, 0.05, 0.0567, 0.06, 0.08, 0.1, 0.16,
                     0.2, 0.24, 0.25, 0.3, 0.5, 0.9):
            values, base_index, _ = _delta_grid(base)
            assert 0 <= base_index < 5, base
            assert values[base_index] == round(base, 4), base

    def test_floored_steps_are_exactly_the_floor(self) -> None:
        # every floored grid steps _DELTA_STEP_FLOOR per cell — the "one
        # strike per cell" guarantee lives in this spacing
        for base in (0.03, 0.05, 0.1, 0.16, 0.24):
            values, _, floored = _delta_grid(base)
            assert floored, base
            steps = [round(b - a, 4) for a, b in zip(values, values[1:],
                                                     strict=False)]
            assert steps == [_DELTA_STEP_FLOOR] * 4, base

    def test_floored_grid_stays_inside_probe_range(self) -> None:
        # engagement ends at 0.25 and shifts stop at the 0.03 edge, so
        # every floored cell is a legal probe value well under the cap
        for base in (0.03, 0.05, 0.1, 0.2, 0.24):
            values, _, floored = _delta_grid(base)
            assert floored, base
            assert min(values) >= _DELTA_SWEEP_MIN
            assert max(values) <= _DELTA_SWEEP_MAX


class TestDeltaMutations:
    def test_small_delta_note_rides_mutations(self) -> None:
        muts, cond_note, delta_note = _mutations(_delta_spec(0.05))
        _, values, base_index, _ = next(m for m in muts if m[0] == "delta")
        assert values == [0.05, 0.075, 0.1, 0.125, 0.15]
        assert base_index == 0
        assert cond_note is None  # delta discloses on its OWN channel
        assert delta_note is not None
        assert "delta 0.05 swept 0.05…0.15" in delta_note
        assert "absolute delta-point steps" in delta_note

    def test_percent_form_delta_normalizes_then_floors(self) -> None:
        # value 5 (whole-number delta) → base 0.05 → same grid and note
        muts, _, delta_note = _mutations(_delta_spec(5))
        _, values, base_index, _ = next(m for m in muts if m[0] == "delta")
        assert values == [0.05, 0.075, 0.1, 0.125, 0.15]
        assert base_index == 0
        assert delta_note is not None and "delta 0.05" in delta_note

    def test_reference_scale_delta_has_no_note(self) -> None:
        muts, _, delta_note = _mutations(_delta_spec(0.30))
        _, values, _, _ = next(m for m in muts if m[0] == "delta")
        assert values == [0.24, 0.27, 0.3, 0.33, 0.36]
        assert delta_note is None

    def test_non_delta_strike_method_has_no_note(self) -> None:
        doc = copy.deepcopy(CANONICAL)
        doc["position"]["legs"][0]["strike_selection"] = {
            "method": "offset_pct", "value": 0.02}
        spec = StrategySpec.model_validate(doc)
        muts, _, delta_note = _mutations(spec)
        assert not any(m[0] == "delta" for m in muts)
        assert delta_note is None

    def test_setter_still_updates_every_delta_leg(self) -> None:
        spec = _delta_spec(0.05)
        muts, _, _ = _mutations(spec)
        _, values, _, setter = next(m for m in muts if m[0] == "delta")
        mutated = copy.deepcopy(spec)
        setter(mutated, values[-1])
        assert mutated.position.legs[0].strike_selection.value == 0.15

    def test_note_numerals_are_grounded(self) -> None:
        # guardrail #4: the note rides into verdict caveats — validated
        # with the SHIPPING validator against an allowed set built the
        # way production builds it (the sweep values the report carries)
        muts, _, delta_note = _mutations(_delta_spec(0.05))
        assert delta_note is not None
        allowed = grounding_set({"params": [{"values": m[1]} for m in muts]})
        assert validate_numbers(delta_note, allowed) == []


def _weekdays(start: date, n: int) -> list[date]:
    out: list[date] = []
    d = start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _fine_delta_store():
    """Persistent integer-strike grid whose put deltas step ~0.02 per $1
    strike (slope 1/50) — fine enough that 0.025Δ cells resolve to
    DIFFERENT strikes while the old 0.005Δ cells around a 0.05Δ base
    would all have landed on the same contract."""
    sessions = _weekdays(date(2024, 1, 1), 160)
    expiries = [s for s in sessions if s.weekday() == 4]
    chains: dict[str, list[dict]] = {}
    underlying: dict[str, tuple[float, float]] = {}
    for i, s in enumerate(sessions):
        spot = round(100.0 + 8.0 * math.sin(i / 6.0) + i * 0.01, 2)
        underlying[s.isoformat()] = (spot, spot)
        rows: list[dict] = []
        for exp in expiries:
            dte = (exp - s).days
            if dte < 1 or dte > 30:
                continue
            for strike in range(60, 131):
                intrinsic = max(strike - spot, 0.0)
                tv = 0.2 * math.sqrt(dte) * math.exp(-abs(strike - spot) / 18.0)
                mid = intrinsic + tv
                if mid < 0.05:
                    continue
                delta = max(0.01, min(0.99, 0.5 + (strike - spot) / 50.0))
                # tight quotes: far-OTM mids are small, and a spread that
                # is a large fraction of mid would trip the 25% liquidity
                # gate and skip every entry (the sweep would test nothing)
                half = round(min(0.05, mid * 0.08), 2)
                rows.append({
                    "expiration": exp.isoformat(), "right": "put",
                    "strike": float(strike),
                    "bid": round(max(mid - half, 0.01), 2),
                    "ask": round(mid + half, 2),
                    "delta": round(-delta, 4), "iv": 0.2,
                })
        chains[s.isoformat()] = rows
    return build_fixture_store("SPY", chains, underlying)


def _lottery_spec() -> StrategySpec:
    return StrategySpec.model_validate({
        "spec_version": 2,
        "meta": {"name": "5-delta tail sell",
                 "description_raw": "delta floor integration"},
        "underlying": {"ticker": "SPY"},
        "position": {"structure": "short_put",
                     "legs": [{"right": "put", "side": "short", "ratio": 1,
                               "strike_selection": {"method": "delta",
                                                    "value": 0.05}}],
                     "expiration_selection": {"target_dte": 14, "min_dte": 5,
                                              "max_dte": 30}},
        "entry": {"schedule": {"frequency": "weekly",
                               "day_of_week": "monday"},
                  "conditions": [], "max_concurrent_positions": 3},
        "exit": {"profit_target_pct": 50, "time_exit_dte": 3},
        "sizing": {"method": "fixed_contracts", "value": 1},
        "costs": {"commission_per_contract": 0.65,
                  "slippage_half_spread_fraction": 0.5,
                  "slippage_half_spread_fraction_sell": 0.5},
        "backtest": {"start": None, "end": None, "initial_capital": 25000,
                     "seed": 42},
    })


class TestDeltaSweepIntegration:
    def test_floored_grid_actually_probes_different_strikes(self) -> None:
        store = _fine_delta_store()
        sens = sensitivity(_lottery_spec(), store)
        row = next(p for p in sens.params if p.name == "delta")
        assert row.values == [0.05, 0.075, 0.1, 0.125, 0.15]
        assert row.base_index == 0
        # the sweep RE-RAN the engine at deltas far enough apart to pick
        # different contracts — the cells genuinely differ (on the old
        # 0.04…0.06 grid every cell landed on the same strike)
        valid = [s for s in row.sharpes if s is not None]
        assert len(valid) >= 3
        assert len({round(s, 6) for s in valid}) > 1
        assert sens.delta_note is not None
        assert "delta 0.05 swept 0.05…0.15" in sens.delta_note

    def test_delta_note_rides_both_verdict_registers(self) -> None:
        store = _fine_delta_store()
        from app.engine.runner import run_backtest
        from app.honesty.gauntlet import run_gauntlet

        spec = _lottery_spec()
        report = run_gauntlet(spec, store, run_backtest(spec, store),
                              trials=1)
        assert report.sensitivity.delta_note is not None
        quant = template_verdict(report)
        assert any("absolute delta-point steps" in c for c in quant.caveats)
        retail = retail_template_verdict(report)
        assert any("stress-testing your strike choice" in c
                   for c in retail.caveats)
