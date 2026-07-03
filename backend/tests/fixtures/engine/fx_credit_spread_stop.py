"""Fixture: put credit spread hits its stop (loss ≥ 100% of credit).

  2025-01-06 (Mon)  entry. Spot 100.00. Exp 2025-01-17.
    short put K=100: bid 2.00 / ask 2.20 → SELL fill = 2.10 − 0.5×0.10 = 2.05
    long  put K=95 : bid 1.00 / ask 1.10 → BUY  fill = 1.05 + 0.5×0.05 = 1.075
    net credit = 2.05 − 1.075 = 0.975  → +$97.50 − 2×0.65 = +96.20 cash
    cash after entry: 10,096.20

  2025-01-08 (Wed)  market drops; marks blow through the stop.
    short K=100: bid 4.00 / ask 4.30 → buy-to-close  = 4.15 + 0.5×0.15 = 4.225
    long  K=95 : bid 1.80 / ask 2.00 → sell-to-close = 1.90 − 0.5×0.10 = 1.85
    cost to close = 4.225 − 1.85 = 2.375
    loss = (2.375 − 0.975) / 0.975 = 143.6% ≥ stop 100% → CLOSE today.
    exit cash = −422.50 + 185.00 − 2×0.65 = −238.80
    final cash = 10,096.20 − 238.80 = 9,857.40

  P/L check: −(2.375 − 0.975)×100 − 4×0.65 = −140.00 − 2.60 = −142.60 ✓
"""

from .common import make_spec, put

ENTRY = "2025-01-06"
STOP_DAY = "2025-01-08"
EXPIRY = "2025-01-17"

CHAINS = {
    ENTRY: [
        put(100.0, 2.00, 2.20, -0.30, EXPIRY),
        put(95.0, 1.00, 1.10, -0.18, EXPIRY),
    ],
    "2025-01-07": [
        # drifting against us but under the stop:
        # btc = (2.90+3.10)/2 + 0.5×0.10 = 3.05 ; stc = 1.40 − 0.5×0.05 = 1.375
        # cost 1.675 → loss (1.675−0.975)/0.975 = 71.8% < 100% → hold
        put(100.0, 2.90, 3.10, -0.42, EXPIRY),
        put(95.0, 1.35, 1.45, -0.26, EXPIRY),
    ],
    STOP_DAY: [
        put(100.0, 4.00, 4.30, -0.55, EXPIRY),
        put(95.0, 1.80, 2.00, -0.35, EXPIRY),
    ],
}

UNDERLYING = {
    ENTRY: (100.0, 100.0),
    "2025-01-07": (99.0, 98.0),
    STOP_DAY: (97.0, 96.0),
}

SPEC = make_spec(
    position={
        "structure": "put_credit_spread",
        "legs": [
            {"right": "put", "side": "short", "ratio": 1,
             "strike_selection": {"method": "delta", "value": 0.30}},
            {"right": "put", "side": "long", "ratio": 1,
             "strike_selection": {"method": "width_from_leg", "value": 5, "reference_leg": 0}},
        ],
        "expiration_selection": {"target_dte": 11, "min_dte": 1, "max_dte": 30},
    },
    entry={"schedule": {"frequency": "weekly", "day_of_week": "monday"},
           "conditions": [], "max_concurrent_positions": 1},
    exit={"stop_loss_pct": 100},
    backtest={"start": ENTRY, "end": STOP_DAY, "initial_capital": 10_000, "seed": 42},
)

EXPECT = {
    "final_cash": 9_857.40,
    "final_equity": 9_857.40,
    "closed_trades": 1,
    "actions": ["OPEN", "CLOSE"],
    "close_reason": "stop_loss",
    "trade_pl": -142.60,
}
