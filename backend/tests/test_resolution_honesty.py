"""FX.4 — mixed-resolution gauntlet honesty, hand-computed.

The resolution_split stage recomputes the headline on the 5-MIN-ONLY
sub-window from recorded returns and fills (no re-run). Owner decisions
pinned here:
  * a SIGN FLIP (full-run edge positive, 5-min-only negative) is a
    data-VALIDITY finding → hard cap to insufficient_evidence, refused not
    weakly blessed (contrast the OOS flip — a ROBUSTNESS signal measured
    at equal resolution — which earns a low level);
  * the cap only ARMS at real-evidence floors (both subsets ≥ 15 sessions
    AND the 5-min subset ≥ MIN_TRADES closed trades) — below them the run
    carries a "too thin to cross-check" caveat, disclosed not judged;
  * only the OPTIMISTIC direction caps — a negative full run blesses
    nothing to protect;
  * walk-forward folds disclose their minute-session share IN the run;
  * the verdict caveats carry the grounded mix disclosure (quant+retail).

Bucket hand-math (TestSplitBuckets): 40 sessions, d1–d20 five_min,
d21–d38 + d40 minute, d39 unlabeled (EOD gap). 16 closed trades of +$5 on
5-min days (pl 80.00), 16 of +$20 on minute days (pl 320.00).
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.engine.types import RunResult, TradeEvent
from app.honesty.report import ResolutionBucket, ResolutionSplit
from app.honesty.stages import resolution_split, walk_forward
from app.honesty.verdict import allowed_numbers, template_verdict
from tests.test_verdict_language import _minimal_report

D0 = date(2025, 1, 6)


def _dates(n: int) -> list[date]:
    out, d = [], D0
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _result(
    n_five: int = 20, n_minute: int = 19, gap_at: int = 38,
    five_step: float = 10.0, minute_step: float = 50.0,
    five_trades: int = 16, minute_trades: int = 16,
    five_pl: float = 5.0, minute_pl: float = 20.0,
) -> RunResult:
    n = n_five + n_minute + 1  # + one unlabeled gap session
    dates = _dates(n)
    labels: dict[date, str] = {}
    equity = [10_000.0]
    i_minute = 0
    for i, d in enumerate(dates):
        if i == gap_at:
            pass  # unlabeled (EOD-fallback day)
        elif i < n_five:
            labels[d] = "five_min"
        else:
            labels[d] = "minute"
            i_minute += 1
        if i > 0:
            step = (0.0 if i == gap_at
                    else five_step if i < n_five else minute_step)
            equity.append(round(equity[-1] + step, 2))
    result = RunResult(ticker="SPY", effective_start=dates[0],
                       effective_end=dates[-1], seed=42)
    result.clock = "5min"
    result.dates = dates
    result.equity = equity
    result.resolution_by_session = labels
    trades: list[TradeEvent] = []
    five_days = [d for d in dates if labels.get(d) == "five_min"]
    minute_days = [d for d in dates if labels.get(d) == "minute"]
    for k in range(five_trades):
        trades.append(TradeEvent(day=five_days[k % len(five_days)],
                                 action="CLOSE", detail="t", pl=five_pl))
    for k in range(minute_trades):
        trades.append(TradeEvent(day=minute_days[k % len(minute_days)],
                                 action="CLOSE", detail="t", pl=minute_pl))
    result.trades = trades
    return result


class TestSplitBuckets:
    def test_hand_computed_buckets(self) -> None:
        split = resolution_split(_result())
        assert split.meaningful and split.judged
        assert split.five_min.sessions == 20  # d1 window-counted + d2..d20
        assert split.minute.sessions == 19
        assert split.eod_fallback_sessions == 1
        assert split.five_min.trades == 16 and split.minute.trades == 16
        assert split.five_min.pl == pytest.approx(80.00)
        assert split.minute.pl == pytest.approx(320.00)
        # both subsets rise → both sharpes positive, no flip
        assert (split.five_min.sharpe or 0) > 0
        assert not split.sign_flip

    def test_sign_flip_caps(self) -> None:
        # 5-min days bleed, minute days boom → full-run edge positive but
        # the 5-min-only sub-window is NEGATIVE: the granularity mirage
        split = resolution_split(_result(five_step=-10.0, minute_step=80.0))
        assert split.judged
        assert (split.full_sharpe or 0) > 0
        assert (split.five_min.sharpe or 0) < 0
        assert split.sign_flip and split.caps_trust

    def test_floors_disarm_the_cap(self) -> None:
        # same mirage but only 14 closed trades on the 5-min side — below
        # MIN_TRADES the sub-window is noise: disclosed, never a cap
        split = resolution_split(
            _result(five_step=-10.0, minute_step=80.0, five_trades=14))
        assert split.meaningful and not split.judged
        assert split.note is not None and "too thin" in split.note
        assert not split.sign_flip and not split.caps_trust

    def test_only_the_optimistic_direction_caps(self) -> None:
        # full-run edge NEGATIVE with a positive 5-min subset blesses
        # nothing — no cap (the verdict is already negative)
        split = resolution_split(_result(five_step=10.0, minute_step=-80.0))
        assert split.judged
        assert (split.full_sharpe or 0) < 0
        assert not split.sign_flip

    def test_inert_paths(self) -> None:
        daily = _result()
        daily.clock = "daily"
        assert not resolution_split(daily).meaningful
        no_map = _result()
        no_map.resolution_by_session = {}
        assert not resolution_split(no_map).meaningful
        single = _result()
        single.resolution_by_session = {
            d: "five_min" for d in single.resolution_by_session}
        s = resolution_split(single)
        assert not s.meaningful and "single-resolution" in (s.note or "")


class TestTrustCap:
    def test_resolution_flip_refuses(self) -> None:
        from app.honesty.trust import compute_trust

        r = _minimal_report()
        split = ResolutionSplit(
            meaningful=True, judged=True, full_sharpe=0.9,
            five_min=ResolutionBucket(sessions=20, trades=16, pl=-500.0,
                                      sharpe=-0.4),
            minute=ResolutionBucket(sessions=19, trades=16, pl=2_000.0,
                                    sharpe=1.5),
            sign_flip=True,
        )
        trust = compute_trust(r.oos, r.walk_forward, r.monte_carlo,
                              r.sensitivity, r.regime_sample, r.dsr,
                              r.coverage, None, None, split)
        assert trust.label == "insufficient_evidence"
        assert any("minute-resolution slice" in reason
                   for reason in trust.reasons)

    def test_unjudged_split_never_caps(self) -> None:
        from app.honesty.trust import compute_trust

        r = _minimal_report()
        split = ResolutionSplit(meaningful=True, judged=False,
                                sign_flip=False, note="too thin")
        trust = compute_trust(r.oos, r.walk_forward, r.monte_carlo,
                              r.sensitivity, r.regime_sample, r.dsr,
                              r.coverage, None, None, split)
        assert trust.label != "insufficient_evidence"


class TestRefusalHeadline:
    def test_resolution_cap_names_the_artifact_not_the_sample(self) -> None:
        # review MAJOR pinned: a resolution-cap-only refusal must never
        # print "too few trades" on a thick sample — both voices name the
        # granularity artifact
        from app.honesty.report import Trust
        from app.honesty.verdict import retail_template_verdict

        r = _minimal_report()
        r.resolution_split = ResolutionSplit(
            meaningful=True, judged=True, full_sharpe=0.9,
            five_min=ResolutionBucket(sessions=2888, trades=900, pl=-500.0,
                                      sharpe=-0.4),
            minute=ResolutionBucket(sessions=91, trades=200, pl=2_000.0,
                                    sharpe=1.5),
            sign_flip=True,
        )
        survived = {"oos": True, "walk_forward": False, "monte_carlo": True,
                    "sensitivity": True, "sample": True}
        r.trust = Trust(level=None, label="insufficient_evidence",
                        survived=survived, survived_count=4,
                        reasons=["the edge lives in the minute-resolution "
                                 "slice"])
        quant = template_verdict(r)
        assert "minute-resolution" in quant.headline
        assert "too few" not in quant.headline.lower()
        retail = retail_template_verdict(r)
        assert "lens" in retail.headline or "measure" in retail.headline
        assert "too few" not in retail.headline.lower()


class TestWalkForwardShares:
    def test_folds_disclose_minute_share(self) -> None:
        # 126 sessions → 3 folds of 42; the last 42 sessions ran the minute
        # grid → shares 0.0 / 0.0 / 1.0 (disclosure lives IN the run)
        result = _result(n_five=84, n_minute=41, gap_at=999,
                         five_trades=16, minute_trades=16)
        wf = walk_forward(result)
        assert wf.meaningful and len(wf.folds) == 3
        assert wf.folds[0].minute_share == 0.0
        assert wf.folds[-1].minute_share == 1.0

    def test_no_map_means_no_share(self) -> None:
        result = _result(n_five=84, n_minute=41, gap_at=999)
        result.resolution_by_session = {}
        wf = walk_forward(result)
        assert wf.meaningful
        assert all(f.minute_share is None for f in wf.folds)


class TestVerdictDisclosure:
    def _report_with_split(self):  # type: ignore[no-untyped-def]
        r = _minimal_report()
        r.resolution_split = ResolutionSplit(
            meaningful=True, judged=True, full_sharpe=0.8,
            five_min=ResolutionBucket(sessions=2888, trades=900, pl=1500.0,
                                      sharpe=0.7, first="2013-01-03",
                                      last="2026-02-23"),
            minute=ResolutionBucket(sessions=91, trades=40, pl=400.0,
                                    sharpe=1.1, first="2026-02-24",
                                    last="2026-07-06"),
            sign_flip=False,
        )
        return r

    def test_quant_caveat_grounded(self) -> None:
        r = self._report_with_split()
        text = template_verdict(r)
        line = next((c for c in text.caveats if "Mixed resolution" in c), None)
        assert line is not None
        assert "91 sessions" in line and ("2,888" in line or "2888" in line)
        # every number in the caveat exists in the report (guardrail #4)
        allowed = allowed_numbers(r)
        assert 91.0 in allowed and 2888.0 in allowed

    def test_fold_disclosure_in_caveats(self) -> None:
        from app.honesty.report import WalkForward, WalkForwardFold

        r = self._report_with_split()
        r.walk_forward = WalkForward(
            meaningful=True, test_sessions=42, consistency=1.0,
            folds=[WalkForwardFold(start="2026-01-05", end="2026-03-06",
                                   ret=0.05, trades=30, minute_share=0.0),
                   WalkForwardFold(start="2026-03-09", end="2026-05-08",
                                   ret=0.09, trades=28, minute_share=0.62)])
        text = template_verdict(r)
        fold_line = next((c for c in text.caveats
                          if "minute grid" in c and "62%" in c), None)
        assert fold_line is not None
        assert "resolution, not regime" in fold_line


class TestReceiptUpgrade:
    def test_differing_mix_is_named(self) -> None:
        from app.api.replay import build_receipt

        receipt = build_receipt(
            "rid",
            {"metrics": {"sharpe": 1.0, "total_return": 0.1},
             "resolutionMix": {"minute": 43, "five_min": 100}},
            {"metrics": {"sharpe": 1.2, "total_return": 0.2}},
            {"fillSources": {}, "resolutionMix": {"minute": 61, "five_min": 100}},
            "2026-07-07T00:00:00Z")
        note = receipt["resolution_upgrade"]
        assert note is not None
        assert "43 → 61" in note and "resolution upgrade" in note

    def test_same_mix_is_silent(self) -> None:
        from app.api.replay import build_receipt

        receipt = build_receipt(
            "rid", {"metrics": {}, "resolutionMix": {"minute": 43}},
            {"metrics": {}},
            {"fillSources": {}, "resolutionMix": {"minute": 43}},
            "2026-07-07T00:00:00Z")
        assert receipt["resolution_upgrade"] is None

    def test_production_shape_daily_parent_never_fires(self) -> None:
        # review BLOCKER pinned: a DAILY parent's stats carry no
        # resolutionMix; the replay always carries a five_min mix — the
        # note must stay silent (no false "upgrade" on ordinary receipts)
        from app.api.replay import build_receipt

        receipt = build_receipt(
            "rid",
            {"metrics": {"sharpe": 1.0}},  # real daily stats: no mix key
            {"metrics": {"sharpe": 0.9}},
            {"fillSources": {"ivol_5min": 86},
             "resolutionMix": {"five_min": 43}},
            "2026-07-07T00:00:00Z")
        assert receipt["resolution_upgrade"] is None
