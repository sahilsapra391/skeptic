"""run_eod's OPRA handling (the 2026-08-04 QQQ/IWM freeze regression).

The Alpaca OPRA-entitlement 403 fires on the JUST-CLOSED session only —
historical days serve fine (the nightly lookback writes them right before
the 403 every night). The old handler `break`-ed out of the day loop, which
fell through the for/else into the OUTER ticker break: SPY's current-session
403 aborted the entire top-up, silently freezing QQQ/IWM from 2026-07-02 to
2026-08-04 while `failures = 0` kept every run green. These tests pin the
fixed semantics: an OPRA day is SKIPPED, everything else still runs, and
earlier real failures are never erased.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

import alpaca

OLDER = date(2026, 8, 1)
CURRENT = date(2026, 8, 4)

OPRA_ERR = RuntimeError(
    "Alpaca returned 403 'OPRA agreement is not signed' — an account "
    "entitlement, not a code failure."
)


@pytest.fixture()
def lake(monkeypatch: pytest.MonkeyPatch):
    """Stub every I/O edge of run_eod; record universe calls and writes."""
    calls: dict[str, list] = {"universe": [], "written": [], "bars": []}

    monkeypatch.setattr(alpaca, "r2_client", lambda: object())
    monkeypatch.setattr(alpaca, "completed_sessions", lambda now, k: [OLDER, CURRENT])
    monkeypatch.setattr(alpaca, "lake_has_session", lambda s3, t, d: False)
    monkeypatch.setattr(
        alpaca, "contract_symbols",
        lambda ticker, gte, lte: calls["universe"].append((ticker, gte)) or ["X"],
    )
    monkeypatch.setattr(
        alpaca, "bars_to_frame",
        lambda ticker, raw: pd.DataFrame({"trading_date": [OLDER]}),
    )
    monkeypatch.setattr(
        alpaca, "write_sessions",
        lambda s3, ticker, frame: calls["written"].append(ticker) or {},
    )
    # underlying leg: nothing to write
    monkeypatch.setattr(alpaca, "fetch_underlying_minute", lambda a, b: pd.DataFrame())
    monkeypatch.setattr(
        alpaca, "write_underlying_month",
        lambda s3, month, frame: pytest.fail("empty frame must not be written"),
    )
    return calls


def test_current_session_opra_skips_the_day_not_the_run(
    lake: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SPY's just-closed-session 403 must not abort QQQ/IWM (the freeze bug)."""

    def bars(symbols, start_iso, end_iso):
        if start_iso.startswith(str(CURRENT)):
            raise OPRA_ERR
        return [{"symbol": "X"}]

    monkeypatch.setattr(alpaca, "fetch_option_bars", bars)

    rc = alpaca.run_eod(["SPY", "QQQ", "IWM"])

    # every (ticker, day) pair was attempted — the outer loop never died
    assert [u for u in lake["universe"]] == [
        ("SPY", OLDER), ("SPY", CURRENT),
        ("QQQ", OLDER), ("QQQ", CURRENT),
        ("IWM", OLDER), ("IWM", CURRENT),
    ]
    # the older (published) day was written for ALL tickers, QQQ/IWM included
    assert lake["written"] == ["SPY", "QQQ", "IWM"]
    # OPRA on the current session is a known condition, not a failure
    assert rc == 0


def test_opra_mid_lookback_still_attempts_later_days(
    lake: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An OPRA day is skipped in place — later days of the same ticker run."""

    def bars(symbols, start_iso, end_iso):
        if start_iso.startswith(str(OLDER)):
            raise OPRA_ERR
        return [{"symbol": "X"}]

    monkeypatch.setattr(alpaca, "fetch_option_bars", bars)
    monkeypatch.setattr(
        alpaca, "bars_to_frame",
        lambda ticker, raw: pd.DataFrame({"trading_date": [CURRENT]}),
    )

    rc = alpaca.run_eod(["SPY"])

    assert lake["universe"] == [("SPY", OLDER), ("SPY", CURRENT)]
    assert lake["written"] == ["SPY"]  # the non-OPRA day still landed
    assert rc == 0


def test_opra_skip_does_not_erase_real_failures(
    lake: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The old handler reset failures=0 on OPRA — a real earlier failure then
    exited green. The fix preserves the non-zero exit."""

    def bars(symbols, start_iso, end_iso):
        if start_iso.startswith(str(OLDER)):
            raise RuntimeError("boom: a genuine fetch failure")
        raise OPRA_ERR  # current session

    monkeypatch.setattr(alpaca, "fetch_option_bars", bars)

    rc = alpaca.run_eod(["SPY", "QQQ"])

    # both tickers fully attempted despite the failures
    assert lake["universe"] == [
        ("SPY", OLDER), ("SPY", CURRENT),
        ("QQQ", OLDER), ("QQQ", CURRENT),
    ]
    assert rc == 1  # the real failures survived the OPRA skips
