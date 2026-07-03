"""Fixture: short put held to expiration, finishes OTM, expires worthless.

  2025-01-06 (Mon)  entry. Spot 100.00.
    Chain: put K=95, exp 2025-01-17, bid 1.00 / ask 1.10, delta −0.30.
    SELL 1 put:
      mid  = 1.05
      fill = 1.05 − 0.5 × (1.05 − 1.00)   = 1.025
      cash = +102.50 − 0.65               = +101.85
      cash after entry: 10,101.85

  2025-01-17 (Fri)  expiration. Spot closes 98.00 > 95 → OTM, worthless.
    final cash = final equity = 10,101.85. P/L +101.85.
"""

from .common import make_spec, put

ENTRY = "2025-01-06"
EXPIRY = "2025-01-17"

CHAINS = {
    ENTRY: [put(95.0, 1.00, 1.10, -0.30, EXPIRY)],
    "2025-01-13": [put(95.0, 0.40, 0.50, -0.15, EXPIRY)],
}

UNDERLYING = {
    ENTRY: (100.0, 100.0),
    "2025-01-13": (99.0, 99.5),
    EXPIRY: (98.5, 98.0),
}

SPEC = make_spec(
    position={
        "structure": "short_put",
        "legs": [
            {"right": "put", "side": "short", "ratio": 1,
             "strike_selection": {"method": "delta", "value": 0.30}}
        ],
        "expiration_selection": {"target_dte": 11, "min_dte": 1, "max_dte": 30},
    },
    entry={"schedule": {"frequency": "weekly", "day_of_week": "monday"},
           "conditions": [], "max_concurrent_positions": 1},
    exit={"time_exit_dte": 0},
    backtest={"start": ENTRY, "end": EXPIRY, "initial_capital": 10_000, "seed": 42},
)

EXPECT = {
    "final_cash": 10_101.85,
    "final_equity": 10_101.85,
    "closed_trades": 1,
    "actions": ["OPEN", "EXPIRE"],
    "trade_pl": 101.85,
}
