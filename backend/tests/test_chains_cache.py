"""Loader cache versioning (D1a): widening COLUMNS changed the cached
artifact's shape, so pre-D1a caches (manifests without the "v" field) must
rebuild automatically — a stale narrow cache would silently starve the
engine of the greeks it now reads."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from app.data import chains, r2


def _yahoo_like_frame() -> pd.DataFrame:
    return pd.DataFrame([{
        "trading_date": "2024-01-02", "expiration": "2024-02-02", "right": "put",
        "strike": 95.0, "bid": 1.0, "ask": 1.2, "last": None, "volume": 12,
        "open_interest": 340, "iv": 0.2, "delta": None, "gamma": None,
        "theta": None, "vega": None, "rho": None, "greeks_source": None,
        "spot": 100.0, "source": "yahoo",
    }])


@pytest.fixture()
def loader_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Any]:
    state = {"fetches": 0}

    def fake_fetch(_s3: Any, _keys: list[str]) -> list[pd.DataFrame]:
        state["fetches"] += 1
        return [_yahoo_like_frame()]

    monkeypatch.setattr(chains, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(r2, "r2_client", lambda: object())
    monkeypatch.setattr(
        chains, "_chain_keys",
        lambda _s3, _t: {"2024-01-02": "options/source=yahoo/.../snap_1.parquet"},
    )
    monkeypatch.setattr(chains, "_fetch_frames", fake_fetch)
    return state


def test_cache_hit_and_stale_schema_rebuild(loader_env: dict[str, Any], tmp_path: Path) -> None:
    spot = {pd.Timestamp("2024-01-02").date(): 100.0}

    first = chains._load_combined("SPY", spot, None)
    assert loader_env["fetches"] == 1
    # computed greeks are part of the cached artifact
    assert first.loc[0, "greeks_source"] == "computed"
    assert pd.notna(first.loc[0, "delta"])

    meta = json.loads((tmp_path / "chains_SPY.json").read_text())
    assert meta["v"] == chains.CACHE_SCHEMA_VERSION

    # warm hit: same manifest → no refetch
    second = chains._load_combined("SPY", spot, None)
    assert loader_env["fetches"] == 1
    assert second.loc[0, "greeks_source"] == "computed"

    # pre-D1a manifest (no "v") → mismatch → rebuild
    (tmp_path / "chains_SPY.json").write_text(json.dumps({"n": 1, "last": "2024-01-02"}))
    third = chains._load_combined("SPY", spot, None)
    assert loader_env["fetches"] == 2
    assert third.loc[0, "greeks_source"] == "computed"


def test_non_finite_rows_are_honest_absences(loader_env: dict[str, Any],
                                             monkeypatch: pytest.MonkeyPatch) -> None:
    # every evaluability surface (the staleness/coverage refusals, the
    # composer's rank unlock dates) reasons from the store's *_dates
    # lists — a date must never point at a value the engine refuses.
    # Loaders drop NaN, but pandas' NaN-only filters keep ±inf.
    daily = pd.DataFrame({
        "date": pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04"]),
        "open": [99.5, 100.5, 101.0], "close": [100.0, 101.0, 102.0],
    })
    vix = pd.DataFrame({
        "date": pd.to_datetime(["2024-01-02", "2024-01-03"]),
        "close": [14.5, float("inf")],
    })
    monkeypatch.setattr(chains, "_underlying_frames", lambda _t: (daily, vix, None))
    d2, d3, d4 = (pd.Timestamp(f"2024-01-0{n}").date() for n in (2, 3, 4))
    # ivx and hv load inside ONE guard — stub both or the except zeroes both
    monkeypatch.setattr(chains.ivol_analytics, "load_ivx_30d",
                        lambda _s3, _t: {d2: 0.20, d3: float("inf"), d4: float("nan")})
    monkeypatch.setattr(chains.ivol_analytics, "load_hv_30d", lambda _s3, _t: {})
    monkeypatch.setattr(chains.gex_signals, "load_dealer_exposure",
                        lambda _s3, _t: ({d2: 1.5e9, d3: float("-inf")}, {d2: -2.0e9}))

    store = chains._build_market_store("SPY")
    assert store.ivx_dates == [d2]
    assert store.ivx_30d == {d2: 0.20}
    assert store.gex_dates == [d2]
    assert store.net_gex == {d2: 1.5e9}
    assert store.dex_dates == [d2]
    # vix_dates is rebuilt WITH its filtered values — a poisoned session
    # must not leave a date pointing at a missing key
    assert store.vix_dates == [d2]
    assert store.vix_close == {d2: 14.5}


def test_store_carries_widened_quote(loader_env: dict[str, Any],
                                     monkeypatch: pytest.MonkeyPatch) -> None:
    daily = pd.DataFrame({
        "date": pd.to_datetime(["2024-01-02"]),
        "open": [99.5], "close": [100.0],
    })
    monkeypatch.setattr(chains, "_underlying_frames", lambda _t: (daily, None, None))

    store = chains._build_market_store("SPY")
    day = pd.Timestamp("2024-01-02").date()
    (quote,) = store.chains[day].values()
    assert quote.volume == 12
    assert quote.open_interest == 340
    assert quote.greeks_source == "computed"
    assert quote.delta is not None and -1.0 < quote.delta < 0.0
    assert quote.gamma is not None and quote.gamma > 0
    assert quote.theta is not None
    assert quote.vega is not None and quote.vega > 0
