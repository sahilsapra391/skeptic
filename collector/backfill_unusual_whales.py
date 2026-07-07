#!/usr/bin/env python3
"""
backfill_unusual_whales.py — bank Unusual Whales options/flow/vol data for
SPY · QQQ · IWM into the R2 lake. Built to run the moment a trial token exists.

Auth: HTTP header `Authorization: Bearer <UW_API_TOKEN>` (env, collector/.env).
Base: https://api.unusualwhales.com  ·  endpoint manifest: uw_manifest.py

The engine is manifest-driven and SELF-THROTTLING: it reads UW's own rate
headers off every response (`x-uw-req-per-minute-remaining`,
`x-uw-token-req-limit`, `x-uw-daily-req-count`) and paces itself under the
per-minute ceiling, stopping cleanly when the daily budget is nearly spent.
Everything is resumable (an R2 object or a state entry = done) and faithful
(rows are banked via json_normalize — we collect now, interpret in the engine
later, exactly the owner's instruction).

Modes:
  probe      hit ONE call per manifest endpoint; report status, row count, and —
             crucially — how many distinct dates a no-date call returns, so we
             learn which `date?` endpoints are one-call series vs per-date. Also
             prints the account's real daily/minute budget. RUN THIS FIRST.
  series     P0/P1 one-call endpoints (histories + snapshots), all tickers
  daily      P2/P3 per-date sweeps, newest session first, budget-gated
  contracts  per-contract daily history (OHLC+NBBO+IV+OI) for every option symbol
             seen in the banked option_chains listings — the QQQ/IWM chain rebuild
  all        series → daily → contracts, in that order

Prefixes:  reference/uw/{name}/...   (series/ohlc)
           uw/{name}/ticker={T}/date={D}/rows.parquet   (ticker_date)
           uw/{name}/date={D}/rows.parquet              (market_date)
           uw/option_hist/ticker={T}/symbol={SYM}.parquet (contracts)
State:     reference/state/uw_backfill.json
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import threading
import time
from datetime import datetime, timezone

import pandas as pd
import requests

from collect import (
    r2_client,
    r2_get_json,
    r2_list_keys,
    r2_put_json,
    r2_put_parquet,
)
from uw_manifest import MANIFEST, OHLC_CANDLES, OPTIONS_FLOOR

log = logging.getLogger("uw")

BASE = "https://api.unusualwhales.com"
STATE_KEY = "reference/state/uw_backfill.json"
TICKERS = ["SPY", "QQQ", "IWM"]
TIMEOUT = 90
RETRIES = 4
BACKOFF_BASE = 2.0
DEFAULT_RATE = 110          # req/min; UW ceiling ≈120, stay under
DAILY_SAFETY_MARGIN = 25    # stop this many requests short of the daily cap
PROBE_DATE = "2024-06-14"   # a boring, definitely-open session for date probes


# --------------------------------------------------------- auth + limiter
_TOKEN = ""


def _load_dotenv() -> None:
    env = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(env):
        return
    for line in open(env):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


class BudgetExhausted(RuntimeError):
    """Daily request budget nearly spent — stop cleanly, resume tomorrow."""


class Limiter:
    """Serial pacer that also honors UW's live headers: spaces requests under
    the per-minute ceiling, sleeps when the minute bucket is nearly empty, and
    raises BudgetExhausted as the daily cap approaches."""

    def __init__(self, per_min: float) -> None:
        self.interval = 60.0 / max(per_min, 1.0)
        self.lock = threading.Lock()
        self.next = 0.0
        self.daily_limit: int | None = None
        self.daily_used: int | None = None
        self.count = 0

    def wait(self) -> None:
        with self.lock:
            now = time.time()
            slot = max(now, self.next)
            self.next = slot + self.interval
            sleep = slot - now
        if sleep > 0:
            time.sleep(sleep)

    def observe(self, headers: "requests.structures.CaseInsensitiveDict") -> None:
        def gi(name: str) -> int | None:
            try:
                return int(headers.get(name))  # type: ignore[arg-type]
            except (TypeError, ValueError):
                return None

        self.count += 1
        lim, used = gi("x-uw-token-req-limit"), gi("x-uw-daily-req-count")
        rem, reset = gi("x-uw-req-per-minute-remaining"), gi("x-uw-req-per-minute-reset")
        if lim is not None:
            self.daily_limit = lim
        if used is not None:
            self.daily_used = used
        if rem is not None and rem <= 1 and reset:
            time.sleep(min(reset / 1000.0 + 0.2, 65))
        if self.daily_limit and self.daily_used is not None:
            if self.daily_used >= self.daily_limit - DAILY_SAFETY_MARGIN:
                raise BudgetExhausted(
                    f"daily budget nearly spent ({self.daily_used}/{self.daily_limit})"
                )


_LIM = Limiter(DEFAULT_RATE)


def _get(path: str, params: dict | None = None) -> tuple[int, object]:
    """Rate-gated Bearer GET with 429/5xx backoff. Returns (status, json)."""
    headers = {"Authorization": f"Bearer {_TOKEN}", "Accept": "application/json"}
    for attempt in range(RETRIES):
        _LIM.wait()
        try:
            r = requests.get(f"{BASE}{path}", params=params or {}, headers=headers, timeout=TIMEOUT)
        except Exception as exc:  # noqa: BLE001 — retry then give up
            if attempt == RETRIES - 1:
                log.warning("request error %s: %s", path, exc)
                return -1, None
            time.sleep(BACKOFF_BASE * 2**attempt)
            continue
        _LIM.observe(r.headers)
        if r.status_code == 200:
            try:
                return 200, r.json()
            except Exception:
                return 200, None
        if r.status_code in (429, 500, 502, 503, 504):
            time.sleep(BACKOFF_BASE * 2**attempt)
            continue
        return r.status_code, None  # 400/403 — no retry (tariff/param)
    return 429, None


def rows_of(body: object) -> list[dict]:
    if isinstance(body, list):
        return [x for x in body if isinstance(x, dict)]
    if isinstance(body, dict):
        for k in ("data", "chains", "rows", "result", "records"):
            v = body.get(k)
            if isinstance(v, list):
                return [x for x in v if isinstance(x, dict)]
        # a single flat object with real content is one row (all-null → none)
        vals = list(body.values())
        if vals and any(v is not None for v in vals) and all(
            not isinstance(v, (list, dict)) for v in vals
        ):
            return [body]
    return []


def to_frame(rows: list[dict], **extra) -> pd.DataFrame:
    """Faithful, schema-agnostic: flatten nested dicts, stringify list/dict cells
    so parquet is happy, stamp provenance. We bank everything now; the engine
    decides what to read later."""
    df = pd.json_normalize(rows, sep="_") if rows else pd.DataFrame()
    for col in df.columns:
        s = df[col].dropna()
        if len(s) and isinstance(s.iloc[0], (list, dict)):
            df[col] = df[col].map(lambda v: json.dumps(v) if isinstance(v, (list, dict)) else v)
    for k, v in extra.items():
        df[k] = v
    df["captured_at"] = datetime.now(timezone.utc).isoformat()
    return df


def _distinct_dates(rows: list[dict]) -> int:
    ds = set()
    for r in rows:
        for k in ("date", "start_date", "trading_date", "timestamp"):
            if r.get(k):
                ds.add(str(r[k])[:10])
                break
    return len(ds)


# ------------------------------------------------------------ sessions
def sessions_desc(start: str, end: str) -> list[str]:
    import exchange_calendars as xcals

    cal = xcals.get_calendar("XNYS")
    sess = cal.sessions_in_range(pd.Timestamp(start), pd.Timestamp(end))
    return [s.date().isoformat() for s in reversed(sess)]


# ------------------------------------------------------------ state
def _state(s3) -> dict:
    return r2_get_json(s3, STATE_KEY, {"units": {}, "dates": {}, "contracts": {}})


def _flush(s3, state: dict) -> None:
    r2_put_json(s3, STATE_KEY, state)


# ------------------------------------------------------------ probe
def probe(s3) -> int:
    report = []
    print(f"{'endpoint':30s} {'mode':12s} {'status':6s} {'rows':>6s} {'dates':>6s}  note")
    for ep in MANIFEST:
        name, path, mode = ep["name"], ep["path"], ep["mode"]
        if mode in ("ticker_series", "ticker_date"):
            url = path.format(ticker="SPY", candle="1d")
        elif mode == "ohlc":
            url = path.format(ticker="SPY", candle="1d")
        else:
            url = path
        code, body = _get(url)
        rows = rows_of(body)
        dd = _distinct_dates(rows)
        note = ""
        if mode in ("ticker_date", "market_date"):
            if dd > 1:
                note = f"SERIES in one call ({dd} dates) → downgrade to *_series"
            elif code == 200:
                note = "single-date → per-date iteration needed"
        if code == 403:
            note = "TARIFF-BLOCKED on this plan"
        print(f"{name:30s} {mode:12s} {code:<6d} {len(rows):>6d} {dd:>6d}  {note}")
        report.append({"name": name, "mode": mode, "status": code,
                       "rows": len(rows), "distinct_dates": dd, "note": note})
    print("\n=== account budget (from live headers) ===")
    print(f"daily limit: {_LIM.daily_limit} · used so far this run's day: {_LIM.daily_used}")
    print(f"requests this probe: {_LIM.count}")
    r2_put_json(s3, "reference/state/uw_probe_report.json",
                {"at": datetime.now(timezone.utc).isoformat(),
                 "daily_limit": _LIM.daily_limit, "daily_used": _LIM.daily_used,
                 "endpoints": report})
    return 0


# ------------------------------------------------------------ runners
def _write(s3, key: str, rows: list[dict], **extra) -> bool:
    if not rows:
        return False
    r2_put_parquet(s3, key, to_frame(rows, **extra))
    return True


def run_series(s3, state: dict, tickers: list[str], priorities: set[int], dry: bool) -> None:
    units = state["units"]
    for ep in MANIFEST:
        if ep["priority"] not in priorities:
            continue
        mode, name, path = ep["mode"], ep["name"], ep["path"]
        if mode == "ticker_series":
            for t in tickers:
                uid = f"{name}|{t}"
                if units.get(uid) in ("done", "empty"):
                    continue
                code, body = _get(path.format(ticker=t))
                if code == 403:
                    log.info("%s %s: tariff-blocked", name, t)
                    units[uid] = "empty"
                    continue
                rows = rows_of(body)
                key = f"reference/uw/{name}/ticker={t}.parquet"
                if not dry and _write(s3, key, rows, ticker=t, endpoint=name):
                    units[uid] = "done"
                    log.info("wrote %s (%d rows)", key, len(rows))
                else:
                    units[uid] = "empty"
                _flush(s3, state)
        elif mode == "market_series":
            uid = f"{name}|market"
            if units.get(uid) in ("done", "empty"):
                continue
            code, body = _get(path)
            rows = rows_of(body)
            key = f"reference/uw/{name}.parquet"
            units[uid] = "done" if (not dry and _write(s3, key, rows, endpoint=name)) else "empty"
            if units[uid] == "done":
                log.info("wrote %s (%d rows)", key, len(rows))
            _flush(s3, state)
        elif mode == "ohlc":
            for t in tickers:
                for candle in OHLC_CANDLES:
                    uid = f"{name}|{t}|{candle}"
                    if units.get(uid) in ("done", "empty"):
                        continue
                    code, body = _get(path.format(ticker=t, candle=candle))
                    rows = rows_of(body)
                    key = f"reference/uw/ohlc/ticker={t}/candle={candle}.parquet"
                    ok = not dry and _write(s3, key, rows, ticker=t, candle=candle, endpoint="ohlc")
                    units[uid] = "done" if ok else "empty"
                    if ok:
                        log.info("wrote %s (%d rows)", key, len(rows))
                    _flush(s3, state)


def run_daily(s3, state: dict, tickers: list[str], priorities: set[int],
              start: str, end: str, dry: bool) -> None:
    dates = state["dates"]
    for ep in MANIFEST:
        if ep["priority"] not in priorities or not ep["mode"].endswith("_date"):
            continue
        name, path, mode = ep["name"], ep["path"], ep["mode"]
        floor = ep.get("min_date") or start
        scopes = tickers if mode == "ticker_date" else ["market"]
        for scope in scopes:
            skey = f"{name}|{scope}"
            st = dates.setdefault(skey, {"done": [], "empty": [], "blocked": []})
            st.setdefault("blocked", [])
            seen = set(st["done"]) | set(st["empty"]) | set(st["blocked"])
            todo = [d for d in sessions_desc(floor, end) if d not in seen]
            if todo:
                log.info("%s %s: %d sessions", name, scope, len(todo))
            for i, d in enumerate(todo):
                url = path.format(ticker=scope) if mode == "ticker_date" else path
                code, body = _get(url, {"date": d})
                if code == 403:
                    # newest-first: a 403 IS this endpoint's history-depth floor
                    # on the current tariff — every OLDER date is unavailable too.
                    # Mark the remaining un-fetched dates blocked (never touching
                    # already-done ones) and stop this scope.
                    remaining = todo[i:]
                    st["blocked"].extend(x for x in remaining if x not in seen)
                    log.info("%s %s: history floor at %s — %d newer captured, %d older blocked",
                             name, scope, d, len(st["done"]), len(remaining))
                    break
                rows = rows_of(body)
                if mode == "ticker_date":
                    key = f"uw/{name}/ticker={scope}/date={d}/rows.parquet"
                    ok = not dry and _write(s3, key, rows, ticker=scope, date=d, endpoint=name)
                else:
                    key = f"uw/{name}/date={d}/rows.parquet"
                    ok = not dry and _write(s3, key, rows, date=d, endpoint=name)
                (st["done"] if ok else st["empty"]).append(d)
                if _LIM.count % 50 == 0:
                    _flush(s3, state)
            _flush(s3, state)


def run_contracts(s3, state: dict, tickers: list[str], dry: bool) -> None:
    """Chain rebuild: every option symbol seen in the banked option_chains
    listings gets its full daily history (one call each) → uw/option_hist/."""
    contracts = state["contracts"]
    for t in tickers:
        st = contracts.setdefault(t, {"done": [], "empty": []})
        seen = set(st["done"]) | set(st["empty"])
        # gather the symbol universe from the daily chain listings we banked
        symbols: set[str] = set()
        for key in r2_list_keys(s3, f"uw/option_chains/ticker={t}/"):
            df = None
            try:
                df = pd.read_parquet(  # noqa: PD901 — small per-day file
                    __import__("io").BytesIO(
                        s3.get_object(Bucket=os.environ["R2_BUCKET"], Key=key)["Body"].read()
                    )
                )
            except Exception:
                continue
            for col in ("option_symbol", "symbol", "chain", "ticker_symbol"):
                if col in df.columns:
                    symbols.update(str(x) for x in df[col].dropna().unique())
                    break
        todo = sorted(s for s in symbols if s not in seen)
        log.info("%s: %d option contracts to pull (%d already done)", t, len(todo), len(seen))
        for sym in todo:
            code, body = _get(f"/api/option-contract/{sym}/historic")
            if code == 403:
                log.info("contracts: tariff-blocked")
                return
            rows = rows_of(body)
            key = f"uw/option_hist/ticker={t}/symbol={sym}.parquet"
            ok = not dry and _write(s3, key, rows, ticker=t, occ_symbol=sym, endpoint="option_hist")
            (st["done"] if ok else st["empty"]).append(sym)
            if _LIM.count % 100 == 0:
                _flush(s3, state)
        _flush(s3, state)


# ------------------------------------------------------------ main
def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mode", choices=("probe", "series", "daily", "contracts", "all"),
                    default="probe")
    ap.add_argument("--tickers", default=",".join(TICKERS))
    ap.add_argument("--from", dest="start", default=OPTIONS_FLOOR)
    ap.add_argument("--to", dest="end", default=None)
    ap.add_argument("--priority", type=int, default=None,
                    help="only run this priority tier (default: all applicable)")
    ap.add_argument("--rate", type=int, default=DEFAULT_RATE, help="req/min ceiling")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()  # exits on --help before we need a token

    _load_dotenv()
    global _TOKEN, _LIM
    _TOKEN = os.environ.get("UW_API_TOKEN", "")
    if not _TOKEN:
        sys.exit("set UW_API_TOKEN in collector/.env")

    _LIM = Limiter(args.rate)
    tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    end = args.end or (datetime.now(timezone.utc).date().isoformat())
    s3 = r2_client()
    state = _state(s3)

    all_prios = {ep["priority"] for ep in MANIFEST}
    prios = {args.priority} if args.priority is not None else all_prios

    try:
        if args.mode == "probe":
            return probe(s3)
        if args.mode in ("series", "all"):
            run_series(s3, state, tickers, prios, args.dry_run)
        if args.mode in ("daily", "all"):
            run_daily(s3, state, tickers, prios, args.start, end, args.dry_run)
        if args.mode in ("contracts", "all"):
            run_contracts(s3, state, tickers, args.dry_run)
    except BudgetExhausted as exc:
        _flush(s3, state)
        log.warning("STOPPED: %s — rerun tomorrow to resume", exc)
        return 0
    _flush(s3, state)
    log.info("done (mode=%s, requests=%d)", args.mode, _LIM.count)
    return 0


if __name__ == "__main__":
    sys.exit(main())
