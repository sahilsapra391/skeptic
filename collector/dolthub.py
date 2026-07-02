#!/usr/bin/env python3
"""
dolthub.py — one-shot SPY EOD backfill from the DoltHub community options
archive (post-no-preference/options), per the accepted conditions in
docs/DOLTHUB-EVAL.md §7. Scope: SPY only, 2020-01-06 → 2026-06-30; the
archive has no QQQ/IWM (eval §3) and the forward record is the Yahoo leg.

Conditions encoded here:
  1. XNYS-calendar filter — only true sessions are ever queried, which
     excludes the 53 holiday "phantom" snapshots; a duplicate-guard also
     drops any session byte-identical to its predecessor.
  2. Spot joined from our own underlying dailies; a session missing spot
     is a hard error, never a silent NaN.
  3. Vendor IV/greeks kept, greeks_source='vendor'.
  4. Reproducibility: the archive's dolt commit hash, per-run counts,
     flags, and gaps are recorded in state/dolthub_backfill.json.
  Known dead-quote-breach sessions (eval §5) are ingested but flagged.

Read path is the public DoltHub SQL API (PK-pinned `date IN (...)` seeks;
full-table scans time out). Row-count verification per date guards against
any response truncation. Resumable; idempotent by date key.

Env vars required: R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY,
R2_BUCKET. Personal-use archive (CC BY-SA 4.0); never redistributed.
Data goes to R2 only. Never commit data to git.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
import time
import urllib.parse
from datetime import date, datetime, timezone

import pandas as pd
import requests

from collect import (
    CANONICAL_COLUMNS,
    nyse,
    r2_client,
    r2_get_json,
    r2_get_parquet,
    r2_put_json,
    r2_put_parquet,
)

log = logging.getLogger("dolthub")

API = "https://www.dolthub.com/api/v1alpha1/post-no-preference/options/master"
TICKER = "SPY"
WINDOW_START = date(2020, 1, 6)
WINDOW_END = date(2026, 6, 30)
BATCH_DATES = 5
PACE_SECONDS = 1.0  # be polite to the free API
STATE_KEY = "state/dolthub_backfill.json"
FLAGGED_SESSIONS = {"2021-03-03", "2025-03-26"}  # dead-quote >20%, eval §5


class RowLimitError(Exception):
    """API response row cap hit — deterministic; split the query, don't retry."""


def query(sql: str) -> list[dict]:
    url = API + "?q=" + urllib.parse.quote(sql)
    for attempt in range(6):
        time.sleep(PACE_SECONDS)
        try:
            resp = requests.get(url, timeout=115,
                                headers={"User-Agent": "skeptic-collector/0.1 (personal research)"})
            payload = resp.json()
        except Exception as exc:
            log.warning("query failed (%s); retry %d", exc, attempt + 1)
            time.sleep(min(10 * 2 ** attempt, 120))
            continue
        status = payload.get("query_execution_status")
        if status == "Success":
            return payload["rows"]
        if status == "RowLimit":
            raise RowLimitError(sql[:80])
        log.warning("query status %s: %.100s", status,
                    payload.get("query_execution_message", ""))
        time.sleep(min(10 * 2 ** attempt, 120))
    raise RuntimeError(f"DoltHub query failed after retries: {sql[:120]}")


def head_commit() -> str:
    return query("SELECT commit_hash FROM dolt_log LIMIT 1")[0]["commit_hash"]


def expected_counts(dates: list[date]) -> dict[str, int]:
    date_list = ",".join(f"'{d}'" for d in dates)
    rows = query("SELECT date, COUNT(*) n FROM option_chain "
                 f"WHERE date IN ({date_list}) AND act_symbol='{TICKER}' GROUP BY date")
    return {r["date"]: int(r["n"]) for r in rows}


def fetch_rows(dates: list[date]) -> dict[str, list[dict]]:
    try:
        date_list = ",".join(f"'{d}'" for d in dates)
        rows = query("SELECT date, expiration, strike, call_put, bid, ask, vol, "
                     "delta, gamma, theta, vega, rho FROM option_chain "
                     f"WHERE date IN ({date_list}) AND act_symbol='{TICKER}'")
    except RowLimitError:
        # deterministic response cap: bisect the batch; a single date splits
        # by call/put (~100 rows per half, far under any observed cap)
        if len(dates) == 1:
            return {dates[0].isoformat(): fetch_single_date(dates[0].isoformat())}
        mid = len(dates) // 2
        out = fetch_rows(dates[:mid])
        out.update(fetch_rows(dates[mid:]))
        return out
    out = {}
    for r in rows:
        out.setdefault(r["date"], []).append(r)
    return out


def fetch_single_date(d: str) -> list[dict]:
    """Fallback with call/put split, in case a batch response was truncated."""
    rows: list[dict] = []
    for cp in ("Call", "Put"):
        rows.extend(query(
            "SELECT date, expiration, strike, call_put, bid, ask, vol, "
            "delta, gamma, theta, vega, rho FROM option_chain "
            f"WHERE date='{d}' AND act_symbol='{TICKER}' AND call_put='{cp}'"))
    return rows


def chain_fingerprint(rows: list[dict]) -> str:
    core = sorted((r["expiration"], r["strike"], r["call_put"],
                   r.get("bid"), r.get("ask"), r.get("vol")) for r in rows)
    return hashlib.sha256(json.dumps(core).encode()).hexdigest()


def to_canonical(d: str, rows: list[dict], spot: float, close_ts: pd.Timestamp) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    trading_date = date.fromisoformat(d)
    out = pd.DataFrame({
        "ticker": TICKER,
        "trading_date": d,
        "snapshot_ts": close_ts.isoformat(),
        "expiration": df["expiration"],
        "dte": (pd.to_datetime(df["expiration"]).dt.date - trading_date).map(lambda x: x.days),
        "right": df["call_put"].str.lower(),
        "strike": df["strike"].astype(float),
        "bid": pd.to_numeric(df["bid"], errors="coerce"),
        "ask": pd.to_numeric(df["ask"], errors="coerce"),
        "last": None,
        "volume": None,
        "open_interest": None,
        "iv": pd.to_numeric(df["vol"], errors="coerce"),
        "delta": pd.to_numeric(df["delta"], errors="coerce"),
        "gamma": pd.to_numeric(df["gamma"], errors="coerce"),
        "theta": pd.to_numeric(df["theta"], errors="coerce"),
        "vega": pd.to_numeric(df["vega"], errors="coerce"),
        "rho": pd.to_numeric(df["rho"], errors="coerce"),
        "greeks_source": "vendor",
        "spot": spot,
        "source": "dolthub",
    })
    return out[CANONICAL_COLUMNS]


def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default=WINDOW_START.isoformat())
    ap.add_argument("--end", default=WINDOW_END.isoformat())
    args = ap.parse_args()

    s3 = r2_client()
    cal = nyse()

    daily = r2_get_parquet(s3, f"underlying/ticker={TICKER}/daily.parquet")
    if daily is None or daily.empty:
        log.error("underlying dailies missing from the lake; refusing to ingest without spot")
        return 1
    closes = {pd.Timestamp(r["date"]).date().isoformat(): float(r["close"])
              for _, r in daily.iterrows()}

    state = r2_get_json(s3, STATE_KEY, {})
    done = set(state.get("done", []))
    missing = set(state.get("missing_in_archive", []))
    duplicates = set(state.get("duplicates_skipped", []))
    commit = head_commit()
    log.info("archive HEAD %s; window %s..%s", commit, args.start, args.end)

    sessions = [s.date() for s in cal.sessions_in_range(args.start, args.end)]
    pending = [s for s in sessions if s.isoformat() not in done | missing | duplicates]
    log.info("%d XNYS sessions in window, %d pending", len(sessions), len(pending))

    prev_fp = state.get("last_fingerprint")
    total_rows = int(state.get("total_rows", 0))
    for i in range(0, len(pending), BATCH_DATES):
        batch = pending[i:i + BATCH_DATES]
        expected = expected_counts(batch)
        got = fetch_rows(batch)
        for d in batch:
            ds = d.isoformat()
            rows = got.get(ds, [])
            want = expected.get(ds, 0)
            if want == 0:
                missing.add(ds)  # archive gap (M/W/F-era Tue/Thu, outages)
                continue
            if len(rows) != want:
                log.warning("%s: got %d rows, expected %d; refetching solo", ds, len(rows), want)
                rows = fetch_single_date(ds)
                if len(rows) != want:
                    raise RuntimeError(f"{ds}: row count mismatch persists ({len(rows)} != {want})")
            fp = chain_fingerprint(rows)
            if fp == prev_fp:
                log.warning("%s: identical to previous session; skipping (duplicate guard)", ds)
                duplicates.add(ds)
                continue
            prev_fp = fp
            spot = closes.get(ds)
            if spot is None:
                raise RuntimeError(f"{ds}: no spot in underlying dailies — condition 2 violation")
            frame = to_canonical(ds, rows, spot, cal.session_close(pd.Timestamp(d)))
            r2_put_parquet(s3, f"options/source=dolthub/ticker={TICKER}/date={ds}/chain.parquet", frame)
            done.add(ds)
            total_rows += len(frame)
        state.update({
            "commit_hash": commit,
            "window": [args.start, args.end],
            "done": sorted(done),
            "missing_in_archive": sorted(missing),
            "duplicates_skipped": sorted(duplicates),
            "flagged_dead_quote_sessions": sorted(FLAGGED_SESSIONS & done),
            "last_fingerprint": prev_fp,
            "total_rows": total_rows,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })
        r2_put_json(s3, STATE_KEY, state)
        if (i // BATCH_DATES) % 20 == 0:
            log.info("progress: %d ingested, %d archive gaps, %d rows",
                     len(done), len(missing), total_rows)

    state["completed_at"] = datetime.now(timezone.utc).isoformat()
    r2_put_json(s3, STATE_KEY, state)
    log.info("DONE: %d sessions ingested (%d rows), %d archive gaps, %d duplicates, "
             "flags on %s", len(done), total_rows, len(missing), len(duplicates),
             sorted(FLAGGED_SESSIONS & done))
    return 0


if __name__ == "__main__":
    sys.exit(main())
