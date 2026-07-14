"""Gauntlet stages 1–6 (TECH-SPEC §6). Every stage is deterministic given
(spec, data, seed); stochastic steps take the run seed. Anything that
cannot be computed honestly reports None or "not meaningful" — never a
fabricated number.
"""

from __future__ import annotations

import copy
import logging
import math
from collections.abc import Callable
from datetime import date
from statistics import NormalDist
from typing import Any

import numpy as np

from app.engine.engine import run_engine
from app.engine.market import IntradayProvider, MarketStore
from app.engine.types import MULT, RungFill, RunResult
from app.honesty.report import (
    Concentration,
    Coverage,
    DataConfidence,
    Dsr,
    HonestyReport,
    LadderDepth,
    LadderRung,
    LadderTier,
    LiquidityProfile,
    MonteCarlo,
    OosSplit,
    PairConfidence,
    ParamSweep,
    RegimeSample,
    ResolutionBucket,
    ResolutionSplit,
    ScaleInHonesty,
    Sensitivity,
    SessionBucket,
    SessionSplit,
    UnlockConditions,
    UnlockNeed,
    WalkForward,
    WalkForwardFold,
)
from app.models.spec import Clock, StrategySpec, StrikeMethod

log = logging.getLogger("skeptic.honesty")

_N = NormalDist()
ANNUAL = math.sqrt(252)

# Owner-set floor (2026-07-02, was 30): below this many closed trades the
# verdict is withheld as insufficient evidence (CLAUDE.md guardrail #5).
MIN_TRADES = 15

# Below this share of the REQUESTED window carrying a usable options chain,
# the run is a "multi-year backtest" that actually tested a handful of days —
# the seventeen-fills self-deception. Capped at insufficient_evidence
# (diagnostics/SEVENTEEN.md §4). Changed only in a reviewed session, never
# at runtime (guardrail: runtime code never modifies scoring rules).
COVERAGE_MIN_RATIO = 0.5

# Liquidity REPORTING thresholds (D1b): when crossed, the run's verdict adds
# a caveat line — these gate disclosure, never scoring. Changed only in a
# reviewed session, like every threshold in this file.
LIQ_PENALIZED_MATERIAL_SHARE = 0.20  # ≥ this share filled above base slip
LIQ_SKIPPED_MATERIAL_COUNT = 5  # ≥ this many entries refused by the gates

# Concentration REPORTING thresholds (D1d): flag when the top 5% of marked
# sessions carry ≥ half the gross |daily P&L|. Reported flag + verdict
# reason, NOT a trust cap (promoting it needs evidence, in a reviewed
# session). Gamma coincidence uses the top-|gamma| decile.
CONC_TOP_DAY_FRACTION = 0.05
CONC_MATERIAL_SHARE = 0.50
CONC_GAMMA_DECILE = 0.90
CONC_MIN_SESSIONS = 40  # fewer marked sessions → the split is noise itself

# Scale-in martingale defenses (D5c). Reviewed thresholds — a ladder that
# trips either HARD cap is refused (insufficient_evidence), the same posture
# the D5a interlock held while these were pending. Changed only in a reviewed
# session, like every threshold in this file.
RUIN_DRAW_THRESHOLD = 0.30  # account drawdown fraction that counts as "ruin"
RUIN_TAIL_PROB = 0.10  # P(resampled max drawdown > threshold) that HARD-caps
RUIN_MIN_BASKETS = 5  # below this the ruin resample is not meaningful
BASKET_CONC_SHARE = 0.50  # top basket's share of gross |basket P&L| → flag (reported)

# 5-min gauntlet cost control (D2d, owner amendment 6 — decided on the D2b
# benchmark): a full-history 5-min run measured 136s (2,252 sessions, no
# conditions) to ~200s (with conditions, after the D2c O(n²) fix); a ±20%
# sweep is ~20 re-runs ≈ 45–70 min — untenable synchronously on the Railway
# box. The sweep therefore re-runs on the TRAILING window below (≈1 year,
# ~23s/run → ~8 min dev, disclosed in the report and the verdict). Parameter
# fragility is a local property; the full-history verdict still comes from
# the full-history run. docs/HONESTY.md carries the arithmetic.
SENS_5MIN_WINDOW_SESSIONS = 252
# entry-time nudge shifts, minutes (owner brief: ±15 / ±30)
NUDGE_SHIFTS_MIN = [-30, -15, 0, 15, 30]

# session-split bucket boundaries, minutes from midnight ET (D2d)
SESSION_OPEN_END = 10 * 60 + 30  # 09:30–10:29 = open
SESSION_MID_END = 15 * 60  # 10:30–14:59 = mid; 15:00+ = close


def _returns(equity: list[float]) -> list[float]:
    return [b / a - 1.0 for a, b in zip(equity, equity[1:], strict=False) if a > 0]


def _sharpe(rets: list[float]) -> float | None:
    if len(rets) < 5:
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    std = math.sqrt(var)
    if std == 0:
        return None
    return mean / std * ANNUAL


# ------------------------------------------------------- stage 1: IS / OOS
def oos_split(result: RunResult) -> OosSplit:
    n = len(result.equity)
    cut = int(n * 0.7)
    split_date = result.dates[cut - 1] if 0 < cut <= n else result.effective_end

    is_eq, oos_eq = result.equity[:cut], result.equity[max(cut - 1, 0):]
    is_sharpe = _sharpe(_returns(is_eq))
    oos_sharpe = _sharpe(_returns(oos_eq))
    is_return = (is_eq[-1] / is_eq[0] - 1.0) if len(is_eq) > 1 and is_eq[0] > 0 else None
    oos_return = (oos_eq[-1] / oos_eq[0] - 1.0) if len(oos_eq) > 1 and oos_eq[0] > 0 else None

    closed = [t for t in result.trades if t.pl is not None]
    is_trades = sum(1 for t in closed if t.day <= split_date)
    oos_trades = len(closed) - is_trades

    degradation: float | None = None
    sign_flip = False
    flagged = False
    if is_sharpe is not None and oos_sharpe is not None:
        sign_flip = is_sharpe > 0 > oos_sharpe
        if is_sharpe > 0:
            degradation = oos_sharpe / is_sharpe
            flagged = degradation < 0.5 or sign_flip
        else:
            flagged = True  # negative in-sample was never evidence of edge
    else:
        flagged = True  # can't demonstrate survival → does not survive

    return OosSplit(
        split_date=split_date.isoformat(),
        is_sharpe=is_sharpe,
        oos_sharpe=oos_sharpe,
        is_return=is_return,
        oos_return=oos_return,
        is_trades=is_trades,
        oos_trades=oos_trades,
        degradation=degradation,
        sign_flip=sign_flip,
        flagged=flagged,
    )


# --------------------------------------------------- stage 2: walk-forward
def walk_forward(result: RunResult) -> WalkForward:
    """Rolling test windows over the equity curve (~2 months = 42 sessions,
    auto-shrunk proportionally on short histories). The spec has fixed
    parameters, so folds measure CONSISTENCY of the same rules across
    periods; below 3 folds the stage refuses (TECH-SPEC §6.2)."""
    n = len(result.equity)
    test_len = 42
    if n < 3 * test_len:
        test_len = max(21, n // 3)
    if n < 3 * test_len or n < 63:
        return WalkForward(
            meaningful=False,
            note="walk-forward not meaningful at this history length",
            test_sessions=test_len,
        )

    closed = [t for t in result.trades if t.pl is not None]
    folds: list[WalkForwardFold] = []
    for start in range(0, n - test_len + 1, test_len):
        end = min(start + test_len, n) - 1
        eq0, eq1 = result.equity[start], result.equity[end]
        d0, d1 = result.dates[start], result.dates[end]
        # FX.4: a fold's minute-session share is disclosed IN the run —
        # out-performance coinciding with a high share must be readable as
        # resolution-flavored, never silently as regime robustness (the
        # deeper fold redesign is a flagged future pass; docs/HONESTY.md)
        minute_share: float | None = None
        if result.resolution_by_session:
            fold_days = result.dates[start:end + 1]
            labeled = [result.resolution_by_session.get(d) for d in fold_days]
            covered = [x for x in labeled if x is not None]
            if covered:
                minute_share = round(
                    sum(1 for x in covered if x == "minute") / len(covered), 4)
        folds.append(
            WalkForwardFold(
                start=d0.isoformat(),
                end=d1.isoformat(),
                ret=(eq1 / eq0 - 1.0) if eq0 > 0 else 0.0,
                trades=sum(1 for t in closed if d0 <= t.day <= d1),
                minute_share=minute_share,
            )
        )
    positive = sum(1 for f in folds if f.ret > 0)
    return WalkForward(
        meaningful=True,
        test_sessions=test_len,
        folds=folds,
        consistency=positive / len(folds) if folds else None,
    )


# ---------------------------------------------------- stage 3: Monte Carlo
def monte_carlo(
    result: RunResult, initial_capital: float, resamples: int = 1000, block: int = 5
) -> MonteCarlo:
    """Circular block bootstrap (block ≈ 5 trades to respect clustering) on
    per-trade P/L. Seeded → same spec + data + seed = identical output."""
    pls = np.array([t.pl for t in result.trades if t.pl is not None], dtype=float)
    n = len(pls)
    base = MonteCarlo(
        resamples=resamples, block=block, seed=result.seed, trades=n,
        terminal_p5=None, terminal_p50=None, terminal_p95=None,
        max_drawdown_p50=None, max_drawdown_p95=None, p_loss=None,
    )
    if n < 5:
        return base

    rng = np.random.RandomState(result.seed)
    n_blocks = math.ceil(n / block)
    starts = rng.randint(0, n, size=(resamples, n_blocks))
    # circular blocks: indices wrap around the trade sequence
    offsets = np.arange(block)
    idx = (starts[:, :, None] + offsets[None, None, :]) % n
    sampled = pls[idx.reshape(resamples, -1)[:, :n]]  # (resamples, n)

    paths = initial_capital + np.cumsum(sampled, axis=1)
    terminals = paths[:, -1]
    running_peak = np.maximum.accumulate(np.maximum(paths, 1e-9), axis=1)
    drawdowns = 1.0 - paths / running_peak
    max_dd = drawdowns.max(axis=1)

    # fan: percentile of equity across paths at each trade step, downsampled
    steps = np.linspace(0, n - 1, num=min(n, 60)).astype(int)
    fan = np.percentile(paths[:, steps], [5, 50, 95], axis=0)

    return MonteCarlo(
        resamples=resamples,
        block=block,
        seed=result.seed,
        trades=n,
        terminal_p5=float(np.percentile(terminals, 5)),
        terminal_p50=float(np.percentile(terminals, 50)),
        terminal_p95=float(np.percentile(terminals, 95)),
        max_drawdown_p50=float(np.percentile(max_dd, 50)),
        max_drawdown_p95=float(np.percentile(max_dd, 95)),
        p_loss=float(np.mean(terminals < initial_capital)),
        fan_p5=[round(float(v), 2) for v in fan[0]],
        fan_p50=[round(float(v), 2) for v in fan[1]],
        fan_p95=[round(float(v), 2) for v in fan[2]],
    )


# ----------------------------------------------------- stage 4: sensitivity
Setter = Callable[[StrategySpec, float], None]


def _mutations(
    spec: StrategySpec,
) -> tuple[list[tuple[str, list[float], int, Setter]], str | None]:
    """((name, values ±20% in 5 steps, base index, setter) per numeric
    param present, conditions-disclosure note). The base index marks the
    as-specced value inside `values`."""
    out: list[tuple[str, list[float], int, Setter]] = []
    factors = [0.8, 0.9, 1.0, 1.1, 1.2]

    lead = spec.position.legs[0].strike_selection
    if lead.method is StrikeMethod.DELTA:
        base = lead.value / 100.0 if lead.value > 1 else lead.value

        def set_delta(s: StrategySpec, v: float) -> None:
            for leg in s.position.legs:
                if leg.strike_selection.method is StrikeMethod.DELTA:
                    leg.strike_selection.value = v

        values = [round(min(0.95, max(0.03, base * f)), 4) for f in factors]
        out.append(("delta", values, 2, set_delta))

    dte = spec.position.expiration_selection.target_dte

    def set_dte(s: StrategySpec, v: float) -> None:
        t = int(round(v))
        s.position.expiration_selection.target_dte = max(1, min(90, t))
        s.position.expiration_selection.min_dte = max(1, t - 10)
        s.position.expiration_selection.max_dte = min(120, t + 15)

    dte_values = [float(max(1, min(90, int(round(dte * f))))) for f in factors]
    dte_base = 2
    if len(set(dte_values)) < 5:
        # multiplicative steps collapse at short tenors (±20% of 2 days is
        # still 2 days) — sweep whole days instead, keeping the specced
        # value inside the window
        start = max(1, min(dte - 2, 86))
        dte_values = [float(start + i) for i in range(5)]
        dte_base = dte - start
    out.append(("dte", dte_values, dte_base, set_dte))

    if spec.exit.profit_target_pct is not None:
        base_pt = spec.exit.profit_target_pct

        def set_pt(s: StrategySpec, v: float) -> None:
            s.exit.profit_target_pct = v

        out.append(("profit_target", [round(base_pt * f, 2) for f in factors], 2, set_pt))

    if spec.exit.stop_loss_pct is not None:
        base_sl = spec.exit.stop_loss_pct

        def set_sl(s: StrategySpec, v: float) -> None:
            s.exit.stop_loss_pct = v

        out.append(("stop_loss", [round(base_sl * f, 2) for f in factors], 2, set_sl))

    # F8: sweep the ENTRY-CONDITION thresholds too — a strategy overfit to
    # "skew > 5" or "RSI < 30" must be caught, not just strike/dte/exits
    # (owner decisions 2026-07-08). Cap at the first 3 conditions (cost on
    # the serialized engine); SKIP sign-at-zero conditions (the sign IS the
    # signal — nothing to perturb, opaque units); rank forms sweep as 0-100.
    # Small thresholds on wide scales sweep an absolute family-scale grid
    # instead of ±20% (_COND_FAMILY_FLOORS), disclosed in the note.
    # Entry conditions only in v1 (exit/rung deferred, disclosed).
    note = _append_condition_sweeps(spec, out, factors)
    return out, note


# Scale-aware sweep floors (PR #97 review follow-up, 2026-07-14). ±20% of a
# SMALL threshold on a WIDE natural scale probes almost nothing: "skew_25d
# > 0.3" would sweep 0.24…0.36 of a vol-point scale whose specced examples
# run to 5+, and five near-identical cells read as a FALSE PLATEAU — the
# honesty layer blessing exactly the fragile threshold it exists to catch.
# Same collapse-guard idea as the dte whole-day fallback in _mutations:
# when the multiplicative step (10% of |threshold| per cell) falls under
# the family's floor, sweep an ABSOLUTE grid of floor-sized steps instead.
# ONE rule grounds every floor: floor = 10% of the family's stated
# reference magnitude, so a small threshold is probed exactly as widely as
# a reference-scale threshold already is by ±20% — never finer. Keyed by
# STRING so vocabulary that lands in a different merge order (ivx_zscore_1y,
# spec v8 on PR #97) picks its floor up the moment it exists. The second
# tuple slot is the family's lower bound (None = signed scale).
_COND_FAMILY_FLOORS: dict[str, tuple[float, float | None]] = {
    # 0-100 oscillators / percentiles / ranks — reference 20, the
    # bottom-quintile edge (the canonical oversold / low-rank threshold)
    "rsi": (2.0, 0.0),
    "iv_percentile_1y": (2.0, 0.0),
    "ivx_rank_1y": (2.0, 0.0),
    "gex_rank_1y": (2.0, 0.0),
    "dex_rank_1y": (2.0, 0.0),
    "net_premium_rank_1y": (2.0, 0.0),
    "market_tide_rank_1y": (2.0, 0.0),
    "nope_rank_1y": (2.0, 0.0),
    # z-scores — reference 2.5σ, the outer edge of the ±3σ usable band
    "ivx_zscore_1y": (0.25, None),
    # vol points (IV/HV units) — reference 5, the repo's own "skew > 5"
    # F8 example (docs/HONESTY.md); signed scales, no bound
    "skew_25d": (0.5, None),
    "term_structure_slope": (0.5, None),
    "hv_iv_spread_30d": (0.5, None),
    # percent-of-price — reference 2.5% (a large intraday/pin distance)
    "price_vs_sma_pct": (0.25, None),
    "price_vs_ema_pct": (0.25, None),
    "price_vs_vwap_pct": (0.25, None),
    "max_pain_distance_pct": (0.25, None),
    # drawdown % — reference 10, a correction's textbook definition
    "drawdown_from_high_pct": (1.0, 0.0),
    # vol levels (VIX/IVX/HV points) — reference 20, the same high-VIX
    # regime line regime_sample already draws
    "vix_level": (2.0, 0.0),
    "ivx_level_30d": (2.0, 0.0),
    "realized_vol_20d": (2.0, 0.0),
    # flow ratio — reference 1.0, put/call parity
    "put_call_flow_ratio": (0.1, 0.0),
    # DELIBERATELY absent: sma/ema (absolute price — % of price IS the
    # scale) and the *_level vendor-unit families (nonzero thresholds are
    # parser-refused; inventing an absolute step for units we refused to
    # let users state would be the invented-convention sin ourselves).
}


def _condition_grid(
    base: float, indicator: str, factors: list[float]
) -> tuple[list[float], int, bool]:
    """The 5 threshold cells for one condition: (values, base index,
    floor-engaged flag). ±20% multiplicative wherever that moves the
    threshold by at least the family floor per cell; when the floor binds,
    an absolute grid of ±2 floor-steps, shifted UP by whole steps if it
    would cross the family's lower bound (mirroring the dte guard — the
    specced value always stays ON the grid). Both paths produce exactly 5
    cells and one classifier pass: the sweep's engine-run cost and its
    multiple-testing arithmetic never change with the grid shape."""
    floor, lo = _COND_FAMILY_FLOORS.get(indicator, (0.0, None))
    is_rank = indicator.endswith("_rank_1y") or indicator == "iv_percentile_1y"
    # factors are 0.1-spaced, so the multiplicative per-cell step is
    # 10% of |base| (0.1 literal: deriving it from float subtraction of
    # the factors makes 5×0.1 land just under a 0.5 floor)
    if floor <= 0.0 or abs(base) * 0.1 >= floor:
        vals = [base * f for f in factors]
        if is_rank:
            vals = [min(100.0, max(0.0, v)) for v in vals]
        return [round(v, 4) for v in vals], 2, False
    vals = [base + (k - 2) * floor for k in range(5)]
    shift = 0
    if lo is not None and vals[0] < lo:
        shift = math.ceil((lo - vals[0]) / floor)
        vals = [v + shift * floor for v in vals]
    # base > lo for any real threshold (value == 0 is sign-skipped), so
    # shift ≤ 2 and the specced value sits at index 2 - shift
    return [round(v, 4) for v in vals], 2 - shift, True


def _append_condition_sweeps(
    spec: StrategySpec, out: list[tuple[str, list[float], int, Setter]],
    factors: list[float],
) -> str | None:
    """Add up to 3 entry-condition threshold sweeps to `out`; return a
    disclosure note for the conditions that were skipped or capped, and
    for any swept on an absolute family-scale grid instead of ±20%."""
    conds = spec.entry.conditions
    swept = 0
    capped = 0  # eligible conditions left unswept by the 3-cap
    skipped_sign: list[str] = []
    floored: list[str] = []  # swept on an absolute family-scale grid
    used_names: set[str] = set()
    # examine EVERY condition — a sign test past the cap must still be
    # disclosed (review finding F8 #1: silently omitting an untested gate
    # is the "absence misread as a free pass" failure this exists to
    # prevent), and the cost-cap count must include everything left out
    for i, cond in enumerate(conds):
        if cond.value == 0:
            # a sign test (e.g. gex_level > 0) has no threshold to perturb
            skipped_sign.append(cond.indicator.value)
            continue
        if swept >= 3:
            capped += 1
            continue
        name = cond.indicator.value
        vals, base_index, floor_hit = _condition_grid(cond.value, name, factors)
        if floor_hit:
            # disclosure numerals must stay grounded (guardrail #4): the
            # grid ENDPOINTS are report values the validator can find;
            # the step itself might not be
            floored.append(f"{name} swept {vals[0]:g}…{vals[-1]:g}")

        # unique sweep name: indicator, else +operator, else +index — two
        # conditions can share an indicator (the max-pain band pair) and a
        # degenerate spec can share both (review #2)
        sweep_name = f"cond_{name}"
        if sweep_name in used_names:
            sweep_name = f"cond_{name}_{cond.operator.value}"
        if sweep_name in used_names:
            sweep_name = f"cond_{name}_{i}"
        used_names.add(sweep_name)

        def _make_setter(idx: int) -> Setter:
            def _set(s: StrategySpec, v: float) -> None:
                s.entry.conditions[idx].value = v
            return _set

        out.append((sweep_name, vals, base_index, _make_setter(i)))
        swept += 1

    parts: list[str] = []
    if floored:
        parts.append(
            f"{', '.join(floored)} — absolute family-scale steps (a ±20% "
            "probe of a threshold this small spans too little of the "
            "indicator's range to test it)"
        )
    if skipped_sign:
        uniq = sorted(set(skipped_sign))
        parts.append(
            f"{', '.join(uniq)} {'is a' if len(uniq) == 1 else 'are'} sign "
            f"test{'' if len(uniq) == 1 else 's'} (threshold 0 — nothing to "
            "perturb), not swept"
        )
    if capped:
        parts.append(f"{capped} further condition"
                     f"{'' if capped == 1 else 's'} not swept (cost cap)")
    return "; ".join(parts) or None


def _classify(sharpes: list[float | None]) -> str | None:
    """Plateau if the median neighbor Sharpe holds ≥ 70% of the peak,
    else cliff (TECH-SPEC §6.4)."""
    valid = [s for s in sharpes if s is not None]
    if len(valid) < 3:
        return None
    peak = max(valid)
    neighbors = sorted(v for v in valid if v != peak) or valid
    median = neighbors[len(neighbors) // 2]
    if peak <= 0:
        return "cliff"  # nothing to stand on anywhere
    return "plateau" if median >= 0.7 * peak else "cliff"


def _sweep_base_spec(
    spec: StrategySpec, intraday: IntradayProvider | None
) -> tuple[StrategySpec, str | None]:
    """The spec every sweep cell re-runs (5-min clock: bounded trailing
    window per SENS_5MIN_WINDOW_SESSIONS — see the constant's rationale).
    Every cell INCLUDING the base re-runs on the same window: cells stay
    comparable, and the note disclosing the window rides the report."""
    if spec.backtest.clock is not Clock.FIVE_MIN or intraday is None:
        return spec, None
    covered = intraday.sessions()
    # respect the requested window: the sweep subsamples the run's OWN
    # history, never sessions outside what the user asked to test
    if spec.backtest.start is not None:
        covered = [d for d in covered if d >= spec.backtest.start]
    if spec.backtest.end is not None:
        covered = [d for d in covered if d <= spec.backtest.end]
    if len(covered) <= SENS_5MIN_WINDOW_SESSIONS:
        return spec, None
    window = covered[-SENS_5MIN_WINDOW_SESSIONS:]
    base = copy.deepcopy(spec)
    base.backtest.start = window[0]
    note = (
        f"5-min sweep re-runs on the trailing {SENS_5MIN_WINDOW_SESSIONS} "
        f"covered sessions ({window[0]} →) — gauntlet cost is benchmark-bound "
        "(docs/HONESTY.md)"
    )
    return base, note


def sensitivity(
    spec: StrategySpec, store: MarketStore, intraday: IntradayProvider | None = None
) -> Sensitivity:
    """Perturb each numeric parameter ±20% in 5 steps, re-run the engine,
    classify the optimum (plateau/cliff). At the 5-min clock the sweep also
    nudges the ENTRY TIME ±15/±30 minutes (D2d, per the brief): an edge that
    only exists at exactly one minute of the day is noise — classified with
    the same plateau/cliff rules as every parameter."""
    sweep_spec, window_note = _sweep_base_spec(spec, intraday)
    mutations, conditions_note = _mutations(sweep_spec)
    sweeps: list[ParamSweep] = []
    for name, values, base_index, setter in mutations:
        sharpes: list[float | None] = []
        for v in values:
            mutated = copy.deepcopy(sweep_spec)
            setter(mutated, v)
            try:
                r = run_engine(mutated, store, intraday)
                sharpes.append(_sharpe(_returns(r.equity)))
            except Exception:
                sharpes.append(None)
        sweeps.append(
            ParamSweep(
                name=name,
                values=[float(v) for v in values],
                sharpes=sharpes,
                base_index=base_index,
                classification=_classify(sharpes),
            )
        )

    if spec.backtest.clock is Clock.FIVE_MIN and intraday is not None:
        nudge_sharpes: list[float | None] = []
        has_tod = spec.entry.schedule.time_of_day is not None
        for shift_min in NUDGE_SHIFTS_MIN:
            if shift_min < 0 and not has_tod:
                # entries can't move before the signal/session start —
                # honest None, never a fabricated cell
                nudge_sharpes.append(None)
                continue
            try:
                r = run_engine(sweep_spec, store, intraday,
                               entry_shift_bars=shift_min // 5)
                nudge_sharpes.append(_sharpe(_returns(r.equity)))
            except Exception:
                nudge_sharpes.append(None)
        sweeps.append(
            ParamSweep(
                name="entry_time",
                values=[float(v) for v in NUDGE_SHIFTS_MIN],
                sharpes=nudge_sharpes,
                base_index=NUDGE_SHIFTS_MIN.index(0),
                classification=_classify(nudge_sharpes),
            )
        )

    classified = [s.classification for s in sweeps if s.classification is not None]
    verdict: str | None = None
    if classified:
        verdict = "cliff" if "cliff" in classified else "plateau"
    return Sensitivity(params=sweeps, verdict=verdict, window_note=window_note,
                       conditions_note=conditions_note)


def session_split(result: RunResult) -> SessionSplit:
    """Bucket entries by their bar time (D2d): open 09:30–10:29, mid
    10:30–14:59, close 15:00+. P/L attributes to the entry's bucket via the
    position's realized P/L. Daily runs have no bar times — not meaningful."""
    if result.clock != "5min":
        return SessionSplit(meaningful=False, note="daily clock has no session buckets")
    pl_by_pid: dict[int, float] = {}
    for t in result.trades:
        if t.pl is not None and t.position_id is not None:
            pl_by_pid[t.position_id] = t.pl
    buckets = {"open_": SessionBucket(), "mid": SessionBucket(), "close": SessionBucket()}
    n = 0
    for t in result.trades:
        if t.action != "OPEN" or t.bar_time is None or t.position_id is None:
            continue
        h, m = t.bar_time.split(":")
        minutes = int(h) * 60 + int(m)
        key = ("open_" if minutes < SESSION_OPEN_END
               else "mid" if minutes < SESSION_MID_END else "close")
        b = buckets[key]
        pl = pl_by_pid.get(t.position_id)
        buckets[key] = SessionBucket(
            trades=b.trades + 1,
            wins=b.wins + (1 if (pl or 0.0) > 0 else 0),
            pl=round(b.pl + (pl or 0.0), 2),
        )
        n += 1
    if n == 0:
        return SessionSplit(meaningful=False, note="no bar-stamped entries")
    return SessionSplit(meaningful=True, open_=buckets["open_"],
                        mid=buckets["mid"], close=buckets["close"])


# --------------------------------- FX.4: mixed-resolution defense (owner 4a)
RESOLUTION_MIN_SESSIONS = 15  # each subset needs this many covered sessions


def resolution_split(result: RunResult) -> ResolutionSplit:
    """The headline recomputed on the 5-MIN-ONLY sub-window from recorded
    per-session returns and closed trades — cheap, no re-run. Judged only
    at real-evidence floors (both subsets ≥ 15 sessions AND the 5-min
    subset ≥ MIN_TRADES closed trades — the SAME evidentiary bar any main
    result must clear); a sign flip then caps trust hard: a resolution
    flip is a data-VALIDITY finding, not a robustness signal. Only the
    optimistic direction caps (full-run edge positive, 5-min-only
    negative) — a negative full run blesses nothing to protect."""
    by_session = result.resolution_by_session
    if result.clock != "5min" or not by_session:
        return ResolutionSplit(
            meaningful=False, note="no per-session resolution record")
    kinds = set(by_session.values())
    if not {"minute", "five_min"} <= kinds:
        return ResolutionSplit(
            meaningful=False,
            note="single-resolution run — nothing to cross-check")

    # per-session returns attributed to the session's own grid; sessions
    # outside the record (EOD-fallback gap days) are counted, not judged
    rets: dict[str, list[float]] = {"minute": [], "five_min": []}
    windows: dict[str, list[date]] = {"minute": [], "five_min": []}
    eod_fallback = 0
    for i in range(1, len(result.dates)):
        d = result.dates[i]
        label = by_session.get(d)
        prev_eq = result.equity[i - 1]
        if label not in rets:
            eod_fallback += 1
            continue
        if prev_eq > 0:
            rets[label].append(result.equity[i] / prev_eq - 1.0)
            windows[label].append(d)
    # the first equity date has no return; count its session for the window
    # (or as a fallback day when it carries no label — review finding)
    if result.dates:
        d0 = result.dates[0]
        label0 = by_session.get(d0)
        if label0 in windows and d0 not in windows[label0]:
            windows[label0].insert(0, d0)
        elif label0 not in windows:
            eod_fallback += 1

    # closed trades attributed to their REALIZATION day. NOTE: a position
    # straddling the resolution boundary accrues its P&L across BOTH
    # subsets' marks while its closed-trade count (and `pl`) lands on one
    # day — a bucket's `pl` and its mark-based `sharpe` can diverge for
    # boundary-straddlers. The flip test is mark-to-market and internally
    # consistent; `trades` is an evidence floor, not a P&L attribution
    # (docs/HONESTY.md).
    trades: dict[str, int] = {"minute": 0, "five_min": 0}
    pl: dict[str, float] = {"minute": 0.0, "five_min": 0.0}
    for t in result.trades:
        if t.pl is None:
            continue
        label = by_session.get(t.day)
        if label in trades:
            trades[label] += 1
            pl[label] += t.pl

    def bucket(label: str) -> ResolutionBucket:
        days = windows[label]
        return ResolutionBucket(
            sessions=len(days),
            trades=trades[label],
            pl=round(pl[label], 2),
            sharpe=_sharpe(rets[label]),
            first=days[0].isoformat() if days else None,
            last=days[-1].isoformat() if days else None,
        )

    five = bucket("five_min")
    minute = bucket("minute")
    full = _sharpe(_returns(result.equity))
    judged = (
        five.sessions >= RESOLUTION_MIN_SESSIONS
        and minute.sessions >= RESOLUTION_MIN_SESSIONS
        and five.trades >= MIN_TRADES
    )
    sign_flip = bool(
        judged
        and full is not None and full > 0
        and five.sharpe is not None and five.sharpe < 0
    )
    note = None
    if not judged:
        note = ("mixed resolution, but the sub-windows are too thin to "
                "cross-check (5-min: "
                f"{five.sessions} sessions / {five.trades} trades; minute: "
                f"{minute.sessions} sessions) — disclosed, not judged")
    return ResolutionSplit(
        meaningful=True, note=note, judged=judged, full_sharpe=full,
        five_min=five, minute=minute, eod_fallback_sessions=eod_fallback,
        sign_flip=sign_flip,
    )


# ------------------------------------------ D5b/D5c: scale-in ladder shared
def _fill_marginal(exit_px: float, fill_price: float, qty: int, commission: float) -> float:
    """P&L attributable to one rung fill: the whole basket exits at ONE price,
    so a fill of `qty` at `fill_price` contributes (exit − fill)·qty·MULT, less
    its entry commission and its share of the exit commission (2·commission·qty).
    A basket's fills' marginals sum EXACTLY to its realized P&L."""
    return (exit_px - fill_price) * qty * MULT - 2 * commission * qty


def _ladder_baskets(result: RunResult, spec: StrategySpec) -> list[dict[str, Any]] | None:
    """Per-basket records for the depth/defense stages: realized pl, total
    contracts, cost, the derived exit price, the deepest rung reached, and the
    fills. None when the spec is not a ladder or no basket closed. Baskets
    still open at run end carry no realized P&L and are skipped (adds are never
    counted as trades — the sample counter uses closed baskets, D5c)."""
    si = spec.entry.scale_in
    if si is None:
        return None
    fills_by_pid: dict[int, list[RungFill]] = {}
    for rf in result.rung_fills:
        fills_by_pid.setdefault(rf.basket_pid, []).append(rf)
    pl_by_pid = {
        t.position_id: t.pl
        for t in result.trades
        if t.pl is not None and t.position_id is not None
    }
    commission = spec.costs.commission_per_contract
    baskets: list[dict[str, Any]] = []
    for pid, bfills in fills_by_pid.items():
        pl = pl_by_pid.get(pid)
        if pl is None:
            continue
        total_qty = sum(f.qty for f in bfills)
        if total_qty <= 0:
            continue
        cost = sum(f.fill_price * f.qty * MULT for f in bfills)
        # exit price from the realized P&L identity:
        #   pl = exit·total·MULT − cost − commission·(entry_qty + total_qty),
        #   entry_qty across a basket's fills == total_qty
        exit_px = (pl + cost + 2 * commission * total_qty) / (total_qty * MULT)
        baskets.append({
            "pl": pl, "total_qty": total_qty, "cost": cost, "exit_px": exit_px,
            "max_rung": max(f.rung_index for f in bfills), "fills": bfills,
        })
    return baskets or None


# ------------------------------------------ D5b: ladder depth attribution
def ladder_depth_attribution(
    result: RunResult, spec: StrategySpec
) -> LadderDepth | None:
    """Group realized basket P&L by the MAX rung depth each basket reached
    (the per-tier table — iVol's P&L-by-ladder-depth), and attribute P&L to
    the fills added AT each depth (the marginal-rung analysis — are the deep
    adds themselves net negative?). Both views sum to the same realized total
    (tested tie-out): each basket sits in exactly one tier, and a basket's
    marginals sum to its realized P&L.

    Returns None when the spec is not a ladder or no basket closed."""
    si = spec.entry.scale_in
    baskets = _ladder_baskets(result, spec)
    if si is None or not baskets:
        return None
    commission = spec.costs.commission_per_contract

    realized_total = sum(b["pl"] for b in baskets)
    gross_profit = sum(b["pl"] for b in baskets if b["pl"] > 0)
    gross_loss = -sum(b["pl"] for b in baskets if b["pl"] < 0)

    # ---- per-tier (baskets grouped by the deepest rung they reached)
    tier_acc: dict[int, dict[str, float]] = {}
    for b in baskets:
        t = tier_acc.setdefault(
            b["max_rung"],
            {"baskets": 0, "wins": 0, "contracts": 0, "total_pl": 0.0},
        )
        t["baskets"] += 1
        t["wins"] += 1 if b["pl"] > 0 else 0
        t["contracts"] += b["total_qty"]
        t["total_pl"] += b["pl"]
    tiers: list[LadderTier] = []
    for idx in sorted(tier_acc):
        t = tier_acc[idx]
        n = int(t["baskets"])
        pos = t["total_pl"] if t["total_pl"] > 0 else 0.0
        neg = -t["total_pl"] if t["total_pl"] < 0 else 0.0
        tiers.append(LadderTier(
            depth=idx + 1,
            threshold=si.rungs[idx].value,
            baskets=n,
            wins=int(t["wins"]),
            win_rate=round(t["wins"] / n, 4) if n else None,
            contracts=int(t["contracts"]),
            total_pl=round(t["total_pl"], 2),
            avg_pl=round(t["total_pl"] / n, 2) if n else 0.0,
            pct_gross_profit=round(pos / gross_profit, 4) if gross_profit > 0 else None,
            pct_gross_loss=round(neg / gross_loss, 4) if gross_loss > 0 else None,
        ))

    # ---- marginal (P&L attributable to the fills added AT each rung depth)
    rung_acc: dict[int, dict[str, float]] = {}
    for b in baskets:
        for f in b["fills"]:
            r = rung_acc.setdefault(
                f.rung_index, {"fires": 0, "contracts": 0, "marginal_pl": 0.0}
            )
            r["fires"] += 1
            r["contracts"] += f.qty
            r["marginal_pl"] += _fill_marginal(b["exit_px"], f.fill_price, f.qty, commission)
    rungs: list[LadderRung] = []
    for idx in sorted(rung_acc):
        r = rung_acc[idx]
        rungs.append(LadderRung(
            rung_index=idx,
            threshold=si.rungs[idx].value,
            add_contracts=si.rungs[idx].add_contracts,
            fires=int(r["fires"]),
            contracts=int(r["contracts"]),
            marginal_pl=round(r["marginal_pl"], 2),
            net_negative=r["marginal_pl"] < 0,
        ))

    deepest = max(b["max_rung"] for b in baskets)
    deepest_net_negative = bool(rung_acc.get(deepest, {}).get("marginal_pl", 0.0) < 0)

    return LadderDepth(
        baskets=len(baskets),
        realized_total=round(realized_total, 2),
        tiers=tiers,
        rungs=rungs,
        deepest_net_negative=deepest_net_negative,
    )


# ------------------------------------------ D5c: scale-in martingale defenses
def scale_in_honesty(
    result: RunResult, spec: StrategySpec, initial_capital: float
) -> ScaleInHonesty | None:
    """Defenses specific to a scale-in ladder (a martingale): a ruin-tail
    Monte Carlo on the basket P&L sequence and a deep-rung-dependency check,
    each a HARD cap, plus a reported basket-size concentration. LIFTS the D5a
    interlock — a ladder that clears these is judged like any strategy."""
    si = spec.entry.scale_in
    baskets = _ladder_baskets(result, spec)
    if si is None or not baskets:
        return None
    commission = spec.costs.commission_per_contract
    pls = [b["pl"] for b in baskets]
    realized_total = sum(pls)
    n = len(baskets)

    # ---- ruin-tail Monte Carlo: resample the basket P&L sequence (seeded,
    # same block bootstrap as the main MC) and measure the account-drawdown
    # tail. The running peak includes the starting capital, so a run of losers
    # draws down from where the account began — the honest ruin view.
    p95 = p99 = p_ruin = None
    ruin_flagged = False
    if n >= RUIN_MIN_BASKETS:
        arr = np.array(pls, dtype=float)
        rng = np.random.RandomState(result.seed)
        block = 5
        n_blocks = math.ceil(n / block)
        starts = rng.randint(0, n, size=(1000, n_blocks))
        offsets = np.arange(block)
        idx = (starts[:, :, None] + offsets[None, None, :]) % n
        sampled = arr[idx.reshape(1000, -1)[:, :n]]
        paths = initial_capital + np.cumsum(sampled, axis=1)
        peak = np.maximum(np.maximum.accumulate(paths, axis=1), initial_capital)
        max_dd = (1.0 - paths / np.maximum(peak, 1e-9)).max(axis=1)
        p95, p99 = float(np.percentile(max_dd, 95)), float(np.percentile(max_dd, 99))
        p_ruin = float(np.mean(max_dd > RUIN_DRAW_THRESHOLD))
        ruin_flagged = p_ruin >= RUIN_TAIL_PROB

    # ---- deep-rung dependency: remove the deepest rung's fills (no re-run —
    # subtract their recorded marginals). A positive edge that flips negative
    # without the deepest, riskiest adds DEPENDS on them (the martingale trap).
    deepest = max(b["max_rung"] for b in baskets)
    deep_marginal = sum(
        _fill_marginal(b["exit_px"], f.fill_price, f.qty, commission)
        for b in baskets
        for f in b["fills"]
        if f.rung_index == deepest
    )
    total_without_deepest = realized_total - deep_marginal
    deep_sign_flip = realized_total > 0 and total_without_deepest <= 0
    deep_flagged = deep_sign_flip or (
        abs(deep_marginal) >= 0.5 * abs(realized_total)
        if realized_total
        else deep_marginal != 0
    )

    # ---- basket-size concentration (reported, never a cap on its own): does a
    # single deep-basket day dominate the P&L?
    gross = sum(abs(p) for p in pls)
    top_share = (max(abs(p) for p in pls) / gross) if gross > 0 else None
    conc_flagged = top_share is not None and top_share >= BASKET_CONC_SHARE

    reasons: list[str] = []
    if deep_sign_flip:
        reasons.append(
            f"the edge depends on the deepest rung ({si.rungs[deepest].value:g}): "
            f"realized ${realized_total:,.0f} flips to −${abs(total_without_deepest):,.0f} "
            "with those adds removed"
        )
    if ruin_flagged and p_ruin is not None:
        reasons.append(
            f"ruinous tail: {p_ruin:.0%} of resampled orderings draw the account down "
            f"more than {RUIN_DRAW_THRESHOLD:.0%}"
        )

    losses = [p for p in pls if p < 0]
    return ScaleInHonesty(
        baskets=n,
        resamples=1000,
        seed=result.seed,
        max_basket_contracts=max(b["total_qty"] for b in baskets),
        worst_basket_loss=round(min(losses), 2) if losses else None,
        ruin_threshold=RUIN_DRAW_THRESHOLD,
        ruin_max_drawdown_p95=None if p95 is None else round(p95, 4),
        ruin_max_drawdown_p99=None if p99 is None else round(p99, 4),
        p_ruin=None if p_ruin is None else round(p_ruin, 4),
        ruin_flagged=ruin_flagged,
        deepest_threshold=si.rungs[deepest].value,
        realized_total=round(realized_total, 2),
        total_without_deepest=round(total_without_deepest, 2),
        deep_rung_sign_flip=deep_sign_flip,
        deep_rung_flagged=deep_flagged,
        top_basket_share=None if top_share is None else round(top_share, 4),
        concentration_flagged=conc_flagged,
        caps_trust=ruin_flagged or deep_sign_flip,
        reasons=reasons,
    )


# ------------------------------------------------------------ stage 5: DSR
def deflated_sharpe(result: RunResult, trials: int) -> Dsr:
    """Deflated Sharpe Ratio (Bailey & López de Prado): the probability the
    observed Sharpe beats the max Sharpe expected from `trials` tries on
    zero-edge data. DSR < 0.5 → the number is likely mined, not earned."""
    rets = _returns(result.equity)
    t = len(rets)
    if t < 20:
        return Dsr(trials=trials, daily_sharpe=None, expected_max_sharpe=None, dsr=None)

    mean = sum(rets) / t
    var = sum((r - mean) ** 2 for r in rets) / (t - 1)
    std = math.sqrt(var)
    if std == 0:
        return Dsr(trials=trials, daily_sharpe=None, expected_max_sharpe=None, dsr=None)
    sr = mean / std  # per-period (daily), NOT annualized

    n = max(trials, 1)
    gamma = 0.5772156649
    if n == 1:
        sr_max = 0.0
    else:
        z1 = _N.inv_cdf(1.0 - 1.0 / n)
        z2 = _N.inv_cdf(1.0 - 1.0 / (n * math.e))
        sr_max = math.sqrt(1.0 / t) * ((1.0 - gamma) * z1 + gamma * z2)

    skew = sum((r - mean) ** 3 for r in rets) / (t * std**3)
    kurt = sum((r - mean) ** 4 for r in rets) / (t * std**4)
    denom = 1.0 - skew * sr + ((kurt - 1.0) / 4.0) * sr * sr
    if denom <= 0:
        return Dsr(trials=trials, daily_sharpe=sr, expected_max_sharpe=sr_max, dsr=None)
    z = (sr - sr_max) * math.sqrt(t - 1) / math.sqrt(denom)
    return Dsr(
        trials=trials,
        daily_sharpe=sr,
        expected_max_sharpe=sr_max,
        dsr=float(_N.cdf(z)),
    )


# ------------------------------------------- stage 6: regime & sample guard
def regime_sample(result: RunResult, store: MarketStore) -> RegimeSample:
    """Guardrail #5: below MIN_TRADES trades or a single VIX regime, trust is
    capped at insufficient evidence no matter how good the numbers look."""
    low = mid = high = 0
    vd = store.vix_dates
    vc = store.vix_close
    if vd:
        from bisect import bisect_right

        for day in result.dates:
            i = bisect_right(vd, day)
            if i == 0:
                continue
            v = vc[vd[i - 1]]
            if v < 15:
                low += 1
            elif v <= 20:
                mid += 1
            else:
                high += 1
    total = max(low + mid + high, 1)
    present = sum(1 for c in (low, mid, high) if c / total >= 0.10)

    trades = sum(1 for t in result.trades if t.pl is not None)
    capped = False
    reason: str | None = None
    if trades < MIN_TRADES:
        capped = True
        reason = f"only {trades} closed trades — minimum is {MIN_TRADES}"
    elif present < 2:
        capped = True
        reason = "history spans a single volatility regime"
    return RegimeSample(
        trades=trades,
        days_low_vix=low,
        days_mid_vix=mid,
        days_high_vix=high,
        regimes_present=present,
        capped=capped,
        cap_reason=reason,
    )


# ---------------------------------------------- stage 6d: P&L concentration
def concentration(result: RunResult) -> Concentration:
    """Does the P&L come from a distribution of days or a handful of them —
    and are the handful high-gamma days (a lottery-ticket shape)? Reported
    flag + verdict reason only; never a trust cap in D1.

    Gamma alignment: daily P&L i = equity[i+1] − equity[i], produced by the
    exposure HELD at the close of day i → gamma index i."""
    n_pnl = len(result.equity) - 1
    if n_pnl < CONC_MIN_SESSIONS:
        return Concentration(
            meaningful=False,
            note=f"concentration needs ≥ {CONC_MIN_SESSIONS} marked sessions",
        )
    pnl = [result.equity[i + 1] - result.equity[i] for i in range(n_pnl)]
    gross = sum(abs(p) for p in pnl)
    if gross <= 0:
        return Concentration(meaningful=False, note="no P&L movement to concentrate")

    k = max(1, math.ceil(CONC_TOP_DAY_FRACTION * n_pnl))
    order = sorted(range(n_pnl), key=lambda i: abs(pnl[i]), reverse=True)
    top_idx = order[:k]
    top_share = sum(abs(pnl[i]) for i in top_idx) / gross

    # gamma coincidence: |portfolio gamma| decile threshold over days where
    # gamma is known; None when the top days' gammas are (honestly) unknown
    gammas = result.portfolio_gamma
    known = sorted(abs(g) for g in gammas[:n_pnl] if g is not None)
    coincidence: float | None = None
    if known and len(known) >= max(10, k):
        threshold = known[min(int(CONC_GAMMA_DECILE * len(known)), len(known) - 1)]
        top_gammas = [gammas[i] for i in top_idx]
        if all(g is not None for g in top_gammas):
            hits = sum(1 for g in top_gammas if abs(g or 0.0) >= threshold)
            coincidence = hits / k

    flagged = top_share >= CONC_MATERIAL_SHARE
    note: str | None = None
    if flagged:
        note = (
            f"{round(top_share * 100)}% of gross daily P&L comes from just {k} "
            f"session{'s' if k != 1 else ''} (top {round(CONC_TOP_DAY_FRACTION * 100)}%)"
        )
        if coincidence is not None and coincidence >= 0.5:
            note += (
                f" — and {round(coincidence * 100)}% of those are top-decile "
                "gamma days (lottery-ticket shape)"
            )
    return Concentration(
        meaningful=True,
        note=note,
        top_days=k,
        top_share=top_share,
        gamma_coincidence=coincidence,
        flagged=flagged,
    )


# --------------------------------------------- stage 6c: liquidity profile
def liquidity_profile(result: RunResult, spec: StrategySpec) -> LiquidityProfile:
    """How real the fills were (guardrail #1's disclosure arm). Counts come
    straight from the engine's per-leg fill bookkeeping; `material` only
    controls whether the verdict carries a caveat line — never scoring."""
    n = result.option_leg_fills
    spreads = sorted(result.fill_spread_pcts)
    median_spread = spreads[len(spreads) // 2] if spreads else None

    def share(count: int) -> float | None:
        return count / n if n > 0 else None

    penalized = share(result.fills_penalized)
    stressed = share(result.fills_stressed)
    skipped = sum(
        1
        for t in result.trades
        if t.action == "SKIP"
        and t.reason in ("illiquid_spread", "illiquid_oi", "illiquid_volume")
    )

    notes: list[str] = []
    if stressed is not None and stressed > 0:
        notes.append(
            f"{round(stressed * 100)}% of option fills paid the full adverse quote "
            "(stress mode on gated contracts)"
        )
    if penalized is not None and penalized >= LIQ_PENALIZED_MATERIAL_SHARE:
        notes.append(
            f"{round(penalized * 100)}% of option fills paid thin-liquidity slippage "
            "above the base fraction"
        )
    if skipped >= LIQ_SKIPPED_MATERIAL_COUNT:
        notes.append(
            f"{skipped} entr{'y was' if skipped == 1 else 'ies were'} refused by the "
            "liquidity gates — the strategy wants markets this data says are thin"
        )

    # F5: fills vs displayed NBBO depth — disclosure only, never scoring.
    # ANY beyond-depth fill is named (no materiality floor: the count is
    # small by construction and the reader decides what it means)
    depth_known = share(result.fills_depth_known)
    beyond = (result.fills_beyond_depth / result.fills_depth_known
              if result.fills_depth_known > 0 else None)
    if result.fills_beyond_depth > 0:
        notes.append(
            f"{result.fills_beyond_depth} of {result.fills_depth_known} "
            "depth-known fills exceeded the displayed NBBO size — the model "
            "filled the whole order at the quote; real execution may have "
            "walked the book (prices unchanged, disclosed not modeled)"
        )

    return LiquidityProfile(
        mode=spec.costs.liquidity_mode.value,
        max_spread_pct=spec.costs.max_spread_pct,
        min_open_interest=spec.costs.min_open_interest,
        min_volume=spec.costs.min_volume,
        option_leg_fills=n,
        median_spread_pct=median_spread,
        penalized_share=penalized,
        stressed_share=stressed,
        unknown_liquidity_share=share(result.fills_unknown_liquidity),
        skipped_illiquid=skipped,
        depth_known_share=depth_known,
        beyond_depth_share=beyond,
        fills_depth_known=result.fills_depth_known,
        fills_beyond_depth=result.fills_beyond_depth,
        material=bool(notes),
        note="; ".join(notes) if notes else None,
    )


# ------------------------------------------- F7: data confidence (reported)
def data_confidence(
    result: RunResult,
    spec: StrategySpec,
    summaries: dict[str, dict[str, dict[str, Any]]] | None = None,
) -> DataConfidence | None:
    """Cross-source agreement over THIS run's window, per pair — REPORTED,
    never scored. `summaries` maps pair → {iso-date: record}; None loads
    the nightly artifacts (honest absence on any failure). Pairs with no
    audited session inside the window are omitted — an empty result is
    None, and the verdict simply says nothing (never a fabricated 100%)."""
    ticker = result.ticker
    if summaries is None:
        summaries = {}
        try:
            from app.data import r2
            from app.data.cross_validation import PAIRS, load_pair_summary

            s3 = r2.r2_client()
            for pair in PAIRS:
                loaded = load_pair_summary(s3, pair, ticker)
                if loaded:
                    summaries[pair] = loaded
        except Exception:
            log.warning("data_confidence: pair artifacts unavailable "
                        "(reported as absence)", exc_info=True)
            return None
    start = result.effective_start.isoformat()
    end = result.effective_end.isoformat()
    window_sessions = result.sessions_with_chain
    pairs: list[PairConfidence] = []
    for pair, by_date in sorted(summaries.items()):
        rows = [(d, r) for d, r in by_date.items() if start <= d <= end]
        if not rows:
            continue
        joined = sum(int(r.get("joined") or 0) for _, r in rows)
        checked = sum(int(r.get("checked") or 0) for _, r in rows)
        within = sum(int(r.get("within_band") or 0) for _, r in rows)
        # parquet round-trips None as NaN — a NaN rate must not survive
        # into min() or the JSON payload (review #3: allow_nan=False in
        # the response encoder would 500 the whole run page)
        session_rates = [(d, float(r["agreement_rate"])) for d, r in rows
                         if r.get("agreement_rate") is not None
                         and not (isinstance(r["agreement_rate"], float)
                                  and math.isnan(r["agreement_rate"]))]
        worst = min(session_rates, key=lambda x: x[1]) if session_rates else None
        pairs.append(PairConfidence(
            pair=pair,
            audited_sessions=len(rows),
            window_sessions=window_sessions,
            joined=joined,
            checked=checked,
            within_band=within,
            agreement_rate=round(within / checked, 4) if checked else None,
            worst_session_rate=(round(worst[1], 4) if worst else None),
            worst_session=worst[0] if worst else None,
        ))
    if not pairs:
        return None
    noted = [p for p in pairs if p.agreement_rate is not None]
    note = None
    if noted:
        parts = [f"{p.pair}: {round((p.agreement_rate or 0.0) * 100, 1)}% of "
                 f"{p.checked} checked rows within band over "
                 f"{p.audited_sessions} of {p.window_sessions} sessions"
                 for p in noted]
        note = "; ".join(parts)
    return DataConfidence(pairs=pairs, note=note)


# ------------------------------------------------ stage 6b: coverage guard
def coverage(result: RunResult) -> Coverage:
    """Guardrail #6 + SEVENTEEN.md: a run whose requested window is mostly
    sessions with NO usable chain tested far less than it claims. Compute the
    honest coverage share; `materially_short` caps trust downstream."""
    requested = max(result.requested_sessions, 0)
    chain_sessions = result.sessions_with_chain
    ratio = min(chain_sessions / requested, 1.0) if requested > 0 else 1.0
    short = requested > 0 and ratio < COVERAGE_MIN_RATIO
    reason = (
        f"only {chain_sessions} of {requested} requested sessions carried a usable "
        f"options chain ({round(ratio * 100)}%) — most of the window was untested"
        if short
        else None
    )
    req_start = result.requested_start or result.effective_start
    req_end = result.requested_end or result.effective_end
    return Coverage(
        requested_start=req_start.isoformat(),
        requested_end=req_end.isoformat(),
        effective_start=result.effective_start.isoformat(),
        effective_end=result.effective_end.isoformat(),
        requested_sessions=requested,
        chain_sessions=chain_sessions,
        coverage_ratio=ratio,
        materially_short=short,
        reason=reason,
    )


# ------------------------------------------------- D3a: unlock conditions
def unlock_conditions(report: HonestyReport, spec: StrategySpec) -> UnlockConditions | None:
    """Structured needs for a REFUSED verdict (trust label
    insufficient_evidence) — built from the SAME stage numbers the refusal
    text shows, so the nightly auto-unlock scan (D3b) compares facts, not
    prose. Returns None for graded verdicts."""
    if report.trust.label != "insufficient_evidence":
        return None
    cov = report.coverage
    sample = report.regime_sample
    # A refusal that no amount of DATA can lift — e.g. the D5a scale-in
    # interlock (defenses pending, not sample/coverage) — must not enter the
    # auto-unlock scan (D3b), or it would re-run and re-refuse forever.
    if not (
        cov.materially_short
        or sample.trades < MIN_TRADES
        or sample.regimes_present < 2
    ):
        return None
    return UnlockConditions(
        ticker=spec.underlying.ticker.value,
        clock=spec.backtest.clock.value,
        requested_start=cov.requested_start,
        requested_end=cov.requested_end,
        coverage=(
            UnlockNeed(has=cov.coverage_ratio, needs=COVERAGE_MIN_RATIO)
            if cov.materially_short else None
        ),
        trades=(
            UnlockNeed(has=float(sample.trades), needs=float(MIN_TRADES))
            if sample.trades < MIN_TRADES else None
        ),
        regimes=(
            UnlockNeed(has=float(sample.regimes_present), needs=2.0)
            if sample.regimes_present < 2 else None
        ),
        sessions_at_refusal=cov.chain_sessions,
    )
