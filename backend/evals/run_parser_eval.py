"""M4 parser eval harness (BUILD-PLAN): runs the golden case set against
the LIVE parser and prints a pass/fail report.

Run:  cd backend && PYTHONPATH=. uv run python evals/run_parser_eval.py
Needs OPENROUTER_API_KEY (loaded via app.config.load_local_env).

Acceptance (same spirit as the original 7/8 + 4/4, generalized when D1c
grew the set): at most ONE clear-case miss, and EVERY ambiguous case must
produce questions with zero fabricated parameters. Any parser-prompt
change re-runs this and needs an owner ACCEPT before merge.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from app.config import load_local_env

load_local_env()

from app.parser.parse import parse_strategy  # noqa: E402

CASES = json.loads((Path(__file__).parent / "parser_cases.json").read_text())["cases"]


def _approx(a: float, b: float, tol: float = 1e-6) -> bool:
    return abs(float(a) - float(b)) <= tol


def _match_leg(expected: dict[str, Any], actual: dict[str, Any]) -> str | None:
    sel = actual.get("strike_selection", {})
    if actual.get("right") != expected["right"]:
        return f"right {actual.get('right')} != {expected['right']}"
    if actual.get("side") != expected["side"]:
        return f"side {actual.get('side')} != {expected['side']}"
    if sel.get("method") != expected["method"]:
        return f"method {sel.get('method')} != {expected['method']}"
    if "value" in expected and not _approx(sel.get("value", 0), expected["value"]):
        return f"value {sel.get('value')} != {expected['value']}"
    if "reference_leg" in expected and sel.get("reference_leg") != expected["reference_leg"]:
        return f"reference_leg {sel.get('reference_leg')} != {expected['reference_leg']}"
    return None


def _match_condition(expected: dict[str, Any], actual: list[dict[str, Any]]) -> str | None:
    for cond in actual:
        if cond.get("indicator") != expected["indicator"]:
            continue
        if "period" in expected and cond.get("period") != expected["period"]:
            return f"{expected['indicator']} period {cond.get('period')} != {expected['period']}"
        if "timeframe" in expected:
            got_tf = cond.get("timeframe") or "daily"
            if got_tf != expected["timeframe"]:
                return f"{expected['indicator']} timeframe {got_tf} != {expected['timeframe']}"
        if cond.get("operator") not in expected["operator"]:
            return (
                f"{expected['indicator']} operator {cond.get('operator')} "
                f"not in {expected['operator']}"
            )
        if "value" in expected and not _approx(cond.get("value", 0), expected["value"], 1e-3):
            return f"{expected['indicator']} value {cond.get('value')} != {expected['value']}"
        if "params" in expected:
            params = cond.get("params") or {}
            for k, v in expected["params"].items():
                if params.get(k) != v:
                    return f"{expected['indicator']} params.{k} {params.get(k)} != {v}"
        return None
    return f"no condition with indicator {expected['indicator']}"


def _match_scale_in(expected: dict[str, Any], actual: dict[str, Any] | None) -> str | None:
    if not isinstance(actual, dict):
        return "no scale_in emitted"
    errs: list[str] = []
    exp_rungs = expected["rungs"]
    act_rungs = actual.get("rungs") or []
    if len(act_rungs) != len(exp_rungs):
        return f"{len(act_rungs)} rungs != {len(exp_rungs)}"
    for i, (er, ar) in enumerate(zip(exp_rungs, act_rungs, strict=True)):
        if ar.get("indicator") != er["indicator"]:
            errs.append(f"rung[{i}] indicator {ar.get('indicator')} != {er['indicator']}")
        if "period" in er and ar.get("period") != er["period"]:
            errs.append(f"rung[{i}] period {ar.get('period')} != {er['period']}")
        if "timeframe" in er and (ar.get("timeframe") or "daily") != er["timeframe"]:
            errs.append(f"rung[{i}] timeframe {ar.get('timeframe')} != {er['timeframe']}")
        if ar.get("operator") not in er["operator"]:
            errs.append(f"rung[{i}] operator {ar.get('operator')} not in {er['operator']}")
        if not _approx(ar.get("value", 0), er["value"], 1e-3):
            errs.append(f"rung[{i}] value {ar.get('value')} != {er['value']}")
        if ar.get("add_contracts") != er["add_contracts"]:
            errs.append(
                f"rung[{i}] add_contracts {ar.get('add_contracts')} != {er['add_contracts']}"
            )
    if actual.get("max_total_contracts") != expected["max_total_contracts"]:
        errs.append(
            f"max_total_contracts {actual.get('max_total_contracts')} "
            f"!= {expected['max_total_contracts']}"
        )
    return "; ".join(errs) or None


def grade_spec(expect: dict[str, Any], spec: dict[str, Any], text: str) -> list[str]:
    errs: list[str] = []
    if spec["meta"]["description_raw"] != text:
        errs.append("description_raw is not verbatim")
    if spec["underlying"]["ticker"] != expect["ticker"]:
        errs.append(f"ticker {spec['underlying']['ticker']} != {expect['ticker']}")
    pos = spec["position"]
    if pos["structure"] != expect["structure"]:
        errs.append(f"structure {pos['structure']} != {expect['structure']}")
    if len(pos["legs"]) != len(expect["legs"]):
        errs.append(f"{len(pos['legs'])} legs != {len(expect['legs'])}")
    else:
        for i, (exp_leg, leg) in enumerate(zip(expect["legs"], pos["legs"], strict=True)):
            err = _match_leg(exp_leg, leg)
            if err:
                errs.append(f"leg[{i}]: {err}")
    tgt = pos["expiration_selection"]["target_dte"]
    if tgt != expect["target_dte"]:
        errs.append(f"target_dte {tgt} != {expect['target_dte']}")

    sched = spec["entry"]["schedule"]
    if sched["frequency"] not in expect["schedule"]["frequency"]:
        errs.append(f"frequency {sched['frequency']} not in {expect['schedule']['frequency']}")
    want_day = expect["schedule"].get("day_of_week")
    if want_day is not None and sched.get("day_of_week") != want_day:
        errs.append(f"day_of_week {sched.get('day_of_week')} != {want_day}")

    conds = spec["entry"].get("conditions") or []
    if not expect.get("conditions") and conds:
        errs.append(f"fabricated conditions: {conds}")
    for exp_cond in expect.get("conditions", []):
        err = _match_condition(exp_cond, conds)
        if err:
            errs.append(err)

    if "max_concurrent_positions" in expect:
        got = spec["entry"]["max_concurrent_positions"]
        if got != expect["max_concurrent_positions"]:
            errs.append(f"max_concurrent {got} != {expect['max_concurrent_positions']}")

    exit_rules = spec["exit"]
    for key in ("profit_target_pct", "stop_loss_pct", "time_exit_dte", "delta_stop_abs"):
        exp_v = expect["exit"].get(key)
        got_v = exit_rules.get(key)
        if exp_v is None:
            if got_v is not None:
                errs.append(f"fabricated exit.{key}={got_v}")
        elif got_v is None or not _approx(got_v, exp_v, 1e-3):
            errs.append(f"exit.{key} {got_v} != {exp_v}")

    exp_th = expect["exit"].get("theta_harvest")
    got_th = exit_rules.get("theta_harvest")
    if exp_th is None:
        if got_th is not None:
            errs.append(f"fabricated exit.theta_harvest={got_th}")
    elif not isinstance(got_th, dict) or any(
        not _approx(got_th.get(k, -1), exp_th[k], 1e-3)
        for k in ("dte_from", "dte_to", "profit_pct")
    ):
        errs.append(f"exit.theta_harvest {got_th} != {exp_th}")

    exp_si = expect.get("scale_in")
    got_si = spec["entry"].get("scale_in")
    if exp_si is None:
        if got_si is not None:
            errs.append(f"fabricated scale_in: {json.dumps(got_si)[:120]}")
    else:
        err = _match_scale_in(exp_si, got_si)
        if err:
            errs.append(f"scale_in: {err}")

    exp_cat = expect["exit"].get("close_at_time")
    got_cat = exit_rules.get("close_at_time")
    if exp_cat is None:
        if got_cat is not None:
            errs.append(f"fabricated exit.close_at_time={got_cat}")
    elif got_cat != exp_cat:
        errs.append(f"exit.close_at_time {got_cat} != {exp_cat}")

    if "spec_version" in expect and spec.get("spec_version") != expect["spec_version"]:
        errs.append(f"spec_version {spec.get('spec_version')} != {expect['spec_version']}")
    if "clock" in expect:
        got_clock = (spec.get("backtest") or {}).get("clock") or "daily"
        if got_clock != expect["clock"]:
            errs.append(f"backtest.clock {got_clock} != {expect['clock']}")
    if "time_of_day" in expect:
        got_tod = sched.get("time_of_day")
        if got_tod != expect["time_of_day"]:
            errs.append(f"schedule.time_of_day {got_tod} != {expect['time_of_day']}")
    return errs


def main() -> int:
    clear_pass = ambiguous_pass = 0
    lines: list[str] = []
    for case in CASES:
        outcome = parse_strategy(case["text"])
        if outcome is None:
            print("OPENROUTER_API_KEY missing — the eval needs the live parser.")
            return 2

        n, kind = case["case"], case["kind"]
        if kind == "questions":
            ok = outcome.status == "questions" and outcome.spec is None
            ambiguous_pass += ok
            detail = (
                " · ".join(q.question for q in outcome.questions)[:150]
                if ok
                else f"FABRICATED A SPEC: {json.dumps(outcome.spec)[:150]}"
            )
            lines.append(f"case {n:>2} [{'PASS' if ok else 'FAIL'}] (questions) {detail}")
        else:
            if outcome.status != "spec" or outcome.spec is None:
                qs = " · ".join(q.question for q in outcome.questions)[:150]
                lines.append(f"case {n:>2} [FAIL] (spec) asked questions instead: {qs}")
                continue
            errs = grade_spec(case["expect"], outcome.spec, case["text"])
            clear_pass += not errs
            detail = "matches ground truth" if not errs else "; ".join(errs)[:220]
            lines.append(f"case {n:>2} [{'PASS' if not errs else 'FAIL'}] (spec) {detail}")

    clear_total = sum(1 for c in CASES if c["kind"] == "spec")
    ambiguous_total = sum(1 for c in CASES if c["kind"] == "questions")
    print("\n".join(lines))
    print(
        f"\nclear: {clear_pass}/{clear_total} (accept >= {clear_total - 1}) · "
        f"ambiguous: {ambiguous_pass}/{ambiguous_total} (accept {ambiguous_total})"
    )
    ok = clear_pass >= clear_total - 1 and ambiguous_pass == ambiguous_total
    print("RESULT:", "ACCEPTED" if ok else "REJECTED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
