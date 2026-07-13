"""In-house forward flow family — hand-computed.

Classification is sign(vwap − mid); exactly-at-mid volume is counted,
never signed. Quantities mirror flow_signals conventions so the
flow_inhouse_vs_uw pair compares like with like. Every bar is accounted:
classified + mid + unquoted = total, and the NOPE sum discloses the
delta-less volume it had to skip.
"""

from __future__ import annotations

import pandas as pd

from app.data.flow_inhouse import reduce_flow_session, tape_side_truth


def _bar(right: str, vwap: float, vol: float, mid: float | None,
         delta: float | None) -> dict:
    return {"right": right, "vwap": vwap, "volume": vol,
            "mid": mid, "delta": delta}


class TestReduceFlowSession:
    def _joined(self) -> pd.DataFrame:
        return pd.DataFrame([
            _bar("call", 2.10, 3, 2.05, 0.5),    # buyer:  +630 prem, +150 dflow
            _bar("call", 2.00, 2, 2.05, 0.5),    # seller: −400 prem, −100 dflow
            _bar("put", 1.00, 4, 0.95, -0.4),    # buyer:  +400 prem, −160 dflow
            _bar("put", 1.00, 1, 0.90, None),    # buyer:  +100 prem, delta-less
            _bar("put", 0.95, 5, 0.95, -0.4),    # AT mid: counted, never signed
            _bar("call", 1.50, 7, None, None),   # no quote that minute
        ])

    def test_hand_computed_reduction(self) -> None:
        row = reduce_flow_session(self._joined(), und_volume=1000.0)
        assert row is not None
        # net call = 630 − 400 = 230; net put = 400 + 100 = 500
        assert row["net_call_premium"] == 230.0
        assert row["net_put_premium"] == 500.0
        assert row["net_premium"] == 230.0 - 500.0
        # puts (4+1+5) / calls (3+2+7) — classification-independent
        assert row["put_call_flow_ratio"] == round(10 / 12, 4)
        # dflow = +150 − 100 − 160 (the delta-less put is skipped, counted)
        assert row["nope_eod"] == round(-110.0 / 1000.0, 6)
        assert row["delta_missing_volume"] == 1.0
        assert row["volume_total"] == 22.0
        assert row["volume_classified"] == 10.0
        assert row["volume_mid"] == 5.0
        assert row["volume_unquoted"] == 7.0

    def test_no_underlying_volume_means_no_nope(self) -> None:
        row = reduce_flow_session(self._joined(), und_volume=None)
        assert row is not None
        assert row["nope_eod"] is None          # never a guess
        assert row["net_premium"] is not None   # premium needs no und volume

    def test_all_unquoted_yields_none_signals_full_accounting(self) -> None:
        row = reduce_flow_session(pd.DataFrame([
            _bar("call", 1.5, 3, None, None),
            _bar("put", 1.0, 2, None, None),
        ]), und_volume=500.0)
        assert row is not None
        assert row["net_premium"] is None and row["nope_eod"] is None
        assert row["put_call_flow_ratio"] == round(2 / 3, 4)  # volume-only
        assert row["volume_unquoted"] == 5.0

    def test_unrecognized_shape_is_none(self) -> None:
        assert reduce_flow_session(pd.DataFrame({"x": [1]}), 1.0) is None
        assert reduce_flow_session(pd.DataFrame(), 1.0) is None


class TestTapeSideTruth:
    def test_majority_and_tie_handling(self) -> None:
        tape = pd.DataFrame([
            # contract A, minute 13:30 — ask 5 lots vs bid 2 → majority +1
            {"executed_at": "2026-07-08 13:30:01+00", "expiry": "2026-07-18",
             "option_type": "call", "strike": "100", "size": "5",
             "tags": "{ask_side,etf}"},
            {"executed_at": "2026-07-08 13:30:40+00", "expiry": "2026-07-18",
             "option_type": "call", "strike": "100", "size": "2",
             "tags": "{bid_side,etf}"},
            # contract A, minute 13:31 — tied 3/3 → excluded (no majority)
            {"executed_at": "2026-07-08 13:31:05+00", "expiry": "2026-07-18",
             "option_type": "call", "strike": "100", "size": "3",
             "tags": "{ask_side,etf}"},
            {"executed_at": "2026-07-08 13:31:50+00", "expiry": "2026-07-18",
             "option_type": "call", "strike": "100", "size": "3",
             "tags": "{bid_side,etf}"},
            # mid print — never side truth
            {"executed_at": "2026-07-08 13:32:00+00", "expiry": "2026-07-18",
             "option_type": "call", "strike": "100", "size": "9",
             "tags": "{mid_side,etf}"},
        ])
        truth = tape_side_truth(tape)
        assert truth is not None and len(truth) == 1
        r = truth.iloc[0]
        assert r["true_sign"] == 1.0 and r["true_volume"] == 7.0
        assert str(r["minute_ts"]) == "2026-07-08 13:30:00+00:00"

    def test_unrecognized_shape_is_none(self) -> None:
        assert tape_side_truth(pd.DataFrame({"x": [1]})) is None
