"""IntradayStore (D2a): loader normalization, per-session disk cache with
versioned manifests, bounded LRU, and the owner-amendment-1 source ranking
(ivol_5min — true NBBO — outranks the ~15-min-delayed cboe_minute wherever
both exist; CBOE serves forward coverage only)."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from app.data import intraday, r2
from app.engine.types import ContractKey


def _ivol_opt_frame(d: str) -> pd.DataFrame:
    return pd.DataFrame([
        {"ticker": "SPY", "trading_date": d, "minute_ts": f"{d} 09:30:00",
         "occ_symbol": "SPY...", "expiration": d, "dte": 0, "right": "put",
         "strike": 100.0, "bid": 2.00, "ask": 2.10, "bid_size": 10, "ask_size": 12,
         "iv": 0.2, "delta": -0.5, "gamma": 0.05, "theta": -0.30, "vega": 0.34,
         "rho": 0.01, "volume": 25, "underlying_price": 100.0,
         "source": "ivolatility", "minute_type": "MINUTE_5"},
        {"ticker": "SPY", "trading_date": d, "minute_ts": f"{d} 09:35:00",
         "occ_symbol": "SPY...", "expiration": d, "dte": 0, "right": "put",
         "strike": 100.0, "bid": 1.80, "ask": 1.90, "bid_size": 9, "ask_size": 11,
         "iv": 0.19, "delta": -0.45, "gamma": 0.05, "theta": -0.29, "vega": 0.33,
         "rho": 0.01, "volume": 40, "underlying_price": 100.4,
         "source": "ivolatility", "minute_type": "MINUTE_5"},
    ])


def _ivol_und_frame(d: str) -> pd.DataFrame:
    # volume is CUMULATIVE in the vendor feed (probed) — per-bar is the diff
    return pd.DataFrame([
        {"minute_ts": f"{d} 09:30:00", "last": 100.0, "volume": 1_000},
        {"minute_ts": f"{d} 09:35:00", "last": 100.4, "volume": 1_600},
    ])


def _cboe_snap_frame(d: str, bid: float) -> pd.DataFrame:
    """Canonical CBOE snapshot: one in-slice contract, one out-of-slice
    (far strike) and one long-dated row that the slice filter must drop."""
    base = {"ticker": "SPY", "trading_date": d, "snapshot_ts": "x", "last": None,
            "volume": 5, "open_interest": 100, "iv": 0.2, "delta": -0.5,
            "gamma": 0.05, "theta": -0.3, "vega": 0.34, "rho": 0.01,
            "greeks_source": "vendor", "spot": 100.0, "source": "cboe_delayed",
            "source_ts": "y"}
    return pd.DataFrame([
        {**base, "expiration": d, "dte": 0, "right": "put", "strike": 100.0,
         "bid": bid, "ask": bid + 0.10},
        {**base, "expiration": d, "dte": 0, "right": "put", "strike": 120.0,
         "bid": 0.01, "ask": 0.05},  # outside ATM±$8 → dropped
        {**base, "expiration": "2026-12-18", "dte": 200, "right": "put",
         "strike": 100.0, "bid": 9.0, "ask": 9.4},  # long-dated → dropped
    ])


KEY = ContractKey(expiration=date(2025, 1, 6), right="put", strike=100.0)
D = "2025-01-06"


@pytest.fixture()
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Any]:
    state = {"parquet_fetches": 0}
    monkeypatch.setattr(intraday, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(r2, "r2_client", lambda: object())
    intraday._STORE_CACHE.clear()
    return state


class TestIvolSessions:
    @pytest.fixture()
    def store(self, env: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> intraday.IntradayStore:
        def fake_prefixes(_s3: Any, prefix: str) -> list[str]:
            return [D] if "ivolatility" in prefix else []

        def fake_parquet(_s3: Any, key: str) -> pd.DataFrame | None:
            env["parquet_fetches"] += 1
            if "underlying_intraday" in key:
                return _ivol_und_frame(D)
            return _ivol_opt_frame(D)

        monkeypatch.setattr(r2, "list_date_prefixes", fake_prefixes)
        monkeypatch.setattr(r2, "get_parquet", fake_parquet)
        return intraday.IntradayStore("SPY")

    def test_normalizes_bars_quotes_and_underlying(self, store: intraday.IntradayStore) -> None:
        slc = store.slice_for(date(2025, 1, 6))
        assert slc is not None
        assert slc.quote_source == "ivol_5min"
        assert slc.bars == [datetime(2025, 1, 6, 9, 30), datetime(2025, 1, 6, 9, 35)]
        q = slc.quotes[datetime(2025, 1, 6, 9, 30)][KEY]
        assert q.bid == 2.00 and q.ask == 2.10
        assert q.theta == -0.30 and q.vega == 0.34  # vendor greeks ride along
        assert q.greeks_source == "vendor"
        assert slc.underlying[datetime(2025, 1, 6, 9, 35)] == 100.4
        # cumulative 1,000 → 1,600 becomes per-bar 1,000 and 600 (D2c)
        assert slc.underlying_volume[datetime(2025, 1, 6, 9, 30)] == 1_000
        assert slc.underlying_volume[datetime(2025, 1, 6, 9, 35)] == 600

    def test_mid_session_nan_volume_does_not_distort_vwap(
        self, env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A single unparseable cumulative-volume cell mid-session must NOT
        inject the whole session cumulative (~52M) as one bar's volume —
        the affected bars sit out of session-anchored VWAP instead."""
        und = pd.DataFrame([
            {"minute_ts": f"{D} 09:30:00", "last": 100.0, "volume": 1_000},
            {"minute_ts": f"{D} 09:35:00", "last": 100.4, "volume": 1_600},
            {"minute_ts": f"{D} 09:40:00", "last": 100.2, "volume": "bad"},
            {"minute_ts": f"{D} 09:45:00", "last": 120.0, "volume": 52_000_000},
        ])

        def fake_prefixes(_s3: Any, prefix: str) -> list[str]:
            return [D] if "ivolatility" in prefix else []

        def fake_parquet(_s3: Any, key: str) -> pd.DataFrame | None:
            return und if "underlying_intraday" in key else _ivol_opt_frame(D)

        monkeypatch.setattr(r2, "list_date_prefixes", fake_prefixes)
        monkeypatch.setattr(r2, "get_parquet", fake_parquet)

        slc = intraday.IntradayStore("SPY").slice_for(date(2025, 1, 6))
        assert slc is not None
        assert slc.underlying_volume[datetime(2025, 1, 6, 9, 30)] == 1_000
        assert slc.underlying_volume[datetime(2025, 1, 6, 9, 35)] == 600
        # the bad cell's bar AND the next (diff spans the gap) are unknown
        assert datetime(2025, 1, 6, 9, 40) not in slc.underlying_volume
        assert datetime(2025, 1, 6, 9, 45) not in slc.underlying_volume

        # session VWAP exactly as the engine accumulates it (missing → 0):
        # (100.0×1000 + 100.4×600) / 1600 = 100.15 — hand-computed. The old
        # fillna(cum_vol) put 52M on the 09:45 bar, dragging VWAP to ~120.
        pv = sum(slc.underlying[b] * slc.underlying_volume.get(b, 0.0)
                 for b in slc.bars if b in slc.underlying)
        vol = sum(slc.underlying_volume.get(b, 0.0)
                  for b in slc.bars if b in slc.underlying)
        assert vol == 1_600
        assert pv / vol == pytest.approx(100.15)

        # the unknown bars must survive the disk-cache round-trip too — a
        # future cast/fillna in _write_cache would regress ONLY cached reads
        cached = intraday.IntradayStore("SPY").slice_for(date(2025, 1, 6))
        assert cached is not None
        assert datetime(2025, 1, 6, 9, 40) not in cached.underlying_volume
        assert datetime(2025, 1, 6, 9, 45) not in cached.underlying_volume

    def test_unsorted_underlying_rows_are_time_ordered_before_diff(
        self, env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The lake preserves vendor row order unguarded; if a payload ever
        arrives out of time order, the cumulative diff must not anchor on a
        mid-session row (which would inject its cumulative as one bar)."""
        und = pd.DataFrame([
            {"minute_ts": f"{D} 09:40:00", "last": 100.6, "volume": 30_000_000},
            {"minute_ts": f"{D} 09:30:00", "last": 100.0, "volume": 1_000},
            {"minute_ts": f"{D} 09:35:00", "last": 100.4, "volume": 1_600},
        ])

        def fake_prefixes(_s3: Any, prefix: str) -> list[str]:
            return [D] if "ivolatility" in prefix else []

        def fake_parquet(_s3: Any, key: str) -> pd.DataFrame | None:
            return und if "underlying_intraday" in key else _ivol_opt_frame(D)

        monkeypatch.setattr(r2, "list_date_prefixes", fake_prefixes)
        monkeypatch.setattr(r2, "get_parquet", fake_parquet)

        slc = intraday.IntradayStore("SPY").slice_for(date(2025, 1, 6))
        assert slc is not None
        assert slc.underlying_volume[datetime(2025, 1, 6, 9, 30)] == 1_000
        assert slc.underlying_volume[datetime(2025, 1, 6, 9, 35)] == 600
        assert slc.underlying_volume[datetime(2025, 1, 6, 9, 40)] == 29_998_400

    def test_disk_cache_serves_second_store(self, store: intraday.IntradayStore,
                                            env: dict[str, Any]) -> None:
        store.slice_for(date(2025, 1, 6))
        fetches = env["parquet_fetches"]
        fresh = intraday.IntradayStore("SPY")  # new store, warm disk cache
        slc = fresh.slice_for(date(2025, 1, 6))
        assert slc is not None and slc.quote_source == "ivol_5min"
        assert env["parquet_fetches"] == fetches  # no new network reads

    def test_stale_cache_version_refetches(self, store: intraday.IntradayStore,
                                           env: dict[str, Any], tmp_path: Path) -> None:
        store.slice_for(date(2025, 1, 6))
        meta = tmp_path / "SPY" / f"{D}_meta.json"
        meta.write_text('{"v": 0, "source": "ivol_5min"}')  # pre-D2a manifest
        fetches = env["parquet_fetches"]
        fresh = intraday.IntradayStore("SPY")
        assert fresh.slice_for(date(2025, 1, 6)) is not None
        assert env["parquet_fetches"] > fetches  # rebuilt from the lake

    def test_stale_und_parquet_never_survives_refetch(
        self, env: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """A refetch whose underlying GET fails (r2 swallows errors → None)
        must not leave the prior version's und parquet behind the fresh
        manifest — _read_cached serves whatever und file exists, so a stale
        frame would ride along under the new schema version forever."""
        state = {"und_available": True}

        def fake_prefixes(_s3: Any, prefix: str) -> list[str]:
            return [D] if "ivolatility" in prefix else []

        def fake_parquet(_s3: Any, key: str) -> pd.DataFrame | None:
            if "underlying_intraday" in key:
                return _ivol_und_frame(D) if state["und_available"] else None
            return _ivol_opt_frame(D)

        monkeypatch.setattr(r2, "list_date_prefixes", fake_prefixes)
        monkeypatch.setattr(r2, "get_parquet", fake_parquet)

        intraday.IntradayStore("SPY").slice_for(date(2025, 1, 6))
        und_p = tmp_path / "SPY" / f"{D}_und.parquet"
        assert und_p.exists()

        # stale manifest (older schema) + underlying outage on the refetch
        (tmp_path / "SPY" / f"{D}_meta.json").write_text(
            '{"v": 0, "source": "ivol_5min"}')
        state["und_available"] = False
        slc = intraday.IntradayStore("SPY").slice_for(date(2025, 1, 6))
        assert slc is not None
        assert not und_p.exists()  # the old-schema volumes went with it
        assert slc.underlying == {}

    def test_lru_is_bounded(self, store: intraday.IntradayStore,
                            monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(intraday, "LRU_SESSIONS", 2)
        store._remember(date(2025, 1, 6), None)
        store._remember(date(2025, 1, 7), None)
        store._remember(date(2025, 1, 8), None)
        assert len(store._lru) == 2
        assert date(2025, 1, 6) not in store._lru  # oldest evicted


class TestSourceRanking:
    def test_ivol_outranks_cboe_where_both_exist(self, env: dict[str, Any],
                                                 monkeypatch: pytest.MonkeyPatch) -> None:
        def fake_prefixes(_s3: Any, prefix: str) -> list[str]:
            if "ivolatility" in prefix:
                return ["2025-01-06"]
            if "cboe_delayed" in prefix:
                return ["2025-01-06", "2025-01-07"]  # overlap + forward-only day
            return []

        monkeypatch.setattr(r2, "list_date_prefixes", fake_prefixes)
        store = intraday.IntradayStore("SPY")
        # owner amendment 1: true NBBO wins the overlap; CBOE keeps the
        # forward session iVol has not captured
        assert store.source_for(date(2025, 1, 6)) == "ivol_5min"
        assert store.source_for(date(2025, 1, 7)) == "cboe_minute"
        assert store.sessions() == [date(2025, 1, 6), date(2025, 1, 7)]


class TestCboeDownsampling:
    def test_first_snapshot_per_bar_and_slice_filter(self, env: dict[str, Any],
                                                     monkeypatch: pytest.MonkeyPatch) -> None:
        # snap keys are UTC (ET+5 in January): 14:30Z=09:30ET, 14:33Z=09:33ET,
        # 14:36Z=09:36ET → bars 09:30 (first snap wins) and 09:35
        keys = [
            f"options_intraday/source=cboe_delayed/ticker=SPY/date={D}/snap_20250106T1430Z.parquet",
            f"options_intraday/source=cboe_delayed/ticker=SPY/date={D}/snap_20250106T1433Z.parquet",
            f"options_intraday/source=cboe_delayed/ticker=SPY/date={D}/snap_20250106T1436Z.parquet",
        ]
        snaps = {keys[0]: _cboe_snap_frame(D, bid=2.00),
                 keys[1]: _cboe_snap_frame(D, bid=5.00),  # same bar — must NOT be used
                 keys[2]: _cboe_snap_frame(D, bid=1.50)}

        def fake_prefixes(_s3: Any, prefix: str) -> list[str]:
            return [D] if "cboe_delayed" in prefix else []

        monkeypatch.setattr(r2, "list_date_prefixes", fake_prefixes)
        monkeypatch.setattr(r2, "list_keys", lambda _s3, _p: keys)
        monkeypatch.setattr(r2, "get_parquet", lambda _s3, k: snaps.get(k))

        store = intraday.IntradayStore("SPY")
        slc = store.slice_for(date(2025, 1, 6))
        assert slc is not None and slc.quote_source == "cboe_minute"
        assert slc.bars == [datetime(2025, 1, 6, 9, 30), datetime(2025, 1, 6, 9, 35)]

        bar930 = slc.quotes[datetime(2025, 1, 6, 9, 30)]
        assert bar930[KEY].bid == 2.00  # FIRST snapshot at/after bar open
        # slice filter: far strike and long-dated rows are gone
        assert all(abs(k.strike - 100.0) <= intraday.SLICE_ATM_BAND for k in bar930)
        assert all(k.expiration == date(2025, 1, 6) for k in bar930)
        # OI/last from the canonical snapshot survive normalization
        assert bar930[KEY].open_interest == 100
        assert slc.underlying[datetime(2025, 1, 6, 9, 30)] == 100.0


@pytest.mark.lake
def test_pinned_live_session_loads_real_nbbo() -> None:
    """Owner-machine probe: the pinned 2026-07-02 SPY session loads from the
    live lake, carries 82 bars of two-sided NBBO, and identifies as
    ivol_5min. Auto-skips without credentials (CI)."""
    from app.config import load_local_env

    load_local_env()
    if not r2.r2_configured():
        pytest.skip("no R2 credentials — lake probe runs on the owner's machine")

    store = intraday.load_intraday_store("SPY")
    slc = store.slice_for(date(2026, 7, 2))
    assert slc is not None
    assert slc.quote_source == "ivol_5min"
    assert len(slc.bars) >= 78  # full session at 5-min cadence
    bar = slc.bars[len(slc.bars) // 2]
    chain = slc.quotes.get(bar, {})
    assert len(chain) >= 20  # the ATM slice is populated mid-session
    two_sided = [q for q in chain.values() if q.bid is not None and q.ask is not None]
    assert len(two_sided) / len(chain) > 0.95
    assert all(q.bid <= q.ask for q in two_sided)  # never crossed (probed 0.0000)
    assert slc.underlying.get(bar) is not None
