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
DEFAULT_MODEL = "deepseek/deepseek-v4-pro"


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
        # narration may round: 2 decimals, whole numbers, or 0-1 → %
        for candidate in (
            v, v * 100, abs(v), abs(v) * 100,
            round(v, 2), round(v), round(v * 100, 1), round(v * 100),
        ):
            out.add(round(candidate, 4))
        return
    if isinstance(obj, dict):
        for item in obj.values():
            _harvest_numbers(item, out)
    elif isinstance(obj, list):
        # the length of a list is a legitimate count ("38 windows")
        out.add(float(len(obj)))
        for item in obj:
            _harvest_numbers(item, out)


_YEAR_RE = re.compile(r"(?<![\d.])(?:19|20)\d{2}(?![\d.])")


def grounding_set(payload: dict[str, Any]) -> set[float]:
    """Every number narration may contain, harvested from computed stats."""
    out: set[float] = {0.0}
    _harvest_numbers(payload, out)
    # small counting numbers and fixed phrasing constants (percentile
    # labels, trading-day count, the resample count) — not statistics
    out.update(float(x) for x in range(0, 31))
    out.update({5.0, 50.0, 95.0, 100.0, 252.0, 1000.0})
    # calendar years inside the payload's dates are identifiers ("since
    # 2020"), not statistics
    out.update(float(m.group(0)) for m in _YEAR_RE.finditer(json.dumps(payload, default=str)))
    return out


def allowed_numbers(report: HonestyReport) -> set[float]:
    out = grounding_set(report.model_dump())
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


def retail_template_verdict(report: HonestyReport) -> VerdictText:
    """The same honest verdict for an everyday retail trader: short
    sentences, everyday words, zero jargon. Every number still comes
    straight from the report."""
    oos, wf, mc = report.oos, report.walk_forward, report.monte_carlo
    sens, trust, sample = report.sensitivity, report.trust, report.regime_sample

    if trust.label == "insufficient_evidence":
        plural = "s" if sample.trades != 1 else ""
        headline = (
            f"No verdict yet — only {sample.trades} finished trade{plural}. "
            "That's too few to judge fairly."
        )
    elif not trust.survived["oos"]:
        headline = "Looked good in training, faded on data it had never seen. Be careful."
    elif trust.survived_count >= 4 and (trust.level or 0) >= 4:
        headline = (
            f"Passed {trust.survived_count} of 5 stress tests — "
            "as solid as our data can show."
        )
    elif "mined" in " ".join(trust.reasons):
        headline = "The good numbers look like luck from too many tries, not a real edge."
    elif sens.verdict == "cliff":
        headline = "Tiny changes to the settings wreck it — that's a bad sign."
    else:
        headline = (
            f"Passed {trust.survived_count} of 5 stress tests. "
            "Interesting, but don't bet the house."
        )

    evidence: list[str] = []
    if oos.is_sharpe is not None and oos.oos_sharpe is not None:
        kept = (
            f" — it kept {_pct(oos.degradation)} of its training score"
            if oos.degradation is not None
            else ""
        )
        evidence.append(
            f"On data it never saw, its risk-adjusted score was {oos.oos_sharpe:.2f} "
            f"(training: {oos.is_sharpe:.2f}){kept}"
        )
    if wf.meaningful and wf.consistency is not None:
        positive = sum(1 for f in wf.folds if f.ret > 0)
        evidence.append(f"It made money in {positive} of {len(wf.folds)} time periods")
    if mc.p_loss is not None:
        evidence.append(
            f"We reshuffled its trades {mc.resamples} times — {_pct(mc.p_loss)} of the "
            f"reshuffles ended with less money than they started"
        )

    breaks_where: list[str] = []
    if not trust.survived["oos"]:
        breaks_where.append("The edge shrank badly on data it had never seen")
    if oos.sign_flip:
        breaks_where.append("It made money in training but LOST money on unseen data")
    if wf.meaningful and not trust.survived["walk_forward"]:
        breaks_where.append("It only won in some periods — the rest were flat or losing")
    if mc.p_loss is not None and not trust.survived["monte_carlo"]:
        breaks_where.append(
            f"{_pct(mc.p_loss)} of the reshuffled versions lost money — "
            "the original order got lucky"
        )
    if sens.verdict == "cliff":
        breaks_where.append("Small changes to the settings make the results fall apart")
    if report.dsr.dsr is not None and report.dsr.dsr < 0.5 and report.dsr.trials > 1:
        breaks_where.append(
            f"This is try number {report.dsr.trials} at this kind of strategy — "
            "at some point the good numbers are just luck"
        )
    if not breaks_where:
        breaks_where.append(
            "No deal-breaker found — but we can only test the history we have"
        )

    plural = "s" if sample.regimes_present != 1 else ""
    caveats = [
        f"{sample.trades} finished trades · {sample.regimes_present} market mood{plural} "
        f"covered · {report.effective_start} → {report.effective_end}",
        "Backtests always look better than real trading. This is research, not advice.",
    ]
    if not wf.meaningful:
        caveats.append("Not enough history to test period-by-period yet.")

    return VerdictText(
        headline=headline,
        evidence=evidence,
        breaks_where=breaks_where,
        caveats=caveats,
        source="template",
    )


# ------------------------------------------------------------ the LLM layer
def _extract_json(content: str) -> dict[str, Any] | None:
    """Models routinely wrap JSON in code fences or prose despite
    response_format — take the outermost {...} slice and parse that."""
    start, end = content.find("{"), content.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        data = json.loads(content[start : end + 1])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _llm_narrate(
    report: HonestyReport, allowed: set[float], retail: bool = False
) -> VerdictText | None:
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        return None
    import requests

    model = os.environ.get("OPENROUTER_MODEL", DEFAULT_MODEL)
    audience = (
        (
            "AUDIENCE: an everyday retail trader with no finance background. Short "
            "sentences, everyday words, zero jargon — never say 'Sharpe' (say "
            "'risk-adjusted score'), never 'percentile' (say 'worst/typical cases'), "
            "never 'in-sample/out-of-sample' (say 'training data' and 'data it never "
            "saw'), never 'Monte Carlo' (say 'reshuffling the trades'). Explain what "
            "each finding MEANS for them. "
        )
        if retail
        else (
            "AUDIENCE: a quantitative practitioner — precise statistical language "
            "is expected. "
        )
    )
    system = (
        "You are the verdict writer for an options backtesting research tool whose "
        "entire identity is adversarial honesty. You receive ONLY computed statistics "
        "as JSON. Write the verdict: lead with the most uncomfortable finding. Plain "
        "verbs, sentence case, no hype, no exclamation marks. " + audience +
        "NUMBERS: copy them "
        "verbatim from the JSON (you may round to 2 decimals or write a 0-1 fraction "
        "as a percent). NEVER do arithmetic — no differences, ratios, averages, "
        "annualizing, or counting of your own. A number not literally in the JSON "
        "must not appear in your text. When in doubt, describe without the number. "
        'Respond with JSON only: {"headline": str, "evidence": [str], '
        '"breaks_where": [str], "caveats": [str]}'
    )
    body: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": report.model_dump_json()},
        ],
        "temperature": 0.0,
        "response_format": {"type": "json_object"},
    }
    violation_note = ""
    for _attempt in range(3):
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
            data = _extract_json(str(content))
            if data is None:
                log.warning("verdict LLM returned non-JSON — retrying")
                violation_note = (
                    "\n\nRespond with the JSON object ONLY — no prose, no code fences."
                )
                continue
            if not str(data.get("headline", "")).strip():
                log.warning("verdict LLM JSON missing headline — retrying")
                violation_note = "\n\nThe JSON must include a non-empty \"headline\"."
                continue
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


def write_verdicts(report: HonestyReport) -> tuple[VerdictText, VerdictText]:
    """(institutional, retail) — same numbers, two registers, narrated in
    PARALLEL (halves the verdict stage's wall time). Each falls back to
    its deterministic template when the LLM can't stay grounded."""
    from concurrent.futures import ThreadPoolExecutor

    allowed = allowed_numbers(report)
    with ThreadPoolExecutor(max_workers=2) as pool:
        inst_f = pool.submit(_llm_narrate, report, allowed, False)
        retail_f = pool.submit(_llm_narrate, report, allowed, True)
        institutional = inst_f.result() or template_verdict(report)
        retail = retail_f.result() or retail_template_verdict(report)
    return institutional, retail
