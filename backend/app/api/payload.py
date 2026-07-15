"""RunResult + HonestyReport + VerdictText → the frontend's run payload.

Every number rendered here is computed by the engine or the gauntlet
(guardrail #4). Insufficient evidence renders the design's refusal state
with the REAL unlock condition; everything else renders the full verdict
with the trust band placed by the deterministic trust level.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from typing import Any

from app.engine.types import RunResult, TradeEvent
from app.honesty.report import HonestyReport
from app.honesty.stages import COVERAGE_MIN_RATIO, MIN_TRADES
from app.honesty.verdict import VerdictText
from app.models.spec import StrategySpec

_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

# the fill model's product-visible label — shared by the run meta line and
# the provenance mechanics record so the two can never drift apart
FILL_MODEL = "liquidity-v1"

# trust band geometry: level 1..5 → marker at 10/30/50/70/90%, band ±15%
_MARKER = {1: 10, 2: 30, 3: 50, 4: 70, 5: 90}

# the equity chart's SVG track width — the OOS shade x is computed against
# it in BOTH the build and the read-time re-grade path
OOS_TRACK_W = 860


def _oos_shade_x(refusal: bool) -> int:
    """Refusals shade the whole track (nothing was blessed to split)."""
    return OOS_TRACK_W if refusal else round(OOS_TRACK_W * 0.7)


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


def _downsample_nullable(
    dates: list[date], values: list[float | None], cap: int = 400
) -> list[dict[str, Any]]:
    """Like _downsample, but a None stays a null point — greek gaps are
    honest gaps, the chart shows a hole rather than a made-up line."""
    n = len(values)
    idxs: list[int]
    if n <= cap:
        idxs = list(range(n))
    else:
        step = n / cap
        idxs = sorted({min(n - 1, int(i * step)) for i in range(cap)} | {n - 1})
    return [
        {"t": dates[i].isoformat(), "v": None if (v := values[i]) is None else round(v, 2)}
        for i in idxs
    ]


def _drawdown_series(dates: list[date], equity: list[float]) -> list[dict[str, Any]]:
    peak = equity[0] if equity else 0.0
    dd: list[float] = []
    for v in equity:
        peak = max(peak, v)
        # a ruined curve (equity ≤ 0 at the halt) reads 100%, never >100% —
        # you can't lose more than everything (the ruin banner carries the
        # negative dollar figure)
        dd.append(min((1.0 - v / peak) * 100.0, 100.0) if peak > 0 else 0.0)
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


def _sweep_display_name(name: str) -> str:
    """Row label for a sweep param. F8 condition sweeps carry a 'cond_'
    prefix (and maybe an operator suffix for a repeated indicator) — strip
    it so the grid reads 'skew 25d', not 'cond skew 25d'."""
    if name.startswith("cond_"):
        return name[len("cond_"):].replace("_", " ")
    return name.replace("_", " ")


def _param_label(name: str, v: float) -> str:
    if name == "delta":
        pct = v * 100
        if abs(pct - round(pct)) < 1e-9:
            return f".{int(round(pct)):02d}Δ"
        # the floored delta grid makes sub-point cells (0.075, 0.0317)
        # routine, and rounding them to two digits would label a value
        # the sweep never ran — recommendations must name the EXACT
        # tested delta (review finding on the strike-floor pass)
        return "." + f"{v:.4f}".rstrip("0")[2:] + "Δ"
    if name == "dte":
        return f"{int(v)}d"
    if name == "entry_time":
        return f"{int(v):+d}m"  # the D2d entry-time nudge, minutes
    if name.startswith("cond_"):
        return f"{v:g}"  # F8: an indicator threshold — unit-free number
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
                "name": _sweep_display_name(sweep.name),
                "cls": sweep.classification or "",
                "base": sweep.base_index,
                "cells": cells,
            }
        )
    return out


def sweep_coverage_notes(sensitivity: dict[str, Any] | None) -> list[str]:
    """The F8 sweep-coverage disclosures — what the sensitivity stage did
    and did NOT probe — selected from a stored honesty report's sensitivity
    dump. The one selector for these keys on the API layer: the notebook
    export reads this list today, and any future api-side surface that
    discloses sweep coverage must too, so none bakes its own key list.
    (The verdict caveats in app/honesty/verdict.py read the same fields
    off the TYPED Sensitivity model — honesty cannot import api — so a
    new disclosure field must be added there too, not only here.)"""
    sens = sensitivity or {}
    return [str(n) for n in (sens.get("conditions_note"),
                             sens.get("window_note")) if n]


def _recommendations(report: HonestyReport, retail: bool = False) -> list[str]:
    """What would improve the strategy — computed ONLY from this run's own
    gauntlet numbers (the sensitivity sweeps re-ran the real engine), never
    from opinion. Guardrail #4 applies: every number below exists in the
    report. `retail` swaps the register, never the numbers."""
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
            name = _sweep_display_name(sweep.name)
            if retail:
                recs.append(
                    f"When we tried {name} at {_param_label(sweep.name, best_v)} instead "
                    f"of your {_param_label(sweep.name, base_v)}, the score improved "
                    f"({base_s:.2f} → {best_s:.2f}). Worth a re-run — but every retry "
                    "makes good numbers a little less trustworthy."
                )
            else:
                recs.append(
                    f"In this run's sensitivity sweep, {name} {_param_label(sweep.name, best_v)} "
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
                "No parameter in the sensitivity sweep beat the specced values by a meaningful "
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
                # FX.4: a minute-flavored fold says so on its tooltip — the
                # disclosure lives in the run, not only the docs
                + (f" · {round(f.minute_share * 100)}% minute grid"
                   if f.minute_share and round(f.minute_share * 100) >= 1
                   else "")
            ),
        }
        for f in folds
    ]


def _ladder_depth_block(report: HonestyReport) -> dict[str, Any] | None:
    """Depth attribution for the results panel (D5b): the per-tier table + a
    marginal-rung bar chart. P/L red/green is fine here — this is a DATA
    panel, never a trust/verdict surface (the color rule)."""
    ld = report.ladder_depth
    if ld is None:
        return None

    def sign(v: float) -> str:
        return "pos" if v > 0 else "neg" if v < 0 else "none"

    def money(v: float) -> str:
        return f"{'+' if v >= 0 else '−'}${abs(v):,.0f}"

    max_tier = max((abs(t.total_pl) for t in ld.tiers), default=0.0) or 1.0
    max_rung = max((abs(r.marginal_pl) for r in ld.rungs), default=0.0) or 1.0
    return {
        "baskets": ld.baskets,
        "realizedTotal": money(ld.realized_total),
        "realizedSign": sign(ld.realized_total),
        "deepestNetNegative": ld.deepest_net_negative,
        "tiers": [
            {
                "depth": t.depth,
                "threshold": f"{t.threshold:g}",
                "baskets": t.baskets,
                "winRate": _pct(t.win_rate, 0),
                "contracts": t.contracts,
                "totalPl": money(t.total_pl),
                "avgPl": money(t.avg_pl),
                "plSign": sign(t.total_pl),
                "pctProfit": _pct(t.pct_gross_profit, 0),
                "pctLoss": _pct(t.pct_gross_loss, 0),
                "barPct": round(abs(t.total_pl) / max_tier * 100, 1),
            }
            for t in ld.tiers
        ],
        "rungs": [
            {
                "label": f"rung {r.rung_index + 1} · {r.threshold:g}",
                "addContracts": r.add_contracts,
                "fires": r.fires,
                "contracts": r.contracts,
                "marginalPl": money(r.marginal_pl),
                "plSign": sign(r.marginal_pl),
                "netNeg": r.net_negative,
                # buying-power gate refusals at this depth (2026-07-15):
                # an UNAFFORDABLE rung is a distinct fact from an
                # unprofitable one
                "unaffordable": r.unaffordable_baskets,
                "barPct": round(abs(r.marginal_pl) / max_rung * 100, 1),
            }
            for r in ld.rungs
        ],
    }


def _below_standard_note(report: HonestyReport, retail: bool = False) -> str | None:
    """The honesty rider on a lowered evidence bar: a GRADED verdict on a
    sample under the standard floor must say so, in both registers — the
    user may lower the gate, never the disclosure (owner ask 2026-07-14).
    Worded run-anchored ('this verdict's bar'), never viewer-anchored
    ('your setting') — a stored payload outlives the setting that made it,
    and a different viewer may be reading it (review finding)."""
    sample = report.regime_sample
    if (
        report.trust.label == "insufficient_evidence"
        or sample.min_trades >= MIN_TRADES
        or sample.trades >= MIN_TRADES
    ):
        return None
    if retail:
        return (
            f"Heads up: only {sample.trades} finished trades — under the standard "
            f"floor of {MIN_TRADES}. This verdict was graded at a lowered bar of "
            f"{sample.min_trades}, so treat it as a sketch, not a study."
        )
    return (
        f"Below-standard sample: {sample.trades} closed trades, graded only "
        f"because this verdict's evidence bar is {sample.min_trades} (standard "
        f"floor {MIN_TRADES}). Statistical power is commensurately thin."
    )


def _verdict_block(report: HonestyReport, verdict: VerdictText) -> dict[str, Any]:
    trust = report.trust
    sample = report.regime_sample
    bar = sample.min_trades  # the evidence bar THIS run was scored at
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
        if sample.trades < bar:
            needs.append(f"≥ {bar} trades (has {sample.trades})")
        if sample.regimes_present < 2:
            needs.append(f"≥ 2 volatility regimes (has {sample.regimes_present})")
        return {
            "kind": "refusal",
            "refusal": True,
            # guardrail #5-adjacent (2026-07-15): the wipeout is structural
            # so every summary surface (library card) can carry it
            "ruined": report.ruin is not None,
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
            # a wiped-out account never unlocks with more data — promising
            # "unlocks at N trades" on a dead account would be a lie
            # (review finding 2026-07-15; unlock_conditions excludes it too)
            "refusalUnlock": (
                "does not unlock with more data — the account was wiped out; "
                "change the strategy or the capital"
                if report.ruin is not None
                else "unlocks at " + " and ".join(needs) if needs else ""
            ),
        }

    level = trust.level or 1
    marker = _MARKER[level]
    # the ±15% band must stay inside the track (level 5 would spill past 100%)
    band_left = min(max(marker - 15, 0), 70)
    caveats = list(verdict.caveats)
    below = _below_standard_note(report)
    if below:
        # appended HERE, not in the templates, so it rides template AND
        # LLM narration alike — the disclosure is not the LLM's to drop
        caveats.append(below)
    return {
        "kind": "graded",
        "refusal": False,
        # guardrail #5: a graded sub-15 sample is marked structurally so
        # every summary surface (library card) can carry the disclosure too
        "belowStandard": bool(below),
        # the account was wiped out (2026-07-15) — structural for the same
        # reason; trust is hard-capped at the floor when this is set
        "ruined": report.ruin is not None,
        "headline": verdict.headline,
        "survived": f"{survived_count} OF 5 ATTACKS SURVIVED",
        "band": {"left": f"{band_left}%", "width": "30%"},
        "marker": f"{marker}%",
        "chips": chips,
        "evidence": verdict.evidence,
        "breaks": verdict.breaks_where,
        "caveat": " · ".join(caveats),
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
        # p(ruin) shown only when some reshuffled path actually died at $0
        # (absorption, 2026-07-15) — a computed ruin share is never silent
        ruin_bit = (
            (f" · {_pct(mc.p_ruin, 0)} went broke" if retail
             else f" · p(ruin) {_pct(mc.p_ruin, 0)}")
            if mc.p_ruin
            else ""
        )
        mc_note = (
            f"{_pct(mc.p_loss, 0)} of reshuffles lost money · worst realistic dip "
            f"−{_pct(mc.max_drawdown_p95, 0)}" + ruin_bit
            if retail
            else f"P(loss) {_pct(mc.p_loss, 0)} · 95th pctile drawdown "
            f"−{_pct(mc.max_drawdown_p95, 0)}" + ruin_bit
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
    if not block["refusal"]:
        below = _below_standard_note(report, retail=True)
        if below:
            # the graded arm appended the institutional wording — retail
            # readers get the retail one, same facts
            block["caveat"] = " · ".join([*retail_verdict.caveats, below])
    else:
        sample = report.regime_sample
        bar = sample.min_trades
        needs = []
        cov = report.coverage
        if cov.materially_short:
            needs.append(
                f"option prices on more of your date range (only "
                f"{round(cov.coverage_ratio * 100)}% of it had them)"
            )
        if sample.trades < bar:
            needs.append(f"at least {bar} finished trades (has {sample.trades})")
        if sample.regimes_present < 2:
            needs.append(
                f"at least 2 kinds of market conditions (has {sample.regimes_present})"
            )
        block["refusalBody"] = (
            "The tests ran, but calling this a verdict would be guessing. The raw "
            "numbers are below, unblessed — look, but don't lean on them. "
            + " · ".join(retail_verdict.caveats)
        )
        block["refusalUnlock"] = (
            "more data won't unlock this — the account ran out of money; "
            "change the strategy or the starting capital"
            if report.ruin is not None
            else "unlocks with " + " and ".join(needs) if needs else ""
        )
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


def _verdict_surfaces(
    report: HonestyReport,
    verdict: VerdictText,
    retail_verdict: VerdictText | None,
) -> dict[str, Any]:
    """EVERY payload key derived from the trust label / verdict text, in one
    place — the initial build, the async narration patch, and the read-time
    re-grade all consume this, so a future verdict-dependent key cannot be
    added to one path and silently go stale on the others."""
    return {
        "verdict": _verdict_block(report, verdict),
        "verdictSource": verdict.source,
        "recommendations": _recommendations(report),
        "retail": _retail_block(report, retail_verdict) if retail_verdict else None,
    }


def build_run_payload(
    run_id: str,
    spec: StrategySpec,
    result: RunResult,
    report: HonestyReport,
    verdict: VerdictText,
    retail_verdict: VerdictText | None = None,
    data_provenance: list[dict[str, str]] | None = None,
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
            f"{result.ticker} · {structure} · clock {result.clock} · run {today} · "
            f"effective window {window} (bounded by coverage) · seed {result.seed} · "
            f"trials {report.dsr.trials} · fill model {FILL_MODEL} · "
            f"verdict: {verdict.source}"
        ),
        "spec": None,
        **_verdict_surfaces(report, verdict, retail_verdict),
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
        # ruin halt (2026-07-15): the chart's terminal marker + banner —
        # additive key, None on every non-ruined and stored pre-ruin payload
        "ruin": (
            {
                "date": report.ruin.ruin_date,
                "finalEquity": report.ruin.final_equity,
                "haltedPositions": report.ruin.positions_closed_at_halt,
            }
            if report.ruin
            else None
        ),
        # buying-power profile (2026-07-15) — additive; the funding caveat's
        # numbers, structured
        "funding": report.funding.model_dump() if report.funding else None,
        "equitySeries": _downsample(result.dates, result.equity),
        "drawdownSeries": _drawdown_series(result.dates, result.equity),
        "oosShadeX": _oos_shade_x(refusal),
        "oosSplitDate": report.oos.split_date,
        "honesty": _honesty_panels(report),
        "coverage": report.coverage.model_dump(),
        "liquidity": report.liquidity.model_dump() if report.liquidity else None,
        "dataConfidence": (report.data_confidence.model_dump()
                           if report.data_confidence else None),
        # forward-record convention seams the window crossed (2026-07-08):
        # which frozen vendor series continued in-house, and from when —
        # additive key, None on single-convention windows and stored runs
        "dataProvenance": data_provenance or None,
        "concentration": report.concentration.model_dump() if report.concentration else None,
        # per-fill provenance (D2b): which quote record priced each leg fill
        "fillSources": result.fill_sources,
        "clock": result.clock,
        # FX.1: per-session bar-resolution record (guardrail #6 — a surface
        # showing results shows what they were computed on). Additive keys;
        # None/empty on daily and pre-v4 runs.
        "resolutionMode": result.resolution_mode,
        "resolutionMix": result.resolution_mix or None,
        "resolutionRuns": result.resolution_runs or None,
        # FX.2: skip-reason distribution. Attempt-level reasons (e.g.
        # conditions_not_met, counted per attempted bar) sit beside
        # episode-level ones (max_concurrent, order_in_flight,
        # no_quote_this_bar — once per setup). Populated for every NEW
        # run at any clock; None only on stored pre-FX.2 payloads.
        "skipReasons": result.skip_reasons or None,
        "sessionSplit": report.session_split.model_dump() if report.session_split else None,
        # FX.4: the mixed-resolution split (full vs 5-min-only vs minute) —
        # additive; None on runs without a per-session resolution record
        "resolutionSplit": (report.resolution_split.model_dump()
                            if report.resolution_split
                            and report.resolution_split.meaningful else None),
        "ladderDepth": _ladder_depth_block(report),
        "greeksSeries": {
            "delta": _downsample_nullable(result.dates, result.portfolio_delta),
            "gamma": _downsample_nullable(result.dates, result.portfolio_gamma),
            "theta": _downsample_nullable(result.dates, result.portfolio_theta),
            "vega": _downsample_nullable(result.dates, result.portfolio_vega),
        },
        "mc": _mc_fan(report),
        "mcTerm": {
            "p95": _dollars(report.monte_carlo.terminal_p95),
            "p50": _dollars(report.monte_carlo.terminal_p50),
            "p05": _dollars(report.monte_carlo.terminal_p5),
        },
        "sensitivity": sens_grid,
        "sensitivityRows": sens_rows,
        "sensitivityDetail": _sensitivity_detail(report),
        "tradeHeader": (
            f"Trade log — {result.filled} filled · {result.skipped} skipped, with reasons"
        ),
        "trades": _trade_rows(result.trades),
    }


def run_summary(run_id: str, payload: dict[str, Any], created: str) -> dict[str, Any]:
    verdict = payload.get("verdict", {})
    survived = verdict.get("survived", "")
    label = "withheld" if verdict.get("refusal") else survived.split(" OF")[0] + "/5 survived"
    # guardrail #5: the library card of a graded sub-15 sample carries the
    # disclosure too — the headline alone would read rosier than the verdict
    if verdict.get("belowStandard"):
        label += " · below-standard sample"
    # the wipeout travels to the card for the same reason (2026-07-15)
    if verdict.get("ruined"):
        label += " · wiped out"
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


def apply_verdict_text(
    payload: dict[str, Any],
    report: HonestyReport,
    verdict: VerdictText,
    retail_verdict: VerdictText | None = None,
) -> dict[str, Any]:
    """Swap ONLY the verdict-derived surfaces of a stored payload — the
    async-narration upgrade path (owner ask 2026-07-14: the verdict stage
    stalled minutes on LLM retries) and the re-grade path below. The blocks
    are rebuilt from the given report via the same _verdict_surfaces the
    initial build used; every data panel is untouched."""
    out = dict(payload)
    surfaces = _verdict_surfaces(report, verdict, retail_verdict)
    if payload.get("retail") is None:
        # a stored run without a retail register never grows one here
        surfaces.pop("retail", None)
    out.update(surfaces)
    # keep the human-readable meta token in step with the structured
    # verdictSource key (display only — consumers read verdictSource)
    out["meta"] = re.sub(
        r"verdict: [a-z]+", f"verdict: {verdict.source}",
        str(payload.get("meta", "")), count=1,
    )
    return out


def _regrade_report(
    report: HonestyReport, min_trades: int
) -> HonestyReport | None:
    """The stored honesty report re-gated at a different evidence bar, or
    None when the bar leaves sample cap, resolution judgment, and trust all
    unchanged. Shared by the payload re-grade and the Q&A re-grade so the
    displayed verdict and the grounded answers can never disagree."""
    from app.honesty.stages import regrade_sample, rejudge_resolution
    from app.honesty.trust import compute_trust

    sample = regrade_sample(report.regime_sample, min_trades)
    stored_rs = report.resolution_split
    res_split = (
        rejudge_resolution(stored_rs, min_trades) if stored_rs is not None else None
    )
    trust = compute_trust(
        report.oos, report.walk_forward, report.monte_carlo,
        report.sensitivity, sample, report.dsr, report.coverage,
        report.concentration, report.scale_in, res_split,
        ruin=report.ruin,
    )
    # trust equality already folds the sample cap (survived/label/reasons);
    # the resolution judgment can move without touching trust and still
    # must reach the resolution panel, so it is checked on its own
    res_changed = (
        res_split is not None and stored_rs is not None
        and (res_split.judged != stored_rs.judged
             or res_split.sign_flip != stored_rs.sign_flip)
    )
    if trust == report.trust and not res_changed:
        return None
    return report.model_copy(update={
        "regime_sample": sample,
        "resolution_split": res_split,
        "trust": trust,
    })


def regrade_for_min_trades(
    payload: dict[str, Any], stats: dict[str, Any] | None, min_trades: int
) -> dict[str, Any]:
    """Re-decide the evidence gate at the CALLER's minimum-trades setting
    (owner decision 2026-07-14: the bar is a user setting — a 13-trade
    refusal unlocks when the bar drops to 1, and a graded run re-caps when
    the bar rises; stored runs re-grade at read time, no re-run).

    Returns the payload untouched when the gate outcome is unchanged (the
    stored — possibly LLM — narration described the same grade). When the
    grade moves, the verdict surfaces are rebuilt from the stored honesty
    report with deterministic template narration: the stored words argued
    a different verdict, so reusing them would be dishonest. The stored
    row is never mutated — this is a per-request view."""
    report_doc = (stats or {}).get("honesty_report")
    if not isinstance(report_doc, dict):
        return payload  # pre-Q&A run: no stats bundle, nothing to re-grade
    # cheap raw-dict peek FIRST: the overwhelmingly common case (caller's
    # bar == stored bar) must not pay a full report validation per poll
    stored_bar = int(
        (report_doc.get("regime_sample") or {}).get("min_trades", MIN_TRADES))
    if min_trades == stored_bar:
        return payload
    try:
        report = HonestyReport.model_validate(report_doc)
    except Exception:
        return payload  # pre-contract dump — the stored payload stands

    from app.honesty.verdict import retail_template_verdict, template_verdict

    report2 = _regrade_report(report, min_trades)
    if report2 is None:
        return payload

    verdict = template_verdict(report2)
    retail_verdict = retail_template_verdict(report2)
    verdict.caveats.append(
        f"re-graded at your minimum-trades setting of {min_trades} — "
        f"this run was scored at {stored_bar} when it ran"
    )
    retail_verdict.caveats.append(
        f"re-judged at your minimum-trades setting of {min_trades} — "
        f"the bar was {stored_bar} when this run was saved"
    )
    out = apply_verdict_text(payload, report2, verdict, retail_verdict)
    refusal = report2.trust.label == "insufficient_evidence"
    star = "*" if refusal else ""
    out["mtiles"] = [
        {**t, "l": str(t.get("l", "")).rstrip("*") + star}
        for t in payload.get("mtiles", [])
    ]
    out["oosShadeX"] = _oos_shade_x(refusal)
    # the resolution panel must tell the same story as the re-graded
    # verdict (review finding: a re-judged sign flip refused the run while
    # the stored panel still said "too thin, not judged")
    rs2 = report2.resolution_split
    out["resolutionSplit"] = (
        rs2.model_dump() if rs2 is not None and rs2.meaningful else None
    )
    out["regraded"] = {"bar": min_trades, "ranAt": stored_bar}
    # the re-graded view is template-narrated by construction
    out["narrationPending"] = False
    return out


def regrade_stats_for_min_trades(
    stats: dict[str, Any], min_trades: int
) -> dict[str, Any]:
    """The stats bundle grounded Q&A quotes from, re-gated at the caller's
    bar — so answers describe the SAME verdict the screen shows and the
    bar number itself is grounded (it lives in the re-graded sample dump).
    Returns the stats untouched when the gate outcome is unchanged."""
    report_doc = stats.get("honesty_report")
    if not isinstance(report_doc, dict):
        return stats
    stored_bar = int(
        (report_doc.get("regime_sample") or {}).get("min_trades", MIN_TRADES))
    if min_trades == stored_bar:
        return stats
    try:
        report = HonestyReport.model_validate(report_doc)
    except Exception:
        return stats
    report2 = _regrade_report(report, min_trades)
    if report2 is None:
        return stats
    return {**stats, "honesty_report": report2.model_dump()}
