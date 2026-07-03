"""Verdict writer (TECH-SPEC §7).

Deterministic template first: every sentence is assembled from numbers in
the HonestyReport, so it is grounded by construction. When
OPENROUTER_API_KEY is configured, an LLM rewrites the narration — its
input is ONLY the report JSON (never user text), and every numeric token
in its output must exist in the report within rounding tolerance or the
narration is rejected (one retry, then the template ships instead).
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.honesty.report import HonestyReport

log = logging.getLogger("verdict")

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "anthropic/claude-sonnet-4.5"


class VerdictText(BaseModel):
    model_config = ConfigDict(extra="forbid")

    headline: str
    evidence: list[str]
    breaks_where: list[str]
    caveats: list[str]
    source: str  # "template" | "llm"


# ------------------------------------------------------- numeric grounding
_NUM_RE = re.compile(r"-?\d+(?:[,.]\d+)*")


def _harvest_numbers(obj: Any, out: set[float]) -> None:
    if isinstance(obj, bool):
        return
    if isinstance(obj, (int, float)):
        v = float(obj)
        for candidate in (v, v * 100, abs(v), abs(v) * 100, round(v, 2), round(v * 100, 1)):
            out.add(round(candidate, 4))
        return
    if isinstance(obj, dict):
        for item in obj.values():
            _harvest_numbers(item, out)
    elif isinstance(obj, list):
        for item in obj:
            _harvest_numbers(item, out)


def allowed_numbers(report: HonestyReport) -> set[float]:
    out: set[float] = {0.0}
    _harvest_numbers(report.model_dump(), out)
    # small counting numbers and fixed phrasing constants (fold counts,
    # percentile labels, the resample count) — not statistics
    out.update(float(x) for x in range(0, 31))
    out.update({5.0, 50.0, 95.0, 100.0, 252.0})
    out.add(float(report.monte_carlo.resamples))
    return out


_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def validate_numbers(text: str, allowed: set[float]) -> list[str]:
    """Return the numeric tokens in `text` that do NOT exist in the report
    (within rounding tolerance). ISO dates are identifiers, not statistics,
    and are skipped. Empty list = grounded."""
    violations: list[str] = []
    scrubbed = _DATE_RE.sub(" ", text.replace(",", ""))
    for token in _NUM_RE.findall(scrubbed):
        try:
            value = float(token)
        except ValueError:
            continue
        ok = any(
            abs(value - a) <= max(0.051, abs(a) * 0.006) for a in allowed
        )
        if not ok:
            violations.append(token)
    return violations


# ------------------------------------------------------------- the template
def _pct(v: float | None, digits: int = 0) -> str:
    return "—" if v is None else f"{v * 100:.{digits}f}%"


def template_verdict(report: HonestyReport) -> VerdictText:
    """Uncomfortable part first, always."""
    oos, wf, mc = report.oos, report.walk_forward, report.monte_carlo
    sens, trust, sample = report.sensitivity, report.trust, report.regime_sample

    if trust.label == "insufficient_evidence":
        plural = "s" if sample.trades != 1 else ""
        headline = (
            f"Verdict withheld. {sample.trades} closed trade{plural}"
            f"{' in a single volatility regime' if sample.regimes_present < 2 else ''}"
            " can’t answer this honestly."
        )
    elif not trust.survived["oos"]:
        headline = "Edge fades out-of-sample. What’s left is thin."
    elif trust.survived_count >= 4 and (trust.level or 0) >= 4:
        headline = (
            f"Survives {trust.survived_count} of 5 attacks. "
            "Strong within the record we have."
        )
    elif "mined" in " ".join(trust.reasons):
        headline = "The Sharpe doesn’t survive deflation — this looks mined, not earned."
    elif sens.verdict == "cliff":
        headline = "The optimum sits on a cliff — neighboring parameters lose."
    else:
        headline = f"Survives {trust.survived_count} of 5 attacks. Treat as suggestive, not proven."

    evidence: list[str] = []
    if oos.is_sharpe is not None and oos.oos_sharpe is not None:
        evidence.append(
            f"Sharpe {oos.is_sharpe:.2f} in-sample vs {oos.oos_sharpe:.2f} out-of-sample"
            + (
                f" ({_pct(oos.degradation)} of in-sample)"
                if oos.degradation is not None
                else ""
            )
        )
    if wf.meaningful and wf.consistency is not None:
        positive = sum(1 for f in wf.folds if f.ret > 0)
        evidence.append(f"Walk-forward: {positive} of {len(wf.folds)} windows profitable")
    if mc.p_loss is not None:
        evidence.append(
            f"Monte Carlo ({mc.resamples} resamples): {_pct(mc.p_loss)} of paths lose money; "
            f"95th-percentile drawdown {_pct(mc.max_drawdown_p95)}"
        )

    breaks_where: list[str] = list(trust.reasons)
    if not breaks_where and sens.verdict == "plateau":
        breaks_where.append("no structural break found — the sample is still the limit")

    caveats = [
        f"{sample.trades} closed trades · "
        f"{sample.regimes_present} volatility regime{'s' if sample.regimes_present != 1 else ''} "
        f"represented · window {report.effective_start} → {report.effective_end}",
        "Self-collected EOD data; fills modeled at bid/ask plus slippage. "
        "Backtests overstate live results.",
    ]
    if not wf.meaningful:
        caveats.append(wf.note or "walk-forward not meaningful at this history length")

    return VerdictText(
        headline=headline,
        evidence=evidence,
        breaks_where=breaks_where,
        caveats=caveats,
        source="template",
    )


# ------------------------------------------------------------ the LLM layer
def _llm_narrate(report: HonestyReport, allowed: set[float]) -> VerdictText | None:
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        return None
    import requests

    model = os.environ.get("OPENROUTER_MODEL", DEFAULT_MODEL)
    system = (
        "You are the verdict writer for an options backtesting research tool whose "
        "entire identity is adversarial honesty. You receive ONLY computed statistics "
        "as JSON. Write the verdict: lead with the most uncomfortable finding. Plain "
        "verbs, sentence case, no hype, no exclamation marks. EVERY number you mention "
        "must appear in the input JSON exactly (you may round to 2 decimals or express "
        "a 0-1 fraction as a percentage). Do not invent, extrapolate or average numbers. "
        'Respond with JSON only: {"headline": str, "evidence": [str], '
        '"breaks_where": [str], "caveats": [str]}'
    )
    body: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": report.model_dump_json()},
        ],
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
    }
    violation_note = ""
    for _attempt in range(2):
        try:
            if violation_note:
                messages: list[dict[str, str]] = body["messages"]
                body["messages"] = [
                    messages[0],
                    {"role": "user", "content": report.model_dump_json() + violation_note},
                ]
            resp = requests.post(
                OPENROUTER_URL,
                headers={"Authorization": f"Bearer {api_key}"},
                json=body,
                timeout=45,
            )
            if resp.status_code != 200:
                log.warning("verdict LLM HTTP %s", resp.status_code)
                return None
            content = resp.json()["choices"][0]["message"]["content"]
            data = json.loads(content)
            candidate = VerdictText(
                headline=str(data["headline"]),
                evidence=[str(x) for x in data.get("evidence", [])],
                breaks_where=[str(x) for x in data.get("breaks_where", [])],
                caveats=[str(x) for x in data.get("caveats", [])],
                source="llm",
            )
            parts = [candidate.headline, *candidate.evidence]
            parts += [*candidate.breaks_where, *candidate.caveats]
            joined = " ".join(parts)
            violations = validate_numbers(joined, allowed)
            if not violations:
                return candidate
            log.warning("verdict LLM ungrounded numbers %s — retrying", violations)
            violation_note = (
                f"\n\nYour previous answer contained numbers not present in the data: "
                f"{violations}. Remove or correct them. Every number must come from the JSON."
            )
        except Exception:
            log.exception("verdict LLM failed")
            return None
    return None


def write_verdict(report: HonestyReport) -> VerdictText:
    allowed = allowed_numbers(report)
    llm = _llm_narrate(report, allowed)
    if llm is not None:
        return llm
    return template_verdict(report)
