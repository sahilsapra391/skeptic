"""Fixture: the far-OTM fantasy fill is refused (D1b acceptance).

Before the liquidity gates, this strategy "worked": sell a 30Δ put quoted
bid 0.02 / ask 0.30 and bank the credit — a fill no live market would give.

  Both sessions. Spot 100.00. Exp 2025-01-17.
    put K=95: bid 0.02 / ask 0.30 → mid 0.16
    spread% = (0.30 − 0.02) / 0.16 = 175% > max_spread_pct 25%
    → entry skipped, reason 'illiquid_spread' (default liquidity_mode=skip)

  No position ever opens: final equity = initial capital, to the cent.
"""

from .common import make_spec, put

DAY1 = "2025-01-06"
DAY2 = "2025-01-07"
EXPIRY = "2025-01-17"

CHAINS = {
    DAY1: [put(95.0, 0.02, 0.30, -0.30, EXPIRY)],
    DAY2: [put(95.0, 0.02, 0.30, -0.30, EXPIRY)],
}

UNDERLYING = {
    DAY1: (100.0, 100.0),
    DAY2: (100.0, 100.0),
}

SPEC = make_spec(
    position={
        "structure": "short_put",
        "legs": [
            {"right": "put", "side": "short", "ratio": 1,
             "strike_selection": {"method": "delta", "value": 0.30}},
        ],
        "expiration_selection": {"target_dte": 11, "min_dte": 1, "max_dte": 30},
    },
    exit={"time_exit_dte": 0},
    backtest={"start": DAY1, "end": DAY2, "initial_capital": 10_000, "seed": 42},
)

EXPECT = {
    "final_cash": 10_000.00,
    "final_equity": 10_000.00,
    "closed_trades": 0,
    "trade_pl": None,
    "actions": ["SKIP"],
    "skip_reasons": ["illiquid_spread"],
}
