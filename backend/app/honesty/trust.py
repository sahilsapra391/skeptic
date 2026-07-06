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
    scale_in_pending: bool = False,
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
    # D5a interlock: a scale-in ladder is a martingale, and until its
    # dedicated defenses land (D5c) NO ladder can be blessed — no matter how
    # good the four attacks look or how many baskets it cleared. This is FIRST
    # so the refusal reads as "defenses pending", not "sample too thin"
    # (docs/HONESTY.md · the whole thesis of shipping scale-in before D5c).
    if scale_in_pending:
        cap_reasons.append("scale-in safety checks pending (D5c)")
    if coverage is not None and coverage.materially_short:
        cap_reasons.append(coverage.reason or "requested window mostly lacks chain data")
    if sample.capped:
        cap_reasons.append(sample.cap_reason or "sample too thin")
    if cap_reasons:
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

    return Trust(
        level=level,
        label=LABELS[level],
        survived=survived,
        survived_count=sum(survived.values()),
        reasons=reasons,
    )
