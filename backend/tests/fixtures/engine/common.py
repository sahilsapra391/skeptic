"""Shared scaffolding for hand-computed engine fixtures.

Every fixture module defines:
  CHAINS:     {iso_date: [row, ...]}  rows: expiration/right/strike/bid/ask/delta
  UNDERLYING: {iso_date: (open, close)}  every session in the sim window
  SPEC:       full StrategySpec JSON
  EXPECT:     hand-computed expectations (final cash/equity to the CENT,
              plus trade-log assertions)

The math lives in comments next to the numbers. Engine conventions the
math relies on (documented once here, enforced by these tests):

  * Fill model (guardrail #1, TECH-SPEC §5): slip ∈ (0,1], default 0.5.
      BUY  fill = mid + slip * (ask − mid)
      SELL fill = mid − slip * (mid − bid)
    Commission (default $0.65) per contract per side, option legs only.
  * Liquidity (D1b): entries are gated (spread > max_spread_pct of mid, or
    KNOWN OI/volume below floors → skip with reason, or full-adverse fill
    in stress mode); exits are never gated. When OI is KNOWN and thin the
    slip fraction scales: slip_eff = slip + (1−slip)·max(0, 1−oi/(10·min_oi)).
    Rows here omit volume/OI (None = unknown) → base slip everywhere, so
    fixtures that predate D1b hold to the cent unchanged.
  * Multiplier 100. Credit/debit bases for profit/stop percentages use
    option fill prices only (commissions excluded from the base).
  * Marking: conservative liquidation side using the same fill model
    (short legs marked at buy-to-close cost, long legs at sell-to-close
    value). Exit triggers AND exit fills use these same prices.
  * Expiration (settled at expiry-day close, TECH-SPEC §5):
      long ITM  → cash-settle intrinsic
      short OTM → expires worthless
      short ITM put  → assigned: buy 100 sh @ strike, liquidate next
                       session OPEN (stock legs carry no added commission
                       or spread — documented approximation)
      short ITM call → covered: shares called away @ strike;
                       uncovered: short 100 sh @ strike, buy back next open
  * time_exit_dte = 0 means hold to expiration settlement; > 0 closes at
    the first session with quotes where DTE ≤ threshold.
"""

from __future__ import annotations

SPEC_BASE: dict = {
    "spec_version": 1,
    "meta": {"name": "fixture", "description_raw": "fixture"},
    "underlying": {"ticker": "SPY"},
    "position": {
        "structure": "short_put",
        "legs": [],
        "expiration_selection": {"target_dte": 11, "min_dte": 1, "max_dte": 30},
    },
    "entry": {
        "schedule": {"frequency": "daily"},
        "conditions": [],
        "max_concurrent_positions": 1,
    },
    "exit": {"time_exit_dte": 0},
    "sizing": {"method": "fixed_contracts", "value": 1},
    "costs": {"commission_per_contract": 0.65, "slippage_half_spread_fraction": 0.5},
    "backtest": {"start": None, "end": None, "initial_capital": 10_000, "seed": 42},
}


def make_spec(**overrides: object) -> dict:
    """Deep-ish merge of overrides into SPEC_BASE (one level per section)."""
    import copy

    spec = copy.deepcopy(SPEC_BASE)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(spec.get(key), dict):
            spec[key].update(value)
        else:
            spec[key] = value
    return spec


def put(strike: float, bid: float, ask: float, delta: float, expiration: str,
        volume: int | None = None, open_interest: int | None = None,
        vega: float | None = None) -> dict:
    return {
        "expiration": expiration, "right": "put", "strike": strike,
        "bid": bid, "ask": ask, "delta": delta,
        "volume": volume, "open_interest": open_interest, "vega": vega,
    }


def call(strike: float, bid: float, ask: float, delta: float, expiration: str,
         volume: int | None = None, open_interest: int | None = None,
         vega: float | None = None) -> dict:
    return {
        "expiration": expiration, "right": "call", "strike": strike,
        "bid": bid, "ask": ask, "delta": delta,
        "volume": volume, "open_interest": open_interest, "vega": vega,
    }
