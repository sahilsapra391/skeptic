"""Gauntlet stages 1–6 (TECH-SPEC §6). Every stage is deterministic given
(spec, data, seed); stochastic steps take the run seed. Anything that
cannot be computed honestly reports None or "not meaningful" — never a
fabricated number.
"""

from __future__ import annotations

import copy
import math
from collections.abc import Callable
from statistics import NormalDist

import numpy as np

from app.engine.engine import run_engine
from app.engine.market import MarketStore
from app.engine.types import RunResult
from app.honesty.report import (
    Dsr,
    MonteCarlo,
    OosSplit,
    ParamSweep,
    RegimeSample,
    Sensitivity,
    WalkForward,
    WalkForwardFold,
)
from app.models.spec import StrategySpec, StrikeMethod

_N = NormalDist()
ANNUAL = math.sqrt(252)

# Owner-set floor (2026-07-02, was 30): below this many closed trades the
# verdict is withheld as insufficient evidence (CLAUDE.md guardrail #5).
MIN_TRADES = 15


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
        folds.append(
            WalkForwardFold(
                start=d0.isoformat(),
                end=d1.isoformat(),
                ret=(eq1 / eq0 - 1.0) if eq0 > 0 else 0.0,
                trades=sum(1 for t in closed if d0 <= t.day <= d1),
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


def _mutations(spec: StrategySpec) -> list[tuple[str, list[float], int, Setter]]:
    """(name, values ±20% in 5 steps, base index, setter) per numeric param
    present. The base index marks the as-specced value inside `values`."""
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

    return out


def sensitivity(spec: StrategySpec, store: MarketStore) -> Sensitivity:
    """Perturb each numeric parameter ±20% in 5 steps, re-run the engine,
    classify the optimum: plateau if median neighbor Sharpe ≥ 70% of the
    peak, else cliff (TECH-SPEC §6.4). Any cliff makes the verdict cliff."""
    sweeps: list[ParamSweep] = []
    for name, values, base_index, setter in _mutations(spec):
        sharpes: list[float | None] = []
        for v in values:
            mutated = copy.deepcopy(spec)
            setter(mutated, v)
            try:
                r = run_engine(mutated, store)
                sharpes.append(_sharpe(_returns(r.equity)))
            except Exception:
                sharpes.append(None)

        valid = [s for s in sharpes if s is not None]
        classification: str | None = None
        if len(valid) >= 3:
            peak = max(valid)
            neighbors = sorted(v for v in valid if v != peak) or valid
            median = neighbors[len(neighbors) // 2]
            if peak <= 0:
                classification = "cliff"  # nothing to stand on anywhere
            else:
                classification = "plateau" if median >= 0.7 * peak else "cliff"
        sweeps.append(
            ParamSweep(
                name=name,
                values=[float(v) for v in values],
                sharpes=sharpes,
                base_index=base_index,
                classification=classification,
            )
        )

    classified = [s.classification for s in sweeps if s.classification is not None]
    verdict: str | None = None
    if classified:
        verdict = "cliff" if "cliff" in classified else "plateau"
    return Sensitivity(params=sweeps, verdict=verdict)


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
