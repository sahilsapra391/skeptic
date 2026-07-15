"""Fixture: assignment drives equity through $0 → the ruin halt stops the
simulation RIGHT THERE (owner decision 2026-07-15, docs/HONESTY.md).

Timeline (SPY stand-in; capital $3,000):

  2025-01-06 (Mon)  spot 100. Put K=100 exp 2025-01-10, bid 2.00 / ask 2.20.
    SELL 1 put:
      fill = 2.10 − 0.5 × 0.10                   = 2.05
      credit = 205.00 − 0.65                     = +204.35
      gate: 3,000 + 204.35 − 2,000 reserve       = 1,204.35 ≥ 0 → FILLS
      cash = 3,204.35
    Mark (buy-to-close) = 2.15 → equity = 3,204.35 − 215 = 2,989.35

  2025-01-07 → 01-09  no chain rows: marks stale, equity 2,989.35.

  2025-01-10 (Fri)  CRASH — spot closes 25.00. Expiration:
    short put ITM by 75.00 → assigned: buy 100 sh @ 100 = −10,000.00
      cash = 3,204.35 − 10,000                   = −6,795.65
      equity = −6,795.65 + 100 sh × 25.00        = −4,295.65  ≤ $0
    → RUIN HALT: HALT event, the position CLOSEd at its mark
      (reason ruin_halt), simulation stops. The 2025-01-13 session in the
      window is NEVER simulated — dates/equity end at 2025-01-10.
      position P/L = cash_flow + mark = (204.35 − 10,000) + 2,500
                                                 = −7,295.65

  The halt fires at exactly $0 crossing — the LATEST possible ruin date
  (a real margin account is liquidated before zero; disclosed).
"""

from .common import make_spec, put

ENTRY = "2025-01-06"
EXPIRY = "2025-01-10"
NEVER_REACHED = "2025-01-13"

CHAINS = {
    ENTRY: [put(100.0, 2.00, 2.20, -0.50, EXPIRY)],
}

UNDERLYING = {
    ENTRY: (100.0, 100.0),
    "2025-01-07": (100.0, 100.0),
    "2025-01-08": (100.0, 100.0),
    "2025-01-09": (100.0, 100.0),
    EXPIRY: (30.0, 25.0),
    NEVER_REACHED: (26.0, 26.0),
}

SPEC = make_spec(
    position={
        "structure": "short_put",
        "legs": [
            {"right": "put", "side": "short", "ratio": 1,
             "strike_selection": {"method": "delta", "value": 0.50}}
        ],
        "expiration_selection": {"target_dte": 4, "min_dte": 1, "max_dte": 30},
    },
    entry={"schedule": {"frequency": "weekly", "day_of_week": "monday"},
           "conditions": [], "max_concurrent_positions": 1},
    exit={"time_exit_dte": 0},
    backtest={"start": ENTRY, "end": NEVER_REACHED, "initial_capital": 3_000,
              "seed": 42},
)

EXPECT = {
    "final_cash": -6_795.65,
    "final_equity": -4_295.65,
    "equity_on": {ENTRY: 2_989.35, EXPIRY: -4_295.65},
    "closed_trades": 1,
    "actions": ["OPEN", "ASSIGN", "HALT", "CLOSE"],
    "close_reason": "ruin_halt",
    "trade_pl": -7_295.65,
    "ruin": {"date": EXPIRY, "equity": -4_295.65},
}
