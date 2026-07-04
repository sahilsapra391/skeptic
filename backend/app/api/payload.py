"""RunResult + HonestyReport + VerdictText → the frontend's run payload.

Every number rendered here is computed by the engine or the gauntlet
(guardrail #4). Insufficient evidence renders the design's refusal state
with the REAL unlock condition; everything else renders the full verdict
with the trust band placed by the deterministic trust level.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from app.engine.types import RunResult, TradeEvent
from app.honesty.report import HonestyReport
from app.honesty.stages import COVERAGE_MIN_RATIO, MIN_TRADES
from app.honesty.verdict import VerdictText
from app.models.spec import StrategySpec

_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

# trust band geometry: level 1..5 → marker at 10/30/50/70/90%, band ±15%
_MARKER = {1: 10, 2: 30, 3: 50, 4: 70, 5: 90}


def _short(d: date) -> str:
    return f"{_MONTHS[d.month - 1]} {d.day} ’{str(d.year)[2:]}"


def _short_iso(iso: str) -> str:
    return _short(date.fromisoformat(iso))


def _pct(v: float | None, digits: int = 1) -> str:
    return "—" if v is None else f"{v * 100:.{digits}f}%"


def _num(v: float | None, digits: int = 2) -> str:
    return "—" if v is None else f"{v:.{digits}f}"


def _dollars(v: float | None) -> str:
    return "" if v is None else f"${v:,.0f}"


def _downsample(dates: list[date], values: list[float], cap: int = 400) -> list[dict[str, Any]]:
    n = len(values)
    idxs: list[int]
    if n <= cap:
        idxs = list(range(n))
    else:
        step = n / cap
        idxs = sorted({min(n - 1, int(i * step)) for i in range(cap)} | {n - 1})
    return [{"t": dates[i].isoformat(), "v": round(values[i], 2)} for i in idxs]


def _drawdown_series(dates: list[date], equity: list[float]) -> list[dict[str, Any]]:
    peak = equity[0] if equity else 0.0
    dd: list[float] = []
    for v in equity:
        peak = max(peak, v)
        dd.append((1.0 - v / peak) * 100.0 if peak > 0 else 0.0)
    return _downsample(dates, dd)


def _trade_rows(trades: list[TradeEvent]) -> list[dict[str, Any]]:
    """Newest-first rows, fills before skips — the COMPLETE log, uncapped
    (owner directive: every fill and every skip must be inspectable).
    Rows are ~100 bytes each and only travel when a run is opened, so even
    a daily-cadence strategy's log stays well under a megabyte."""
    filled_events = [t for t in trades if t.action != "SKIP"]
    skip_events = [t for t in trades if t.action == "SKIP"]
    rows: list[dict[str, Any]] = []
    for t in reversed(filled_events + skip_events):
        pl = t.pl
        rows.append(
            {
                "d": _short(t.day),
                "a": t.action,
                "det": t.detail,
                "pl": "—" if pl is None else f"{'+' if pl >= 0 else '−'}${abs(pl):,.2f}",
                "plSign": (
                    "none" if pl is None else "pos" if pl > 0 else "neg" if pl < 0 else "none"
                ),
                "n": t.reason or "",
                "skip": t.action == "SKIP",
            }
        )
    return rows


def _fan_points(fan: list[float], lo: float, hi: float) -> str:
    if not fan or hi <= lo:
        return ""
    n = len(fan)
    pts = []
    for i, v in enumerate(fan):
        x = (i / max(n - 1, 1)) * 400
        y = 6 + (1 - (v - lo) / (hi - lo)) * 88
        pts.append(f"{x:.1f},{y:.1f}")
    return " ".join(pts)


def _mc_fan(report: HonestyReport) -> dict[str, str]:
    mc = report.monte_carlo
    all_vals = mc.fan_p5 + mc.fan_p50 + mc.fan_p95
    if not all_vals:
        return {"p95": "", "p50": "", "p05": ""}
    lo, hi = min(all_vals), max(all_vals)
    return {
        "p95": _fan_points(mc.fan_p95, lo, hi),
        "p50": _fan_points(mc.fan_p50, lo, hi),
        "p05": _fan_points(mc.fan_p5, lo, hi),
    }


def _param_label(name: str, v: float) -> str:
    if name == "delta":
        return f".{int(round(v * 100)):02d}Δ"
    if name == "dte":
        return f"{int(v)}d"
    return f"{v:g}%"


def _sensitivity_detail(report: HonestyReport) -> list[dict[str, Any]]:
    """Cell-level sweep data so the grid can explain itself on hover."""
    out: list[dict[str, Any]] = []
    for sweep in report.sensitivity.params:
        valid = [s for s in sweep.sharpes if s is not None]
        if not valid:
            continue
        top, bottom = max(valid), min(valid)
        span = top - bottom or 1.0
        cells = [
            {
                "label": _param_label(sweep.name, v),
                "sharpe": "—" if s is None else f"{s:.2f}",
                "o": 0.06 if s is None else round(0.10 + 0.82 * (s - bottom) / span, 2),
            }
            for v, s in zip(sweep.values, sweep.sharpes, strict=True)
        ]
        out.append(
            {
                "name": sweep.name.replace("_", " "),
                "cls": sweep.classification or "",
                "base": sweep.base_index,
                "cells": cells,
            }
        )
    return out


def _recommendations(report: HonestyReport, retail: bool = False) -> list[str]:
    """What would improve the strategy — computed ONLY from this run's own
    gauntlet numbers (the ±20% sweeps re-ran the real engine), never from
    opinion. Guardrail #4 applies: every number below exists in the report.
    `retail` swaps the register, never the numbers."""
    sample = report.regime_sample
    if report.trust.label == "insufficient_evidence":
        plural = "s" if sample.trades != 1 else ""
        return [
            (
                f"With only {sample.trades} finished trade{plural} there's nothing "
                "honest to suggest yet — test a longer date range, trade more often, "
                "or wait for more data."
            )
            if retail
            else (
                f"Nothing can honestly be recommended from {sample.trades} closed "
                f"trade{plural} — widen the date window, trade more frequently, "
                "or wait for more coverage before tuning anything."
            ),
        ]

    recs: list[str] = []
    for sweep in report.sensitivity.params:
        base_s = sweep.sharpes[sweep.base_index]
        pairs = [
            (v, s) for v, s in zip(sweep.values, sweep.sharpes, strict=True) if s is not None
        ]
        if base_s is None or not pairs:
            continue
        best_v, best_s = max(pairs, key=lambda t: t[1])
        base_v = sweep.values[sweep.base_index]
        if best_v != base_v and best_s >= base_s + 0.05:
            name = sweep.name.replace("_", " ")
            if retail:
                recs.append(
                    f"When we tried {name} at {_param_label(sweep.name, best_v)} instead "
                    f"of your {_param_label(sweep.name, base_v)}, the score improved "
                    f"({base_s:.2f} → {best_s:.2f}). Worth a re-run — but every retry "
                    "makes good numbers a little less trustworthy."
                )
            else:
                recs.append(
                    f"In this run's ±20% sweep, {name} {_param_label(sweep.name, best_v)} "
                    f"beat the specced {_param_label(sweep.name, base_v)}: backtest Sharpe "
                    f"{base_s:.2f} → {best_s:.2f}. Re-run with it — the change re-enters "
                    "the gauntlet as a new trial."
                )

    oos = report.oos
    if oos.flagged and oos.degradation is not None:
        recs.append(
            (
                f"It kept only {oos.degradation * 100:.0f}% of its training score on "
                "data it never saw. The honest fix is simpler settings and more "
                "history — more tweaking will make it worse."
            )
            if retail
            else (
                f"The edge concentrates in-sample (OOS keeps {oos.degradation * 100:.0f}% "
                "of in-sample Sharpe). Fewer tuned parameters and a longer window are "
                "the only honest fixes — more tuning will make this worse."
            )
        )

    mc = report.monte_carlo
    if mc.p_loss is not None and mc.p_loss > 0.20:
        recs.append(
            (
                f"{mc.p_loss * 100:.0f}% of the reshuffled versions of this strategy "
                "lost money — the original got a lucky order of trades. A version with "
                "capped losses (a spread) or smaller size handles bad luck better; "
                "test that as its own run."
            )
            if retail
            else (
                f"{mc.p_loss * 100:.0f}% of resampled paths lose money — the result "
                "leans on trade ordering. A defined-risk structure or smaller size "
                "survives more of the bad orderings; test it as its own run."
            )
        )

    wf = report.walk_forward
    if wf.meaningful and wf.consistency is not None and wf.consistency < 0.6:
        positive = sum(1 for f in wf.folds if f.ret > 0)
        recs.append(
            (
                f"It only made money in {positive} of {len(wf.folds)} time periods — "
                "a few good stretches carry everything. Adding a market-condition "
                "filter for entries is worth testing as its own run."
            )
            if retail
            else (
                f"Only {positive} of {len(wf.folds)} walk-forward windows were "
                "profitable — the total return comes from a few stretches. An entry "
                "filter (volatility or trend regime) is worth testing as a separate run."
            )
        )

    if report.dsr.dsr is not None and report.dsr.dsr < 0.5 and report.dsr.trials > 1:
        recs.append(
            (
                f"This is try number {report.dsr.trials} at this kind of strategy — "
                "the math says the good numbers now look more like luck than skill. "
                "Best move: stop tweaking and let new market data decide."
            )
            if retail
            else (
                f"Deflated Sharpe {report.dsr.dsr:.2f} after {report.dsr.trials} trials "
                "on this family — the remaining edge is likely mined. The recommendation "
                "is restraint: stop tuning and let new data arrive."
            )
        )

    if not recs:
        recs.append(
            (
                "None of the nearby settings we tried beat yours — the setup is "
                "already at its local best. The most valuable improvement is simply "
                "more market history, not more tweaking."
            )
            if retail
            else (
                "No parameter in the ±20% sweep beat the specced values by a meaningful "
                "margin — the configuration already sits on its local plateau. The "
                "highest-value improvement is more history, not more tuning."
            )
        )
    return recs[:4]


def _sensitivity_grid(report: HonestyReport) -> tuple[list[list[float]], list[str]]:
    rows: list[list[float]] = []
    names: list[str] = []
    for sweep in report.sensitivity.params:
        valid = [s for s in sweep.sharpes if s is not None]
        if not valid:
            continue
        top = max(valid)
        bottom = min(valid)
        span = top - bottom or 1.0
        rows.append(
            [
                0.06 if s is None else round(0.10 + 0.82 * (s - bottom) / span, 2)
                for s in sweep.sharpes
            ]
        )
        cls = f" · {sweep.classification}" if sweep.classification else ""
        names.append(f"{sweep.name}{cls}")
    return rows, names


def _wf_bars(report: HonestyReport) -> list[dict[str, Any]]:
    # every tested window, oldest → newest: showing the full walk-forward
    # history makes the depth of testing visible and fills the panel (the
    # frontend flexes bar width to the count). Heights normalize against all
    # shown folds.
    folds = report.walk_forward.folds
    if not folds:
        return []
    biggest = max(abs(f.ret) for f in folds) or 1.0
    return [
        {
            "h": round(14 + 38 * abs(f.ret) / biggest, 1),
            "pos": f.ret > 0,
            "t": (
                f"{_short_iso(f.start)} → {_short_iso(f.end)} · "
                f"{f.ret * 100:+.1f}% · {f.trades} trade{'s' if f.trades != 1 else ''}"
            ),
        }
        for f in folds
    ]


def _verdict_block(report: HonestyReport, verdict: VerdictText) -> dict[str, Any]:
    trust = report.trust
    sample = report.regime_sample
    survived_count = trust.survived_count
    chip_names = [
        ("oos", "OOS"),
        ("walk_forward", "walk-fwd"),
        ("monte_carlo", "monte carlo"),
        ("sensitivity", "sensitivity"),
        ("sample", "sample"),
    ]
    chips = [f"{label} {'✓' if trust.survived[key] else '✗'}" for key, label in chip_names]

    if trust.label == "insufficient_evidence":
        needs = []
        cov = report.coverage
        if cov.materially_short:
            needs.append(
                f"chain coverage ≥ {round(COVERAGE_MIN_RATIO * 100)}% of the requested "
                f"window (has {round(cov.coverage_ratio * 100)}%)"
            )
        if sample.trades < MIN_TRADES:
            needs.append(f"≥ {MIN_TRADES} trades (has {sample.trades})")
        if sample.regimes_present < 2:
            needs.append(f"≥ 2 volatility regimes (has {sample.regimes_present})")
        return {
            "kind": "refusal",
            "refusal": True,
            "headline": verdict.headline,
            "survived": "NOT EVALUATED",
            "chips": [],
            "evidence": [],
            "breaks": [],
            "caveat": "",
            "refusalBody": (
                "The gauntlet ran, but blessing this sample would be a guess wearing a "
                "lab coat. Raw output below, unblessed. " + " · ".join(verdict.caveats)
            ),
            "refusalUnlock": "unlocks at " + " and ".join(needs) if needs else "",
        }

    level = trust.level or 1
    marker = _MARKER[level]
    # the ±15% band must stay inside the track (level 5 would spill past 100%)
    band_left = min(max(marker - 15, 0), 70)
    return {
        "kind": "graded",
        "refusal": False,
        "headline": verdict.headline,
        "survived": f"{survived_count} OF 5 ATTACKS SURVIVED",
        "band": {"left": f"{band_left}%", "width": "30%"},
        "marker": f"{marker}%",
        "chips": chips,
        "evidence": verdict.evidence,
        "breaks": verdict.breaks_where,
        "caveat": " · ".join(verdict.caveats),
    }


def _panel_notes(report: HonestyReport, retail: bool = False) -> list[str]:
    oos, wf, mc, sens = report.oos, report.walk_forward, report.monte_carlo, report.sensitivity

    if oos.degradation is not None:
        ok = not oos.flagged
        oos_note = (
            f"kept {_pct(oos.degradation, 0)} of its training score on unseen data — "
            + ("pass ✓" if ok else "fail ✗")
            if retail
            else f"OOS keeps {_pct(oos.degradation, 0)} of in-sample sharpe — "
            + ("holds ✓" if ok else "fails ✗")
        )
    else:
        oos_note = (
            "not enough history to split" if retail else "not computable on this window"
        )

    if wf.meaningful and wf.consistency is not None:
        positive = sum(1 for f in wf.folds if f.ret > 0)
        mark = "✓" if wf.consistency >= 0.6 else "✗"
        wf_note = (
            f"made money in {positive} of {len(wf.folds)} time periods {mark}"
            if retail
            else f"{positive} / {len(wf.folds)} windows profitable {mark}"
        )
    else:
        wf_note = (
            "history too short to slice into periods"
            if retail
            else (wf.note or "not meaningful at this history length")
        )

    if mc.p_loss is not None:
        mc_note = (
            f"{_pct(mc.p_loss, 0)} of reshuffles lost money · worst realistic dip "
            f"−{_pct(mc.max_drawdown_p95, 0)}"
            if retail
            else f"P(loss) {_pct(mc.p_loss, 0)} · 95th pctile drawdown "
            f"−{_pct(mc.max_drawdown_p95, 0)}"
        )
    else:
        mc_note = "needs at least 5 finished trades" if retail else "needs ≥ 5 closed trades"

    if sens.verdict:
        good = sens.verdict == "plateau"
        sens_note = (
            ("still works when nudged ✓" if good else "falls apart when nudged ✗")
            if retail
            else f"optimum is a {sens.verdict} " + ("✓" if good else "✗")
        )
    else:
        sens_note = "not classifiable"

    return [oos_note, wf_note, mc_note, sens_note]


def _honesty_panels(report: HonestyReport) -> dict[str, Any]:
    oos = report.oos
    bar1 = 88 if (oos.is_sharpe or 0) > 0 else 0
    ratio = max(0.0, min(oos.degradation if oos.degradation is not None else 0.0, 1.2))
    bar2 = round(bar1 * ratio)
    return {
        "isSharpe": _num(oos.is_sharpe),
        "oosSharpe": _num(oos.oos_sharpe),
        "bar1": f"{bar1}%",
        "bar2": f"{max(bar2, 0)}%",
        "wf": _wf_bars(report),
        "notes": _panel_notes(report),
    }


def _retail_block(report: HonestyReport, retail_verdict: VerdictText) -> dict[str, Any]:
    """Everything the UI swaps when Verbiage Complexity = Retail — the same
    computed numbers, everyday words."""
    block = _verdict_block(report, retail_verdict)
    if block["refusal"]:
        sample = report.regime_sample
        needs = []
        cov = report.coverage
        if cov.materially_short:
            needs.append(
                f"option prices on more of your date range (only "
                f"{round(cov.coverage_ratio * 100)}% of it had them)"
            )
        if sample.trades < MIN_TRADES:
            needs.append(f"at least {MIN_TRADES} finished trades (has {sample.trades})")
        if sample.regimes_present < 2:
            needs.append(
                f"at least 2 kinds of market conditions (has {sample.regimes_present})"
            )
        block["refusalBody"] = (
            "The tests ran, but calling this a verdict would be guessing. The raw "
            "numbers are below, unblessed — look, but don't lean on them. "
            + " · ".join(retail_verdict.caveats)
        )
        block["refusalUnlock"] = "unlocks with " + " and ".join(needs) if needs else ""
    return {
        **{
            k: block[k]
            for k in (
                "headline", "survived", "evidence", "breaks", "caveat",
            )
            if k in block
        },
        **({"refusalBody": block["refusalBody"], "refusalUnlock": block["refusalUnlock"]}
           if block["refusal"] else {}),
        "notes": _panel_notes(report, retail=True),
        "recommendations": _recommendations(report, retail=True),
    }


def build_run_payload(
    run_id: str,
    spec: StrategySpec,
    result: RunResult,
    report: HonestyReport,
    verdict: VerdictText,
    retail_verdict: VerdictText | None = None,
) -> dict[str, Any]:
    m = result.metrics
    window = f"{_short(result.effective_start)} → {_short(result.effective_end)}"
    today = _short(datetime.now(UTC).date())
    structure = spec.position.structure.value.replace("_", " ")
    refusal = report.trust.label == "insufficient_evidence"
    star = "*" if refusal else ""

    sens_grid, sens_rows = _sensitivity_grid(report)

    return {
        "id": run_id,
        "demo": False,
        "status": "done",
        "stage": 6,
        "name": spec.meta.name,
        "meta": (
            f"{result.ticker} · {structure} · run {today} · effective window {window} "
            f"(bounded by coverage) · seed {result.seed} · trials {report.dsr.trials} · "
            f"verdict: {verdict.source}"
        ),
        "spec": None,
        "verdict": _verdict_block(report, verdict),
        "mtiles": [
            {"v": _pct(m.get("cagr")), "l": f"CAGR{star}"},
            {"v": _num(m.get("sharpe")), "l": f"SHARPE{star}"},
            {"v": _num(m.get("sortino")), "l": f"SORTINO{star}"},
            {
                "v": "—" if m.get("max_drawdown") is None else f"−{_pct(m.get('max_drawdown'))}",
                "l": f"MAX DD{star}",
                "neg": True,
            },
            {"v": _pct(m.get("win_rate"), 0), "l": f"WIN RATE{star}"},
            {"v": _num(m.get("profit_factor")), "l": f"P·FACTOR{star}"},
        ],
        "equityPoints": "",
        "drawdownPoints": "",
        "equitySeries": _downsample(result.dates, result.equity),
        "drawdownSeries": _drawdown_series(result.dates, result.equity),
        "oosShadeX": round(860 * 0.7) if not refusal else 860,
        "oosSplitDate": report.oos.split_date,
        "honesty": _honesty_panels(report),
        "coverage": report.coverage.model_dump(),
        "mc": _mc_fan(report),
        "mcTerm": {
            "p95": _dollars(report.monte_carlo.terminal_p95),
            "p50": _dollars(report.monte_carlo.terminal_p50),
            "p05": _dollars(report.monte_carlo.terminal_p5),
        },
        "sensitivity": sens_grid,
        "sensitivityRows": sens_rows,
        "sensitivityDetail": _sensitivity_detail(report),
        "recommendations": _recommendations(report),
        "retail": _retail_block(report, retail_verdict) if retail_verdict else None,
        "tradeHeader": (
            f"Trade log — {result.filled} filled · {result.skipped} skipped, with reasons"
        ),
        "trades": _trade_rows(result.trades),
    }


def run_summary(run_id: str, payload: dict[str, Any], created: str) -> dict[str, Any]:
    verdict = payload.get("verdict", {})
    survived = verdict.get("survived", "")
    label = "withheld" if verdict.get("refusal") else survived.split(" OF")[0] + "/5 survived"
    retail = payload.get("retail") or {}
    return {
        "id": run_id,
        "demo": False,
        "name": payload.get("name", run_id),
        "meta": f"{created} · {label}",
        "quote": f"“{verdict.get('headline', '')}”",
        # retail-register headline for the library card, when the run has one
        "quoteRetail": f"“{retail['headline']}”" if retail.get("headline") else None,
        "kind": "refusal" if verdict.get("refusal") else "graded",
        "band": verdict.get("band"),
        "marker": verdict.get("marker"),
    }
