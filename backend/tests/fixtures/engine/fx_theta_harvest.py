"""Fixture: theta-harvest fires INSIDE its DTE window and only there (D1c,
owner-confirmed semantics: DTE-band profit harvest {dte_from, dte_to,
profit_pct} — inside [dte_to, dte_from], close as soon as profit ≥ X%).

  2025-01-06 (Mon)  entry. Spot 100.00. Exp 2025-01-31 → 25 DTE.
    short put K=100: bid 2.00/ask 2.20 → SELL = 2.10 − 0.5×0.10 = 2.05
    long  put K=95 : bid 1.00/ask 1.10 → BUY  = 1.05 + 0.5×0.05 = 1.075
    net credit = 0.975
    cash = +205.00 − 107.50 − 2×0.65 = +96.20 → 10,096.20

  2025-01-07 (Tue)  24 DTE — OUTSIDE the [7, 21] window.
    btc 100 = (0.90+1.00)/2 + 0.5×0.05 = 0.975
    stc  95 = (0.50+0.56)/2 − 0.5×0.03 = 0.515
    cost 0.46 → profit (0.975−0.46)/0.975 = 52.8% ≥ 50%
    …but 24 DTE > dte_from 21 → HOLD. The window gates the harvest.

  2025-01-14 (Tue)  17 DTE — INSIDE the window. (A Tuesday on purpose:
    a Monday harvest day would let the weekly schedule re-enter the same
    session — legal engine behavior, but this fixture isolates the exit.)
    btc 100 = (0.80+0.90)/2 + 0.5×0.05 = 0.875
    stc  95 = (0.44+0.50)/2 − 0.5×0.03 = 0.455
    cost 0.42 → profit (0.975−0.42)/0.975 = 56.9% ≥ 50% → THETA HARVEST.
    exit cash = −87.50 + 45.50 − 2×0.65 = −42.00 − 1.30 = −43.30
    final cash = 10,096.20 − 43.30 = 10,052.90

  P/L check: (0.975 − 0.42)×100 − 4×0.65 = 55.50 − 2.60 = +52.90 ✓
"""

from .common import make_spec, put

ENTRY = "2025-01-06"
OUTSIDE_DAY = "2025-01-07"
HARVEST_DAY = "2025-01-14"
EXPIRY = "2025-01-31"

CHAINS = {
    ENTRY: [
        put(100.0, 2.00, 2.20, -0.30, EXPIRY),
        put(95.0, 1.00, 1.10, -0.18, EXPIRY),
    ],
    OUTSIDE_DAY: [
        put(100.0, 0.90, 1.00, -0.22, EXPIRY),
        put(95.0, 0.50, 0.56, -0.12, EXPIRY),
    ],
    HARVEST_DAY: [
        put(100.0, 0.80, 0.90, -0.20, EXPIRY),
        put(95.0, 0.44, 0.50, -0.10, EXPIRY),
    ],
}

UNDERLYING = {
    ENTRY: (100.0, 100.0),
    OUTSIDE_DAY: (100.5, 101.0),
    HARVEST_DAY: (101.0, 101.5),
}

SPEC = make_spec(
    spec_version=2,
    position={
        "structure": "put_credit_spread",
        "legs": [
            {"right": "put", "side": "short", "ratio": 1,
             "strike_selection": {"method": "delta", "value": 0.30}},
            {"right": "put", "side": "long", "ratio": 1,
             "strike_selection": {"method": "width_from_leg", "value": 5, "reference_leg": 0}},
        ],
        "expiration_selection": {"target_dte": 25, "min_dte": 1, "max_dte": 40},
    },
    entry={"schedule": {"frequency": "weekly", "day_of_week": "monday"},
           "conditions": [], "max_concurrent_positions": 1},
    exit={"theta_harvest": {"dte_from": 21, "dte_to": 7, "profit_pct": 50}},
    backtest={"start": ENTRY, "end": HARVEST_DAY, "initial_capital": 10_000, "seed": 42},
)

EXPECT = {
    "final_cash": 10_052.90,
    "final_equity": 10_052.90,
    "closed_trades": 1,
    "actions": ["OPEN", "CLOSE"],
    "close_reason": "theta_harvest",
    "trade_pl": 52.90,
}
