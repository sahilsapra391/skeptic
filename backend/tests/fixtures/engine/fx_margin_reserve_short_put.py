"""Fixture: the short-put margin reserve to the cent — a second concurrent
entry is unfundable while the reserve is held, and fundable again the
session the first position closes and releases it (owner 2026-07-15).

Reserve arithmetic (margin.py, the 20% rule; spot 100, strike 100, ATM):
  per share  = max(0.20 × 100 − max(0, 100 − 100), 0.10 × 100)
             = max(20.00, 10.00) = 20.00
  per contract = 20.00 × 100 = $2,000.00

Timeline (SPY stand-in; capital $2,500; max_concurrent 2; PT 50%):

  2025-01-06 (Mon)  spot 100. Put K=100 exp 01-17, bid 2.00 / ask 2.20.
    SELL 1 put (entry 1):
      fill = 2.10 − 0.5 × 0.10                  = 2.05
      credit = 205.00 − 0.65                    = +204.35
      gate: 2,500 + 204.35 − 2,000 reserve      = 704.35 ≥ 0 → FILLS
      cash 2,704.35 · reserved 2,000.00
    Mark (buy-to-close) = 2.10 + 0.5 × 0.10     = 2.15
    equity = 2,704.35 − 215.00                  = 2,489.35

  2025-01-07 (Tue)  same quote. Entry 2 attempt:
      gate: 2,704.35 + 204.35 − 2,000 held − 2,000 new = −1,091.30 < 0
      → SKIP insufficient_buying_power. equity unchanged 2,489.35.

  2025-01-08 (Wed)  put cheapens: bid 0.80 / ask 1.00.
    Exit first (PT 50): btc = 0.90 + 0.5 × 0.10 = 0.95
      profit = (2.05 − 0.95) / 2.05 = 53.66% ≥ 50% → CLOSE
      cash 2,704.35 − 95.65 = 2,608.70 · reserve RELEASED (0.00)
      P/L = 204.35 − 95.65 = +108.70
    Then entry 3 (same session, reserve free):
      fill = 0.90 − 0.5 × 0.10                  = 0.85
      credit = 85.00 − 0.65                     = +84.35
      gate: 2,608.70 + 84.35 − 2,000            = 693.05 ≥ 0 → FILLS
      cash 2,693.05 · reserved 2,000.00
    Mark = 0.90 + 0.5 × 0.10 = 0.95 → equity = 2,693.05 − 95 = 2,598.05
"""

from .common import make_spec, put

ENTRY = "2025-01-06"
SKIP_DAY = "2025-01-07"
CYCLE_DAY = "2025-01-08"
EXPIRY = "2025-01-17"

CHAINS = {
    ENTRY: [put(100.0, 2.00, 2.20, -0.50, EXPIRY)],
    SKIP_DAY: [put(100.0, 2.00, 2.20, -0.50, EXPIRY)],
    CYCLE_DAY: [put(100.0, 0.80, 1.00, -0.35, EXPIRY)],
}

UNDERLYING = {
    ENTRY: (100.0, 100.0),
    SKIP_DAY: (100.0, 100.0),
    CYCLE_DAY: (100.0, 100.0),
}

SPEC = make_spec(
    position={
        "structure": "short_put",
        "legs": [
            {"right": "put", "side": "short", "ratio": 1,
             "strike_selection": {"method": "delta", "value": 0.50}}
        ],
        "expiration_selection": {"target_dte": 11, "min_dte": 1, "max_dte": 30},
    },
    entry={"schedule": {"frequency": "daily"},
           "conditions": [], "max_concurrent_positions": 2},
    exit={"profit_target_pct": 50},
    backtest={"start": ENTRY, "end": CYCLE_DAY, "initial_capital": 2_500,
              "seed": 42},
)

EXPECT = {
    "final_cash": 2_693.05,
    "final_equity": 2_598.05,
    "equity_on": {ENTRY: 2_489.35, SKIP_DAY: 2_489.35, CYCLE_DAY: 2_598.05},
    "closed_trades": 1,
    "actions": ["OPEN", "SKIP", "CLOSE", "OPEN"],
    "skip_reasons": ["insufficient_buying_power"],
    "close_reason": "profit_target",
    "trade_pl": 108.70,
}
