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
import unicodedata
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.honesty.report import HonestyReport

log = logging.getLogger("verdict")

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
# Two models on purpose (owner decision 2026-07-06). The NARRATION calls
# (verdict, ask, health) use the cheaper/faster flash model — their numeric
# validator + English guard + template fallback keep them honest. The PARSER
# stays on the pro model: flash fabricated an exit on the no-exit ladder case
# in the parser eval (guardrail #3), so it does not clear the parser gate.
# Each is overridable by its own env var; a prod value still wins.
DEFAULT_MODEL = "deepseek/deepseek-v4-flash"  # verdict · ask · health (OPENROUTER_MODEL)
PARSER_MODEL = "deepseek/deepseek-v4-pro"  # parser only (OPENROUTER_PARSER_MODEL)


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
    # the walk-forward narration counts PROFITABLE folds — a derived count
    # the dump doesn't carry as a number. Latent until histories grew long
    # enough (10 years of 1DTE ≈ 58 folds) to push it past the 0–30
    # counting-number range; caught by the D2 acceptance run's validator.
    out.add(float(sum(1 for f in report.walk_forward.folds if f.ret > 0)))
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


# ------------------------------------------------ language grounding (English)
# The numeric validator proves every number is real, but it is blind to
# LANGUAGE. A DeepSeek-class model handed a numbers-only JSON payload with no
# English prose to anchor on will happily write a fully-grounded verdict in
# Chinese (observed 2026-07). For a tool whose product is an honest, READABLE
# verdict, "is this even English" is as load-bearing as "is this number real".
# We reject on SCRIPT, not vocabulary: count the alphabetic characters that are
# not Latin. Punctuation, digits, and the template's — · → ’ glyphs are not
# letters, so they never count — only a real language switch trips the guard.
_NON_LATIN_TOLERANCE = 0.10


def non_latin_ratio(text: str) -> float:
    """Fraction of the alphabetic characters in `text` that are not Latin
    script. 0.0 for English; ~1.0 for Chinese/Cyrillic/etc."""
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 0.0
    non_latin = 0
    for c in letters:
        try:
            if not unicodedata.name(c).startswith("LATIN"):
                non_latin += 1
        except ValueError:  # unnamed / private-use codepoint → not Latin
            non_latin += 1
    return non_latin / len(letters)


def is_english(text: str) -> bool:
    """Latin-script heuristic: True unless a meaningful fraction of the letters
    are non-Latin. Guards the verdict against a model that silently switches
    languages on numbers-only input; the deterministic template is always
    English by construction, so only the LLM narration is checked."""
    return non_latin_ratio(text) <= _NON_LATIN_TOLERANCE


# ------------------------------------------------------------- the template
def _pct(v: float | None, digits: int = 0) -> str:
    return "—" if v is None else f"{v * 100:.{digits}f}%"


def _ladder_caveat(report: HonestyReport, retail: bool = False) -> str | None:
    """One grounded sentence on depth attribution — required whenever a ladder
    ran (brief D5b). Every number comes from report.ladder_depth, so it is
    grounded by construction; it rides in the caveats so it surfaces even when
    the verdict is withheld (the D5a interlock withholds every ladder for now)."""
    ld = report.ladder_depth
    if ld is None or not ld.rungs:
        return None
    deep = ld.rungs[-1]
    mpl = abs(deep.marginal_pl)
    if retail:
        verb = "lost" if deep.net_negative else "made"
        tail = (
            "those deep add-ins are what dragged it down"
            if deep.net_negative
            else "the deep add-ins pulled their weight"
        )
        return (
            f"Depth: adding more at the deepest level ({deep.threshold:g}) {verb} "
            f"${mpl:,.0f} of the ${abs(ld.realized_total):,.0f} across {ld.baskets} "
            f"baskets — {tail}."
        )
    sign = "−" if deep.net_negative else "+"
    tail = (
        "the edge does NOT come from the deep adds"
        if deep.net_negative
        else "the deep adds are net positive here"
    )
    return (
        f"Ladder depth: fills at the deepest rung ({deep.threshold:g}) net {sign}"
        f"${mpl:,.0f} of the ${abs(ld.realized_total):,.0f} realized across "
        f"{ld.baskets} baskets — {tail}."
    )


def template_verdict(report: HonestyReport) -> VerdictText:
    """Uncomfortable part first, always."""
    oos, wf, mc = report.oos, report.walk_forward, report.monte_carlo
    sens, trust, sample = report.sensitivity, report.trust, report.regime_sample

    if trust.label == "insufficient_evidence":
        cov = report.coverage
        si = report.scale_in
        if si is not None and si.caps_trust and si.deep_rung_sign_flip:
            headline = (
                "Verdict withheld — the edge depends on the deepest, riskiest adds: "
                f"${si.realized_total:,.0f} flips to −${abs(si.total_without_deepest):,.0f} "
                f"without the deepest rung ({si.deepest_threshold:g})."
            )
        elif si is not None and si.caps_trust and si.p_ruin is not None:
            headline = (
                f"Verdict withheld — ruinous tail: {si.p_ruin * 100:.0f}% of resampled "
                f"orderings draw the account down more than {si.ruin_threshold * 100:.0f}%."
            )
        elif cov.materially_short:
            headline = (
                f"Verdict withheld. {cov.chain_sessions} of {cov.requested_sessions} "
                "requested sessions had usable option chains — the window is mostly untested."
            )
        else:
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
    cov = report.coverage
    if cov.coverage_ratio < 1.0:
        caveats.insert(
            1,
            f"Chain coverage: {cov.chain_sessions} of {cov.requested_sessions} requested "
            f"sessions carried usable option chains ({_pct(cov.coverage_ratio)}). "
            f"Requested {cov.requested_start} → {cov.requested_end}; "
            f"tested {cov.effective_start} → {cov.effective_end}.",
        )
    if report.liquidity is not None and report.liquidity.material and report.liquidity.note:
        caveats.append(f"Liquidity: {report.liquidity.note}.")
    fs = report.fill_sources
    modeled = fs.get("alpaca_modeled", 0)
    total_fills = sum(fs.values())
    if modeled > 0 and total_fills > 0:
        caveats.append(
            f"{modeled} of {total_fills} option fills "
            f"({round(modeled / total_fills * 100)}%) are MODELED quotes — trade "
            "prints plus a modeled spread, filled at full adverse slippage. No "
            "real NBBO existed for them; treat those fills as estimates."
        )
    if report.sensitivity.window_note:
        caveats.append(report.sensitivity.window_note + ".")
    ladder = _ladder_caveat(report)
    if ladder:
        caveats.append(ladder)
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
        cov = report.coverage
        si = report.scale_in
        if si is not None and si.caps_trust and si.deep_rung_sign_flip:
            headline = (
                "No verdict — this only makes money because of the deepest, riskiest "
                f"add-ins: ${si.realized_total:,.0f} turns into "
                f"−${abs(si.total_without_deepest):,.0f} without them. That's a bet on "
                "the most dangerous part working."
            )
        elif si is not None and si.caps_trust and si.p_ruin is not None:
            headline = (
                f"No verdict — too ruinous: in {si.p_ruin * 100:.0f}% of reshuffles the "
                f"account fell more than {si.ruin_threshold * 100:.0f}%. Adding into losers "
                "blows up too often."
            )
        elif cov.materially_short:
            headline = (
                f"No verdict yet — only {cov.chain_sessions} of {cov.requested_sessions} "
                "days in your date range actually had option prices to trade on."
            )
        else:
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
    cov = report.coverage
    if cov.coverage_ratio < 1.0:
        caveats.insert(
            1,
            f"Only {cov.chain_sessions} of {cov.requested_sessions} days in your date range "
            f"had option prices ({_pct(cov.coverage_ratio)}) — the rest couldn't be tested.",
        )
    fs = report.fill_sources
    modeled = fs.get("alpaca_modeled", 0)
    total_fills = sum(fs.values())
    if modeled > 0 and total_fills > 0:
        caveats.append(
            f"{modeled} of {total_fills} fills ({round(modeled / total_fills * 100)}%) "
            "used ESTIMATED prices (no real quotes existed) at worst-case slippage — "
            "take those with extra salt."
        )
    liq = report.liquidity
    if liq is not None and liq.material:
        if liq.stressed_share is not None and liq.stressed_share > 0:
            caveats.append(
                f"{_pct(liq.stressed_share)} of fills paid the worst quoted price — "
                "these options trade thin."
            )
        elif liq.skipped_illiquid >= 1 and liq.note and "refused" in liq.note:
            caveats.append(
                f"{liq.skipped_illiquid} trades were refused because the options looked "
                "too thin to fill honestly."
            )
        elif liq.penalized_share is not None:
            caveats.append(
                f"{_pct(liq.penalized_share)} of fills paid extra slippage for thin "
                "markets."
            )
    ladder = _ladder_caveat(report, retail=True)
    if ladder:
        caveats.append(ladder)
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
        "If the JSON has a ladder_depth object, you MUST state which rung depth "
        "carried the P&L and whether the deepest adds were net negative, using its "
        "numbers — a scale-in ladder's depth attribution is the point. "
        "Write every field in English, regardless of the field names in the JSON. "
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
            if not is_english(joined):
                log.warning("verdict LLM returned non-English — retrying")
                violation_note = (
                    "\n\nYour previous answer was not written in English. Write "
                    "every field — headline, evidence, breaks_where, caveats — in "
                    "English."
                )
                continue
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
