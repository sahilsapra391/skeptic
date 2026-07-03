"""RunResult → the frontend's run payload.

Until the honesty gauntlet (M3) exists, every real run renders in the
VERDICT-WITHHELD state: real stats, real equity curve, real trade log —
explicitly unblessed. Every number in the payload is computed by the
engine (guardrail #4 by construction: this is a template, no LLM).
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from app.engine.types import RunResult, TradeEvent
from app.models.spec import StrategySpec

_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _short(d: date) -> str:
    return f"{_MONTHS[d.month - 1]} {d.day} ’{str(d.year)[2:]}"


def _pct(v: float | None, digits: int = 1) -> str:
    return "—" if v is None else f"{v * 100:.{digits}f}%"


def _num(v: float | None, digits: int = 2) -> str:
    return "—" if v is None else f"{v:.{digits}f}"


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


def _trade_rows(trades: list[TradeEvent], cap: int = 250) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for t in reversed(trades[-cap:]):
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


def build_run_payload(run_id: str, spec: StrategySpec, result: RunResult) -> dict[str, Any]:
    m = result.metrics
    window = f"{_short(result.effective_start)} → {_short(result.effective_end)}"
    today = _short(datetime.now(UTC).date())
    structure = spec.position.structure.value.replace("_", " ")

    refusal_body = (
        f"Raw engine output: {result.filled} trades filled, {result.skipped} skipped, "
        f"{result.sessions_with_chain} chain sessions in the window. Nothing here has "
        f"survived an out-of-sample split, walk-forward, Monte Carlo or sensitivity "
        f"attack — treat it as machinery output, not evidence. unblessed"
    )

    return {
        "id": run_id,
        "demo": False,
        "status": "done",
        "stage": 6,
        "name": spec.meta.name,
        "meta": (
            f"{result.ticker} · {structure} · run {today} · effective window {window} "
            f"(bounded by coverage) · seed {result.seed}"
        ),
        "spec": None,
        "verdict": {
            "kind": "refusal",
            "refusal": True,
            "headline": "Verdict withheld — the honesty gauntlet lands at M3.",
            "survived": "NOT EVALUATED",
            "chips": [],
            "evidence": [],
            "breaks": [],
            "caveat": "",
            "refusalBody": refusal_body,
            "refusalUnlock": "unlocks when the anti-overfitting gauntlet (M3) runs this strategy",
        },
        "mtiles": [
            {"v": _pct(m.get("cagr")), "l": "CAGR*"},
            {"v": _num(m.get("sharpe")), "l": "SHARPE*"},
            {"v": _num(m.get("sortino")), "l": "SORTINO*"},
            {"v": "—" if m.get("max_drawdown") is None else f"−{_pct(m.get('max_drawdown'))}",
             "l": "MAX DD*", "neg": True},
            {"v": _pct(m.get("win_rate"), 0), "l": "WIN RATE*"},
            {"v": _num(m.get("profit_factor")), "l": "P·FACTOR*"},
        ],
        "equityPoints": "",
        "drawdownPoints": "",
        "equitySeries": _downsample(result.dates, result.equity),
        "drawdownSeries": _drawdown_series(result.dates, result.equity),
        "oosShadeX": 860,
        "honesty": {
            "isSharpe": "—",
            "oosSharpe": "—",
            "bar1": "0%",
            "bar2": "0%",
            "wf": [],
            "notes": [
                "not run — honesty gauntlet lands at M3",
                "not run — honesty gauntlet lands at M3",
                "not run — honesty gauntlet lands at M3",
                "not run — honesty gauntlet lands at M3",
            ],
        },
        "mc": {"p95": "", "p50": "", "p05": ""},
        "sensitivity": [],
        "tradeHeader": (
            f"Trade log — {result.filled} filled · {result.skipped} skipped, with reasons"
        ),
        "trades": _trade_rows(result.trades),
    }


def run_summary(run_id: str, payload: dict[str, Any], created: str) -> dict[str, Any]:
    return {
        "id": run_id,
        "demo": False,
        "name": payload.get("name", run_id),
        "meta": f"{created} · verdict withheld until M3",
        "quote": "“Real engine output — unblessed until the gauntlet exists.”",
        "kind": "refusal",
    }
