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
import time
from typing import Any

from app.honesty.verdict import (
    DEFAULT_MODEL,
    OPENROUTER_URL,
    grounding_set,
    validate_numbers,
)

log = logging.getLogger("ask")

MAX_QUESTION_CHARS = 400

# /runs/{id}/ask runs behind the SAME 100s frontend-proxy leash as /parse
# (frontend/app/api/[...path]/route.ts) — both grounded attempts must answer
# inside it, or the proxy 504s a healthy engine mid-retry. Same treatment as
# the parser's PARSE_BUDGET_SECONDS: one wall-clock budget across attempts,
# per-phase (connect, read) bounds because requests applies its timeout per
# phase, and a retry too thin to finish is refused instead of launched.
ASK_BUDGET_SECONDS = 90.0
_ATTEMPT_READ_SECONDS = 45.0
_CONNECT_TIMEOUT_SECONDS = 10.0
_MIN_RETRY_SECONDS = 10.0

_monotonic = time.monotonic  # patchable in tests — the budget clock

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
    deadline = _monotonic() + ASK_BUDGET_SECONDS
    for attempt in range(2):
        remaining = deadline - _monotonic()
        if attempt and remaining - _CONNECT_TIMEOUT_SECONDS < _MIN_RETRY_SECONDS:
            # the grounding retry can't finish inside the proxy's leash —
            # refuse honestly rather than let the proxy 504 a healthy engine
            log.warning("ask retry refused — budget exhausted")
            return REFUSAL
        try:
            resp = requests.post(
                OPENROUTER_URL,
                headers={"Authorization": f"Bearer {api_key}"},
                json=body,
                timeout=(
                    _CONNECT_TIMEOUT_SECONDS,
                    min(_ATTEMPT_READ_SECONDS,
                        max(1.0, remaining - _CONNECT_TIMEOUT_SECONDS)),
                ),
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
