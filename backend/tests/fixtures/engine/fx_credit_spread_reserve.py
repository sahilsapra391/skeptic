"""Fixture: the paired-leg (width) reserve — a put credit spread fits on
capital a NAKED short put could not (owner 2026-07-15).

Reserve arithmetic (margin.py pairing rule; spot 100):
  naked short put K=100 would reserve max(20, 10) × 100 = $2,000
  paired with the long K=95: reserve = width × 100 = (100 − 95) × 100 = $500

Timeline (SPY stand-in; capital $1,000 — under the naked reserve):

  2025-01-06 (Mon)  spot 100. Exp 2025-01-10.
    short put K=100: bid 2.00 / ask 2.20 → SELL fill = 2.10 − 0.05 = 2.05
    long  put K=95 : bid 1.00 / ask 1.20 → BUY  fill = 1.10 + 0.05 = 1.15
    entry cash = +205.00 − 0.65 − 115.00 − 0.65   = +88.70
    gate: 1,000 + 88.70 − 500 reserve             = 588.70 ≥ 0 → FILLS
    cash 1,088.70 · reserved 500.00
    Marks: btc K100 = 2.10 + 0.05 = 2.15 (−215) · stc K95 = 1.10 − 0.05
    = 1.05 (+105) → equity = 1,088.70 − 215 + 105 = 978.70

  2025-01-10 (Fri)  expiration, spot closes 98.00.
    long K=95 OTM → expires worthless.
    short K=100 ITM by 2.00 → assigned: buy 100 sh @ 100 = −10,000.00
      cash 1,088.70 − 10,000 = −8,911.30
      equity = −8,911.30 + 100 sh × 98             = 888.70 (> 0, no ruin)

  2025-01-13 (Mon)  liquidation at OPEN 98.50: +9,850.00
      final cash = equity = 938.70
      P/L = 88.70 − 10,000 + 9,850                 = −61.30
"""

from .common import make_spec, put

ENTRY = "2025-01-06"
EXPIRY = "2025-01-10"
SETTLE = "2025-01-13"

CHAINS = {
    ENTRY: [
        put(100.0, 2.00, 2.20, -0.50, EXPIRY),
        put(95.0, 1.00, 1.20, -0.30, EXPIRY),
    ],
}

UNDERLYING = {
    ENTRY: (100.0, 100.0),
    "2025-01-07": (100.0, 100.0),
    "2025-01-08": (100.0, 100.0),
    "2025-01-09": (100.0, 99.0),
    EXPIRY: (99.0, 98.0),
    SETTLE: (98.50, 98.80),
}

SPEC = make_spec(
    position={
        "structure": "put_credit_spread",
        "legs": [
            {"right": "put", "side": "short", "ratio": 1,
             "strike_selection": {"method": "delta", "value": 0.50}},
            {"right": "put", "side": "long", "ratio": 1,
             "strike_selection": {"method": "width_from_leg", "value": 5,
                                  "reference_leg": 0}},
        ],
        "expiration_selection": {"target_dte": 4, "min_dte": 1, "max_dte": 30},
    },
    entry={"schedule": {"frequency": "weekly", "day_of_week": "monday"},
           "conditions": [], "max_concurrent_positions": 1},
    exit={"time_exit_dte": 0},
    backtest={"start": ENTRY, "end": SETTLE, "initial_capital": 1_000,
              "seed": 42},
)

EXPECT = {
    "final_cash": 938.70,
    "final_equity": 938.70,
    "equity_on": {ENTRY: 978.70, EXPIRY: 888.70},
    "closed_trades": 1,
    "actions": ["OPEN", "EXPIRE", "ASSIGN", "STOCK_SELL"],
    "trade_pl": -61.30,
}
