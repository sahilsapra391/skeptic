"""Multi-session scale-in stores for the D5a interlock proof (fixture 5).

`scale_in_multi_session` builds a martingale ladder that clears the 15-trade
bar across two volatility regimes — deliberately NOT sample-capped — so the
interlock test can prove the refusal is the pending-defenses cap (D5c), not
luck of a thin sample. Rungs use price_vs_vwap_pct (session-anchored, so no
cross-session contamination). `ruin_single_session` is the one-basket ruin run
(mirrors test_scale_in_engine's fixture 2) used to show the interlock LEADS
even when the sample is also thin.
"""

from __future__ import annotations

from datetime import date, timedelta

from app.engine.market import MarketStore, SessionSlice, build_fixture_slice, build_fixture_store
from app.models.spec import StrategySpec


class ScaleInIntraday:
    """Plain-dict IntradayProvider for scale-in gauntlet fixtures."""

    def __init__(self, slices: dict[str, SessionSlice], max_dte: int = 2) -> None:
        self._slices = {date.fromisoformat(k): v for k, v in slices.items()}
        self._max_dte = max_dte

    @property
    def slice_max_trading_dte(self) -> int:
        return self._max_dte

    def sessions(self) -> list[date]:
        return sorted(self._slices)

    def slice_for(self, session: date) -> SessionSlice | None:
        return self._slices.get(session)


def _weekdays(start: date, n: int) -> list[date]:
    out: list[date] = []
    d = start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _call(expiry: str, bid: float, ask: float) -> dict:
    return {"expiration": expiry, "right": "call", "strike": 100.0, "bid": bid, "ask": ask}


def _session(session_iso: str, expiry_iso: str, ruin: bool) -> SessionSlice:
    """One session, one basket. A SHALLOW win only trips rung0 (−1% vs VWAP)
    and takes profit; a DEEP loss cascades into rung1 (−2.5%) and is
    force-flatted at 15:45. So the deep tier IS the loss — the martingale
    tell the depth attribution must surface. Equal per-bar volume ⇒ VWAP is
    the running mean of session lasts."""
    quotes = {
        "09:30": [_call(expiry_iso, 1.00, 1.10)],
        "09:35": [_call(expiry_iso, 1.00, 1.10)],
        "09:40": [_call(expiry_iso, 0.55, 0.65)],  # −1.34% vs VWAP → rung0 opens
    }
    und = {"09:30": 100.0, "09:35": 100.0, "09:40": 98.0}
    if ruin:
        # dips deeper into rung1, then collapses → force-flat at a loss (depth 2)
        quotes["09:45"] = [_call(expiry_iso, 0.25, 0.35)]  # −2.54% vs VWAP → rung1
        quotes["09:50"] = [_call(expiry_iso, 0.05, 0.15)]
        quotes["15:45"] = [_call(expiry_iso, 0.02, 0.10)]
        und["09:45"], und["09:50"], und["15:45"] = 96.0, 94.0, 93.0
    else:
        # recovers before rung1 → shallow basket takes profit (depth 1)
        quotes["09:45"] = [_call(expiry_iso, 0.50, 0.60)]  # −0.63% vs VWAP → no rung1
        quotes["09:50"] = [_call(expiry_iso, 1.50, 1.60)]  # PT
        quotes["15:45"] = [_call(expiry_iso, 1.50, 1.60)]
        und["09:45"], und["09:50"], und["15:45"] = 98.5, 101.0, 101.0
    volumes = {t: 100.0 for t in und}
    return build_fixture_slice(session_iso, quotes, und, volumes=volumes)


def _vwap_rung(value: float, add: int) -> dict:
    return {"indicator": "price_vs_vwap_pct", "timeframe": "5min",
            "operator": "<=", "value": value, "add_contracts": add}


def scale_in_ladder_spec() -> StrategySpec:
    """The martingale ladder for the multi-session interlock proof."""
    return StrategySpec.model_validate({
        "spec_version": 3,
        "meta": {"name": "vwap scale-in", "description_raw": "martingale ladder"},
        "underlying": {"ticker": "SPY"},
        "position": {
            "structure": "long_call",
            "legs": [{"right": "call", "side": "long", "ratio": 1,
                      "strike_selection": {"method": "atm", "value": 0}}],
            "expiration_selection": {"target_dte": 1, "min_dte": 0, "max_dte": 2},
        },
        "entry": {
            "schedule": {"frequency": "signal_only"}, "conditions": [],
            "max_concurrent_positions": 1,
            "scale_in": {
                "mode": "signal_ladder", "basket": True,
                "rungs": [_vwap_rung(-1.0, 2), _vwap_rung(-2.5, 5)],
                "rearm": {"indicator": "price_vs_vwap_pct", "timeframe": "5min",
                          "operator": ">=", "value": 0.0},
                "max_total_contracts": 10,
            },
        },
        "exit": {"profit_target_pct": 40, "close_at_time": "15:45"},
        "sizing": {"method": "fixed_contracts", "value": 1},
        "costs": {"commission_per_contract": 0.65, "slippage_half_spread_fraction": 0.5,
                  "max_spread_pct": 500},
        "backtest": {"start": None, "end": None, "initial_capital": 10_000,
                     "seed": 42, "clock": "5min"},
    })


def scale_in_multi_session(
    n: int = 20, ruin_every: int = 3
) -> tuple[MarketStore, ScaleInIntraday]:
    """`n` sessions, one basket each (≥15 baskets), split across a low-VIX and
    a high-VIX regime so regime_sample does NOT cap it."""
    days = _weekdays(date(2025, 1, 6), n + 1)  # +1 for the last session's expiry
    isos = [d.isoformat() for d in days]
    slices = {
        isos[i]: _session(isos[i], isos[i + 1], ruin=(i % ruin_every == 0))
        for i in range(n)
    }
    underlying = {iso: (100.0, 100.0) for iso in isos}
    vix = {isos[i]: (12.0 if i < n // 2 else 25.0) for i in range(n)}
    store = build_fixture_store("SPY", {}, underlying, vix=vix)
    return store, ScaleInIntraday(slices)


def ruin_single_session() -> tuple[MarketStore, ScaleInIntraday, StrategySpec]:
    """The one-basket martingale-ruin run (mirrors fixture 2): four rungs
    cascade as the call decays, force-flat books the loss. Used to show the
    interlock leads over the thin-sample cap."""
    exp = "2025-01-07"

    def c(bid: float, ask: float) -> dict:
        return _call(exp, bid, ask)

    def rung(value: float, add: int) -> dict:
        return {"indicator": "sma", "timeframe": "5min", "period": 2,
                "operator": "<=", "value": value, "add_contracts": add}

    slc = build_fixture_slice(
        "2025-01-06",
        quotes={
            "09:30": [c(1.00, 1.10)], "09:35": [c(1.00, 1.10)],
            "09:40": [c(0.60, 0.70)], "09:45": [c(0.60, 0.70)],
            "09:50": [c(0.40, 0.50)], "09:55": [c(0.40, 0.50)],
            "10:00": [c(0.25, 0.35)], "10:05": [c(0.25, 0.35)],
            "10:10": [c(0.12, 0.20)], "10:15": [c(0.12, 0.20)],
            "15:45": [c(0.05, 0.15)],
        },
        underlying={"09:30": 100.0, "09:35": 100.0, "09:40": 99.5, "09:45": 99.5,
                    "09:50": 99.0, "09:55": 99.0, "10:00": 98.5, "10:05": 98.5,
                    "10:10": 98.0, "10:15": 98.0, "15:45": 97.5},
    )
    spec = StrategySpec.model_validate({
        "spec_version": 3,
        "meta": {"name": "ruin ladder", "description_raw": "martingale ruin"},
        "underlying": {"ticker": "SPY"},
        "position": {
            "structure": "long_call",
            "legs": [{"right": "call", "side": "long", "ratio": 1,
                      "strike_selection": {"method": "atm", "value": 0}}],
            "expiration_selection": {"target_dte": 1, "min_dte": 0, "max_dte": 2},
        },
        "entry": {
            "schedule": {"frequency": "signal_only"}, "conditions": [],
            "max_concurrent_positions": 1,
            "scale_in": {
                "mode": "signal_ladder", "basket": True,
                "rungs": [rung(99.5, 2), rung(99.0, 3), rung(98.5, 5), rung(98.0, 10)],
                "rearm": {"indicator": "sma", "timeframe": "5min", "period": 2,
                          "operator": ">", "value": 99.5},
                "max_total_contracts": 25,
            },
        },
        "exit": {"profit_target_pct": 40, "stop_loss_pct": 95, "close_at_time": "15:45"},
        "sizing": {"method": "fixed_contracts", "value": 1},
        "costs": {"commission_per_contract": 0.65, "slippage_half_spread_fraction": 0.5,
                  "max_spread_pct": 500},
        "backtest": {"start": None, "end": "2025-01-06", "initial_capital": 10_000,
                     "seed": 42, "clock": "5min"},
    })
    store = build_fixture_store(
        "SPY", {}, {"2025-01-06": (100.0, 100.0), "2025-01-07": (100.0, 100.0)}
    )
    return store, ScaleInIntraday({"2025-01-06": slc}), spec
