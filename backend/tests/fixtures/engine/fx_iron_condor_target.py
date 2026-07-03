"""Fixture: iron condor exits at its 50% profit target.

  2025-01-06 (Mon)  entry. Spot 100.00. Exp 2025-01-17.
    short put  K=95 : bid 1.20/ask 1.30 → SELL = 1.25 − 0.5×0.05 = 1.225
    long  put  K=90 : bid 0.60/ask 0.70 → BUY  = 0.65 + 0.5×0.05 = 0.675
    short call K=105: bid 1.10/ask 1.20 → SELL = 1.15 − 0.5×0.05 = 1.125
    long  call K=110: bid 0.55/ask 0.65 → BUY  = 0.60 + 0.5×0.05 = 0.625
    net credit = (1.225 + 1.125) − (0.675 + 0.625) = 2.35 − 1.30 = 1.05
    cash = +105.00 − 4×0.65 = +102.40 → 10,102.40

  2025-01-10 (Fri)  theta has done its job.
    btc  put 95  = (0.40+0.50)/2 + 0.5×0.05 = 0.475
    stc  put 90  = (0.20+0.26)/2 − 0.5×0.03 = 0.215
    btc call 105 = (0.35+0.45)/2 + 0.5×0.05 = 0.425
    stc call 110 = (0.18+0.24)/2 − 0.5×0.03 = 0.195
    cost to close = (0.475 + 0.425) − (0.215 + 0.195) = 0.90 − 0.41 = 0.49
    captured = (1.05 − 0.49) / 1.05 = 53.3% ≥ 50% target → CLOSE.
    exit cash = −47.50 − 42.50 + 21.50 + 19.50 − 4×0.65 = −49.00 − 2.60 = −51.60
    final cash = 10,102.40 − 51.60 = 10,050.80

  P/L check: (1.05 − 0.49)×100 − 8×0.65 = 56.00 − 5.20 = +50.80 ✓
"""

from .common import call, make_spec, put

ENTRY = "2025-01-06"
TARGET_DAY = "2025-01-10"
EXPIRY = "2025-01-17"

CHAINS = {
    ENTRY: [
        put(95.0, 1.20, 1.30, -0.20, EXPIRY),
        put(90.0, 0.60, 0.70, -0.12, EXPIRY),
        call(105.0, 1.10, 1.20, 0.20, EXPIRY),
        call(110.0, 0.55, 0.65, 0.12, EXPIRY),
    ],
    "2025-01-08": [
        # cost to close = (0.775+0.725) − (0.385+0.345) = 1.50 − 0.73 = 0.77
        # captured (1.05−0.77)/1.05 = 26.7% < 50% → hold
        put(95.0, 0.70, 0.85, -0.18, EXPIRY),
        put(90.0, 0.36, 0.41, -0.10, EXPIRY),
        call(105.0, 0.65, 0.80, 0.18, EXPIRY),
        call(110.0, 0.32, 0.37, 0.10, EXPIRY),
    ],
    TARGET_DAY: [
        put(95.0, 0.40, 0.50, -0.10, EXPIRY),
        put(90.0, 0.20, 0.26, -0.05, EXPIRY),
        call(105.0, 0.35, 0.45, 0.10, EXPIRY),
        call(110.0, 0.18, 0.24, 0.05, EXPIRY),
    ],
}

UNDERLYING = {
    ENTRY: (100.0, 100.0),
    "2025-01-08": (100.2, 100.4),
    TARGET_DAY: (100.1, 100.2),
}

SPEC = make_spec(
    position={
        "structure": "iron_condor",
        "legs": [
            {"right": "put", "side": "short", "ratio": 1,
             "strike_selection": {"method": "delta", "value": 0.20}},
            {"right": "put", "side": "long", "ratio": 1,
             "strike_selection": {"method": "width_from_leg", "value": 5, "reference_leg": 0}},
            {"right": "call", "side": "short", "ratio": 1,
             "strike_selection": {"method": "delta", "value": 0.20}},
            {"right": "call", "side": "long", "ratio": 1,
             "strike_selection": {"method": "width_from_leg", "value": 5, "reference_leg": 2}},
        ],
        "expiration_selection": {"target_dte": 11, "min_dte": 1, "max_dte": 30},
    },
    entry={"schedule": {"frequency": "weekly", "day_of_week": "monday"},
           "conditions": [], "max_concurrent_positions": 1},
    exit={"profit_target_pct": 50},
    backtest={"start": ENTRY, "end": TARGET_DAY, "initial_capital": 10_000, "seed": 42},
)

EXPECT = {
    "final_cash": 10_050.80,
    "final_equity": 10_050.80,
    "closed_trades": 1,
    "actions": ["OPEN", "CLOSE"],
    "close_reason": "profit_target",
    "trade_pl": 50.80,
}
