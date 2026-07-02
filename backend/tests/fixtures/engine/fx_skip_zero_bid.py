"""Fixture: scheduled entry skipped — the selected short strike has a zero
bid (selling into no market is never modeled as a fill; TECH-SPEC §5).

  2025-01-06 (Mon)  entry day. Spot 100.00.
    The delta-selected put (K=95, delta −0.30) quotes bid 0.00 / ask 0.35.
    Zero bid on a SELL leg → trade skipped, reason 'zero_bid_short'.
    No position ever opens; equity stays exactly 10,000.00.
"""

from .common import make_spec, put

ENTRY = "2025-01-06"
EXPIRY = "2025-01-17"

CHAINS = {
    ENTRY: [put(95.0, 0.00, 0.35, -0.30, EXPIRY)],
}

UNDERLYING = {
    ENTRY: (100.0, 100.0),
    "2025-01-07": (100.1, 100.3),
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
    exit={"profit_target_pct": 50},
    backtest={"start": ENTRY, "end": "2025-01-07", "initial_capital": 10_000, "seed": 42},
)

EXPECT = {
    "final_cash": 10_000.00,
    "final_equity": 10_000.00,
    "closed_trades": 0,
    "skip_reasons": ["zero_bid_short"],
    "actions": ["SKIP"],
}
