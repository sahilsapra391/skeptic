#!/usr/bin/env python3
"""
derive_cross_validation.py — nightly cross-source validation (ENGINE-V4 F7).

Reduces every session (or Massive symbol) where two INDEPENDENT sources
overlap to uniform agreement records:
  reference/derived/cross_validation/pair={pair}/ticker={T}.parquet
      date · joined · checked · within_band · agreement_rate (+extras)

Pairs (comparator MATH lives in app/data/cross_validation.py — one
implementation, fixture-tested):
  dolthub_vs_alpaca   SPY: EOD closing quotes vs minute trades (near-close
                      + delta-adjusted, the proven one-off methodology)
  dolthub_vs_uw       SPY: chain volume/OI per expiry vs UW volume_oi_expiry
  yahoo_vs_ivol5m     EOD quote chain vs the last 5-min NBBO (0-2 DTE)
  massive_vs_ivol5m   QQQ/IWM: daily close vs the 5-min NBBO day range —
                      unit of work is the SYMBOL (each aggs file holds the
                      contract's whole history); per-session COUNTS
                      accumulate as the crawl lands (F5-deferred check)
  hv_inhouse_vs_ivol · ivs_cboe_vs_ivol · positioning_cboe_vs_uw
                      in-house continuations vs their frozen vendor series
                      (one table-driven runner; see _run_signal_pair)
  recorder_vs_uw_tape SPY/QQQ/IWM: every UW full-tape print vs the
                      recorder's displayed quote valid at that moment
                      (measured 15-min feed lag; tape-banked sessions)

Incremental by SET DIFFERENCE per pair (the F4 self-healing rule): session
pairs derive exactly the overlapping sessions absent from the artifact;
the Massive pair tracks processed symbols in a companion parquet.
Unreadable inputs leave no row and retry next run.

Run:  cd collector && uv run python derive_cross_validation.py [--pairs a,b]
Env:  R2_* vars (same as collect.py).
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
from pathlib import Path

import pandas as pd


def _load_dotenv(path: Path = Path(__file__).parent / ".env") -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv()

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.data.cross_validation import (  # noqa: E402
    HV_ABS_TOL,
    HV_REL_TOL,
    INHOUSE_VOLPT_TOL,
    MPD_ABS_TOL,
    PAIR_KEY,
    PCR_REL_TOL,
    compare_dolthub_alpaca,
    compare_dolthub_uw,
    compare_massive_ivol5m,
    compare_quote_close,
    compare_recorder_tape_window,
    compare_signal_values,
    merge_records,
    recorder_tape_window,
)
from app.data.flow_inhouse import FLOW_INHOUSE_KEY  # noqa: E402
from app.data.flow_signals import FLOW_KEY  # noqa: E402
from app.data.gex_signals import load_dealer_exposure  # noqa: E402
from app.data.inhouse_signals import CHAIN_SIGNALS_KEY, HV_KEY  # noqa: E402
from app.data.ivol_analytics import load_hv_30d  # noqa: E402
from app.data.ivs_signals import SIGNALS_KEY as IVS_SIGNALS_KEY  # noqa: E402

from collect import (  # noqa: E402
    r2_client,
    r2_get_parquet,
    r2_get_parquet_spooled,
    r2_list_keys,
    r2_put_parquet,
)

log = logging.getLogger("cross_validation")
_DATE_RE = re.compile(r"date=(\d{4}-\d{2}-\d{2})")
MASSIVE_DONE_KEY = ("reference/derived/cross_validation/"
                    "massive_symbols_done/ticker={ticker}.parquet")


def _sessions(s3, prefix: str) -> list[str]:
    dates: list[str] = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=os.environ["R2_BUCKET"],
                                   Prefix=prefix, Delimiter="/"):
        for cp in page.get("CommonPrefixes", []):
            m = _DATE_RE.search(cp["Prefix"])
            if m:
                dates.append(m.group(1))
    return sorted(dates)


def _artifact(s3, key: str) -> tuple[pd.DataFrame | None, set[str]]:
    df = r2_get_parquet(s3, key)
    if df is None or df.empty or "date" not in df.columns:
        return None, set()
    return df, set(df["date"].astype(str))


def _write(s3, key: str, existing: pd.DataFrame | None,
           rows: list[dict]) -> None:
    fresh = pd.DataFrame(rows)
    combined = (pd.concat([existing, fresh], ignore_index=True)
                if existing is not None and not existing.empty else fresh)
    combined = (combined.drop_duplicates(subset=["date"], keep="last")
                .sort_values("date").reset_index(drop=True))
    r2_put_parquet(s3, key, combined)


class _UndMinutes:
    """Month-cached SPY underlying minute closes (the one-off's cache)."""

    def __init__(self, s3) -> None:
        self.s3 = s3
        self.cache: dict[str, pd.DataFrame | None] = {}

    def day(self, d: str) -> pd.Series | None:
        month = d[:7]
        if month not in self.cache:
            df = r2_get_parquet(
                self.s3, f"underlying_minute/ticker=SPY/month={month}/bars.parquet")
            if df is None or df.empty:
                self.cache[month] = None
            else:
                self.cache[month] = df.assign(
                    et=pd.to_datetime(df["minute_ts"]).dt.tz_convert(
                        "America/New_York"))
            if len(self.cache) > 4:  # bound the month cache (OOM guard)
                self.cache.pop(next(iter(self.cache)))
        df = self.cache[month]
        if df is None:
            return None
        day = df[df["et"].dt.date.astype(str) == d]
        return day.set_index("et")["close"].sort_index() if len(day) else None


def run_dolthub_vs_alpaca(s3) -> int:
    key = PAIR_KEY.format(pair="dolthub_vs_alpaca", ticker="SPY")
    existing, have = _artifact(s3, key)
    dolt = set(_sessions(s3, "options/source=dolthub/ticker=SPY/"))
    alp = set(_sessions(s3, "options_minute/source=alpaca/ticker=SPY/"))
    todo = sorted((dolt & alp) - have)
    if not todo:
        log.info("dolthub_vs_alpaca: up to date (%d sessions)", len(have))
        return 0
    und = _UndMinutes(s3)
    rows: list[dict] = []
    skipped = 0
    for d in todo:
        eod = r2_get_parquet(s3, f"options/source=dolthub/ticker=SPY/date={d}/chain.parquet")
        bars = r2_get_parquet(s3, f"options_minute/source=alpaca/ticker=SPY/date={d}/bars.parquet")
        spots = und.day(d)
        rec = (compare_dolthub_alpaca(eod, bars, spots)
               if spots is not None else None)
        if rec is None:
            skipped += 1
            continue
        rec["date"] = d
        rows.append(rec)
    if rows:
        _write(s3, key, existing, rows)
    log.info("dolthub_vs_alpaca: derived %d sessions (%d skipped) → r2://%s",
             len(rows), skipped, key)
    return len(rows)


def run_dolthub_vs_uw(s3) -> int:
    key = PAIR_KEY.format(pair="dolthub_vs_uw", ticker="SPY")
    existing, have = _artifact(s3, key)
    dolt = set(_sessions(s3, "options/source=dolthub/ticker=SPY/"))
    uw = set(_sessions(s3, "uw/volume_oi_expiry/ticker=SPY/"))
    todo = sorted((dolt & uw) - have)
    if not todo:
        log.info("dolthub_vs_uw: up to date (%d sessions)", len(have))
        return 0
    rows: list[dict] = []
    for d in todo:
        rec = compare_dolthub_uw(
            r2_get_parquet(s3, f"options/source=dolthub/ticker=SPY/date={d}/chain.parquet"),
            r2_get_parquet(s3, f"uw/volume_oi_expiry/ticker=SPY/date={d}/rows.parquet"),
        )
        if rec is None:
            continue
        rec["date"] = d
        rows.append(rec)
    if rows:
        _write(s3, key, existing, rows)
    log.info("dolthub_vs_uw: derived %d sessions → r2://%s", len(rows), key)
    return len(rows)


def _ivol_last_quotes(day: pd.DataFrame) -> pd.DataFrame | None:
    """Last 5-min NBBO per contract from an iVol intraday day frame."""
    if day is None or day.empty:
        return None
    day = day.sort_values("minute_ts")
    return (day.groupby(["expiration", "right", "strike"])
            .agg(bid=("bid", "last"), ask=("ask", "last")).reset_index())


def _latest_yahoo_snapshot(s3, ticker: str, d: str):
    """Yahoo banks intraday snapshots (snap_<ts>.parquet), not a single
    chain.parquet — read the LAST one of the session (closest to the
    close), the same convention the coverage builder uses."""
    prefix = f"options/source=yahoo/ticker={ticker}/date={d}/"
    keys = [o["Key"] for page in s3.get_paginator("list_objects_v2").paginate(
        Bucket=os.environ["R2_BUCKET"], Prefix=prefix)
        for o in page.get("Contents", [])
        if o["Key"].endswith(".parquet")]
    if not keys:
        return None
    return r2_get_parquet(s3, sorted(keys)[-1])


def run_yahoo_vs_ivol5m(s3, ticker: str) -> int:
    key = PAIR_KEY.format(pair="yahoo_vs_ivol5m", ticker=ticker)
    existing, have = _artifact(s3, key)
    yah = set(_sessions(s3, f"options/source=yahoo/ticker={ticker}/"))
    iv = set(_sessions(s3, f"options_intraday/source=ivolatility/ticker={ticker}/"))
    todo = sorted((yah & iv) - have)
    if not todo:
        log.info("yahoo_vs_ivol5m %s: up to date (%d sessions)", ticker, len(have))
        return 0
    rows: list[dict] = []
    for d in todo:
        ref = _latest_yahoo_snapshot(s3, ticker, d)
        day = r2_get_parquet(
            s3, f"options_intraday/source=ivolatility/ticker={ticker}/date={d}/bars.parquet")
        rec = compare_quote_close(ref, _ivol_last_quotes(day))
        if rec is None:
            continue
        rec["date"] = d
        rows.append(rec)
    if rows:
        _write(s3, key, existing, rows)
    log.info("yahoo_vs_ivol5m %s: derived %d sessions → r2://%s",
             ticker, len(rows), key)
    return len(rows)


def run_massive_vs_ivol5m(s3, ticker: str) -> int:
    """Unit of work = the SYMBOL (each Massive aggs file holds one
    contract's whole daily history). Per-session COUNTS accumulate into
    the artifact as the crawl lands; agreement_rate recomputed on write."""
    key = PAIR_KEY.format(pair="massive_vs_ivol5m", ticker=ticker)
    done_key = MASSIVE_DONE_KEY.format(ticker=ticker)
    done_df = r2_get_parquet(s3, done_key)
    done: set[str] = (set(done_df["symbol"].astype(str))
                      if done_df is not None and not done_df.empty else set())
    paginator = s3.get_paginator("list_objects_v2")
    symbols: list[str] = []
    prefix = f"reference/massive/option_agg/ticker={ticker}/"
    for page in paginator.paginate(Bucket=os.environ["R2_BUCKET"], Prefix=prefix):
        for obj in page.get("Contents", []):
            m = re.search(r"symbol=([^/]+)\.parquet$", obj["Key"])
            if m and m.group(1) not in done:
                symbols.append(m.group(1))
    if not symbols:
        log.info("massive_vs_ivol5m %s: up to date (%d symbols)", ticker, len(done))
        return 0
    existing, _ = _artifact(s3, key)
    counts: dict[str, dict[str, int]] = {}
    if existing is not None:
        for row in existing.to_dict("records"):
            counts[str(row["date"])] = {
                "joined": int(row["joined"]), "checked": int(row["checked"]),
                "within_band": int(row["within_band"]),
            }
    day_cache: dict[str, pd.DataFrame | None] = {}
    processed: list[str] = []
    for sym in symbols:
        m_df = r2_get_parquet(s3, f"{prefix}symbol={sym}.parquet")
        if m_df is None or m_df.empty or "date" not in m_df.columns:
            processed.append(sym)  # empty aggs file — nothing to compare, done
            continue
        for d, day_rows in m_df.groupby(m_df["date"].astype(str)):
            if d not in day_cache:
                if len(day_cache) > 30:  # bound (OOM guard)
                    day_cache.pop(next(iter(day_cache)))
                day_cache[d] = r2_get_parquet(
                    s3, f"options_intraday/source=ivolatility/ticker={ticker}"
                        f"/date={d}/bars.parquet")
            rec = compare_massive_ivol5m(day_rows, day_cache[d])
            if rec is None:
                continue
            c = counts.setdefault(d, {"joined": 0, "checked": 0, "within_band": 0})
            c["joined"] += rec["joined"]
            c["checked"] += rec["checked"]
            c["within_band"] += rec["within_band"]
        processed.append(sym)
    rows = [{"date": d, **c,
             "agreement_rate": (round(c["within_band"] / c["checked"], 4)
                                if c["checked"] else None)}
            for d, c in counts.items()]
    if rows:
        _write(s3, key, None, rows)  # counts already include existing
    all_done = pd.DataFrame({"symbol": sorted(done | set(processed))})
    r2_put_parquet(s3, done_key, all_done)
    log.info("massive_vs_ivol5m %s: +%d symbols (%d total) → r2://%s",
             ticker, len(processed), len(done) + len(processed), key)
    return len(processed)


_TAPE_COLUMNS = ["executed_at", "expiry", "option_type", "strike", "price"]
_SNAP_COLUMNS = ["source_ts", "expiration", "right", "strike", "bid", "ask"]


def _tape_day(s3, ticker: str, d: str) -> pd.DataFrame | None:
    """One session's tape prints, ts-sorted. Spooled read — the file is
    millions of prints across ~40 columns, so the bytes stream to disk
    and only the five projected columns are decoded; the raw executed_at
    strings are dropped once parsed (OOM rule: nothing dead resident)."""
    df = r2_get_parquet_spooled(
        s3, f"uw/option_tape/ticker={ticker}/date={d}/trades.parquet",
        columns=_TAPE_COLUMNS)
    if df is None or df.empty:
        return None
    df["_ts"] = pd.to_datetime(df["executed_at"], utc=True,
                               format="ISO8601", errors="coerce")
    return (df.drop(columns=["executed_at"]).dropna(subset=["_ts"])
            .sort_values("_ts").reset_index(drop=True))


def _snap_keys(s3, ticker: str, d: str) -> list[str]:
    prefix = f"options_intraday/source=cboe_delayed/ticker={ticker}/date={d}/"
    return sorted(k for k in r2_list_keys(s3, prefix)
                  if k.endswith(".parquet"))


def run_recorder_vs_uw_tape(s3, ticker: str) -> int:
    """Recorder displayed quotes vs the UW full-tape prints. Per snap:
    prints inside the snap's shifted validity window (recorder_tape_window
    — the measured 15-min feed lag, 60 s span, clamped disjoint so a
    stalled feed never judges the same print twice) are checked against
    that snap's two-sided quotes; per-session counts fold through
    merge_records, the single-sourced rate math.

    Session semantics:
      * the CURRENT ET session is never derived — both inputs can exist
        in partial form intraday, and a partial row would freeze forever
        under set-difference incrementality;
      * any snap READ failure aborts the session row (transient — retried
        next run, the same depth the tape-read failure already gets);
      * a session whose snaps yield no usable window writes an explicit
        zero-coverage row (snaps=0): honest absence that also stops the
        nightly from re-downloading the whole tape day forever.

    Extras: below_bid / beyond_ask (violation direction — the
    displayed-quote calibration signal), tape_trades (session prints),
    windowed_trades (prints the snap windows could actually see, so a
    recorder gap day is visibly partial), snaps (usable windows)."""
    key = PAIR_KEY.format(pair="recorder_vs_uw_tape", ticker=ticker)
    existing, have = _artifact(s3, key)
    rec = set(_sessions(s3, f"options_intraday/source=cboe_delayed/ticker={ticker}/"))
    tape = set(_sessions(s3, f"uw/option_tape/ticker={ticker}/"))
    today_et = pd.Timestamp.now(tz="America/New_York").date().isoformat()
    todo = sorted(d for d in (rec & tape) - have if d < today_et)
    if not todo:
        log.info("recorder_vs_uw_tape %s: up to date (%d sessions)",
                 ticker, len(have))
        return 0
    rows: list[dict] = []
    for d in todo:
        trades = _tape_day(s3, ticker, d)
        if trades is None or trades.empty:
            continue  # unreadable tape — no row, retries next run
        parts: list[dict] = []
        windowed = 0
        failed_reads = 0
        not_before = None
        for skey in _snap_keys(s3, ticker, d):
            snap = r2_get_parquet(s3, skey, columns=_SNAP_COLUMNS)
            if snap is None:
                failed_reads += 1  # transient R2 failure — the session
                continue           # must not freeze half-covered
            if snap.empty:
                continue
            src = pd.to_datetime(str(snap["source_ts"].iloc[0]),
                                 utc=True, errors="coerce")
            if pd.isna(src):
                continue  # unplaceable on the feed clock — skipped,
                # never joined at the wrong moment (permanent shape,
                # not a read failure)
            lo, hi, not_before = recorder_tape_window(
                trades["_ts"], src, not_before)
            if lo >= hi:
                continue
            rec_row = compare_recorder_tape_window(snap, trades.iloc[lo:hi])
            if rec_row is None:
                continue
            windowed += hi - lo
            parts.append(rec_row)
        if failed_reads:
            log.warning("recorder_vs_uw_tape %s %s: %d snap reads failed — "
                        "no row, retrying next run", ticker, d, failed_reads)
            continue
        row = merge_records(parts, "below_bid", "beyond_ask",
                            tape_trades=int(len(trades)),
                            windowed_trades=int(windowed),
                            snaps=len(parts))
        row["date"] = d
        rows.append(row)
    if rows:
        _write(s3, key, existing, rows)
    log.info("recorder_vs_uw_tape %s: derived %d sessions → r2://%s",
             ticker, len(rows), key)
    return len(rows)


def _col_series(df: pd.DataFrame | None, col: str) -> dict[str, float]:
    """iso-date → value from a derived artifact frame (NaN rows dropped)."""
    if df is None or df.empty or "date" not in df.columns or col not in df.columns:
        return {}
    vals = pd.to_numeric(df[col], errors="coerce")
    return {str(d): float(v) for d, v in zip(df["date"].astype(str), vals)
            if pd.notna(v)}


def _run_signal_pair(
    s3, pair: str, ticker: str,
    ours_loader,  # () -> {column: {iso-date: value}}
    vendor_loader,  # () -> {column: {iso-date: value}}
    fields: list[tuple[str, str, str, float, float]],
) -> int:
    """ONE driver for every in-house-continuation pair (review finding:
    three pasted skeletons WILL drift; the next pair is a table entry).
    fields = (our_column, vendor_column, mode, abs_tol, rel_tol).

    The vendor side loads LAZILY: these vendor series are FROZEN (the
    2026-07-08 no-subscription decision), so once every new in-house
    session lies past the banked overlap edge there is nothing left to
    compare and the expensive vendor load (22 HV year files per ticker)
    is skipped. Documented assumption: a frozen vendor never back-fills
    behind the banked edge — if a feed ever RESUMES, delete the pair
    artifact so the overlap re-derives from scratch."""
    key = PAIR_KEY.format(pair=pair, ticker=ticker)
    existing, have = _artifact(s3, key)
    ours = ours_loader()
    our_dates: set[str] = set().union(*(set(s) for s in ours.values())) \
        if ours else set()
    candidates = our_dates - have
    if not candidates:
        log.info("%s %s: up to date (%d sessions)", pair, ticker, len(have))
        return 0
    if have and not any(min(have) <= c <= max(have) for c in candidates):
        # every un-banked in-house session lies OUTSIDE the banked overlap
        # window — before the vendor's history starts or past its frozen
        # edge — so the vendor load can prove nothing new
        log.info("%s %s: no new sessions inside the banked vendor window "
                 "%s → %s — nothing to compare (%d banked)",
                 pair, ticker, min(have), max(have), len(have))
        return 0
    vendor = vendor_loader()
    vendor_dates: set[str] = set().union(*(set(s) for s in vendor.values())) \
        if vendor else set()
    todo = sorted((our_dates & vendor_dates) - have)
    if not todo:
        log.info("%s %s: up to date (%d sessions)", pair, ticker, len(have))
        return 0
    rows: list[dict] = []
    for d in todo:
        rec = compare_signal_values([
            (ours.get(oc, {}).get(d), vendor.get(vc, {}).get(d), mode, at, rt)
            for oc, vc, mode, at, rt in fields
        ])
        if rec is None:
            continue
        rec["date"] = d
        rows.append(rec)
    if rows:
        _write(s3, key, existing, rows)
    log.info("%s %s: derived %d sessions → r2://%s", pair, ticker, len(rows), key)
    return len(rows)


def _artifact_cols(s3, key: str, cols: tuple[str, ...]) -> dict[str, dict[str, float]]:
    df = r2_get_parquet(s3, key)
    return {c: _col_series(df, c) for c in cols}


_IVS_FIELDS = ("skew_25d", "term_slope_30_90", "atm_iv_30d", "atm_iv_90d")
_POS_FIELDS = ("net_gex", "net_dex", "put_call_ratio", "max_pain_dist_pct")


def run_hv_inhouse_vs_ivol(s3, ticker: str) -> int:
    """In-house HV (own dailies) vs the frozen vendor 30d HV — the seam
    with the deepest overlap (~5,400 sessions), measured not asserted."""
    return _run_signal_pair(
        s3, "hv_inhouse_vs_ivol", ticker,
        ours_loader=lambda: _artifact_cols(
            s3, HV_KEY.format(ticker=ticker), ("hv_30d",)),
        vendor_loader=lambda: {"hv_30d": {
            d.isoformat(): v for d, v in load_hv_30d(s3, ticker).items()}},
        fields=[("hv_30d", "hv_30d", "band", HV_ABS_TOL, HV_REL_TOL)],
    )


def run_ivs_cboe_vs_ivol(s3, ticker: str) -> int:
    """In-house chain-interpolated surface signals vs the frozen vendor
    fitted-surface artifact, on whatever overlap exists (vol points)."""
    return _run_signal_pair(
        s3, "ivs_cboe_vs_ivol", ticker,
        ours_loader=lambda: _artifact_cols(
            s3, CHAIN_SIGNALS_KEY.format(ticker=ticker), _IVS_FIELDS),
        vendor_loader=lambda: _artifact_cols(
            s3, IVS_SIGNALS_KEY.format(ticker=ticker), _IVS_FIELDS),
        fields=[(f, f, "band", INHOUSE_VOLPT_TOL, 0.0) for f in _IVS_FIELDS],
    )


def _uw_positioning_cols(s3, ticker: str) -> dict[str, dict[str, float]]:
    gex_v, dex_v = load_dealer_exposure(s3, ticker)
    flow = _artifact_cols(s3, FLOW_KEY.format(ticker=ticker),
                          ("put_call_ratio", "max_pain_dist_pct"))
    return {
        "net_gex": {d.isoformat(): v for d, v in gex_v.items()},
        "net_dex": {d.isoformat(): v for d, v in dex_v.items()},
        **flow,
    }


def run_positioning_cboe_vs_uw(s3, ticker: str) -> int:
    """In-house chain positioning/flow vs the frozen UW series: GEX/DEX
    agree on SIGN only (the conventions share nothing else); PCR within a
    loose band (chain volume vs flow volume); max-pain distance tight
    (same formula, same OI)."""
    return _run_signal_pair(
        s3, "positioning_cboe_vs_uw", ticker,
        ours_loader=lambda: _artifact_cols(
            s3, CHAIN_SIGNALS_KEY.format(ticker=ticker), _POS_FIELDS),
        vendor_loader=lambda: _uw_positioning_cols(s3, ticker),
        fields=[
            ("net_gex", "net_gex", "sign", 0.0, 0.0),
            ("net_dex", "net_dex", "sign", 0.0, 0.0),
            ("put_call_ratio", "put_call_ratio", "band", 0.0, PCR_REL_TOL),
            ("max_pain_dist_pct", "max_pain_dist_pct", "band", MPD_ABS_TOL, 0.0),
        ],
    )


def run_flow_inhouse_vs_uw(s3, ticker: str) -> int:
    """In-house classified flow vs the frozen UW flow artifact: net
    premium and NOPE agree on SIGN (different captures, own conventions);
    the flow-volume ratio within the loose PCR band (Alpaca minute volume
    vs UW per-print volume)."""
    return _run_signal_pair(
        s3, "flow_inhouse_vs_uw", ticker,
        ours_loader=lambda: _artifact_cols(
            s3, FLOW_INHOUSE_KEY.format(ticker=ticker),
            ("net_premium", "put_call_flow_ratio", "nope_eod")),
        vendor_loader=lambda: _artifact_cols(
            s3, FLOW_KEY.format(ticker=ticker),
            ("net_premium", "put_call_ratio", "nope_eod")),
        fields=[
            ("net_premium", "net_premium", "sign", 0.0, 0.0),
            ("put_call_flow_ratio", "put_call_ratio", "band", 0.0, PCR_REL_TOL),
            ("nope_eod", "nope_eod", "sign", 0.0, 0.0),
        ],
    )


def main() -> int:
    logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                        format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pairs", default="all")
    args = ap.parse_args()
    want = (set(args.pairs.split(",")) if args.pairs != "all"
            else {"dolthub_vs_alpaca", "dolthub_vs_uw", "yahoo_vs_ivol5m",
                  "massive_vs_ivol5m", "hv_inhouse_vs_ivol",
                  "ivs_cboe_vs_ivol", "positioning_cboe_vs_uw",
                  "recorder_vs_uw_tape", "flow_inhouse_vs_uw"})
    s3 = r2_client()
    n = 0
    if "dolthub_vs_alpaca" in want:
        n += run_dolthub_vs_alpaca(s3)
    if "dolthub_vs_uw" in want:
        n += run_dolthub_vs_uw(s3)
    if "yahoo_vs_ivol5m" in want:
        for t in ("SPY", "QQQ", "IWM"):
            n += run_yahoo_vs_ivol5m(s3, t)
    if "massive_vs_ivol5m" in want:
        for t in ("QQQ", "IWM"):
            n += run_massive_vs_ivol5m(s3, t)
    if "hv_inhouse_vs_ivol" in want:
        for t in ("SPY", "QQQ", "IWM"):
            n += run_hv_inhouse_vs_ivol(s3, t)
    if "ivs_cboe_vs_ivol" in want:
        for t in ("SPY", "QQQ", "IWM"):
            n += run_ivs_cboe_vs_ivol(s3, t)
    if "positioning_cboe_vs_uw" in want:
        for t in ("SPY", "QQQ", "IWM"):
            n += run_positioning_cboe_vs_uw(s3, t)
    if "recorder_vs_uw_tape" in want:
        for t in ("SPY", "QQQ", "IWM"):
            n += run_recorder_vs_uw_tape(s3, t)
    if "flow_inhouse_vs_uw" in want:
        for t in ("SPY", "QQQ", "IWM"):
            n += run_flow_inhouse_vs_uw(s3, t)
    log.info("done: %d units derived", n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
