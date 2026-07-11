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

State only records FINAL outcomes: 200 (a definitive body, even empty) and 403
(tariff/depth floor). Transient failures — network errors, 429/5xx after
backoff, stray 4xx — leave the date/unit unrecorded so a later run retries it,
and per-date sweeps never include the current session before the options close
(16:15 ET): a pre-close sweep would finalize a partial day.

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
import tempfile
import threading
import time
import zipfile
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from collect import (
    r2_client,
    r2_get_json,
    r2_list_keys,
    r2_put_json,
    r2_put_parquet,
)
from uw_manifest import (
    CONTRACT_SUBS,
    EXPIRY_ENDPOINTS,
    MANIFEST,
    OHLC_CANDLES,
    OPTIONS_FLOOR,
)

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
FINAL_CODES = (200, 403)    # the only outcomes ever recorded in state
TRANSIENT_STOP = 3          # consecutive transient failures before a scope is abandoned
NETWORK_DOWN_AFTER = 8      # consecutive transient outcomes → abort the whole run
ET = ZoneInfo("America/New_York")
OPTIONS_CLOSE_ET = (16, 15)  # options close 16:15 ET; before that, today is partial


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


class NetworkDown(RuntimeError):
    """Transient outcomes on every recent request across scopes — the host,
    not the API, is failing. TRANSIENT_STOP already caps each scope at 3
    futile tries, but with ~50 scopes a dead network still grinds for hours
    (each try survives 4 in-function retries with up to 90 s timeouts) and
    then exits 0 — invisible to the collection-guaranteed-daily alerting.
    Abort instead: unrecorded work retries next run by design."""


# run-level network health, fed by _get: `consecutive` trips NetworkDown,
# `transient_total` makes a completed-but-partial run exit non-zero
_net = {"consecutive": 0, "transient_total": 0}


def _note_outcome(code: int) -> None:
    if _transient(code):
        _net["consecutive"] += 1
        _net["transient_total"] += 1
        if _net["consecutive"] >= NETWORK_DOWN_AFTER:
            raise NetworkDown(
                f"{_net['consecutive']} consecutive transient outcomes")
    else:
        _net["consecutive"] = 0


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
    """Rate-gated Bearer GET with 429/5xx backoff. Returns (status, json).
    Every outcome feeds _note_outcome, which raises NetworkDown once
    NETWORK_DOWN_AFTER consecutive requests come back transient."""
    headers = {"Authorization": f"Bearer {_TOKEN}", "Accept": "application/json"}
    for attempt in range(RETRIES):
        _LIM.wait()
        try:
            r = requests.get(f"{BASE}{path}", params=params or {}, headers=headers, timeout=TIMEOUT)
        except Exception as exc:  # noqa: BLE001 — retry then give up
            if attempt == RETRIES - 1:
                log.warning("request error %s: %s", path, exc)
                _note_outcome(-1)
                return -1, None
            time.sleep(BACKOFF_BASE * 2**attempt)
            continue
        _LIM.observe(r.headers)
        if r.status_code == 200:
            _note_outcome(200)
            try:
                return 200, r.json()
            except Exception:
                return 200, None
        if r.status_code in (429, 500, 502, 503, 504):
            time.sleep(BACKOFF_BASE * 2**attempt)
            continue
        _note_outcome(r.status_code)
        return r.status_code, None  # 400/403 — no retry (tariff/param)
    _note_outcome(429)
    return 429, None


def _transient(code: int) -> bool:
    """True when the outcome could differ on a future run and must therefore
    never be recorded in state. Only 200 (a definitive body, even an empty one)
    and 403 (tariff/depth floor) are final answers; -1 (network down after all
    retries), 429/5xx (backoff exhausted) and stray 4xx (expired token) would
    permanently poison the date as "empty" if banked — a DNS-hijacked network
    did exactly that to 89 scopes on 2026-07-10."""
    return code not in FINAL_CODES


def _last_complete_session(end: str) -> str:
    """Refuse to sweep the CURRENT session before the options close (16:15 ET):
    pre-close, today's endpoints answer with partial (or empty) data and the
    date would be finalized in state as if that were the whole day. Past end
    dates pass through untouched."""
    now = datetime.now(ET)
    today = now.date().isoformat()
    if end < today:
        return end
    if (now.hour, now.minute) >= OPTIONS_CLOSE_ET:
        return today
    clamped = (now.date() - timedelta(days=1)).isoformat()
    log.info("end %s clamped to %s — today's options session isn't closed yet (16:15 ET)",
             end, clamped)
    return clamped


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
                if _transient(code):
                    log.warning("%s %s: transient %d — unit left unrecorded", name, t, code)
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
            if _transient(code):
                log.warning("%s: transient %d — unit left unrecorded", name, code)
                continue
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
                    if _transient(code):
                        log.warning("ohlc %s %s: transient %d — unit left unrecorded",
                                    t, candle, code)
                        continue
                    rows = rows_of(body)
                    key = f"reference/uw/ohlc/ticker={t}/candle={candle}.parquet"
                    ok = not dry and _write(s3, key, rows, ticker=t, candle=candle, endpoint="ohlc")
                    units[uid] = "done" if ok else "empty"
                    if ok:
                        log.info("wrote %s (%d rows)", key, len(rows))
                    _flush(s3, state)


def run_daily(s3, state: dict, tickers: list[str], priorities: set[int],
              start: str, end: str, dry: bool) -> None:
    end = _last_complete_session(end)
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
            transient = 0
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
                if _transient(code):
                    transient += 1
                    log.warning("%s %s %s: transient %d — date left unrecorded",
                                name, scope, d, code)
                    if transient >= TRANSIENT_STOP:
                        log.warning("%s %s: %d consecutive transient failures — "
                                    "scope abandoned this run", name, scope, transient)
                        break
                    continue
                transient = 0
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


def _active_expiries(s3, ticker: str) -> list[str]:
    """Enumerate a ticker's option expiries from expiry-breakdown (falls back to
    the option-contracts listing). Used to fan the expiry-sliced endpoints out."""
    exps: set[str] = set()
    for path in (f"/api/stock/{ticker}/expiry-breakdown",
                 f"/api/stock/{ticker}/option-contracts"):
        _, body = _get(path)
        for r in rows_of(body):
            for k in ("expiry", "expiration", "expires", "expiration_date"):
                v = r.get(k)
                if v:
                    exps.add(str(v)[:10])
                    break
        if exps:
            break
    return sorted(exps)


def run_expiry(s3, state: dict, tickers: list[str], dry: bool) -> None:
    """Expiry-sliced endpoints: a current snapshot per (ticker, active expiry)."""
    exp_state = state.setdefault("expiry", {})
    for t in tickers:
        expiries = _active_expiries(s3, t)
        log.info("%s: %d active expiries", t, len(expiries))
        for ep in EXPIRY_ENDPOINTS:
            name, param = ep["name"], ep["param"]
            st = exp_state.setdefault(f"{name}|{t}", {"done": [], "empty": []})
            seen = set(st["done"]) | set(st["empty"])
            transient = 0
            for e in [x for x in expiries if x not in seen]:
                if param == "path":
                    url, params = ep["path"].format(ticker=t, expiry=e), None
                else:
                    url, params = ep["path"].format(ticker=t), {param: e}
                code, body = _get(url, params)
                if code == 403:
                    log.info("%s %s: tariff-blocked", name, t)
                    st["empty"].extend(x for x in expiries if x not in st["empty"])
                    break
                if _transient(code):
                    transient += 1
                    log.warning("%s %s %s: transient %d — expiry left unrecorded",
                                name, t, e, code)
                    if transient >= TRANSIENT_STOP:
                        log.warning("%s %s: %d consecutive transient failures — "
                                    "scope abandoned this run", name, t, transient)
                        break
                    continue
                transient = 0
                rows = rows_of(body)
                key = f"uw/{name}/ticker={t}/expiry={e}/rows.parquet"
                ok = not dry and _write(s3, key, rows, ticker=t, expiry=e, endpoint=name)
                (st["done"] if ok else st["empty"]).append(e)
            _flush(s3, state)


def _contract_symbols(s3, ticker: str) -> list[str]:
    """The option-symbol universe for a ticker: the per-date chain listings we
    banked plus the current option-contracts snapshot."""
    symbols: set[str] = set()
    keys = r2_list_keys(s3, f"uw/option_chains/ticker={ticker}/")
    keys.append(f"reference/uw/option_contracts/ticker={ticker}.parquet")
    for key in keys:
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
    return sorted(symbols)


def run_contracts(s3, state: dict, tickers: list[str], dry: bool) -> None:
    """Chain rebuild: every option symbol seen in the banked option_chains
    listings gets each per-contract sub-endpoint (historic/flow/volume-profile),
    one call each → uw/option_{sub}/ticker={T}/symbol={SYM}.parquet."""
    contracts = state["contracts"]
    for t in tickers:
        st = contracts.setdefault(t, {"done": [], "empty": []})
        seen = set(st["done"])
        todo = [s for s in _contract_symbols(s3, t) if s not in seen]
        log.info("%s: %d option contracts × %d sub-endpoints", t, len(todo), len(CONTRACT_SUBS))
        transient = 0
        for sym in todo:
            blocked = failed = False
            for sub, path in CONTRACT_SUBS:
                code, body = _get(path.format(id=sym))
                if code == 403:
                    log.info("contracts %s: tariff-blocked", sub)
                    blocked = True
                    break
                if _transient(code):
                    log.warning("contracts %s %s: transient %d — symbol left unrecorded",
                                sub, sym, code)
                    failed = True
                    break
                rows = rows_of(body)
                key = f"uw/option_{sub}/ticker={t}/symbol={sym}.parquet"
                if not dry:
                    _write(s3, key, rows, ticker=t, occ_symbol=sym, endpoint=f"option_{sub}")
            if blocked:
                return
            if failed:
                transient += 1
                if transient >= TRANSIENT_STOP:
                    log.warning("%s contracts: %d consecutive transient failures — "
                                "abandoned this run", t, transient)
                    return
                continue
            transient = 0
            st["done"].append(sym)
            if _LIM.count % 100 == 0:
                _flush(s3, state)
        _flush(s3, state)


# consecutive empty (pre-listing) days after which a contract's intraday walk stops
INTRADAY_EMPTY_STOP = 5


def run_contracts_intraday(s3, state: dict, tickers: list[str], start: str, end: str,
                           dry: bool) -> None:
    """Per-contract INTRADAY minute bars (OHLC + IV + premium-by-aggressor-side).
    One call per (contract, session): walk sessions newest-first, stop a contract
    at its history-depth floor (403) or once past its listing (N empty days).
    → uw/option_intraday/ticker={T}/symbol={SYM}/date={D}/bars.parquet."""
    end = _last_complete_session(end)
    intraday = state.setdefault("intraday", {})
    for t in tickers:
        symbols = _contract_symbols(s3, t)
        sessions = sessions_desc(start, end)
        pending = [s for s in symbols if not intraday.get(f"{t}|{s}", {}).get("complete")]
        log.info("%s: %d contracts to walk for intraday (%d sessions each, newest-first)",
                 t, len(pending), len(sessions))
        transient = 0
        for sym in pending:
            skey = f"{t}|{sym}"
            st = intraday.setdefault(skey, {"done": [], "empty": [], "complete": False})
            seen = set(st["done"]) | set(st["empty"])
            consecutive_empty = 0
            failed = False
            for d in [x for x in sessions if x not in seen]:
                code, body = _get(f"/api/option-contract/{sym}/intraday", {"date": d})
                if code == 403:
                    st["complete"] = True  # depth floor — older is unavailable
                    break
                if _transient(code):
                    # break WITHOUT complete: the date stays unrecorded and the
                    # whole contract walk resumes on the next run
                    log.warning("intraday %s %s: transient %d — walk paused", sym, d, code)
                    failed = True
                    break
                rows = rows_of(body)
                if rows:
                    consecutive_empty = 0
                    key = f"uw/option_intraday/ticker={t}/symbol={sym}/date={d}/bars.parquet"
                    if not dry:
                        _write(s3, key, rows, ticker=t, occ_symbol=sym, date=d,
                               endpoint="option_intraday")
                    st["done"].append(d)
                else:
                    consecutive_empty += 1
                    st["empty"].append(d)
                    if consecutive_empty >= INTRADAY_EMPTY_STOP:
                        st["complete"] = True  # walked past the contract's listing
                        break
                if _LIM.count % 100 == 0:
                    _flush(s3, state)
            else:
                st["complete"] = True  # exhausted the session range
            _flush(s3, state)
            if failed:
                transient += 1
                if transient >= TRANSIENT_STOP:
                    log.warning("intraday %s: %d consecutive transient walks — "
                                "abandoned this run", t, transient)
                    return
            else:
                transient = 0


def _underlying_col(df: pd.DataFrame) -> str | None:
    for c in ("underlying_symbol", "underlying", "root_symbol", "root", "ticker"):
        if c in df.columns:
            return c
    return None


def run_tape(s3, state: dict, tickers: list[str], start: str, end: str, dry: bool) -> None:
    """FINEST granularity: the full options tape — EVERY trade market-wide per day
    (~1.8 GB zipped CSV each) — streamed, unzipped and filtered to our tickers.
    One request per session, newest-first, depth-floor aware, resumable.
    → uw/option_tape/ticker={T}/date={D}/trades.parquet."""
    end = _last_complete_session(end)
    ts = state.setdefault("tape", {"done": [], "empty": [], "blocked": []})
    want = {t.upper() for t in tickers}
    seen = set(ts["done"]) | set(ts["empty"]) | set(ts["blocked"])
    sessions = sessions_desc(start, end)
    todo = [x for x in sessions if x not in seen]
    log.info("tape: %d sessions to pull (~1.8 GB download each)", len(todo))
    hdr = {"Authorization": f"Bearer {_TOKEN}"}
    transient = 0

    def _trip(d: str, why: str) -> bool:
        """Count one transient failure (the date stays unrecorded — retried
        next run); True = TRANSIENT_STOP hit, abandon the sweep this run."""
        nonlocal transient
        transient += 1
        log.warning("tape %s: %s — date left unrecorded", d, why)
        if transient >= TRANSIENT_STOP:
            log.warning("tape: %d consecutive transient failures — abandoned this run",
                        transient)
            return True
        return False

    for d in todo:
        _LIM.wait()
        try:
            r = requests.get(f"{BASE}/api/option-trades/full-tape/{d}",
                             headers=hdr, stream=True, timeout=900)
        except Exception as exc:  # noqa: BLE001
            if _trip(d, str(exc)):
                break
            continue
        _LIM.observe(r.headers)
        if r.status_code == 403:
            ts["blocked"].extend(x for x in todo if x <= d and x not in ts["blocked"])
            log.info("tape: history floor at %s", d)
            break
        if r.status_code != 200:
            if _trip(d, f"transient status {r.status_code}"):
                break
            continue
        with tempfile.NamedTemporaryFile(suffix=".zip") as tmp:
            for chunk in r.iter_content(chunk_size=1 << 20):  # stream to disk, 1 MB
                tmp.write(chunk)
            tmp.flush()
            parts: dict[str, list[pd.DataFrame]] = {t: [] for t in want}
            try:
                with zipfile.ZipFile(tmp.name) as z:
                    name = next((n for n in z.namelist() if n.endswith(".csv")), None)
                    if name:
                        with z.open(name) as f:
                            for ch in pd.read_csv(f, chunksize=500_000, dtype=str,
                                                  low_memory=False):
                                col = _underlying_col(ch)
                                if col is None:
                                    break
                                hit = ch[ch[col].str.upper().isin(want)]
                                for t in want:
                                    tt = hit[hit[col].str.upper() == t]
                                    if len(tt):
                                        parts[t].append(tt)
            except Exception as exc:  # noqa: BLE001 — bad zip/csv → retry next run
                # counts toward the breaker: N consecutive corrupt days must not
                # keep downloading ~1.8 GB each, unbounded
                if _trip(d, f"parse error: {exc}"):
                    break
                continue
        wrote = 0
        for t in want:
            if parts[t]:
                df = pd.concat(parts[t], ignore_index=True)
                df["captured_at"] = datetime.now(timezone.utc).isoformat()
                if not dry:
                    r2_put_parquet(s3, f"uw/option_tape/ticker={t}/date={d}/trades.parquet", df)
                wrote += len(df)
        ts["done"].append(d)
        transient = 0
        log.info("tape %s: %d trades kept for %s", d, wrote, "/".join(sorted(want)))
        _flush(s3, state)


# ------------------------------------------------------------ main
def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mode",
                    choices=("probe", "series", "daily", "expiry", "contracts",
                             "intraday", "tape", "all"),
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
        if args.mode in ("expiry", "all"):
            run_expiry(s3, state, tickers, args.dry_run)
        if args.mode in ("contracts", "all"):
            run_contracts(s3, state, tickers, args.dry_run)
        if args.mode == "intraday":  # heavy; explicit opt-in, not part of "all"
            run_contracts_intraday(s3, state, tickers, args.start, end, args.dry_run)
        if args.mode == "tape":  # finest + heaviest (~1.8 GB/day); explicit opt-in
            run_tape(s3, state, tickers, args.start, end, args.dry_run)
    except BudgetExhausted as exc:
        _flush(s3, state)
        log.warning("STOPPED: %s — rerun tomorrow to resume", exc)
        return 0
    except NetworkDown as exc:
        _flush(s3, state)  # transient work is unrecorded → retried next run
        log.error("ABORTED: %s — exiting non-zero so the outage is visible",
                  exc)
        return 1
    _flush(s3, state)
    if _net["transient_total"]:
        # completed the walk, but transient failures left work unrecorded —
        # a partial run must LOOK partial to daily alerting (exit 0 + "done"
        # is how the 2026-07-09/-10 outages stayed invisible)
        log.warning("done DEGRADED (mode=%s, requests=%d, %d transient "
                    "failures left work unrecorded for retry)",
                    args.mode, _LIM.count, _net["transient_total"])
        return 1
    log.info("done (mode=%s, requests=%d)", args.mode, _LIM.count)
    return 0


if __name__ == "__main__":
    sys.exit(main())
