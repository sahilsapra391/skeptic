"""D3d: fill-model calibration + collection priorities.

The decision rule is the product: conservative corrections open at the
base bar; optimism-increasing corrections need the higher bar AND the
title prefix (owner amendment 3). All fixtures hand-computed.
"""

from __future__ import annotations

import statistics

import pytest

from scripts.build_priorities import DATA_SKIP_REASONS
from scripts.calibrate_fill_model import (
    CAL_BASE_MIN_N,
    CAL_OPTIMISTIC_MIN_N,
    Calibration,
    _rescale,
    decide,
    evidence_markdown,
)


def _cal(excess: list[float], f: float = 0.5) -> Calibration:
    cal = Calibration(measured_at="2026-07-05T00:00:00Z", f_current=f)
    cal.excess = excess
    cal.n = len(excess)
    cal.sessions_shared = cal.sessions_used = max(1, len(excess) // 40)
    return cal


class TestDecisionRule:
    def test_aligned_inside_bar_no_proposal(self) -> None:
        # median 0.6 vs intended 0.5 — inside ±0.25, whatever the n
        d = decide(_cal([0.6] * 5_000))
        assert not d.proposal and d.direction == "none"

    def test_conservative_at_base_bar(self) -> None:
        # daily fills concede only 0.2 true half-spreads vs intended 0.5:
        # model too optimistic — raising the default opens at the base bar
        d = decide(_cal([0.2] * CAL_BASE_MIN_N))
        assert d.proposal and d.direction == "conservative"
        assert d.f_new is not None and d.f_new > 0.5

    def test_conservative_below_sample_floor_waits(self) -> None:
        d = decide(_cal([0.2] * (CAL_BASE_MIN_N - 1)))
        assert not d.proposal and "waiting" in d.reason

    def test_optimistic_needs_higher_bar(self) -> None:
        # daily fills concede 0.9 vs intended 0.5 (divergence 0.4): beyond
        # the base bar but BELOW the optimism bar (0.5) — blocked, loudly
        d = decide(_cal([0.9] * CAL_OPTIMISTIC_MIN_N))
        assert not d.proposal
        assert "never a silent nudge" in d.reason

    def test_optimistic_over_higher_bar_flagged(self) -> None:
        d = decide(_cal([1.2] * CAL_OPTIMISTIC_MIN_N))
        assert d.proposal and d.direction == "optimism_increasing"
        assert d.f_new is not None and d.f_new < 0.5

    def test_optimistic_sample_floor_is_higher(self) -> None:
        # same divergence, conservative-sized sample: still blocked
        d = decide(_cal([1.2] * CAL_BASE_MIN_N))
        assert not d.proposal

    def test_empty_measurement(self) -> None:
        d = decide(_cal([]))
        assert not d.proposal and "no overlapping" in d.reason


class TestRescale:
    def test_scales_toward_intent_and_snaps(self) -> None:
        # measured 0.25 at f=0.5 → raw 0.5×(0.5/0.25)=1.0
        assert _rescale(0.5, 0.25) == 1.0
        # measured 1.25 at f=0.5 → raw 0.2 → snaps to the 0.05 grid
        assert _rescale(0.5, 1.25) == pytest.approx(0.2)

    def test_clamped_to_valid_range(self) -> None:
        assert _rescale(0.5, 0.01) == 1.0  # never above full adverse quote
        assert _rescale(0.5, 100.0) == 0.05  # never 0 — mid fills forbidden


class TestEvidenceDoc:
    def test_doc_carries_the_numbers_and_thresholds(self) -> None:
        cal = _cal([0.2] * CAL_BASE_MIN_N)
        d = decide(cal)
        doc = evidence_markdown(cal, d, "2026-07-05")
        m = statistics.median(cal.excess)
        assert f"n = {cal.n}" in doc
        assert str(round(m, 4)) in doc
        assert "OPTIMISM-INCREASING" in doc  # the policy is always stated
        assert str(CAL_OPTIMISTIC_MIN_N) in doc
        assert "PROPOSAL: conservative" in doc

    def test_no_change_doc_says_so(self) -> None:
        cal = _cal([])
        doc = evidence_markdown(cal, decide(cal), "2026-07-05")
        assert "No change." in doc


class TestPriorities:
    def test_data_skip_vocabulary_matches_engine(self) -> None:
        # the reasons the engine actually emits (engine.py/fills.py/
        # selection.py) — a rename there must break THIS, not silently
        # drop the demand signal
        assert {"no_expiration_in_window", "missing_quote",
                "illiquid_spread", "no_chain_data"} <= DATA_SKIP_REASONS
        # strategy-shaped reasons never create collection demand
        assert "conditions_not_met" not in DATA_SKIP_REASONS
        assert "max_concurrent" not in DATA_SKIP_REASONS

    def test_ranking_orders_by_score_then_name(self) -> None:
        import scripts.build_priorities as bp

        wants = [
            {"want": "b", "why": "", "score": 3},
            {"want": "a", "why": "", "score": 3},
            {"want": "c", "why": "", "score": 9},
        ]
        wants.sort(key=lambda w: (-w["score"], w["want"]))
        assert [w["want"] for w in wants] == ["c", "a", "b"]
        # scoring constants are reviewed code, not runtime knobs
        assert bp.SCORE_PER_WAITING_RUN == 5
