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
    # probe 2026-07-06: no-date call returns the full aggregate-GEX TIME SERIES
    # (≈250 sessions) in ONE request — bank as a series, not per-date. Strike/
    # expiry-level GEX cross-sections still come per-date below.
    {"name": "greek_exposure", "path": "/api/stock/{ticker}/greek-exposure", "mode": "ticker_series", "priority": 1},
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
    # probe 2026-07-06: returns the full VRP time series (≈231 sessions) in one call
    {"name": "vol_variance_risk_premium", "path": "/api/stock/{ticker}/volatility/variance-risk-premium",
     "mode": "ticker_series", "priority": 1},
    {"name": "etf_tide", "path": "/api/market/{ticker}/etf-tide", "mode": "ticker_date", "priority": 2},
    {"name": "darkpool", "path": "/api/darkpool/{ticker}", "mode": "ticker_date", "priority": 2},
    {"name": "lit_flow", "path": "/api/lit-flow/{ticker}", "mode": "ticker_date", "priority": 2},

    # ---- P1b: full ticker coverage — every remaining one-call ticker endpoint
    #      (owner directive 2026-07-06: get EVERY endpoint for these tickers).
    #      Fundamentals/corporate/positioning; banked now, use decided later. --
    {"name": "financials", "path": "/api/stock/{ticker}/financials", "mode": "ticker_series", "priority": 1},
    {"name": "balance_sheets", "path": "/api/stock/{ticker}/balance-sheets", "mode": "ticker_series", "priority": 1},
    {"name": "cash_flows", "path": "/api/stock/{ticker}/cash-flows", "mode": "ticker_series", "priority": 1},
    {"name": "income_statements", "path": "/api/stock/{ticker}/income-statements", "mode": "ticker_series", "priority": 1},
    {"name": "fundamental_breakdown", "path": "/api/stock/{ticker}/fundamental-breakdown",
     "mode": "ticker_series", "priority": 1},
    {"name": "flow_alerts", "path": "/api/stock/{ticker}/flow-alerts", "mode": "ticker_series", "priority": 1},
    {"name": "insider_buy_sells", "path": "/api/stock/{ticker}/insider-buy-sells", "mode": "ticker_series", "priority": 1},
    {"name": "option_contracts", "path": "/api/stock/{ticker}/option-contracts", "mode": "ticker_series", "priority": 1},
    {"name": "earnings_ticker", "path": "/api/earnings/{ticker}", "mode": "ticker_series", "priority": 1},
    {"name": "insider_ticker", "path": "/api/insider/{ticker}", "mode": "ticker_series", "priority": 1},
    {"name": "insider_ticker_flow", "path": "/api/insider/{ticker}/ticker-flow", "mode": "ticker_series", "priority": 1},
    {"name": "institution_ownership", "path": "/api/institution/{ticker}/ownership", "mode": "ticker_series", "priority": 1},
    {"name": "shorts_interest_float_v1", "path": "/api/shorts/{ticker}/interest-float",
     "mode": "ticker_series", "priority": 1},
    {"name": "companies_profile", "path": "/api/companies/{ticker}/profile", "mode": "ticker_series", "priority": 1},
    {"name": "companies_dividends", "path": "/api/companies/{ticker}/dividends", "mode": "ticker_series", "priority": 1},
    {"name": "companies_splits", "path": "/api/companies/{ticker}/splits", "mode": "ticker_series", "priority": 1},
    {"name": "companies_earnings_estimates", "path": "/api/companies/{ticker}/earnings-estimates",
     "mode": "ticker_series", "priority": 1},
    {"name": "seasonality_monthly", "path": "/api/seasonality/{ticker}/monthly", "mode": "ticker_series", "priority": 1},
    {"name": "seasonality_year_month", "path": "/api/seasonality/{ticker}/year-month",
     "mode": "ticker_series", "priority": 1},
    {"name": "politician_holders", "path": "/api/politician-portfolios/holders/{ticker}",
     "mode": "ticker_series", "priority": 1},

    # ---- P3: market-wide per-date series --------------------------------------
    {"name": "market_tide", "path": "/api/market/market-tide", "mode": "market_date",
     "priority": 3, "min_date": "2022-09-28"},
    {"name": "market_oi_change", "path": "/api/market/oi-change", "mode": "market_date", "priority": 3},
    {"name": "market_top_net_impact", "path": "/api/market/top-net-impact", "mode": "market_date", "priority": 3},
    {"name": "options_pulse_total", "path": "/api/options-pulse/total", "mode": "market_date", "priority": 3},
    {"name": "net_flow_expiry", "path": "/api/net-flow/expiry", "mode": "market_date", "priority": 3},
]

# ---- expiry-sliced endpoints (mode `expiry`): enumerate each ticker's active
#      expiries, pull a CURRENT snapshot per expiry (historical is depth-capped on
#      the tariff anyway) → uw/{name}/ticker={T}/expiry={E}/rows.parquet.
#      `param`: "path" = expiry goes in the URL; else the query-param name. -------
EXPIRY_ENDPOINTS: list[dict[str, str]] = [
    {"name": "atm_chains", "path": "/api/stock/{ticker}/atm-chains", "param": "expirations[]"},
    {"name": "greek_exposure_strike_expiry", "path": "/api/stock/{ticker}/greek-exposure/strike-expiry",
     "param": "expiry"},
    {"name": "greek_flow_expiry", "path": "/api/stock/{ticker}/greek-flow/{expiry}", "param": "path"},
    {"name": "spot_exposures_by_expiry", "path": "/api/stock/{ticker}/spot-exposures/{expiry}/strike",
     "param": "path"},
    {"name": "spot_exposures_expiry_strike", "path": "/api/stock/{ticker}/spot-exposures/expiry-strike",
     "param": "expirations[]"},
]

# per-contract sub-endpoints (mode `contracts`): one call each per option symbol
# seen in the banked option_chains listings. historic = daily OHLC/NBBO/IV/OI.
CONTRACT_SUBS: list[tuple[str, str]] = [
    ("historic", "/api/option-contract/{id}/historic"),
    ("flow", "/api/option-contract/{id}/flow"),
    ("volume_profile", "/api/option-contract/{id}/volume-profile"),
]

# ---- deliberately excluded (documented, not forgotten) -----------------------
# stock/{ticker}/ownership — enterprise-only (confirmed 422, not on this plan).
# technical-indicator/{function} — derived indicators the engine already computes
#   natively (rsi/sma/ema…), not source data; function enum unpublished.
# companies/{ticker}/transcripts/{quarter} — earnings-call TEXT, no backtest value,
#   quarter format unconfirmed. option-contract/{id}/intraday — per-contract-per-day,
#   astronomically many requests (we hold iVol 5-min options already). NOT our
#   tickers / not options data: group-flow, sector-*, whole-market tape, congress·
#   crypto·forex·private-markets·predictions·news·socket (live-only websockets).
