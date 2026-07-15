"""Deterministic trust rules (TECH-SPEC §7): the trust level is COMPUTED
here, in auditable code — the LLM narrates it, it never chooses it.

The five attacks (matching the design's chips):
  oos          — survives when the OOS split is not flagged
  walk_forward — meaningful AND ≥ 60% of folds profitable
  monte_carlo  — P(loss) ≤ 20%
  sensitivity  — the parameter neighborhood is a plateau, not a cliff
  sample       — ≥ MIN_TRADES (15) trades across ≥ 2 volatility regimes

Level = 1 + (# of the four statistical attacks survived), then gated:
  DSR < 0.5 (likely mined) caps at 2; an OOS sign flip caps at 2.
Two data-integrity guardrails override everything with insufficient_evidence
(no level): a thin sample, and materially short chain coverage of the
requested window (the seventeen-fills case, diagnostics/SEVENTEEN.md).
"""

from __future__ import annotations

from app.honesty.report import (
    Concentration,
    Coverage,
    Dsr,
    MonteCarlo,
    OosSplit,
    RegimeSample,
    ResolutionSplit,
    RuinDisclosure,
    ScaleInHonesty,
    Sensitivity,
    Trust,
    WalkForward,
)

LABELS = {1: "noise", 2: "weak", 3: "suggestive", 4: "robust", 5: "proven"}


def compute_trust(
    oos: OosSplit,
    wf: WalkForward,
    mc: MonteCarlo,
    sens: Sensitivity,
    sample: RegimeSample,
    dsr: Dsr,
    coverage: Coverage | None = None,
    concentration: Concentration | None = None,
    scale_in: ScaleInHonesty | None = None,
    resolution: ResolutionSplit | None = None,
    ruin: RuinDisclosure | None = None,
) -> Trust:
    survived = {
        "oos": not oos.flagged,
        "walk_forward": bool(wf.meaningful and (wf.consistency or 0.0) >= 0.6),
        "monte_carlo": mc.p_loss is not None and mc.p_loss <= 0.20,
        "sensitivity": sens.verdict == "plateau",
        "sample": not sample.capped,
    }

    # data-integrity caps → insufficient_evidence, most fundamental first: no
    # amount of good statistics rescues a window that was barely tested.
    cap_reasons: list[str] = []
    # D5c martingale defenses (LIFTS the D5a interlock): a scale-in ladder that
    # trips the ruin-tail MC or the deep-rung-dependency check is refused —
    # FIRST, so the refusal reads as the martingale failure, not "sample too
    # thin". A ladder that clears them is judged like any strategy
    # (docs/HONESTY.md · scale-in defenses).
    if scale_in is not None and scale_in.caps_trust:
        cap_reasons.extend(scale_in.reasons)
    # FX.4 mixed-resolution defense: a sign flip on the 5-min-only
    # sub-window is a DATA-VALIDITY finding — the edge appears only on the
    # recent minute slice and reverses at the resolution the deep history
    # was tested at. A granularity mirage is refused, never weakly blessed
    # (owner decision; contrast the OOS flip, a ROBUSTNESS signal measured
    # at equal resolution, which earns a low level instead).
    if resolution is not None and resolution.caps_trust:
        full = resolution.full_sharpe
        sub = resolution.five_min.sharpe
        # sign_flip guarantees both non-None today; guard anyway — the
        # model permits the invalid state and this function takes the model
        full_s = f"{full:.2f}" if full is not None else "n/a"
        sub_s = f"{sub:.2f}" if sub is not None else "n/a"
        cap_reasons.append(
            "the edge lives in the minute-resolution slice — it flips sign "
            f"on the 5-min-only sub-window (Sharpe {full_s} full vs "
            f"{sub_s} on {resolution.five_min.sessions} 5-min sessions): "
            "a data artifact until proven otherwise"
        )
    if coverage is not None and coverage.materially_short:
        cap_reasons.append(coverage.reason or "requested window mostly lacks chain data")
    if sample.capped:
        cap_reasons.append(sample.cap_reason or "sample too thin")
    # the ruin halt (owner 2026-07-15): the account was wiped out — on a
    # refused run the refusal carries the ruin reason FIRST (both facts
    # disclosed); on a gradeable run the level hard-caps at the lowest band
    # below. Ruin never rescues a refusal into a grade.
    ruin_reason = None
    if ruin is not None:
        ruin_reason = (
            f"the account was wiped out on {ruin.ruin_date} "
            f"(equity ${ruin.final_equity:,.0f}) — no statistic computed "
            "past that point is investable"
        )
    if cap_reasons:
        if ruin_reason:
            cap_reasons.insert(0, ruin_reason)
        return Trust(
            level=None,
            label="insufficient_evidence",
            survived=survived,
            survived_count=sum(survived.values()),
            reasons=cap_reasons,
        )

    reasons: list[str] = []
    core = ["oos", "walk_forward", "monte_carlo", "sensitivity"]
    level = 1 + sum(1 for k in core if survived[k])
    if ruin_reason:
        level = 1  # a blown account is the floor, whatever else survived
        reasons.append(ruin_reason)

    if oos.sign_flip:
        level = min(level, 2)
        reasons.append("in-sample edge flips sign out-of-sample")
    if dsr.dsr is not None and dsr.dsr < 0.5:
        level = min(level, 2)
        reasons.append(
            f"deflated Sharpe {dsr.dsr:.2f} after {dsr.trials} trials — likely mined"
        )
    if not survived["oos"]:
        reasons.append("edge does not survive the out-of-sample split")
    if not survived["walk_forward"] and wf.meaningful:
        reasons.append("walk-forward folds are inconsistent")
    if not survived["monte_carlo"] and mc.p_loss is not None:
        reasons.append(f"{mc.p_loss:.0%} of resampled paths lose money")
    if not survived["sensitivity"] and sens.verdict == "cliff":
        reasons.append("the parameter optimum is a cliff — neighbors lose")
    # D1d: concentration is a REPORTED reason, never a level change —
    # promoting it to a cap requires evidence in a reviewed session
    if concentration is not None and concentration.flagged and concentration.note:
        reasons.append(concentration.note)
    # D5c: a ladder that cleared the hard caps can still carry reported
    # martingale signals — the deepest rung moving the total, or one deep
    # basket dominating the P&L (the martingale tell). Reported, not a cap.
    if scale_in is not None:
        if scale_in.deep_rung_flagged and not scale_in.deep_rung_sign_flip:
            reasons.append(
                f"the deepest rung ({scale_in.deepest_threshold:g}) moves the total "
                f"materially: ${scale_in.realized_total:,.0f} → "
                f"${scale_in.total_without_deepest:,.0f} without it"
            )
        if scale_in.concentration_flagged and scale_in.top_basket_share is not None:
            reasons.append(
                f"one basket drove {scale_in.top_basket_share:.0%} of gross basket P&L"
            )

    return Trust(
        level=level,
        label=LABELS[level],
        survived=survived,
        survived_count=sum(survived.values()),
        reasons=reasons,
    )
