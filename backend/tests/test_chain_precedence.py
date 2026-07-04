"""Chain source precedence: ivolatility > alphavantage > yahoo > dolthub."""

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
