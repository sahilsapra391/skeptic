"""Grounded Q&A about a finished run (guardrail #4 applied to answers).

The model receives the run's computed stats bundle (engine metrics +
HonestyReport) and the user's question — nothing else. Every numeric
token in the answer must exist in that bundle or the answer is rejected
(one retry, then an honest refusal). No key configured → no answers,
never a fake one.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from app.honesty.verdict import (
    DEFAULT_MODEL,
    OPENROUTER_URL,
    grounding_set,
    validate_numbers,
)

log = logging.getLogger("ask")

MAX_QUESTION_CHARS = 400

REFUSAL = (
    "I can't ground an answer to that in this run's computed statistics — "
    "and I don't invent numbers. Try asking about the Sharpe, drawdown, "
    "trade counts, the OOS split, walk-forward windows, Monte Carlo, "
    "sensitivity, or the trust verdict."
)

_SYSTEM = (
    "You answer questions about ONE finished options backtest for a research "
    "tool whose identity is adversarial honesty. You receive the run's computed "
    "statistics as JSON and a question. Rules: answer ONLY from the JSON; if the "
    "JSON does not contain what the question needs, say plainly that this run did "
    "not compute it — never estimate or extrapolate. NUMBERS: copy them verbatim "
    "from the JSON (you may round to 2 decimals or write a 0-1 fraction as a "
    "percent). NEVER do arithmetic — no differences, ratios, averages, or counting "
    "of your own; when in doubt, describe without the number. Never give trading "
    "advice, predictions, or buy/sell recommendations — this is historical "
    "research only. Plain verbs, no hype. Answer in at most 90 words of plain "
    "text (no markdown)."
)


def stats_numbers(stats: dict[str, Any]) -> set[float]:
    """Every number a grounded answer may contain."""
    return grounding_set(stats)


_RETAIL_NOTE = (
    " AUDIENCE OVERRIDE: an everyday retail trader with no finance background — "
    "short sentences, everyday words, zero jargon; say 'risk-adjusted score' not "
    "'Sharpe', 'reshuffling the trades' not 'Monte Carlo', 'data it never saw' not "
    "'out-of-sample'. Explain what the numbers mean for them."
)


def answer_question(
    question: str, stats: dict[str, Any], retail: bool = False
) -> str | None:
    """Grounded answer, or None when no LLM key is configured."""
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        return None
    import requests

    question = question.strip()[:MAX_QUESTION_CHARS]
    allowed = stats_numbers(stats)
    stats_json = json.dumps(stats)
    user = f"STATS:\n{stats_json}\n\nQUESTION: {question}"

    body: dict[str, Any] = {
        "model": os.environ.get("OPENROUTER_MODEL", DEFAULT_MODEL),
        "messages": [
            {"role": "system", "content": _SYSTEM + (_RETAIL_NOTE if retail else "")},
            {"role": "user", "content": user},
        ],
        "temperature": 0.2,
    }
    for _attempt in range(2):
        try:
            resp = requests.post(
                OPENROUTER_URL,
                headers={"Authorization": f"Bearer {api_key}"},
                json=body,
                timeout=45,
            )
            if resp.status_code != 200:
                log.warning("ask LLM HTTP %s", resp.status_code)
                return REFUSAL
            answer = str(resp.json()["choices"][0]["message"]["content"]).strip()
            violations = validate_numbers(answer, allowed)
            if not violations:
                return answer
            log.warning("ask LLM ungrounded numbers %s — retrying", violations)
            body["messages"] = [
                body["messages"][0],
                {
                    "role": "user",
                    "content": user
                    + f"\n\nYour previous answer contained numbers not present in the "
                    f"stats: {violations}. Remove or correct them — every number must "
                    f"come from the JSON.",
                },
            ]
        except Exception:
            log.exception("ask LLM failed")
            return REFUSAL
    return REFUSAL
