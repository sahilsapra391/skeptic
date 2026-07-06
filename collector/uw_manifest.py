#!/usr/bin/env python3
"""Unusual Whales endpoint manifest — declarative, so backfill_unusual_whales.py
stays engine-only. Scope: SPY / QQQ / IWM options, volatility, flow and dealer
positioning. Everything ticker-scoped for our three tickers plus the market-wide
options context; deliberately excludes congress / crypto / forex / private-markets /
predictions / institutions / darkpool-market / sector / full-tape (see EXCLUDED).

modes:
  ticker_series   one GET per ticker, no date — full history OR a current snapshot
                  → reference/uw/{name}/ticker={T}.parquet
  ticker_date     one GET per (ticker, session), date= param, iterated newest-first
                  → uw/{name}/ticker={T}/date={D}.parquet
  ohlc            one GET per (ticker, candle) — returns the full candle history
                  → reference/uw/ohlc/ticker={T}/candle={C}.parquet
  market_series   one GET, market-wide, no date
                  → reference/uw/{name}.parquet
  market_date     one GET per session, market-wide time series
                  → uw/{name}/date={D}.parquet

priority: 0 = cheapest+highest-value (one-call histories), rising to per-date sweeps
and the market-wide per-date series. The runner banks lower numbers first and the
newest sessions first, so a budget cutoff always keeps the most valuable + recent.

The probe REVISES these: a `date?` endpoint that returns many distinct dates in one
no-date call is really a series (downgrade ticker_date→ticker_series, huge budget
win); one that returns a single day must stay per-date.
"""

from __future__ import annotations

from typing import Any

# US options history on UW effectively begins here (market-tide 2022-09-28,
# full tape 2022-01-01). Per-endpoint floors override.
OPTIONS_FLOOR = "2022-01-01"

# candle sizes worth banking per ticker (each call returns full history for that
# size). 1m depth is typically shallower than the daily history — the collector
# banks whatever the endpoint returns and the empty/short tail is honest.
OHLC_CANDLES = ["1d", "1h", "30m", "5m", "1m"]

MANIFEST: list[dict[str, Any]] = [
    # ---- P0: one-call histories (full time series in a single request) --------
    {"name": "iv_rank", "path": "/api/stock/{ticker}/iv-rank", "mode": "ticker_series", "priority": 0},
    {"name": "greeks", "path": "/api/stock/{ticker}/greeks", "mode": "ticker_series", "priority": 0},
    {"name": "hist_risk_reversal_skew", "path": "/api/stock/{ticker}/historical-risk-reversal-skew",
     "mode": "ticker_series", "priority": 0},
    {"name": "volatility_realized", "path": "/api/stock/{ticker}/volatility/realized",
     "mode": "ticker_series", "priority": 0},
    {"name": "earnings", "path": "/api/stock/{ticker}/earnings", "mode": "ticker_series", "priority": 0},
    {"name": "ohlc", "path": "/api/stock/{ticker}/ohlc/{candle}", "mode": "ohlc", "priority": 0},
    {"name": "vix_term_structure", "path": "/api/volatility/vix-term-structure",
     "mode": "market_series", "priority": 0},
    {"name": "total_options_volume", "path": "/api/market/total-options-volume",
     "mode": "market_series", "priority": 0},

    # ---- P1: ticker snapshots (current state; one call each) -------------------
    {"name": "info", "path": "/api/stock/{ticker}/info", "mode": "ticker_series", "priority": 1},
    {"name": "stock_state", "path": "/api/stock/{ticker}/stock-state", "mode": "ticker_series", "priority": 1},
    {"name": "atm_chains", "path": "/api/stock/{ticker}/atm-chains", "mode": "ticker_series", "priority": 1},
    {"name": "flow_recent", "path": "/api/stock/{ticker}/flow-recent", "mode": "ticker_series", "priority": 1},
    {"name": "flow_per_expiry", "path": "/api/stock/{ticker}/flow-per-expiry",
     "mode": "ticker_series", "priority": 1},
    {"name": "options_volume", "path": "/api/stock/{ticker}/options-volume",
     "mode": "ticker_series", "priority": 1},
    {"name": "ownership", "path": "/api/stock/{ticker}/ownership", "mode": "ticker_series", "priority": 1},
    {"name": "etf_exposure", "path": "/api/etfs/{ticker}/exposure", "mode": "ticker_series", "priority": 1},
    {"name": "etf_holdings", "path": "/api/etfs/{ticker}/holdings", "mode": "ticker_series", "priority": 1},
    {"name": "etf_inoutflow", "path": "/api/etfs/{ticker}/in-outflow", "mode": "ticker_series", "priority": 1},
    {"name": "etf_info", "path": "/api/etfs/{ticker}/info", "mode": "ticker_series", "priority": 1},
    {"name": "etf_weights", "path": "/api/etfs/{ticker}/weights", "mode": "ticker_series", "priority": 1},
    {"name": "shorts_data", "path": "/api/shorts/{ticker}/data", "mode": "ticker_series", "priority": 1},
    {"name": "shorts_ftds", "path": "/api/shorts/{ticker}/ftds", "mode": "ticker_series", "priority": 1},
    {"name": "shorts_interest_float", "path": "/api/shorts/{ticker}/interest-float/v2",
     "mode": "ticker_series", "priority": 1},
    {"name": "shorts_volume_ratio", "path": "/api/shorts/{ticker}/volume-and-ratio",
     "mode": "ticker_series", "priority": 1},
    {"name": "shorts_volumes_by_exchange", "path": "/api/shorts/{ticker}/volumes-by-exchange",
     "mode": "ticker_series", "priority": 1},

    # ---- P2: ticker × date sweeps (dealer positioning, flow, vol — the signal
    #          families no other lake source carries) --------------------------
    {"name": "greek_exposure", "path": "/api/stock/{ticker}/greek-exposure", "mode": "ticker_date", "priority": 2},
    {"name": "greek_exposure_strike", "path": "/api/stock/{ticker}/greek-exposure/strike",
     "mode": "ticker_date", "priority": 2},
    {"name": "greek_exposure_expiry", "path": "/api/stock/{ticker}/greek-exposure/expiry",
     "mode": "ticker_date", "priority": 2},
    {"name": "greek_flow", "path": "/api/stock/{ticker}/greek-flow", "mode": "ticker_date", "priority": 2},
    {"name": "gex_levels", "path": "/api/stock/{ticker}/gex-levels", "mode": "ticker_date", "priority": 2},
    {"name": "spot_exposures", "path": "/api/stock/{ticker}/spot-exposures", "mode": "ticker_date", "priority": 2},
    {"name": "spot_exposures_strike", "path": "/api/stock/{ticker}/spot-exposures/strike",
     "mode": "ticker_date", "priority": 2},
    {"name": "max_pain", "path": "/api/stock/{ticker}/max-pain", "mode": "ticker_date", "priority": 2},
    {"name": "net_prem_ticks", "path": "/api/stock/{ticker}/net-prem-ticks", "mode": "ticker_date", "priority": 2},
    {"name": "nope", "path": "/api/stock/{ticker}/nope", "mode": "ticker_date", "priority": 2},
    {"name": "oi_change", "path": "/api/stock/{ticker}/oi-change", "mode": "ticker_date", "priority": 2},
    {"name": "oi_per_strike", "path": "/api/stock/{ticker}/oi-per-strike", "mode": "ticker_date", "priority": 2},
    {"name": "oi_per_expiry", "path": "/api/stock/{ticker}/oi-per-expiry", "mode": "ticker_date", "priority": 2},
    {"name": "interpolated_iv", "path": "/api/stock/{ticker}/interpolated-iv", "mode": "ticker_date", "priority": 2},
    {"name": "expiry_breakdown", "path": "/api/stock/{ticker}/expiry-breakdown", "mode": "ticker_date", "priority": 2},
    {"name": "option_chains", "path": "/api/stock/{ticker}/option-chains", "mode": "ticker_date", "priority": 2},
    {"name": "flow_per_strike", "path": "/api/stock/{ticker}/flow-per-strike", "mode": "ticker_date", "priority": 2},
    {"name": "flow_per_strike_intraday", "path": "/api/stock/{ticker}/flow-per-strike-intraday",
     "mode": "ticker_date", "priority": 2},
    {"name": "options_pulse", "path": "/api/stock/{ticker}/options-pulse", "mode": "ticker_date", "priority": 2},
    {"name": "volume_oi_expiry", "path": "/api/stock/{ticker}/option/volume-oi-expiry",
     "mode": "ticker_date", "priority": 2},
    {"name": "stock_price_levels", "path": "/api/stock/{ticker}/option/stock-price-levels",
     "mode": "ticker_date", "priority": 2},
    {"name": "stock_volume_price_levels", "path": "/api/stock/{ticker}/stock-volume-price-levels",
     "mode": "ticker_date", "priority": 2},
    {"name": "vol_anomaly", "path": "/api/stock/{ticker}/volatility/anomaly", "mode": "ticker_date", "priority": 2},
    {"name": "vol_character", "path": "/api/stock/{ticker}/volatility/character", "mode": "ticker_date", "priority": 2},
    {"name": "vol_stats", "path": "/api/stock/{ticker}/volatility/stats", "mode": "ticker_date", "priority": 2},
    {"name": "vol_term_structure", "path": "/api/stock/{ticker}/volatility/term-structure",
     "mode": "ticker_date", "priority": 2},
    {"name": "vol_variance_risk_premium", "path": "/api/stock/{ticker}/volatility/variance-risk-premium",
     "mode": "ticker_date", "priority": 2},
    {"name": "etf_tide", "path": "/api/market/{ticker}/etf-tide", "mode": "ticker_date", "priority": 2},
    {"name": "darkpool", "path": "/api/darkpool/{ticker}", "mode": "ticker_date", "priority": 2},
    {"name": "lit_flow", "path": "/api/lit-flow/{ticker}", "mode": "ticker_date", "priority": 2},

    # ---- P3: market-wide per-date series --------------------------------------
    {"name": "market_tide", "path": "/api/market/market-tide", "mode": "market_date",
     "priority": 3, "min_date": "2022-09-28"},
    {"name": "market_oi_change", "path": "/api/market/oi-change", "mode": "market_date", "priority": 3},
    {"name": "market_top_net_impact", "path": "/api/market/top-net-impact", "mode": "market_date", "priority": 3},
    {"name": "options_pulse_total", "path": "/api/options-pulse/total", "mode": "market_date", "priority": 3},
    {"name": "net_flow_expiry", "path": "/api/net-flow/expiry", "mode": "market_date", "priority": 3},
]

# ---- deliberately excluded (documented, not forgotten) -----------------------
# Needs a param we can't enumerate cheaply / not our tickers / not options-strategy
# data: /group-flow/{flow_group}, /stock/{sector}/tickers, /market/{sector}/sector-tide,
# /technical-indicator/{function} (we compute our own), /option-trades/full-tape/{date}
# and /exchange-breakdown/{date} (whole-market tape, enormous), congress·crypto·forex·
# digital-currencies·commodities·private-markets·predictions·politician·institutions·
# insider·companies·economy·calendar·screener·news·socket (websockets, live-only).
# Per-contract history (/option-contract/{id}/historic) is its OWN mode: `contracts`,
# fed by the banked option_chains listings — see backfill_unusual_whales.py.
