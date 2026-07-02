"""Fixture: short put held to expiration, finishes ITM, assigned.

Timeline (all prices hand-picked; SPY stand-in):

  2025-01-06 (Mon)  entry day. Spot closes 100.00.
    Chain: put K=100, exp 2025-01-17, bid 2.00 / ask 2.20, delta −0.30.
    SELL 1 put:
      mid  = (2.00 + 2.20) / 2                  = 2.10
      fill = 2.10 − 0.5 × (2.10 − 2.00)         = 2.05
      cash = +2.05 × 100 − 0.65 commission      = +204.35
      cash after entry: 10,000 + 204.35         = 10,204.35

  2025-01-17 (Fri)  expiration. Spot closes 94.00 → put is ITM by 6.00.
    time_exit_dte = 0 → hold to settlement. Assignment:
      buy 100 shares @ strike 100               = −10,000.00
      cash: 10,204.35 − 10,000.00               = 204.35
      equity that evening = cash + 100 sh × 94  = 204.35 + 9,400 = 9,604.35

  2025-01-21 (Tue; Mon 1/20 is MLK — next session)  liquidation at OPEN.
    Open 94.50: sell 100 sh                     = +9,450.00
      final cash = 204.35 + 9,450.00            = 9,654.35

  P/L check (independent route):
    intrinsic loss  −6.00 × 100  = −600.00
    premium kept    +204.35
    open-vs-close recovery (94.50 − 94.00)×100 = +50.00
    total: −600.00 + 204.35 + 50.00 = −345.65 → 10,000 − 345.65 = 9,654.35 ✓
"""

from .common import make_spec, put

ENTRY = "2025-01-06"
EXPIRY = "2025-01-17"
SETTLE = "2025-01-21"

CHAINS = {
    ENTRY: [put(100.0, 2.00, 2.20, -0.30, EXPIRY)],
    # mid-life snapshot so marking has something to chew on (no exit rules hit)
    "2025-01-13": [put(100.0, 3.00, 3.20, -0.45, EXPIRY)],
}

UNDERLYING = {
    ENTRY: (100.0, 100.0),
    "2025-01-13": (98.0, 97.0),
    EXPIRY: (95.0, 94.0),
    SETTLE: (94.50, 94.80),
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
    backtest={"start": ENTRY, "end": SETTLE, "initial_capital": 10_000, "seed": 42},
)

EXPECT = {
    "final_cash": 9_654.35,
    "final_equity": 9_654.35,
    "equity_on": {EXPIRY: 9_604.35},  # 204.35 cash + 9,400 stock
    "closed_trades": 1,
    "actions": ["OPEN", "ASSIGN", "STOCK_SELL"],
    "trade_pl": -345.65,
}
