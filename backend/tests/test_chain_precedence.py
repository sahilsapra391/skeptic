"""Chain source precedence: ivolatility > alphavantage > cboe_eod > yahoo > dolthub."""

from typing import Any

import pytest

from app.data import chains, r2


def test_ivolatility_wins_every_overlap(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_list_chain_dates(_s3: Any, source: str, _ticker: str) -> list[str]:
        return {
            "ivolatility": ["2024-01-02", "2024-01-03"],
            "alphavantage": ["2024-01-03", "2024-01-04"],
            "dolthub": ["2024-01-02", "2024-01-05"],
        }.get(source, [])

    monkeypatch.setattr(r2, "list_chain_dates", fake_list_chain_dates)
    yahoo_key = "options/source=yahoo/ticker=SPY/date=2024-01-04/snap_1.parquet"
    monkeypatch.setattr(chains, "_latest_yahoo_keys", lambda _s3, _t: {"2024-01-04": yahoo_key})
    monkeypatch.setattr(
        r2, "get_json", lambda _s3, _k, _d: {"done": ["2024-01-02", "2024-01-05"]}
    )

    winners = chains._chain_keys(object(), "SPY")

    assert "source=ivolatility" in winners["2024-01-02"]  # beats dolthub
    assert "source=ivolatility" in winners["2024-01-03"]  # beats alphavantage
    assert "source=alphavantage" in winners["2024-01-04"]  # av beats yahoo
    assert "source=dolthub" in winners["2024-01-05"]  # dolthub fills the rest


def test_cboe_eod_beats_yahoo_loses_to_vendor_records(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The forward record: cboe_eod outranks the 60-DTE Yahoo snapshot and
    loses to the true vendor EOD records (iVol / AV) wherever they exist."""

    def fake_list_chain_dates(_s3: Any, source: str, _ticker: str) -> list[str]:
        return {
            "ivolatility": ["2026-07-02"],
            "alphavantage": ["2026-07-03"],
            "cboe_eod": ["2026-07-02", "2026-07-03", "2026-07-06", "2026-07-07"],
        }.get(source, [])

    monkeypatch.setattr(r2, "list_chain_dates", fake_list_chain_dates)
    monkeypatch.setattr(
        chains,
        "_latest_yahoo_keys",
        lambda _s3, _t: {
            d: f"options/source=yahoo/ticker=SPY/date={d}/snap_1.parquet"
            for d in ("2026-07-06", "2026-07-07", "2026-07-08")
        },
    )
    monkeypatch.setattr(r2, "get_json", lambda _s3, _k, _d: {"done": []})

    winners = chains._chain_keys(object(), "SPY")

    assert "source=ivolatility" in winners["2026-07-02"]  # vendor record wins
    assert "source=alphavantage" in winners["2026-07-03"]  # vendor record wins
    assert "source=cboe_eod" in winners["2026-07-06"]  # beats yahoo
    assert "source=cboe_eod" in winners["2026-07-07"]  # beats yahoo
    assert "source=yahoo" in winners["2026-07-08"]  # yahoo fills the rest
