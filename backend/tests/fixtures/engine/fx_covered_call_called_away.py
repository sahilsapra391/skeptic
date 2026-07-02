"""Fixture: covered call — stock rallies through the strike, called away.

  2025-01-06 (Mon)  entry. Spot closes 100.00. Capital 12,000.
    BUY 100 shares @ close 100.00 = −10,000.00 (stock legs: reference
      print, no added spread/commission — documented approximation)
    SELL 1 call K=105, exp 2025-01-17: bid 1.50 / ask 1.60
      fill = 1.55 − 0.5 × (1.55 − 1.50) = 1.525 → +152.50 − 0.65 = +151.85
    cash after entry: 12,000 − 10,000 + 151.85 = 2,151.85

  2025-01-17 (Fri)  expiration. Spot closes 108.00 → call ITM by 3.00.
    Called away: 100 shares delivered at strike 105 → +10,500.00
    final cash = 2,151.85 + 10,500.00 = 12,651.85

  P/L check: stock +5.00×100 + premium 151.85 = +651.85 ✓
    (the 3.00 the stock is above the strike belongs to the call buyer)
"""

from .common import call, make_spec

ENTRY = "2025-01-06"
EXPIRY = "2025-01-17"

CHAINS = {
    ENTRY: [call(105.0, 1.50, 1.60, 0.30, EXPIRY)],
    "2025-01-13": [call(105.0, 2.60, 2.80, 0.55, EXPIRY)],
}

UNDERLYING = {
    ENTRY: (99.5, 100.0),
    "2025-01-13": (104.0, 105.5),
    EXPIRY: (107.0, 108.0),
}

SPEC = make_spec(
    position={
        "structure": "covered_call",
        "legs": [
            {"right": "call", "side": "short", "ratio": 1,
             "strike_selection": {"method": "delta", "value": 0.30}}
        ],
        "expiration_selection": {"target_dte": 11, "min_dte": 1, "max_dte": 30},
    },
    entry={"schedule": {"frequency": "weekly", "day_of_week": "monday"},
           "conditions": [], "max_concurrent_positions": 1},
    exit={"time_exit_dte": 0},
    backtest={"start": ENTRY, "end": EXPIRY, "initial_capital": 12_000, "seed": 42},
)

EXPECT = {
    "final_cash": 12_651.85,
    "final_equity": 12_651.85,
    "closed_trades": 1,
    "actions": ["STOCK_BUY", "OPEN", "CALLED_AWAY"],
    "trade_pl": 651.85,
}
