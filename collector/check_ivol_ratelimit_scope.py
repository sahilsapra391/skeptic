#!/usr/bin/env python3
"""
check_ivol_ratelimit_scope.py — is the iVol trial's ~90/min throttle PER-KEY or
PER-ACCOUNT?  Decides whether extra API keys multiply capture throughput.

Method: burst key[0] alone (baseline), then burst ALL keys CONCURRENTLY. If the
combined rate scales with the number of keys → independent budgets (PER-KEY, N
keys ≈ N×). If combined ≈ baseline → one shared bucket (PER-ACCOUNT, no gain).

Reads keys from env by name (values never printed). Bounded bursts (25 req/key on
the cheap underlying endpoint). Pause other iVol jobs first so they don't
contaminate a shared-account signal.

Run:
  uv run --no-project --with requests --env-file <path>/collector/.env \
      python check_ivol_ratelimit_scope.py
Env: KEY_VARS (CSV, default "IVOL_API_KEY,IVOL_API_KEY_2,IVOL_API_KEY_3").
"""
from __future__ import annotations

import os
import sys
import threading
import time

import requests

BASE = "https://restapi.ivolatility.com"
EP = "/equities/intraday/stock-prices"
PARAMS = {"symbol": "SPY", "date": "2024-06-14", "region": "USA", "minuteType": "MINUTE_5"}
N = int(os.environ.get("N", "25"))                 # requests per key per phase
PACE = float(os.environ.get("PACE", "0"))          # sec between requests per key (0=burst)
COOLDOWN = float(os.environ.get("COOLDOWN", "0"))  # idle before test so the limiter resets
KEY_VARS = os.environ.get("KEY_VARS", "IVOL_API_KEY,IVOL_API_KEY_2,IVOL_API_KEY_3")


def burst(key: str, n: int, out: dict) -> None:
    ok = c429 = other = 0
    t0 = time.time()
    for _ in range(n):
        try:
            r = requests.get(f"{BASE}{EP}", params=PARAMS, headers={"apiKey": key}, timeout=60)
            if r.status_code == 200:
                ok += 1
            elif r.status_code == 429:
                c429 += 1
            else:
                other += 1
        except Exception:
            other += 1
        if PACE:
            time.sleep(PACE)
    dt = time.time() - t0
    out.update(ok=ok, c429=c429, other=other, secs=round(dt, 1),
               rate=round(ok / dt * 60, 1) if dt else 0.0)


def main() -> int:
    keys: list[tuple[str, str]] = []
    seen: set[str] = set()
    for var in [v.strip() for v in KEY_VARS.split(",") if v.strip()]:
        val = os.environ.get(var)
        if not val:
            print(f"  (skip {var}: not set)")
            continue
        if val in seen:
            print(f"  (skip {var}: duplicate value)")
            continue
        seen.add(val)
        keys.append((var, val))
    if len(keys) < 2:
        sys.exit("need >=2 distinct active keys in env (set KEY_VARS)")

    if COOLDOWN:
        print(f"cooling down {COOLDOWN:.0f}s so the limiter resets…")
        time.sleep(COOLDOWN)

    print(f"=== iVol rate-limit scope test · {len(keys)} keys "
          f"({', '.join(k for k, _ in keys)}) · {N} req/phase ===\n")

    print(f"[baseline] {keys[0][0]} alone")
    base: dict = {}
    burst(keys[0][1], N, base)
    print(f"    {base['ok']}/{N} ok, {base['c429']} 429, {base['other']} other → {base['rate']}/min ({base['secs']}s)")

    print(f"\n[concurrent] all {len(keys)} keys at once")
    outs = [dict() for _ in keys]
    threads = [threading.Thread(target=burst, args=(val, N, o)) for (_, val), o in zip(keys, outs)]
    t0 = time.time()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    wall = time.time() - t0
    total_ok = sum(o["ok"] for o in outs)
    total_429 = sum(o["c429"] for o in outs)
    combined = round(total_ok / wall * 60, 1) if wall else 0.0
    for (var, _), o in zip(keys, outs):
        print(f"    {var}: {o['ok']}/{N} ok, {o['c429']} 429 → {o['rate']}/min")
    print(f"    COMBINED: {combined}/min, {total_429} total 429, {round(wall,1)}s")

    ratio = combined / base["rate"] if base["rate"] else 0.0
    nk = len(keys)
    print(f"\n=== verdict (combined/baseline = {round(ratio,2)}×, ideal {nk}×) ===")
    if ratio >= 0.8 * nk:
        print(f"PER-KEY — near-linear scaling. Keys are independent buckets.")
        print(f" -> use all {nk} keys → ~{nk}× capture. Split the work across them.")
    elif ratio <= 1.25:
        print("PER-ACCOUNT — concurrency did NOT raise throughput (one shared bucket).")
        print(" -> extra keys give no speedup. Keep one job; use a spare key as rotation.")
    else:
        eff = max(2, int(round(ratio)))
        print(f"SHARED-WITH-HEADROOM — ~{round(ratio,1)}× gain, plateaus ≈ {combined}/min.")
        print(f" -> ~{eff} keys saturate it; more won't help.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
