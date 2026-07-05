"""Fixture: an iron condor's delta stop fires on the short put (D1c,
owner amendment 5 — the delta-stop fixture set must include a multi-leg
case; the WHOLE condor closes when one watched leg breaches).

  2025-01-06 (Mon)  entry. Spot 100.00. Exp 2025-01-17.
    (same entry chain as fx_iron_condor_target)
    short put  K=95 : bid 1.20/ask 1.30 → SELL = 1.25 − 0.5×0.05 = 1.225
    long  put  K=90 : bid 0.60/ask 0.70 → BUY  = 0.65 + 0.5×0.05 = 0.675
    short call K=105: bid 1.10/ask 1.20 → SELL = 1.15 − 0.5×0.05 = 1.125
    long  call K=110: bid 0.55/ask 0.65 → BUY  = 0.60 + 0.5×0.05 = 0.625
    net credit = 2.35 − 1.30 = 1.05
    cash = +105.00 − 4×0.65 = +102.40 → 10,102.40

  2025-01-07 (Tue)  spot gaps to 96. Watched legs = the SHORT legs:
    short put delta −0.65 → |−0.65| ≥ 0.60 threshold → DELTA STOP.
    (short call at 0.06 is quiet; one breaching watched leg is enough.)
    Close ALL four legs at today's quotes:
      btc  put 95  = (2.90+3.10)/2 + 0.5×0.10 = 3.05
      stc  put 90  = (1.30+1.44)/2 − 0.5×0.07 = 1.335
      btc call 105 = (0.20+0.30)/2 + 0.5×0.05 = 0.275
      stc call 110 = (0.08+0.16)/2 − 0.5×0.04 = 0.10
    exit cash = −305.00 + 133.50 − 27.50 + 10.00 − 4×0.65
              = −189.00 − 2.60 = −191.60
    final cash = 10,102.40 − 191.60 = 9,910.80

  P/L check: credit 1.05 − close cost (3.05 − 1.335 + 0.275 − 0.10 = 1.89)
             = −0.84 × 100 = −84.00 − 8×0.65 = −89.20 ✓
"""

from .common import call, make_spec, put

ENTRY = "2025-01-06"
STOP_DAY = "2025-01-07"
EXPIRY = "2025-01-17"

CHAINS = {
    ENTRY: [
        put(95.0, 1.20, 1.30, -0.20, EXPIRY),
        put(90.0, 0.60, 0.70, -0.12, EXPIRY),
        call(105.0, 1.10, 1.20, 0.20, EXPIRY),
        call(110.0, 0.55, 0.65, 0.12, EXPIRY),
    ],
    STOP_DAY: [
        put(95.0, 2.90, 3.10, -0.65, EXPIRY),
        put(90.0, 1.30, 1.44, -0.35, EXPIRY),
        call(105.0, 0.20, 0.30, 0.06, EXPIRY),
        call(110.0, 0.08, 0.16, 0.03, EXPIRY),
    ],
}

UNDERLYING = {
    ENTRY: (100.0, 100.0),
    STOP_DAY: (97.0, 96.0),
}

SPEC = make_spec(
    spec_version=2,
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
    exit={"delta_stop_abs": 0.60},
    backtest={"start": ENTRY, "end": STOP_DAY, "initial_capital": 10_000, "seed": 42},
)

EXPECT = {
    "final_cash": 9_910.80,
    "final_equity": 9_910.80,
    "closed_trades": 1,
    "actions": ["OPEN", "CLOSE"],
    "close_reason": "delta_stop",
    "trade_pl": -89.20,
}
