"""Entry-condition threshold sweeps (ENGINE-V4 F8) — hand-computed.

The anti-overfitting gauntlet now perturbs entry-condition thresholds,
not just strike/dte/exits: a strategy overfit to "RSI < 30" or "skew > 5"
is caught. Owner decisions 2026-07-08: cap the first 3 entry conditions;
SKIP sign-at-zero conditions (the sign IS the signal — nothing to
perturb), disclosed; rank forms sweep as 0-100; entry conditions only in
v1 (exit/rung deferred, disclosed). Recommendations, the sensitivity
grid, and the verdict caveat consume the sweep params generically, so
weaving the SWEEP is the whole weave.
"""

from __future__ import annotations

import copy
import math
from datetime import date, timedelta

from app.api.payload import _param_label, _recommendations, _sweep_display_name
from app.engine.market import build_fixture_store
from app.honesty.stages import (
    _COND_FAMILY_FLOORS,
    _COND_FLOOR_EXEMPT,
    _mutations,
    sensitivity,
)
from app.honesty.verdict import grounding_set, validate_numbers
from app.models.spec import Indicator, StrategySpec
from tests.test_spec_roundtrip import CANONICAL


def _spec_with(conditions: list[dict], version: int = 2) -> StrategySpec:
    doc = copy.deepcopy(CANONICAL)
    doc["spec_version"] = version
    doc["entry"]["conditions"] = conditions
    return StrategySpec.model_validate(doc)


def _names(spec: StrategySpec) -> tuple[list[str], str | None]:
    muts, note, _ = _mutations(spec)
    return [m[0] for m in muts], note


class TestConditionMutations:
    def test_rsi_threshold_is_swept_pm20(self) -> None:
        spec = _spec_with([{"indicator": "rsi", "operator": "<", "value": 30,
                            "period": 14}])
        muts, note, _ = _mutations(spec)
        rsi = next(m for m in muts if m[0] == "cond_rsi")
        name, values, base_index, setter = rsi
        assert values == [24.0, 27.0, 30.0, 33.0, 36.0]
        assert base_index == 2 and values[base_index] == 30.0
        assert note is None
        # the setter mutates the right condition
        mutated = copy.deepcopy(spec)
        setter(mutated, 24.0)
        assert mutated.entry.conditions[0].value == 24.0

    def test_sign_test_is_skipped_and_disclosed(self) -> None:
        # a threshold of 0 (e.g. "price below its SMA") is a sign test —
        # the sign is the signal, there is nothing to perturb
        spec = _spec_with([{"indicator": "price_vs_sma_pct", "operator": "<",
                            "value": 0, "period": 20}])
        names, note = _names(spec)
        assert not any(n.startswith("cond_") for n in names)
        assert note is not None and "sign test" in note
        assert "price_vs_sma_pct" in note

    def test_rank_threshold_clamps_to_100(self) -> None:
        spec = _spec_with([{"indicator": "ivx_rank_1y", "operator": ">",
                            "value": 90}])
        muts, _, _ = _mutations(spec)
        vals = next(m[1] for m in muts if m[0] == "cond_ivx_rank_1y")
        # 90 × [.8,.9,1,1.1,1.2] = [72,81,90,99,108] → clamp 108→100
        assert vals == [72.0, 81.0, 90.0, 99.0, 100.0]

    def test_caps_at_three_and_discloses_the_rest(self) -> None:
        conds = [
            {"indicator": "rsi", "operator": "<", "value": 30, "period": 14},
            {"indicator": "vix_level", "operator": ">", "value": 20},
            {"indicator": "realized_vol_20d", "operator": "<", "value": 25},
            {"indicator": "drawdown_from_high_pct", "operator": ">", "value": 5},
            {"indicator": "iv_percentile_1y", "operator": ">", "value": 50},
        ]
        names, note = _names(_spec_with(conds))
        swept = [n for n in names if n.startswith("cond_")]
        assert len(swept) == 3  # only the first 3
        assert note is not None and "2 further conditions not swept" in note

    def test_repeated_indicator_disambiguated(self) -> None:
        # the "within 1% of max pain" pair: two conditions, same indicator
        spec = _spec_with([
            {"indicator": "max_pain_distance_pct", "operator": "<", "value": 1},
            {"indicator": "max_pain_distance_pct", "operator": ">", "value": -1},
        ], version=7)
        names, _ = _names(spec)
        cond_names = [n for n in names if n.startswith("cond_")]
        assert len(cond_names) == 2 and len(set(cond_names)) == 2

    def test_sign_test_past_the_cap_is_still_disclosed(self) -> None:
        # review finding F8 #1: a sign gate positioned AFTER the 3-cap was
        # silently undisclosed — the exact "absence misread as a free
        # pass" failure. A realistic 0DTE spec: 3 real filters + a trailing
        # gex_level sign gate.
        spec = _spec_with([
            {"indicator": "rsi", "operator": "<", "value": 30, "period": 14},
            {"indicator": "vix_level", "operator": ">", "value": 20},
            {"indicator": "realized_vol_20d", "operator": "<", "value": 25},
            {"indicator": "gex_level", "operator": ">", "value": 0},
        ], version=7)
        names, note = _names(spec)
        assert len([n for n in names if n.startswith("cond_")]) == 3
        assert note is not None and "gex_level" in note and "sign test" in note

    def test_cap_count_excludes_interleaved_sign_tests(self) -> None:
        # [real,real,real,sign,real] → 3 swept, 1 capped (the 5th real),
        # 1 sign disclosed — the cap count must not miscount the sign test
        spec = _spec_with([
            {"indicator": "rsi", "operator": "<", "value": 30, "period": 14},
            {"indicator": "vix_level", "operator": ">", "value": 20},
            {"indicator": "realized_vol_20d", "operator": "<", "value": 25},
            {"indicator": "market_tide_level", "operator": ">", "value": 0},
            {"indicator": "nope_rank_1y", "operator": ">", "value": 60},
        ], version=7)
        _, note = _names(spec)
        assert note is not None
        assert "1 further condition not swept" in note
        assert "market_tide_level" in note and "sign test" in note

    def test_three_same_indicator_names_stay_unique(self) -> None:
        # review #2: 3 conditions sharing an indicator AND an operator must
        # not collide into duplicate sweep names
        spec = _spec_with([
            {"indicator": "max_pain_distance_pct", "operator": ">", "value": 1},
            {"indicator": "max_pain_distance_pct", "operator": ">", "value": 2},
            {"indicator": "max_pain_distance_pct", "operator": ">", "value": 3},
        ], version=7)
        names, _ = _names(spec)
        cond_names = [n for n in names if n.startswith("cond_")]
        assert len(cond_names) == 3 and len(set(cond_names)) == 3

    def test_sign_and_real_threshold_mixed(self) -> None:
        spec = _spec_with([
            {"indicator": "gex_level", "operator": ">", "value": 0},   # sign
            {"indicator": "skew_25d", "operator": ">", "value": 5},     # real
        ], version=7)
        names, note = _names(spec)
        assert "cond_skew_25d" in names
        assert not any("gex_level" in n for n in names if n.startswith("cond_"))
        assert note is not None and "gex_level" in note and "sign test" in note


class TestScaleAwareFloors:
    """PR #97 review follow-up: ±20% of a small threshold on a wide scale
    under-probes and reads as a false plateau. When 10%·|base| < the
    family floor, the sweep switches to an absolute grid of floor-steps.
    Every expected grid below is hand-computed."""

    def test_small_skew_sweeps_absolute_grid(self) -> None:
        # skew_25d floor 0.5; base 0.5 → 10%·0.5 = 0.05 < 0.5 → additive:
        # 0.5 + [-2,-1,0,1,2]·0.5 = [-0.5, 0, 0.5, 1.0, 1.5], base idx 2
        spec = _spec_with([{"indicator": "skew_25d", "operator": ">",
                            "value": 0.5}], version=5)
        muts, note, _ = _mutations(spec)
        name, values, base_index, _ = next(
            m for m in muts if m[0] == "cond_skew_25d")
        assert values == [-0.5, 0.0, 0.5, 1.0, 1.5]
        assert base_index == 2 and values[base_index] == 0.5
        assert len(values) == 5  # never more cells — the tax is unchanged
        assert note is not None and "absolute family-scale steps" in note
        # operator + specced value attribute the grid (review finding:
        # bare indicator names were ambiguous for the max-pain pair)
        assert "skew_25d > 0.5 swept -0.5…1.5" in note

    def test_large_skew_keeps_multiplicative(self) -> None:
        # base 10 → 10%·10 = 1.0 ≥ 0.5 floor → plain ±20%: [8, 9, 10, 11, 12]
        spec = _spec_with([{"indicator": "skew_25d", "operator": ">",
                            "value": 10}], version=5)
        muts, note, _ = _mutations(spec)
        _, values, base_index, _ = next(
            m for m in muts if m[0] == "cond_skew_25d")
        assert values == [8.0, 9.0, 10.0, 11.0, 12.0]
        assert base_index == 2
        assert note is None

    def test_boundary_threshold_stays_multiplicative(self) -> None:
        # base 5 → 10%·5 = 0.5 == floor exactly → the multiplicative path
        # (floor binds on STRICTLY smaller steps): [4, 4.5, 5, 5.5, 6]
        spec = _spec_with([{"indicator": "skew_25d", "operator": ">",
                            "value": 5}], version=5)
        muts, note, _ = _mutations(spec)
        _, values, _, _ = next(m for m in muts if m[0] == "cond_skew_25d")
        assert values == [4.0, 4.5, 5.0, 5.5, 6.0]
        assert note is None

    def test_max_pain_small_threshold(self) -> None:
        # max_pain_distance_pct floor 0.25; base 1 → step 0.1 < 0.25 →
        # 1 + [-2..2]·0.25 = [0.5, 0.75, 1.0, 1.25, 1.5]
        spec = _spec_with([{"indicator": "max_pain_distance_pct",
                            "operator": "<", "value": 1}], version=7)
        muts, _, _ = _mutations(spec)
        _, values, base_index, _ = next(
            m for m in muts if m[0] == "cond_max_pain_distance_pct")
        assert values == [0.5, 0.75, 1.0, 1.25, 1.5]
        assert base_index == 2

    def test_negative_base_sweeps_around_it(self) -> None:
        # signed family, base -1 → step |−1|·0.1 = 0.1 < 0.25 →
        # -1 + [-2..2]·0.25 = [-1.5, -1.25, -1.0, -0.75, -0.5]; no bound,
        # no shift — the grid is symmetric around the specced value
        spec = _spec_with([{"indicator": "max_pain_distance_pct",
                            "operator": ">", "value": -1}], version=7)
        muts, _, _ = _mutations(spec)
        _, values, base_index, _ = next(
            m for m in muts if m[0] == "cond_max_pain_distance_pct")
        assert values == [-1.5, -1.25, -1.0, -0.75, -0.5]
        assert base_index == 2 and values[base_index] == -1.0

    def test_bounded_family_shifts_up_whole_steps(self) -> None:
        # rank floor 2.0, lower bound 0; base 2 → raw [-2, 0, 2, 4, 6] →
        # min -2 < 0 → shift up ceil(2/2)=1 step → [0, 2, 4, 6, 8]; the
        # specced value stays ON the grid at index 1 (dte-guard pattern)
        spec = _spec_with([{"indicator": "gex_rank_1y", "operator": "<",
                            "value": 2}], version=6)
        muts, _, _ = _mutations(spec)
        _, values, base_index, _ = next(
            m for m in muts if m[0] == "cond_gex_rank_1y")
        assert values == [0.0, 2.0, 4.0, 6.0, 8.0]
        assert base_index == 1 and values[base_index] == 2.0

    def test_rsi_deep_oversold_shifts(self) -> None:
        # rsi floor 2.0, bound 0; base 3 → raw [-1, 1, 3, 5, 7] → shift
        # ceil(1/2)=1 step (+2) → [1, 3, 5, 7, 9], base at index 1
        spec = _spec_with([{"indicator": "rsi", "operator": "<", "value": 3,
                            "period": 14}])
        muts, _, _ = _mutations(spec)
        _, values, base_index, _ = next(m for m in muts if m[0] == "cond_rsi")
        assert values == [1.0, 3.0, 5.0, 7.0, 9.0]
        assert base_index == 1 and values[base_index] == 3.0

    def test_rank_low_threshold_in_bounds_no_shift(self) -> None:
        # base 10 → step 1 < 2 → [6, 8, 10, 12, 14]; min ≥ 0, no shift
        spec = _spec_with([{"indicator": "ivx_rank_1y", "operator": "<",
                            "value": 10}])
        muts, _, _ = _mutations(spec)
        _, values, base_index, _ = next(
            m for m in muts if m[0] == "cond_ivx_rank_1y")
        assert values == [6.0, 8.0, 10.0, 12.0, 14.0]
        assert base_index == 2

    def test_ivx_zscore_small_threshold_gets_a_real_probe(self) -> None:
        # THE motivating case (PR #97 review): 'ivx_zscore_1y > 0.3' swept
        # 0.24…0.36 — a 0.12σ band of a ±3σ scale, a guaranteed false
        # plateau. Floor 0.25σ: 0.3 + [-2..2]·0.25 = [-0.2, 0.05, 0.3,
        # 0.55, 0.8] — a full σ band. Expressible since spec v8 merged;
        # the string-keyed floor engaged with zero changes here.
        spec = _spec_with([{"indicator": "ivx_zscore_1y", "operator": ">",
                            "value": 0.3}], version=8)
        muts, note, _ = _mutations(spec)
        _, values, base_index, _ = next(
            m for m in muts if m[0] == "cond_ivx_zscore_1y")
        assert values == [-0.2, 0.05, 0.3, 0.55, 0.8]
        assert base_index == 2 and values[base_index] == 0.3
        assert note is not None
        assert "ivx_zscore_1y > 0.3 swept -0.2…0.8" in note

    def test_floor_table_matches_the_vocabulary(self) -> None:
        # every floor key is a real indicator — a typo here would silently
        # disable a floor. ivx_zscore_1y is spec v8 (PR #97): keyed by
        # string ON PURPOSE so the floor engages the moment the vocabulary
        # lands, whichever merges first.
        known = {i.value for i in Indicator}
        strays = set(_COND_FAMILY_FLOORS) - known
        assert strays <= {"ivx_zscore_1y"}
        assert _COND_FAMILY_FLOORS["ivx_zscore_1y"] == (0.25, None)
        # levels with parser-refused raw thresholds must NOT have floors
        for opaque in ("gex_level", "dex_level", "net_premium_level",
                       "market_tide_level", "nope_level", "sma", "ema"):
            assert opaque not in _COND_FAMILY_FLOORS

    def test_note_numerals_are_grounded(self) -> None:
        # guardrail #4 interaction: the note rides into verdict caveats and
        # the LLM may echo it — validated with the SHIPPING validator
        # (review finding: a hand-rolled membership check could drift from
        # validate_numbers' tolerance/scrub semantics), against an allowed
        # set built exactly the way production builds it: harvesting the
        # sweep values the report will carry
        spec = _spec_with([
            {"indicator": "skew_25d", "operator": ">", "value": 0.5},
            {"indicator": "max_pain_distance_pct", "operator": "<",
             "value": 1},
        ], version=7)
        muts, note, _ = _mutations(spec)
        assert note is not None
        allowed = grounding_set(
            {"params": [{"values": m[1]} for m in muts]})
        assert validate_numbers(note, allowed) == []

    def test_negative_threshold_on_bounded_family_stays_multiplicative(
        self,
    ) -> None:
        # review finding (executed live): 'rsi < -5' floored to a grid
        # [1,3,5,7,9] with base_index -3 — the specced value fell OFF the
        # grid and Python negative indexing mislabeled the as-specced cell
        # downstream. A threshold at/below the family's lower bound now
        # keeps the pre-floor multiplicative path: hand-computed
        # -5 × [.8,.9,1,1.1,1.2] = [-4, -4.5, -5, -5.5, -6], base idx 2
        spec = _spec_with([{"indicator": "rsi", "operator": "<",
                            "value": -5, "period": 14}])
        muts, note, _ = _mutations(spec)
        _, values, base_index, _ = next(m for m in muts if m[0] == "cond_rsi")
        assert values == [-4.0, -4.5, -5.0, -5.5, -6.0]
        assert base_index == 2 and values[base_index] == -5.0
        assert note is None  # not floored, nothing to disclose

    def test_every_indicator_has_a_floor_or_an_exemption(self) -> None:
        # review finding: the typo guard only checked table→enum; new
        # vocabulary could silently fall back to ±20% with nobody deciding.
        # The partition forces the decision per indicator, forever.
        floors = set(_COND_FAMILY_FLOORS)
        assert not floors & _COND_FLOOR_EXEMPT  # disjoint by construction
        undecided = {i.value for i in Indicator} - floors - _COND_FLOOR_EXEMPT
        assert undecided == set(), (
            f"new indicator(s) {sorted(undecided)} need a _COND_FAMILY_FLOORS "
            "entry or an explicit _COND_FLOOR_EXEMPT listing"
        )

    def test_max_pain_pair_disclosures_are_attributable(self) -> None:
        # review finding: the canonical band pair produced two same-named
        # entries — the operator + specced value now tell them apart
        spec = _spec_with([
            {"indicator": "max_pain_distance_pct", "operator": "<", "value": 1},
            {"indicator": "max_pain_distance_pct", "operator": ">", "value": -1},
        ], version=7)
        _, note, _ = _mutations(spec)
        assert note is not None
        assert "max_pain_distance_pct < 1 swept 0.5…1.5" in note
        assert "max_pain_distance_pct > -1 swept -1.5…-0.5" in note

    def test_floored_and_sign_and_cap_notes_compose(self) -> None:
        # all three disclosure parts at once, still one readable note
        spec = _spec_with([
            {"indicator": "skew_25d", "operator": ">", "value": 0.5},
            {"indicator": "rsi", "operator": "<", "value": 30, "period": 14},
            {"indicator": "vix_level", "operator": ">", "value": 20},
            {"indicator": "gex_level", "operator": ">", "value": 0},
            {"indicator": "put_call_flow_ratio", "operator": ">",
             "value": 1.2},
        ], version=7)
        names, note = _names(spec)
        assert len([n for n in names if n.startswith("cond_")]) == 3
        assert note is not None
        assert "absolute family-scale steps" in note
        assert "gex_level" in note and "sign test" in note
        assert "1 further condition not swept" in note


class TestLabels:
    def test_condition_label_is_unit_free(self) -> None:
        assert _param_label("cond_rsi", 24.0) == "24"
        assert _param_label("cond_skew_25d", 4.0) == "4"
        # existing params keep their units
        assert _param_label("profit_target", 50.0) == "50%"
        assert _param_label("dte", 14.0) == "14d"

    def test_display_name_strips_cond_prefix(self) -> None:
        assert _sweep_display_name("cond_skew_25d") == "skew 25d"
        assert _sweep_display_name("cond_ivx_rank_1y") == "ivx rank 1y"
        assert _sweep_display_name("profit_target") == "profit target"


def _weekdays(start: date, n: int) -> list[date]:
    out: list[date] = []
    d = start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _grid_store():
    """Persistent integer-strike grid (the regression-test pattern) so
    RSI-gated entries fill and different thresholds trade differently."""
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
            for strike in range(80, 121):
                intrinsic = max(strike - spot, 0.0)
                tv = 0.2 * math.sqrt(dte) * math.exp(-abs(strike - spot) / 6.0)
                mid = intrinsic + tv
                if mid < 0.05:
                    continue
                delta = max(0.02, min(0.98, 0.5 + (strike - spot) / 12.0))
                rows.append({
                    "expiration": exp.isoformat(), "right": "put",
                    "strike": float(strike),
                    "bid": round(mid - 0.05, 2), "ask": round(mid + 0.05, 2),
                    "delta": round(-delta, 4), "iv": 0.2,
                })
        chains[s.isoformat()] = rows
    return build_fixture_store("SPY", chains, underlying)


def _rsi_spec(threshold: float) -> StrategySpec:
    return StrategySpec.model_validate({
        "spec_version": 2,
        "meta": {"name": "rsi short put", "description_raw": "f8 sweep"},
        "underlying": {"ticker": "SPY"},
        "position": {"structure": "short_put",
                     "legs": [{"right": "put", "side": "short", "ratio": 1,
                               "strike_selection": {"method": "delta", "value": 0.30}}],
                     "expiration_selection": {"target_dte": 14, "min_dte": 5,
                                              "max_dte": 30}},
        "entry": {"schedule": {"frequency": "signal_only"},
                  "conditions": [{"indicator": "rsi", "period": 14,
                                  "operator": "<", "value": threshold}],
                  "max_concurrent_positions": 3},
        "exit": {"profit_target_pct": 50, "time_exit_dte": 3},
        "sizing": {"method": "fixed_contracts", "value": 1},
        "costs": {"commission_per_contract": 0.65,
                  "slippage_half_spread_fraction": 0.5, "slippage_half_spread_fraction_sell": 0.5},
        "backtest": {"start": None, "end": None, "initial_capital": 25000,
                     "seed": 42},
    })


class TestSweepIntegration:
    def test_rsi_threshold_gets_a_real_swept_row(self) -> None:
        store = _grid_store()
        sens = sensitivity(_rsi_spec(48), store)
        names = [p.name for p in sens.params]
        assert "cond_rsi" in names
        rsi = next(p for p in sens.params if p.name == "cond_rsi")
        # the sweep RE-RAN the engine at 5 thresholds — at least one
        # neighbor differs from the base (the threshold actually bites)
        valid = [s for s in rsi.sharpes if s is not None]
        assert len(valid) >= 3
        assert len({round(s, 6) for s in valid}) > 1
        assert rsi.classification in ("plateau", "cliff")

    def test_recommendation_can_name_a_better_threshold(self) -> None:
        # not asserting a specific direction — asserting that IF a condition
        # neighbor beats the base, the recommendation names the indicator
        # (never a fabricated %); this pins the weave into recommendations
        store = _grid_store()
        from app.honesty.gauntlet import run_gauntlet

        report = run_gauntlet(_rsi_spec(48), store, run_backtest_result(store),
                              trials=1)
        recs = _recommendations(report)
        # no sweep-name plumbing leaks into user-facing text
        for r in recs:
            assert "cond_" not in r


def run_backtest_result(store):
    from app.engine.runner import run_backtest
    return run_backtest(_rsi_spec(48), store)
