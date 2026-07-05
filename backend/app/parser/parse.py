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
from app.models.spec import V2_INDICATORS, StrategySpec

_V2_INDICATOR_NAMES = {i.value for i in V2_INDICATORS}


def _required_spec_version(raw_spec: dict[str, Any]) -> int:
    """Server-computed from the vocabulary actually used — the version is a
    contract, never trusted from the LLM."""
    exit_rules = raw_spec.get("exit") or {}
    position = raw_spec.get("position") or {}
    conds = list((raw_spec.get("entry") or {}).get("conditions") or []) + list(
        exit_rules.get("conditions") or []
    )
    schedule = (raw_spec.get("entry") or {}).get("schedule") or {}
    expiration = position.get("expiration_selection") or {}
    backtest = raw_spec.get("backtest") or {}
    uses_v2 = (
        exit_rules.get("delta_stop_abs") is not None
        or exit_rules.get("theta_harvest") is not None
        or position.get("max_vega_per_contract") is not None
        or backtest.get("clock") not in (None, "daily")
        or schedule.get("time_of_day") is not None
        or expiration.get("min_dte") == 0
        or expiration.get("target_dte") == 0
        or any(
            isinstance(c, dict)
            and (c.get("indicator") in _V2_INDICATOR_NAMES or c.get("timeframe") == "5min")
            for c in conds
        )
    )
    return 2 if uses_v2 else 1

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
   "expiration_selection": {"target_dte": <1-90>, "min_dte": <int>, "max_dte": <int>},
   "max_vega_per_contract": <number, OPTIONAL — dollars of NET position vega
                             per contract-set per vol point>
 },
 "entry": {"schedule": {"frequency": "daily"|"weekly"|"monthly"|"signal_only",
                        "day_of_week": "monday"..."friday" (weekly only),
                        "time_of_day": "HH:MM" (OPTIONAL, ET, clock "5min" only —
                                       earliest bar an entry may fill)},
           "conditions": [{"indicator": "rsi"|"sma"|"ema"|"price_vs_sma_pct"|"price_vs_ema_pct"
                          |"ema_cross_state"|"iv_percentile_1y"|"vix_level"
                          |"realized_vol_20d"|"drawdown_from_high_pct"
                          |"ivx_rank_1y"|"ivx_level_30d"|"hv_iv_spread_30d"
                          |"price_vs_vwap_pct",
                           "period": <int, optional>, "params": {..optional..},
                           "timeframe": "daily" (default) | "5min" (intraday bars;
                                        price-series indicators only),
                           "operator": "<"|"<="|">"|">="|"above"|"below"
                                     |"crosses_above"|"crosses_below",
                           "value": <number>}],
           "max_concurrent_positions": <1-10>},
 "exit": {"profit_target_pct": <number>, "stop_loss_pct": <number>,
          "time_exit_dte": <int>, "conditions": [...],
          "delta_stop_abs": <decimal in (0,1), OPTIONAL>,
          "theta_harvest": {"dte_from": <int>, "dte_to": <int>,
                            "profit_pct": <number>} (OPTIONAL)},
 "sizing": {"method": "fixed_contracts", "value": 1},
 "costs": {"commission_per_contract": 0.65, "slippage_half_spread_fraction": 0.5},
 "backtest": {"start": null, "end": null, "initial_capital": 25000, "seed": 42,
              "clock": "daily" (default) | "5min"}
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
  (0.30), delta_stop_abs (0.60) and offset_pct (-0.05) take decimal values.
- A DELTA-based stop ("close/stop out if the short strike reaches/hits
  60 delta", "close when it goes to 60 delta") → delta_stop_abs 0.60 —
  NEVER stop_loss_pct (that is a percent-of-credit stop; a delta trigger is
  not a percent, and substituting one is fabrication). Rolling is NOT
  supported: "roll at X delta / X DTE" → ask whether CLOSING there is
  acceptable (offer delta_stop_abs / time_exit_dte as options).
- Distinguish the two profit-taking forms precisely:
    "close at 50% profit or 21 DTE" (single DTE bound, an OR)
        → profit_target_pct 50 AND time_exit_dte 21 — NOT theta_harvest.
    "take profits at 50% BETWEEN 21 and 7 DTE" (a DTE WINDOW, two bounds)
        → theta_harvest {"dte_from": 21, "dte_to": 7, "profit_pct": 50},
          dte_from always the HIGHER number; emit NO separate
          profit_target_pct/time_exit_dte for it.
  theta_harvest is ONLY valid on defined-max-profit structures (short_put,
  put/call credit spreads, iron_condor, covered_call). On long_call/long_put
  it is INVALID — you must ask instead (offer a plain profit target or a
  time exit). Never emit theta_harvest on a long option.
- "IV rank above 50" / "IVR > 50" → {"indicator": "ivx_rank_1y",
  "operator": ">", "value": 50} (a percentile, 0-100). "IVX above 25" (a
  LEVEL) → ivx_level_30d with value 25 (percentage points, like vix_level).
  "IV rich vs realized by 4 points" → hv_iv_spread_30d with value 4.
- "keep position vega under $30 per contract" → max_vega_per_contract 30.
- INTRADAY (clock "5min"): "0DTE"/"same-day expiry" → target_dte 0, min_dte 0,
  max_dte 0-1, clock "5min". "1DTE" → target_dte 1 (TRADING days at this clock:
  Friday 1DTE correctly finds Monday). Any 0/1/2-DTE strategy, a time-of-day
  entry, or a 5-minute indicator ⇒ set backtest.clock "5min". Intraday quote
  coverage is SPY, 0-2 trading-DTE, near the money — that is the engine's
  problem to disclose, not yours to block.
- "enter at/after 10am" / "wait for the first 30 minutes" →
  schedule.time_of_day "10:00" (ET, on the 5-minute grid). Sub-5-minute times
  ("10:02") do not exist in the record → ask (offer the nearest 5-min bars).
- "5-minute RSI(14) under 30" → {"indicator": "rsi", "period": 14,
  "operator": "<", "value": 30, "timeframe": "5min"}. Same for 5-min SMA/EMA.
- "below VWAP" → {"indicator": "price_vs_vwap_pct", "operator": "<", "value": 0,
  "timeframe": "5min"}; "1% above VWAP" → value 1, operator ">". VWAP is
  session-anchored and intraday-only — NEVER emit it with timeframe "daily".
- A 1-minute chart/indicator request → ask: only the 5-minute record exists.
- sizing/costs/backtest: use the defaults shown unless the user states otherwise.
  spec_version: always emit 1 — the server recomputes it from the vocabulary used.

WHEN TO ASK (result "questions") — the tool's identity depends on this:
- ZERO exit rules stated → ask. No strike selection (delta/offset/ATM) stated → ask.
- Underlying missing or not one of SPY/QQQ/IWM → ask (offer the three).
- Vague triggers ("when it dips", "when it looks oversold") → ask what defines them
  (offer concrete options like "drawdown_from_high_pct >= 2" or "rsi(14) < 30").
- Unsupported structures (wheel, strangles, calendars, ratio spreads) → ask, offering
  the nearest supported structures as options.
- theta_harvest semantics requested on a long_call/long_put (no defined max
  profit) → ask; offer a profit target or time exit instead. Never guess.
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
        raw_spec["spec_version"] = _required_spec_version(raw_spec)
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
    if schedule.get("time_of_day"):
        cadence += f" · {schedule['time_of_day']} ET"

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
