#!/usr/bin/env python3
"""derive_flow_inhouse.py — Alpaca minute trades × recorder minute quotes →
the in-house forward flow family (post-UW continuation candidate; math and
honesty contract in backend app/data/flow_inhouse.py, fixture-tested).

  reference/derived/flow_inhouse/ticker={T}.parquet
      date · net_premium · net_call/put_premium · put_call_flow_ratio ·
      nope_eod · volume accounting · tape_side_agreement (overlap only)

Sessions = Alpaca options_minute ∩ recorder cboe_delayed, strictly before
the current ET session (both inputs exist in partial form intraday — the
recorder_vs_uw_tape rule). Bars are sliced into each snapshot's shifted
validity window (source_ts − the measured 15-min feed lag, 60 s, clamped
disjoint) via the same recorder_tape_window helper the tape pair uses;
bars no window covers stay in the reduction as quote-less volume (counted,
never classified). On the frozen tape-overlap sessions the classifier's
per-(contract, minute) side is scored against the tape's true sides and
the volume-weighted agreement rides the row.

Incremental by SET DIFFERENCE, checkpointed every 10 sessions. Grows
nightly with the Alpaca top-up + recorder — the forward record the frozen
UW families cannot provide.

Run:  cd collector && uv run python derive_flow_inhouse.py [--tickers ...]
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
from app.data.cross_validation import recorder_tape_window  # noqa: E402
from app.data.flow_inhouse import (  # noqa: E402
    FLOW_INHOUSE_KEY,
    reduce_flow_session,
    tape_side_truth,
)

from collect import (  # noqa: E402
    r2_client,
    r2_get_parquet,
    r2_get_parquet_spooled,
    r2_list_keys,
    r2_put_parquet,
)

log = logging.getLogger("flow_inhouse")
_DATE_RE = re.compile(r"date=(\d{4}-\d{2}-\d{2})")

_BAR_COLUMNS = ["minute_ts", "expiration", "right", "strike", "vwap", "volume"]
_SNAP_FULL = ["source_ts", "expiration", "right", "strike", "bid", "ask",
              "delta", "und_volume"]
_SNAP_LEAN = _SNAP_FULL[:-1]  # pre-#79 snaps carry no und_volume column
_TAPE_COLUMNS = ["executed_at", "expiry", "option_type", "strike", "size",
                 "tags"]
_JOIN = ["expiration", "right", "strike"]


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


def _snap_keys(s3, ticker: str, d: str) -> list[str]:
    prefix = f"options_intraday/source=cboe_delayed/ticker={ticker}/date={d}/"
    return sorted(k for k in r2_list_keys(s3, prefix)
                  if k.endswith(".parquet"))


def _read_snap(s3, key: str, tier: list[str]) -> pd.DataFrame | None:
    """Tier-sniffing read: snaps within a session are schema-homogeneous,
    so after the first snap decides full vs lean the rest pay ONE download
    (pre-#79 sessions lack und_volume; the two-tier retry would otherwise
    double every read). Falls back to the other tier per snap so a
    transient failure still gets its second chance."""
    order = ([_SNAP_FULL, _SNAP_LEAN] if tier[0] == "full"
             else [_SNAP_LEAN, _SNAP_FULL])
    for cols in order:
        snap = r2_get_parquet(s3, key, columns=cols)
        if snap is not None:
            tier[0] = "full" if cols is _SNAP_FULL else "lean"
            if "und_volume" not in snap.columns:
                snap = snap.assign(und_volume=pd.NA)
            return snap
    return None


def _norm_keys(df: pd.DataFrame) -> pd.DataFrame:
    df["expiration"] = df["expiration"].astype(str).str[:10]
    df["right"] = df["right"].astype(str).str.lower()
    df["strike"] = pd.to_numeric(df["strike"], errors="coerce")
    return df


def derive_session(s3, ticker: str, d: str, has_tape: bool) -> dict | None:
    """One session → the flow row, or None (unreadable — retried next run)."""
    bars = r2_get_parquet(
        s3, f"options_minute/source=alpaca/ticker={ticker}/date={d}/bars.parquet",
        columns=_BAR_COLUMNS)
    if bars is None or bars.empty:
        return None
    bars = _norm_keys(bars)
    bars["minute_ts"] = pd.to_datetime(bars["minute_ts"], utc=True,
                                       errors="coerce")
    bars = (bars.dropna(subset=["minute_ts", "strike"])
            .assign(_mid_ts=lambda x: x["minute_ts"] + pd.Timedelta(seconds=30))
            .sort_values("_mid_ts").reset_index(drop=True))
    if bars.empty:
        return None

    pieces: list[pd.DataFrame] = []
    covered = pd.Series(False, index=bars.index)
    und_volume: float | None = None
    failed_reads = 0
    not_before = None
    tier = ["full"]
    for skey in _snap_keys(s3, ticker, d):
        snap = _read_snap(s3, skey, tier)
        if snap is None:
            failed_reads += 1
            continue
        if snap.empty:
            continue
        src = pd.to_datetime(str(snap["source_ts"].iloc[0]),
                             utc=True, errors="coerce")
        if pd.isna(src):
            continue
        uv = pd.to_numeric(snap["und_volume"], errors="coerce").iloc[0]
        if pd.notna(uv):
            und_volume = float(uv)  # last snap's cumulative wins
        lo, hi, not_before = recorder_tape_window(
            bars["_mid_ts"], src, not_before)
        if lo >= hi:
            continue
        snap = _norm_keys(snap)
        b = pd.to_numeric(snap["bid"], errors="coerce")
        a = pd.to_numeric(snap["ask"], errors="coerce")
        # build on the FULL frame, then filter once (the documented
        # fill_calibration footgun — an all-bad-quote snap must yield an
        # empty quote table, never resurrected NaN-keyed rows)
        work = snap.assign(
            mid=(b + a) / 2,
            _delta=pd.to_numeric(snap["delta"], errors="coerce"),
        )
        two = (work[(b > 0) & a.notna() & (a >= b)]
               .drop_duplicates(subset=_JOIN, keep="first"))
        sl = bars.iloc[lo:hi]
        covered.iloc[lo:hi] = True
        pieces.append(sl.merge(two[[*_JOIN, "mid", "_delta"]],
                               on=_JOIN, how="left")
                      .rename(columns={"_delta": "delta"}))
    if failed_reads:
        log.warning("%s %s: %d snap reads failed — no row, retrying next run",
                    ticker, d, failed_reads)
        return None
    uncovered = bars[~covered].assign(mid=pd.NA, delta=pd.NA)
    joined = pd.concat([*pieces, uncovered], ignore_index=True) \
        if pieces else uncovered
    row = reduce_flow_session(joined, und_volume)
    if row is None:
        return None

    # tape-overlap accuracy: score the classifier against true sides
    row["tape_checked_volume"] = None
    row["tape_side_agreement"] = None
    tape = (r2_get_parquet_spooled(
        s3, f"uw/option_tape/ticker={ticker}/date={d}/trades.parquet",
        columns=_TAPE_COLUMNS) if has_tape else None)
    truth = tape_side_truth(tape) if tape is not None else None
    if truth is not None and pieces:
        cls = pd.concat(pieces, ignore_index=True)
        cls = cls[cls["mid"].notna()]
        sign = pd.Series(0.0, index=cls.index)
        vw = pd.to_numeric(cls["vwap"], errors="coerce")
        sign[vw > cls["mid"]] = 1.0
        sign[vw < cls["mid"]] = -1.0
        cls = cls[sign != 0].assign(_sign=sign[sign != 0])
        truth["minute_ts"] = pd.to_datetime(truth["minute_ts"], utc=True)
        j = cls.merge(truth, on=[*_JOIN, "minute_ts"], how="inner")
        if len(j):
            vol = pd.to_numeric(j["volume"], errors="coerce").fillna(0.0)
            agree = float(vol[j["_sign"] == j["true_sign"]].sum())
            row["tape_checked_volume"] = float(vol.sum())
            row["tape_side_agreement"] = (round(agree / float(vol.sum()), 4)
                                          if vol.sum() > 0 else None)
    return row


def run(s3, ticker: str) -> int:
    key = FLOW_INHOUSE_KEY.format(ticker=ticker)
    existing = r2_get_parquet(s3, key)
    have = (set(existing["date"].astype(str))
            if existing is not None and not existing.empty
            and "date" in existing.columns else set())
    alp = set(_sessions(s3, f"options_minute/source=alpaca/ticker={ticker}/"))
    rec = set(_sessions(s3, f"options_intraday/source=cboe_delayed/ticker={ticker}/"))
    tape_dates = set(_sessions(s3, f"uw/option_tape/ticker={ticker}/"))
    today_et = pd.Timestamp.now(tz="America/New_York").date().isoformat()
    todo = sorted(d for d in (alp & rec) - have if d < today_et)
    if not todo:
        log.info("%s: up to date (%d sessions)", ticker, len(have))
        return 0
    frames = [existing] if existing is not None and not existing.empty else []
    derived = 0
    for d in todo:
        row = derive_session(s3, ticker, d, has_tape=d in tape_dates)
        if row is None:
            continue
        row["date"] = d
        frames.append(pd.DataFrame([row]))
        derived += 1
        if derived % 10 == 0:
            r2_put_parquet(s3, key, _combined(frames))
            log.info("%s: %d/%d sessions derived (checkpointed)",
                     ticker, derived, len(todo))
    if derived:
        r2_put_parquet(s3, key, _combined(frames))
    log.info("%s: derived %d sessions → r2://%s", ticker, derived, key)
    return derived


def _combined(frames: list[pd.DataFrame]) -> pd.DataFrame:
    return (pd.concat(frames, ignore_index=True)
            .drop_duplicates(subset=["date"], keep="last")
            .sort_values("date").reset_index(drop=True))


def main() -> int:
    logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                        format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tickers", default="SPY,QQQ,IWM")
    args = ap.parse_args()
    s3 = r2_client()
    n = 0
    for t in [x.strip().upper() for x in args.tickers.split(",") if x.strip()]:
        n += run(s3, t)
    log.info("done: %d ticker-sessions derived", n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
