"""State-poisoning guards for backfill_unusual_whales.

A transient failure (network down → _get returns -1, or 429/5xx after backoff
exhausted) must NEVER be recorded in reference/state/uw_backfill.json: on
2026-07-10 a DNS-hijacked guest network made every request fail and 89
endpoint scopes banked that date as "empty" — permanently, because state is
the resume ledger. These tests pin the fix: only 200 and 403 are final
outcomes; anything else leaves state untouched so a later run retries, and
per-date sweeps refuse to finalize the current session before the options
close (16:15 ET).
"""

from __future__ import annotations

from datetime import datetime

import pytest

import backfill_unusual_whales as uw

# a boring, definitely-open stretch of sessions, newest-first
DATES = ["2024-06-14", "2024-06-13", "2024-06-12", "2024-06-11", "2024-06-10"]


@pytest.fixture(autouse=True)
def _no_side_effects(monkeypatch):
    monkeypatch.setattr(uw, "_flush", lambda s3, state: None)
    monkeypatch.setattr(uw, "_LIM", uw.Limiter(10**9))


def _daily_manifest() -> list[dict]:
    return [{"name": "flow", "path": "/api/stock/{ticker}/flow-per-strike",
             "mode": "ticker_date", "priority": 2}]


def _fresh_state() -> dict:
    return {"units": {}, "dates": {}, "contracts": {}}


# ------------------------------------------------------------ run_daily
def test_run_daily_network_failure_leaves_dates_unrecorded(monkeypatch):
    """The 2026-07-10 incident: every request fails (-1). No date may land in
    done/empty/blocked, and the scope stops after TRANSIENT_STOP tries."""
    calls: list[str] = []

    def get(url, params=None):
        calls.append(params["date"])
        return -1, None

    monkeypatch.setattr(uw, "MANIFEST", _daily_manifest())
    monkeypatch.setattr(uw, "sessions_desc", lambda a, b: list(DATES))
    monkeypatch.setattr(uw, "_get", get)
    state = _fresh_state()
    uw.run_daily(None, state, ["SPY"], {2}, DATES[-1], DATES[0], dry=False)
    assert state["dates"]["flow|SPY"] == {"done": [], "empty": [], "blocked": []}
    assert len(calls) == uw.TRANSIENT_STOP


def test_run_daily_final_outcomes_still_recorded(monkeypatch):
    """200-with-rows → done, 200-empty → empty, 403 → blocked (unchanged)."""
    bodies = {
        "2024-06-14": (200, {"data": [{"v": 1}]}),
        "2024-06-13": (200, {"data": []}),
        "2024-06-12": (403, None),
    }
    monkeypatch.setattr(uw, "MANIFEST", _daily_manifest())
    monkeypatch.setattr(uw, "sessions_desc", lambda a, b: list(DATES[:3]))
    monkeypatch.setattr(uw, "_get", lambda url, params=None: bodies[params["date"]])
    monkeypatch.setattr(uw, "_write", lambda s3, key, rows, **kw: bool(rows))
    state = _fresh_state()
    uw.run_daily(None, state, ["SPY"], {2}, DATES[2], DATES[0], dry=False)
    st = state["dates"]["flow|SPY"]
    assert st["done"] == ["2024-06-14"]
    assert st["empty"] == ["2024-06-13"]
    assert st["blocked"] == ["2024-06-12"]


def test_run_daily_transient_date_is_skipped_not_blocked(monkeypatch):
    """A transient date is skipped, the sweep continues to older sessions, and
    a later 403 depth floor must NOT drag the skipped (newer) date into
    blocked — it stays unrecorded for the next run."""
    bodies = {
        "2024-06-14": (-1, None),
        "2024-06-13": (200, {"data": [{"v": 1}]}),
        "2024-06-12": (403, None),
    }
    monkeypatch.setattr(uw, "MANIFEST", _daily_manifest())
    monkeypatch.setattr(uw, "sessions_desc", lambda a, b: list(DATES[:3]))
    monkeypatch.setattr(uw, "_get", lambda url, params=None: bodies[params["date"]])
    monkeypatch.setattr(uw, "_write", lambda s3, key, rows, **kw: bool(rows))
    state = _fresh_state()
    uw.run_daily(None, state, ["SPY"], {2}, DATES[2], DATES[0], dry=False)
    st = state["dates"]["flow|SPY"]
    assert st["done"] == ["2024-06-13"]
    assert st["blocked"] == ["2024-06-12"]
    assert "2024-06-14" not in (st["done"] + st["empty"] + st["blocked"])


def test_run_daily_end_is_clamped_to_last_complete_session(monkeypatch):
    seen: dict = {}

    def sess(a, b):
        seen["range"] = (a, b)
        return []

    monkeypatch.setattr(uw, "MANIFEST", _daily_manifest())
    monkeypatch.setattr(uw, "_last_complete_session", lambda e: "2024-06-13")
    monkeypatch.setattr(uw, "sessions_desc", sess)
    uw.run_daily(None, _fresh_state(), ["SPY"], {2}, "2024-06-10", "2024-06-14", dry=True)
    assert seen["range"] == ("2024-06-10", "2024-06-13")


# ------------------------------------------------------------ run_series
def test_run_series_transient_leaves_unit_unrecorded(monkeypatch):
    manifest = [{"name": "greeks", "path": "/api/stock/{ticker}/greeks",
                 "mode": "ticker_series", "priority": 0}]
    monkeypatch.setattr(uw, "MANIFEST", manifest)
    monkeypatch.setattr(uw, "_get", lambda url, params=None: (-1, None))
    state = _fresh_state()
    uw.run_series(None, state, ["SPY"], {0}, dry=False)
    assert state["units"] == {}


def test_run_series_403_still_marks_empty(monkeypatch):
    manifest = [{"name": "greeks", "path": "/api/stock/{ticker}/greeks",
                 "mode": "ticker_series", "priority": 0}]
    monkeypatch.setattr(uw, "MANIFEST", manifest)
    monkeypatch.setattr(uw, "_get", lambda url, params=None: (403, None))
    state = _fresh_state()
    uw.run_series(None, state, ["SPY"], {0}, dry=False)
    assert state["units"] == {"greeks|SPY": "empty"}


# ------------------------------------------------------------ run_expiry
def test_run_expiry_transient_leaves_expiry_unrecorded(monkeypatch):
    eps = [{"name": "greek-exposure-expiry", "path": "/api/stock/{ticker}/greek-exposure",
            "param": "expiry"}]
    monkeypatch.setattr(uw, "EXPIRY_ENDPOINTS", eps)
    monkeypatch.setattr(uw, "_active_expiries", lambda s3, t: ["2024-07-19"])
    monkeypatch.setattr(uw, "_get", lambda url, params=None: (-1, None))
    state = _fresh_state()
    uw.run_expiry(None, state, ["SPY"], dry=False)
    assert state["expiry"]["greek-exposure-expiry|SPY"] == {"done": [], "empty": []}


# ------------------------------------------------------------ run_contracts
def test_run_contracts_transient_symbol_not_marked_done(monkeypatch):
    monkeypatch.setattr(uw, "CONTRACT_SUBS",
                        [("historic", "/api/option-contract/{id}/historic")])
    monkeypatch.setattr(uw, "_contract_symbols", lambda s3, t: ["SPY240614C00500000"])
    monkeypatch.setattr(uw, "_get", lambda url, params=None: (-1, None))
    state = _fresh_state()
    uw.run_contracts(None, state, ["SPY"], dry=False)
    assert state["contracts"]["SPY"] == {"done": [], "empty": []}


# ------------------------------------------- run_contracts_intraday
def test_run_intraday_transient_neither_empty_nor_complete(monkeypatch):
    """A transient failure must not count toward INTRADAY_EMPTY_STOP, not be
    recorded as an empty day, and not mark the contract walk complete."""
    monkeypatch.setattr(uw, "_contract_symbols", lambda s3, t: ["SPY240614C00500000"])
    monkeypatch.setattr(uw, "sessions_desc", lambda a, b: list(DATES[:2]))
    monkeypatch.setattr(uw, "_last_complete_session", lambda e: e)
    monkeypatch.setattr(uw, "_get", lambda url, params=None: (-1, None))
    state = _fresh_state()
    uw.run_contracts_intraday(None, state, ["SPY"], DATES[1], DATES[0], dry=False)
    st = state["intraday"]["SPY|SPY240614C00500000"]
    assert st == {"done": [], "empty": [], "complete": False}


def test_run_intraday_definitive_empty_still_recorded(monkeypatch):
    monkeypatch.setattr(uw, "_contract_symbols", lambda s3, t: ["SPY240614C00500000"])
    monkeypatch.setattr(uw, "sessions_desc", lambda a, b: list(DATES[:2]))
    monkeypatch.setattr(uw, "_last_complete_session", lambda e: e)
    monkeypatch.setattr(uw, "_get", lambda url, params=None: (200, {"data": []}))
    state = _fresh_state()
    uw.run_contracts_intraday(None, state, ["SPY"], DATES[1], DATES[0], dry=False)
    st = state["intraday"]["SPY|SPY240614C00500000"]
    assert st["empty"] == DATES[:2]
    assert st["complete"] is True  # exhausted the session range


# ------------------------------------------------------------ run_tape
def test_run_tape_non200_leaves_date_unrecorded(monkeypatch):
    class _Resp:
        status_code = 500
        headers: dict = {}

    calls: list[str] = []

    def fake_get(url, **kw):
        calls.append(url)
        return _Resp()

    monkeypatch.setattr(uw, "sessions_desc", lambda a, b: list(DATES))
    monkeypatch.setattr(uw, "_last_complete_session", lambda e: e)
    monkeypatch.setattr(uw.requests, "get", fake_get)
    state = _fresh_state()
    uw.run_tape(None, state, ["SPY"], DATES[-1], DATES[0], dry=True)
    assert state["tape"] == {"done": [], "empty": [], "blocked": []}
    assert len(calls) == uw.TRANSIENT_STOP


# ------------------------------------------- _last_complete_session
def _freeze(monkeypatch, *, hour: int, minute: int):
    class _FakeDT(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2024, 6, 14, hour, minute, tzinfo=uw.ET)

    monkeypatch.setattr(uw, "datetime", _FakeDT)


def test_last_complete_session_refuses_today_before_close(monkeypatch):
    _freeze(monkeypatch, hour=12, minute=0)  # midday ET, options still trading
    assert uw._last_complete_session("2024-06-14") == "2024-06-13"
    assert uw._last_complete_session("2024-06-20") == "2024-06-13"  # future end too
    assert uw._last_complete_session("2024-06-01") == "2024-06-01"  # past untouched


def test_last_complete_session_allows_today_after_close(monkeypatch):
    _freeze(monkeypatch, hour=16, minute=15)  # exactly the options close
    assert uw._last_complete_session("2024-06-14") == "2024-06-14"
    assert uw._last_complete_session("2024-06-01") == "2024-06-01"
