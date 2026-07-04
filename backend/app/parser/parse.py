"""English → StrategySpec-or-questions (TECH-SPEC §2–3, BUILD-PLAN M4).

The one rule that outranks convenience: missing exit rules, strike
selection, underlying, or an undefined trigger ("when it dips") produce
QUESTIONS — never fabricated parameters. The single allowed convention:
unstated tenor on a premium trade whose time-stop implies one (e.g.
"close at 21 DTE") may use the standard 45-DTE cycle, because the spec
screen shows every dial and nothing runs unconfirmed.

description_raw is overwritten server-side with the user's verbatim
text, so the LLM cannot paraphrase it. No key → None (the route 501s).
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from app.honesty.verdict import DEFAULT_MODEL, OPENROUTER_URL, _extract_json
from app.models.spec import StrategySpec

log = logging.getLogger("parser")

MAX_TEXT_CHARS = 1200


class Question(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    question: str
    options: list[str] = []


class ParseOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str  # "spec" | "questions"
    spec: dict[str, Any] | None = None
    questions: list[Question] = []


_SYSTEM = """You compile plain-English options strategies into a strict JSON spec for a
backtesting research tool, or you ask clarifying questions. You NEVER guess.
Write every clarifying question and any prose you emit in English.

Respond with JSON only, one of:
  {"result": "spec", "spec": { ...full spec... }}
  {"result": "questions",
   "questions": [{"id": "kebab-id", "question": "...", "options": ["...", ...]}]}

THE SPEC (all fields required unless noted):
{
 "spec_version": 1,
 "meta": {"name": "<= 80 chars, e.g. 'SPY .30Δ short put'",
          "description_raw": "<verbatim user text>"},
 "underlying": {"ticker": "SPY" | "QQQ" | "IWM"},
 "position": {
   "structure": "short_put" | "put_credit_spread" | "call_credit_spread" | "iron_condor"
                | "covered_call" | "long_call" | "long_put",
   "legs": [{"right": "call"|"put", "side": "long"|"short", "ratio": 1,
             "strike_selection": {"method": "delta"|"offset_pct"|"width_from_leg",
                                  "value": <number>, "reference_leg": <int, width_from_leg only>}}],
   "expiration_selection": {"target_dte": <1-90>, "min_dte": <int>, "max_dte": <int>}
 },
 "entry": {"schedule": {"frequency": "daily"|"weekly"|"monthly"|"signal_only",
                        "day_of_week": "monday"..."friday" (weekly only)},
           "conditions": [{"indicator": "rsi"|"sma"|"ema"|"price_vs_sma_pct"|"price_vs_ema_pct"
                          |"ema_cross_state"|"iv_percentile_1y"|"vix_level"
                          |"realized_vol_20d"|"drawdown_from_high_pct",
                           "period": <int, optional>, "params": {..optional..},
                           "operator": "<"|"<="|">"|">="|"above"|"below"
                                     |"crosses_above"|"crosses_below",
                           "value": <number>}],
           "max_concurrent_positions": <1-10>},
 "exit": {"profit_target_pct": <number>, "stop_loss_pct": <number>,
          "time_exit_dte": <int>, "conditions": [...]},
 "sizing": {"method": "fixed_contracts", "value": 1},
 "costs": {"commission_per_contract": 0.65, "slippage_half_spread_fraction": 0.5},
 "backtest": {"start": null, "end": null, "initial_capital": 25000, "seed": 42}
}

CONVENTIONS:
- "30 delta" → {"method": "delta", "value": 0.30}. Delta values are decimals in (0, 1).
- "5% below spot" (a put) → {"method": "offset_pct", "value": -0.05}; above spot → positive.
- "ATM" / "at the money" → {"method": "delta", "value": 0.50} — an at-the-money
  option IS the 50-delta strike; never emit method "atm".
- "$5 wide" spread long leg →
  {"method": "width_from_leg", "value": 5, "reference_leg": <short leg index>}.
- Iron condor leg order: short put, long put (width ref 0), short call, long call (width ref 2).
- min_dte/max_dte: a sensible window around target
  (about target-10 floored at 1, target+15 capped at 120).
- "stop at 2x credit" → stop_loss_pct 200.
- "exit at expiration" / "hold to expiry" → time_exit_dte 0. NEVER include
  time_exit_dte unless the user states a time-based exit — untriggered positions
  are handled by the engine's expiration model; encoding an unstated rule is
  fabrication.
- The exit object contains ONLY rules the user stated. ONE stated rule (a profit
  target, OR a stop, OR a time exit) is a COMPLETE exit — do not ask for the
  others and do not add them.
- "close at X% profit or N days" → profit_target_pct X AND time_exit_dte N;
  a bare "or N days" / "at N days" / "N days left" in an exit clause means a
  time exit at N DTE — don't ask about those forms.
  BUT exits counted FROM ENTRY ("sell it after 10 days", "hold for two
  weeks") are NOT expressible as DTE without knowing the tenor relationship —
  ask ONE question offering the DTE equivalent (e.g. "exit at 35 DTE, i.e.
  10 days after entering a 45 DTE position?"). Never silently convert.
- "9 EMA below the 20 EMA" → {"indicator": "ema_cross_state", "operator": "below",
  "value": 0, "params": {"fast": 9, "slow": 20}}.
- Entry conditions present but no cadence stated → frequency "signal_only";
  a stated evaluation cadence (e.g. "daily signal") keeps that frequency with the
  condition attached.
- "weekly" with no day named → day_of_week "monday" (the standard cycle);
  do NOT ask which day.
- The number attached to an indicator IS its period and must be included:
  "RSI(14)" → period 14; "its 50 SMA" / "50-day SMA" → period 50;
  "9 EMA below the 20 EMA" → params {"fast": 9, "slow": 20}.
- "one at a time" → max_concurrent_positions 1; otherwise 5 unless stated. Never
  ask about position count or sizing — the defaults cover them.
- Percent profit/stop numbers are percents (50 = 50%). The same for percent
  indicators: price_vs_sma_pct / price_vs_ema_pct / drawdown_from_high_pct
  values are percents ("3% below its SMA" → value 3, never 0.03). Only delta
  (0.30) and offset_pct (-0.05) take decimal values.
- sizing/costs/backtest: use the defaults shown unless the user states otherwise.

WHEN TO ASK (result "questions") — the tool's identity depends on this:
- ZERO exit rules stated → ask. No strike selection (delta/offset/ATM) stated → ask.
- Underlying missing or not one of SPY/QQQ/IWM → ask (offer the three).
- Vague triggers ("when it dips", "when it looks oversold") → ask what defines them
  (offer concrete options like "drawdown_from_high_pct >= 2" or "rsi(14) < 30").
- Unsupported structures (wheel, strangles, calendars, ratio spreads) → ask, offering
  the nearest supported structures as options.
- No entry cadence AND no entry condition → ask.
Ask AT MOST 4 questions, each answerable in a word or two, most important first.
Include 2-4 concrete "options" per question whenever sensible.

THE ONE ALLOWED CONVENTION: if tenor is unstated but an exit like "close at 21 DTE"
implies a longer tenor on a premium-selling structure, use target_dte 45 (the
standard monthly cycle). Do not invent anything else.

If the user supplied ANSWERS to earlier questions, merge them with the original
text and re-evaluate: emit the spec if now unambiguous, or ask ONLY what is
still genuinely missing."""


def _call_llm(messages: list[dict[str, str]], api_key: str) -> dict[str, Any] | None:
    import requests

    body: dict[str, Any] = {
        "model": os.environ.get("OPENROUTER_MODEL", DEFAULT_MODEL),
        "messages": messages,
        "temperature": 0.0,
        "response_format": {"type": "json_object"},
    }
    resp = requests.post(
        OPENROUTER_URL,
        headers={"Authorization": f"Bearer {api_key}"},
        json=body,
        timeout=60,
    )
    if resp.status_code != 200:
        log.warning("parser LLM HTTP %s", resp.status_code)
        return None
    content = str(resp.json()["choices"][0]["message"]["content"])
    return _extract_json(content)


def _user_message(text: str, answers: dict[str, str] | None) -> str:
    payload: dict[str, Any] = {"strategy_text": text}
    if answers:
        payload["answers_to_your_questions"] = answers
    return json.dumps(payload)


def parse_strategy(text: str, answers: dict[str, str] | None = None) -> ParseOutcome | None:
    """Parse NL → spec or questions. None when no LLM key is configured."""
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        return None

    text = text.strip()[:MAX_TEXT_CHARS]
    messages = [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": _user_message(text, answers)},
    ]

    retry_note: str | None = None
    for _attempt in range(2):
        if retry_note:
            messages = [
                messages[0],
                {"role": "user", "content": _user_message(text, answers) + retry_note},
            ]
        try:
            data = _call_llm(messages, api_key)
        except Exception:
            log.exception("parser LLM failed")
            return ParseOutcome(
                status="questions",
                questions=[
                    Question(
                        id="parser-unavailable",
                        question=(
                            "The parser hit an upstream error — try again, or "
                            "rephrase the strategy in one sentence."
                        ),
                    )
                ],
            )
        if data is None:
            retry_note = "\n\nRespond with the JSON object ONLY — no prose, no code fences."
            continue

        if data.get("result") == "questions":
            qs = [
                Question(
                    id=str(q.get("id", f"q{i}")),
                    question=str(q.get("question", "")).strip(),
                    options=[str(o) for o in q.get("options", [])][:4],
                )
                for i, q in enumerate(data.get("questions", []))
                if str(q.get("question", "")).strip()
            ][:4]
            if qs:
                return ParseOutcome(status="questions", questions=qs)
            retry_note = "\n\nYour questions list was empty. Ask real questions or emit the spec."
            continue

        raw_spec = data.get("spec")
        if not isinstance(raw_spec, dict):
            retry_note = "\n\nYour reply had neither a spec nor questions. Follow the contract."
            continue

        # guardrail: the user's words are the record, never a paraphrase
        raw_spec.setdefault("meta", {})
        raw_spec["meta"]["description_raw"] = text
        raw_spec["spec_version"] = 1
        # (ATM → .50Δ normalization lives on StrikeSelection itself — every
        # ingress that validates a spec gets it, not just this one)
        try:
            spec = StrategySpec.model_validate(raw_spec)
        except ValidationError as exc:
            log.warning("parser spec failed validation: %s", exc.errors()[:3])
            retry_note = (
                "\n\nYour spec failed schema validation: "
                + json.dumps(
                    [f"{e.get('loc')}: {e.get('msg')}" for e in exc.errors()[:5]]
                )
                + ". Fix these exact fields (or ask questions if the information is missing)."
            )
            continue
        return ParseOutcome(status="spec", spec=json.loads(spec.model_dump_json()))

    return ParseOutcome(
        status="questions",
        questions=[
            Question(
                id="could-not-compile",
                question=(
                    "I couldn't compile that into a valid spec without guessing. "
                    "Which structure is this closest to?"
                ),
                options=["short put", "put credit spread", "iron condor", "long call"],
            )
        ],
    )


# --------------------------------------------------------- UI draft mapping
def spec_to_draft(spec: dict[str, Any], text: str) -> dict[str, Any]:
    """Project a validated spec onto the UI's editable dial surface."""
    position = spec["position"]
    lead = position["legs"][0]
    sel = lead["strike_selection"]
    method = sel["method"]
    if method == "delta":
        # method "atm" can't reach here — StrikeSelection normalizes it to
        # delta 0.5 during validation, so the dial is always a real .XXΔ
        delta = int(round(abs(sel["value"]) * 100 / 5.0) * 5) or 5
        strike_label = None
    else:  # offset_pct / anything non-delta keeps its honest label
        delta = 30
        pct = sel["value"] * 100
        strike_label = f"{abs(pct):g}% {'below' if pct < 0 else 'above'} spot"

    schedule = spec["entry"]["schedule"]
    freq = schedule["frequency"]
    if freq == "weekly":
        cadence = f"weekly · {(schedule.get('day_of_week') or 'monday')[:3]}"
    elif freq == "signal_only":
        cadence = "on signal"
    else:
        cadence = freq

    exit_rules = spec["exit"]
    parts: list[str] = []
    if exit_rules.get("profit_target_pct") is not None:
        parts.append(f"{exit_rules['profit_target_pct']:g}% profit")
    if exit_rules.get("stop_loss_pct") is not None:
        parts.append(f"stop {exit_rules['stop_loss_pct']:g}%")
    if exit_rules.get("time_exit_dte") is not None:
        t = exit_rules["time_exit_dte"]
        parts.append("hold to expiry" if t == 0 else f"{t} DTE")
    if not parts and exit_rules.get("conditions"):
        parts.append("on exit signal")

    conditions = spec["entry"].get("conditions") or []
    trigger_spec = None
    if conditions:
        c = conditions[0]
        trigger_spec = {
            "indicator": c["indicator"],
            "operator": c["operator"],
            "value": c["value"],
            **({"period": c["period"]} if c.get("period") is not None else {}),
        }

    sizing = spec["sizing"]
    size = (
        f"{int(sizing['value'])} contract{'s' if sizing['value'] != 1 else ''}"
        if sizing["method"] == "fixed_contracts"
        else f"{sizing['value']:g}% risk"
    )

    return {
        "ticker": spec["underlying"]["ticker"],
        "structure": position["structure"],
        "strikeDelta": delta,
        "strikeLabel": strike_label,
        "dte": position["expiration_selection"]["target_dte"],
        "cadence": cadence,
        "size": size,
        "exit": " · ".join(parts) if parts else None,
        "fromChart": False,
        "quote": text,
        **({"triggerSpec": trigger_spec, "trigger": None} if trigger_spec else {}),
    }
