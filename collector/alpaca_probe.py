#!/usr/bin/env python3
"""
alpaca_probe.py — M1.5 step-0 verification (BUILD-PLAN M1.5).

Read-only probes against Alpaca, run BEFORE any bulk pull:
  A. Does /v2/options/contracts (status=inactive) reach expiries back to 2024-02?
  B. Do 1-min bars return for long-expired contracts?
  C. Are historical option QUOTES servable on the Basic plan (per feed)?
  D. Probe-day bar density -> refined volume / storage / runtime estimate.
  E. Underlying minute bars entitlement.

Prints FINDING lines (grep-able from Actions logs). Writes nothing to R2.
Env: APCA_API_KEY_ID, APCA_API_SECRET_KEY.
"""

from __future__ import annotations

import io
import os
import sys
import time
from datetime import date

import pandas as pd
import requests

TRADING = "https://paper-api.alpaca.markets"
DATA = "https://data.alpaca.markets"
HEADERS = {
    "APCA-API-KEY-ID": os.environ["APCA_API_KEY_ID"],
    "APCA-API-SECRET-KEY": os.environ["APCA_API_SECRET_KEY"],
}
TICKERS = ["SPY", "QQQ", "IWM"]
PROBE_DAYS = ("2025-03-12", "2025-03-13")  # Wed+Thu, normal recent week
PROBE_EXP_LTE = "2025-06-13"  # <=90d out from probe week
HISTORY_START = date(2024, 2, 1)
REQUEST_BUDGET = 900
PACE_SECONDS = 0.35

_requests_used = 0
_last_rl_headers: dict = {}


def get(url: str, params: dict) -> requests.Response:
    global _requests_used, _last_rl_headers
    if _requests_used >= REQUEST_BUDGET:
        raise RuntimeError(f"probe request budget ({REQUEST_BUDGET}) exhausted")
    time.sleep(PACE_SECONDS)
    r = requests.get(url, params=params, headers=HEADERS, timeout=30)
    _requests_used += 1
    rl = {k: v for k, v in r.headers.items() if k.lower().startswith("x-ratelimit")}
    if rl:
        _last_rl_headers = rl
    if r.status_code == 429:
        time.sleep(20)
        return get(url, params)
    return r


def contracts(underlying: str, exp_gte: str, exp_lte: str, status: str) -> list[dict]:
    out, token = [], None
    while True:
        params = {
            "underlying_symbols": underlying, "status": status,
            "expiration_date_gte": exp_gte, "expiration_date_lte": exp_lte,
            "limit": 10000,
        }
        if token:
            params["page_token"] = token
        r = get(f"{TRADING}/v2/options/contracts", params)
        if r.status_code != 200:
            print(f"    contracts {underlying} {status} {exp_gte}..{exp_lte}: HTTP {r.status_code} {r.text[:150]}")
            return out
        d = r.json()
        out.extend(d.get("option_contracts") or d.get("contracts") or [])
        token = d.get("next_page_token")
        if not token:
            return out


def bars(symbols: list[str], start: str, end: str) -> tuple[int, list[dict]]:
    """1-min bars for up to 100 symbols; returns (row_count, flat_rows)."""
    rows, token = [], None
    while True:
        params = {
            "symbols": ",".join(symbols), "timeframe": "1Min",
            "start": start, "end": end, "limit": 10000,
        }
        if token:
            params["page_token"] = token
        r = get(f"{DATA}/v1beta1/options/bars", params)
        if r.status_code != 200:
            print(f"    bars HTTP {r.status_code}: {r.text[:150]}")
            return len(rows), rows
        d = r.json()
        for sym, bs in (d.get("bars") or {}).items():
            for b in bs or []:
                rows.append({"symbol": sym, **b})
        token = d.get("next_page_token")
        if not token:
            return len(rows), rows


def main() -> None:
    print(f"== M1.5 step-0 probe, {date.today().isoformat()} ==", flush=True)

    # ---- A: expired-contract listing depth --------------------------------
    print("\n[A] contracts endpoint, expired depth")
    feb24 = {}
    for t in TICKERS:
        cs = contracts(t, "2024-02-01", "2024-02-29", "inactive")
        exps = sorted({c["expiration_date"] for c in cs})
        feb24[t] = cs
        print(f"FINDING A {t}: {len(cs)} inactive contracts expiring 2024-02; "
              f"expirations {exps[:3]}{'...' if len(exps) > 3 else ''}")

    # ---- B: bars for long-expired contracts -------------------------------
    print("\n[B] 1-min bars on expired contracts (2024-02 SPY)")
    if feb24["SPY"]:
        cs = sorted(feb24["SPY"], key=lambda c: float(c["strike_price"]))
        mid = len(cs) // 2
        probe_syms = [c["symbol"] for c in cs[mid - 2: mid + 3]]
        n, _ = bars(probe_syms, "2024-02-05T00:00:00Z", "2024-02-16T23:59:59Z")
        print(f"FINDING B: {n} 1-min bars for 5 ATM-ish symbols {probe_syms[:2]}... over 2024-02-05..16")
    else:
        print("FINDING B: SKIPPED (no contracts from A)")

    # ---- C: historical quotes entitlement ---------------------------------
    print("\n[C] historical option quotes on Basic plan")
    sym = feb24["SPY"][len(feb24['SPY']) // 2]["symbol"] if feb24["SPY"] else "SPY250321C00560000"
    for feed in (None, "indicative", "opra"):
        params = {"symbols": sym, "start": "2024-02-15T15:00:00Z",
                  "end": "2024-02-15T15:05:00Z", "limit": 100}
        if feed:
            params["feed"] = feed
        r = get(f"{DATA}/v1beta1/options/quotes", params)
        nrows = sum(len(v) for v in (r.json().get("quotes") or {}).values()) if r.status_code == 200 else 0
        print(f"FINDING C feed={feed or 'default'}: HTTP {r.status_code}, rows={nrows}, body={r.text[:120] if r.status_code != 200 else 'ok'}")

    # ---- D: probe-day density -> lake size estimate ------------------------
    print(f"\n[D] bar density, {PROBE_DAYS}, exps<= {PROBE_EXP_LTE}")
    all_rows: list[dict] = []
    est = {}
    for t in TICKERS:
        cs = contracts(t, PROBE_DAYS[0], PROBE_EXP_LTE, "inactive")
        syms = sorted(c["symbol"] for c in cs)
        sample_factor = 1
        if len(syms) > 4000:  # keep probe lean; sample every 2nd, scale by 2
            syms, sample_factor = syms[::2], 2
        total = 0
        for i in range(0, len(syms), 100):
            n, rows = bars(syms[i:i + 100], f"{PROBE_DAYS[0]}T00:00:00Z", f"{PROBE_DAYS[1]}T23:59:59Z")
            total += n
            all_rows.extend(rows[:50000])
        est[t] = (len(cs), total * sample_factor)
        print(f"FINDING D {t}: universe(<=90d)={len(cs)} contracts, "
              f"bars_2days~{total * sample_factor} (sample_factor={sample_factor})")

    # parquet bytes/row on the real schema-ish frame
    bpr = 20.0
    if all_rows:
        df = pd.DataFrame(all_rows)
        buf = io.BytesIO()
        df.to_parquet(buf, index=False)
        bpr = len(buf.getvalue()) / len(df)
    sessions = 600  # ~2024-02 -> now
    rows_per_day = sum(v[1] for v in est.values()) / 2
    total_rows = rows_per_day * sessions
    gb = total_rows * bpr / 1e9
    reqs = total_rows / 8000  # ~10k page limit, imperfect packing
    print(f"FINDING D-EST: ~{rows_per_day:,.0f} bar-rows/session all 3 tickers; "
          f"~{total_rows/1e6:,.0f}M rows total; ~{bpr:.1f} B/row parquet -> ~{gb:.1f} GB; "
          f"~{reqs:,.0f} requests ~= {reqs/200/60:.1f}h at 200/min")

    # ---- E: underlying minute bars -----------------------------------------
    print("\n[E] underlying 1-min bars")
    r = get(f"{DATA}/v2/stocks/bars", {
        "symbols": "SPY,QQQ,IWM", "timeframe": "1Min",
        "start": "2024-02-05T00:00:00Z", "end": "2024-02-06T23:59:59Z", "limit": 10000,
    })
    if r.status_code == 200:
        d = r.json()
        counts = {k: len(v) for k, v in (d.get("bars") or {}).items()}
        print(f"FINDING E: HTTP 200, bars {counts}")
    else:
        print(f"FINDING E: HTTP {r.status_code} {r.text[:150]}")

    print(f"\nFINDING RATE-LIMIT headers: {_last_rl_headers}")
    print(f"FINDING requests used: {_requests_used}")
    print("== probe complete ==")


if __name__ == "__main__":
    sys.exit(main())
