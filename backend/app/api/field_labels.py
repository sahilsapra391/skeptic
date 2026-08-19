"""V-208: ONE path-to-label table, read by every surface that shows a field.

The SUPERSEDED marker on a carried exchange and the WHAT CHANGED list further
down the same screen name the same fields. Until this existed the marker said
`exit.profit_target_pct` and so did the diff, which connected the two surfaces
at the cost of making both read like a stack trace; the alternative on offer was
prose in one place and a path in the other, which is worse, because then the
reader has to translate between them to notice they are the same field.

So: one table, one label, both surfaces, and the path stays visible as a
secondary. Nobody has to translate and nobody has to guess.

PRESENTATION ONLY. Nothing here reaches matching. `diff_specs` still emits paths
(V-164's pinned vocabulary), the reconciler still matches on stored values
(V-200), and the lock check still prefix-matches paths. A label is a caption on
a decision that was already made.

An unmapped path is NOT an error and never blocks: `label_for` returns None, the
caller renders the raw path, and the gap is counted so the table's holes report
themselves rather than waiting to be noticed (the V-204 posture, applied to
labels).

Keys are written with `[]` where a path carries a list index, and a template may
use `{n}` for the 1-based position, so `position.legs[1].strike_selection.value`
renders as "leg 2 strike". The table was built from the 67 distinct paths that
actually occur across every stored spec in both the local and production
databases, not from reading the schema: the schema permits far more than anyone
has run, and labels for fields nobody uses are labels nobody checks.
"""

from __future__ import annotations

import re

_INDEX = re.compile(r"\[(\d+)\]")

FIELD_LABELS: dict[str, str] = {
    # the run window and its mechanics
    "backtest.start": "window start",
    "backtest.end": "window end",
    "backtest.initial_capital": "starting capital",
    "backtest.clock": "clock",
    "backtest.resolution": "data resolution",
    "backtest.seed": "random seed",
    # fills and liquidity
    "costs.commission_per_contract": "commission per contract",
    "costs.liquidity_mode": "liquidity filter",
    "costs.max_spread_pct": "max spread",
    "costs.min_open_interest": "min open interest",
    "costs.min_volume": "min volume",
    "costs.slippage_half_spread_fraction": "slippage on buys",
    "costs.slippage_half_spread_fraction_sell": "slippage on sells",
    # entry
    "entry.conditions[].indicator": "entry condition {n} indicator",
    "entry.conditions[].operator": "entry condition {n} comparison",
    "entry.conditions[].value": "entry condition {n} threshold",
    "entry.conditions[].period": "entry condition {n} period",
    "entry.conditions[].timeframe": "entry condition {n} timeframe",
    "entry.conditions[].params": "entry condition {n} parameters",
    "entry.intraday_scan": "intraday scanning",
    "entry.max_concurrent_positions": "max concurrent positions",
    "entry.schedule.frequency": "entry frequency",
    "entry.schedule.day_of_week": "entry day of week",
    "entry.schedule.day_of_month": "entry day of month",
    "entry.schedule.time_of_day": "entry time of day",
    # scaling in
    "entry.scale_in": "scale-in",
    "entry.scale_in.mode": "scale-in mode",
    "entry.scale_in.basket": "scale-in basket",
    "entry.scale_in.max_total_contracts": "scale-in contract cap",
    "entry.scale_in.stop_adding_on.mode": "scale-in stop rule",
    "entry.scale_in.rearm.indicator": "scale-in re-arm indicator",
    "entry.scale_in.rearm.operator": "scale-in re-arm comparison",
    "entry.scale_in.rearm.value": "scale-in re-arm threshold",
    "entry.scale_in.rearm.period": "scale-in re-arm period",
    "entry.scale_in.rearm.timeframe": "scale-in re-arm timeframe",
    "entry.scale_in.rearm.params": "scale-in re-arm parameters",
    "entry.scale_in.rungs[].add_contracts": "scale-in rung {n} contracts",
    "entry.scale_in.rungs[].indicator": "scale-in rung {n} indicator",
    "entry.scale_in.rungs[].operator": "scale-in rung {n} comparison",
    "entry.scale_in.rungs[].value": "scale-in rung {n} threshold",
    "entry.scale_in.rungs[].period": "scale-in rung {n} period",
    "entry.scale_in.rungs[].timeframe": "scale-in rung {n} timeframe",
    "entry.scale_in.rungs[].params": "scale-in rung {n} parameters",
    # exit
    "exit.profit_target_pct": "profit target",
    "exit.stop_loss_pct": "stop loss",
    "exit.time_exit_dte": "time exit",
    "exit.close_at_time": "close at time",
    "exit.delta_stop_abs": "delta stop",
    "exit.theta_harvest": "theta harvest",
    "exit.conditions": "exit conditions",
    # the position itself
    "position.structure": "structure",
    "position.expiration_selection.target_dte": "target DTE",
    "position.expiration_selection.min_dte": "min DTE",
    "position.expiration_selection.max_dte": "max DTE",
    "position.legs[].strike_selection.value": "leg {n} strike",
    "position.legs[].strike_selection.method": "leg {n} strike rule",
    "position.legs[].strike_selection.reference_leg": "leg {n} strike reference",
    "position.legs[].ratio": "leg {n} ratio",
    "position.legs[].right": "leg {n} right",
    "position.legs[].side": "leg {n} side",
    "position.max_vega_per_contract": "max vega per contract",
    # sizing and identity
    "sizing.method": "sizing method",
    "sizing.value": "position size",
    "underlying.ticker": "ticker",
    "meta.name": "run name",
    "meta.description_raw": "original description",
    "spec_version": "spec version",
}


def normalize(path: str) -> str:
    """The lookup key for a path: list indices collapse to `[]`.

    `position.legs[2].ratio` and `position.legs[0].ratio` are the same FIELD
    wearing different positions, and the table should not need an entry per leg
    anyone might add.
    """
    return _INDEX.sub("[]", path)


def label_for(path: str) -> str | None:
    """The human label for a diff row's field path, or None if unmapped.

    None is a normal answer, not a failure: the caller renders the raw path,
    which is always correct and always connects to the diff, and counts the gap.
    """
    template = FIELD_LABELS.get(normalize(path))
    if template is None:
        return None
    if "{n}" not in template:
        return template
    found = _INDEX.findall(path)
    position = int(found[0]) + 1 if found else 1
    return template.format(n=position)


def label_rows(rows: list[dict[str, object]]) -> tuple[list[dict[str, object]], int]:
    """Copies of `rows` carrying a `label`, plus the count that had none.

    COPIES, deliberately. The same row objects are read by the lock check, the
    zero-edit guard and the reconciler (V-162: one comparison, four consumers),
    and a presentation concern must not reach into the list those share.
    """
    labelled: list[dict[str, object]] = []
    unlabeled = 0
    for row in rows:
        field = str(row.get("field", ""))
        label = label_for(field)
        if label is None:
            unlabeled += 1
        labelled.append({**row, **({"label": label} if label else {})})
    return labelled, unlabeled
