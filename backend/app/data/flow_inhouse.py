"""In-house forward flow family (post-UW continuation candidate — derived,
validated, NOT spliced).

The UW flow families froze with the trial (net_prem_ticks through
2026-07-10; rank forms locked). This module rebuilds the same session
quantities from sources we own forever: Alpaca per-contract minute TRADE
bars (side-blind) classified against the CBOE recorder's minute quotes
(15-min feed lag, measured — the derive slices bars into each snapshot's
shifted validity window via the same recorder_tape_window helper the
tape cross-validation uses).

Classification is quote-rule (Lee-Ready style): a bar whose VWAP sits
above the contemporaneous mid is buyer-aggressor (+1), below is seller
(−1), exactly at mid is a MID bucket — counted, never signed. Its
accuracy is MEASURED against the UW tape's true per-print sides on the
frozen overlap sessions and carried per row (tape_side_agreement), so
the family's trustworthiness is a number, not a hope.

Session reductions (mirroring app/data/flow_signals.py conventions so the
flow_inhouse_vs_uw cross-validation pair compares like with like):
  net_premium           Σ sign·vwap·volume·100 over calls MINUS the same
                        over puts (dollar units, own capture → the
                        engine may only ever consume sign/rank forms)
  put_call_flow_ratio   Σ put volume / Σ call volume over ALL bars —
                        classification-independent
  nope_eod              Σ sign·delta·volume·100 / underlying session
                        volume (deltas from the matched snapshot; own
                        NOPE implementation → sign/rank only)

Honesty: every bar is accounted — classified, mid, quote-less
(volume_unquoted) or delta-less (delta_missing_volume for the NOPE sum).
An empty/unrecognized session derives None per signal, never a guess.
This artifact is a NEW family with its own history start (first recorder
∩ Alpaca session); it is never spliced into the frozen UW columns — any
future unfreeze/substitution is an owner decision citing the
cross-validation and tape-agreement numbers this module produces.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

FLOW_INHOUSE_KEY = "reference/derived/flow_inhouse/ticker={ticker}.parquet"

_NEEDED = ("right", "vwap", "volume", "mid", "delta")


def reduce_flow_session(
    joined: pd.DataFrame, und_volume: float | None
) -> dict[str, Any] | None:
    """One session's quote-joined minute bars → the in-house flow row.
    `joined` columns: right ("call"/"put"), vwap, volume, mid (NaN when
    the bar's minute had no two-sided quote for the contract), delta
    (NaN when the snapshot carried none). Returns None on an
    unrecognized shape; missing pieces yield None per signal."""
    if joined is None or joined.empty \
            or not set(_NEEDED).issubset(joined.columns):
        return None
    t = joined.copy()
    for col in ("vwap", "volume", "mid", "delta"):
        t[col] = pd.to_numeric(t[col], errors="coerce")
    t["right"] = t["right"].astype(str).str.lower()
    t = t[t["vwap"].notna() & (t["volume"] > 0)]
    if t.empty:
        return None

    vol_total = float(t["volume"].sum())
    quoted = t[t["mid"].notna()]
    vol_unquoted = float(vol_total - quoted["volume"].sum())
    sign = pd.Series(0.0, index=quoted.index)
    sign[quoted["vwap"] > quoted["mid"]] = 1.0
    sign[quoted["vwap"] < quoted["mid"]] = -1.0
    classified = quoted[sign != 0]
    csign = sign[sign != 0]
    vol_mid = float(quoted.loc[sign == 0, "volume"].sum())

    out: dict[str, Any] = {
        "net_premium": None, "net_call_premium": None,
        "net_put_premium": None, "put_call_flow_ratio": None,
        "nope_eod": None,
        "volume_total": vol_total,
        "volume_classified": float(classified["volume"].sum()),
        "volume_mid": vol_mid,
        "volume_unquoted": vol_unquoted,
        "delta_missing_volume": None,
    }

    # put/call flow ratio: classification-independent, min_count semantics
    cv = t.loc[t["right"] == "call", "volume"].sum(min_count=1)
    pv = t.loc[t["right"] == "put", "volume"].sum(min_count=1)
    if pd.notna(cv) and pd.notna(pv) and cv > 0:
        out["put_call_flow_ratio"] = round(float(pv / cv), 4)

    if not classified.empty:
        prem = csign * classified["vwap"] * classified["volume"] * 100.0
        ncp = prem[classified["right"] == "call"].sum(min_count=1)
        npp = prem[classified["right"] == "put"].sum(min_count=1)
        out["net_call_premium"] = round(float(ncp), 2) if pd.notna(ncp) else None
        out["net_put_premium"] = round(float(npp), 2) if pd.notna(npp) else None
        if pd.notna(ncp) or pd.notna(npp):
            out["net_premium"] = round(
                float((0.0 if pd.isna(ncp) else ncp)
                      - (0.0 if pd.isna(npp) else npp)), 2)
        has_delta = classified["delta"].notna()
        out["delta_missing_volume"] = float(
            classified.loc[~has_delta, "volume"].sum())
        if has_delta.any() and und_volume is not None and und_volume > 0:
            dflow = (csign[has_delta] * classified.loc[has_delta, "delta"]
                     * classified.loc[has_delta, "volume"] * 100.0).sum()
            out["nope_eod"] = round(float(dflow) / float(und_volume), 6)
    return out


def tape_side_truth(tape: pd.DataFrame) -> pd.DataFrame | None:
    """Per (contract, minute) majority TRUE side from tape prints (the
    tags token — see fill_calibration's schema note). Returns columns
    [expiration, right, strike, minute_ts, true_sign, true_volume] for
    the accuracy join; None on unrecognized shape."""
    need = {"executed_at", "expiry", "option_type", "strike", "size", "tags"}
    if tape is None or tape.empty or not need.issubset(tape.columns):
        return None
    t = tape.copy()
    ts = pd.to_datetime(t["executed_at"], utc=True, format="ISO8601",
                        errors="coerce")
    t = t[ts.notna()]
    ts = ts[ts.notna()]
    tags = t["tags"].astype(str)
    is_ask = tags.str.contains("ask_side", regex=False)
    is_bid = ~is_ask & tags.str.contains("bid_side", regex=False)
    t = t[is_ask | is_bid].assign(
        _sign=pd.Series(1.0, index=t.index).where(is_ask, -1.0),
        _minute=ts.dt.floor("min"),
        _size=pd.to_numeric(t["size"], errors="coerce"),
        _strike=pd.to_numeric(t["strike"], errors="coerce"),
    )
    t = t[t["_size"].notna() & t["_strike"].notna()]
    if t.empty:
        return None
    t["_signed"] = t["_sign"] * t["_size"]
    g = (t.groupby([t["expiry"].astype(str).str[:10],
                    t["option_type"].astype(str).str.lower(),
                    "_strike", "_minute"])
         .agg(net=("_signed", "sum"), true_volume=("_size", "sum"))
         .reset_index())
    g.columns = ["expiration", "right", "strike", "minute_ts",
                 "net", "true_volume"]
    g = g[g["net"] != 0]  # a tied minute has no majority side — excluded
    g["true_sign"] = g["net"].apply(lambda x: 1.0 if x > 0 else -1.0)
    return g.drop(columns=["net"])
