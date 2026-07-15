"""Fixture: the buying-power gate refuses a debit the cash can't cover,
then a cheaper session fills (owner decision 2026-07-15).

Timeline (all prices hand-picked; SPY stand-in; capital $1,000):

  2025-01-06 (Mon)  spot closes 100.00.
    Chain: call K=100, exp 2025-01-10, bid 10.00 / ask 10.40, delta 0.50.
    BUY 1 call attempt:
      mid  = (10.00 + 10.40) / 2                 = 10.20
      fill = 10.20 + 0.5 × (10.40 − 10.20)       = 10.30
      debit = 10.30 × 100 + 0.65 commission      = 1,030.65 > 1,000 cash
      → SKIP insufficient_buying_power (shortfall $30.65); cash unchanged.
    Equity that evening = 1,000.00 (no position).

  2025-01-07 (Tue)  the call cheapens: bid 8.00 / ask 8.40.
    BUY 1 call:
      fill = 8.20 + 0.5 × (8.40 − 8.20)          = 8.30
      debit = 830.00 + 0.65                      = 830.65 ≤ 1,000 → FILLS
      cash = 1,000 − 830.65                      = 169.35
    Mark (sell-to-close) = 8.20 − 0.5 × 0.20     = 8.10
    equity = 169.35 + 810.00                     = 979.35

  2025-01-08 / 01-09  no chain rows → marks stale, equity stays 979.35.

  2025-01-10 (Fri)  expiration. Spot closes 99.00 → call OTM, expires
    worthless. Final cash = equity = 169.35; P/L = −830.65.
"""

from .common import call, make_spec

ENTRY_SKIP = "2025-01-06"
ENTRY_FILL = "2025-01-07"
EXPIRY = "2025-01-10"

CHAINS = {
    ENTRY_SKIP: [call(100.0, 10.00, 10.40, 0.50, EXPIRY)],
    ENTRY_FILL: [call(100.0, 8.00, 8.40, 0.50, EXPIRY)],
}

UNDERLYING = {
    ENTRY_SKIP: (100.0, 100.0),
    ENTRY_FILL: (100.0, 99.5),
    "2025-01-08": (99.5, 99.5),
    "2025-01-09": (99.5, 99.5),
    EXPIRY: (99.5, 99.0),
}

SPEC = make_spec(
    position={
        "structure": "long_call",
        "legs": [
            {"right": "call", "side": "long", "ratio": 1,
             "strike_selection": {"method": "delta", "value": 0.50}}
        ],
        "expiration_selection": {"target_dte": 4, "min_dte": 1, "max_dte": 30},
    },
    entry={"schedule": {"frequency": "daily"},
           "conditions": [], "max_concurrent_positions": 1},
    exit={"time_exit_dte": 0},
    backtest={"start": ENTRY_SKIP, "end": EXPIRY, "initial_capital": 1_000,
              "seed": 42},
)

EXPECT = {
    "final_cash": 169.35,
    "final_equity": 169.35,
    "equity_on": {ENTRY_SKIP: 1_000.00, ENTRY_FILL: 979.35, "2025-01-09": 979.35},
    "closed_trades": 1,
    "actions": ["SKIP", "OPEN", "EXPIRE"],
    "skip_reasons": ["insufficient_buying_power"],
    "trade_pl": -830.65,
}
