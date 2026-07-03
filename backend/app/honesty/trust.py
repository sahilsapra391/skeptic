"""Deterministic trust rules (TECH-SPEC §7): the trust level is COMPUTED
here, in auditable code — the LLM narrates it, it never chooses it.

The five attacks (matching the design's chips):
  oos          — survives when the OOS split is not flagged
  walk_forward — meaningful AND ≥ 60% of folds profitable
  monte_carlo  — P(loss) ≤ 20%
  sensitivity  — the parameter neighborhood is a plateau, not a cliff
  sample       — ≥ 30 trades across ≥ 2 volatility regimes

Level = 1 + (# of the four statistical attacks survived), then gated:
  DSR < 0.5 (likely mined) caps at 2; an OOS sign flip caps at 2.
The sample guardrail overrides everything: insufficient_evidence (no level).
"""

from __future__ import annotations

from app.honesty.report import (
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
) -> Trust:
    survived = {
        "oos": not oos.flagged,
        "walk_forward": bool(wf.meaningful and (wf.consistency or 0.0) >= 0.6),
        "monte_carlo": mc.p_loss is not None and mc.p_loss <= 0.20,
        "sensitivity": sens.verdict == "plateau",
        "sample": not sample.capped,
    }
    reasons: list[str] = []

    if sample.capped:
        reasons.append(sample.cap_reason or "sample too thin")
        return Trust(
            level=None,
            label="insufficient_evidence",
            survived=survived,
            survived_count=sum(survived.values()),
            reasons=reasons,
        )

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

    return Trust(
        level=level,
        label=LABELS[level],
        survived=survived,
        survived_count=sum(survived.values()),
        reasons=reasons,
    )
