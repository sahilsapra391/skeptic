"""heartbeat.py lane selection: the pager now watches the yahoo redundancy
lane too (its worker thread can die without freezing the CBOE cadence that
used to trip the alert indirectly), so newest_snapshot takes a source."""

from __future__ import annotations

import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "deploy"))
import heartbeat  # noqa: E402  (deploy/ put on the path above)

KEYS = [
    "options_intraday/source=cboe_delayed/ticker=SPY/date=2026-07-13/snap_20260713T1401Z.parquet",
    "options_intraday/source=cboe_delayed/ticker=SPY/date=2026-07-13/snap_20260713T1402Z.parquet",
    "options_intraday/source=yahoo/ticker=SPY/date=2026-07-13/snap_20260713T1334Z.parquet",
]


class _StubS3:
    def get_paginator(self, op):
        class _P:
            def paginate(self, Bucket, Prefix):
                yield {"Contents": [{"Key": k} for k in KEYS if k.startswith(Prefix)]}
        return _P()


def test_newest_snapshot_defaults_to_cboe_lane():
    ts = heartbeat.newest_snapshot(_StubS3(), "bucket", "SPY", "2026-07-13")
    assert ts == pd.Timestamp("2026-07-13T14:02", tz="UTC")


def test_newest_snapshot_yahoo_lane_is_separate():
    ts = heartbeat.newest_snapshot(_StubS3(), "bucket", "SPY", "2026-07-13", source="yahoo")
    assert ts == pd.Timestamp("2026-07-13T13:34", tz="UTC")


def test_newest_snapshot_missing_lane_is_none():
    ts = heartbeat.newest_snapshot(_StubS3(), "bucket", "QQQ", "2026-07-13", source="yahoo")
    assert ts is None
