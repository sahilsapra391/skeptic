"""F0 per-session resolution map — hand-computed derivation fixtures.

The derivation (app/data/resolution.py) is the SINGLE implementation the
collector imports; these fixtures pin every clock × quote combination the
lake can produce, including the real edge case observed on 2026-07-06
(UW minute + recorder exist, iVol 5-min not yet banked).
"""

from __future__ import annotations

import pandas as pd
import pytest

from app.data import r2, resolution


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    resolution.clear_cache()


def _derive(**kwargs: object) -> list[dict[str, object]]:
    base: dict[str, object] = {
        "minute_contracts_by_session": {},
        "ivol_5min_sessions": [],
        "recorder_sessions": [],
        "eod_sessions": [],
        "ivs_sessions": [],
        "uw_families_by_session": {},
    }
    base.update(kwargs)
    return resolution.derive_resolution_rows(**base)  # type: ignore[arg-type]


def test_every_clock_quote_combination() -> None:
    rows = _derive(
        minute_contracts_by_session={"2026-07-02": 70, "2026-07-06": 68},
        ivol_5min_sessions=["2019-03-11", "2026-07-02"],
        recorder_sessions=["2026-07-02", "2026-07-06"],
        eod_sessions=["2019-03-11", "2020-05-04", "2026-07-02", "2026-07-06"],
        ivs_sessions=["2019-03-11"],
        uw_families_by_session={"2026-07-02": 24},
    )
    by = {r["session"]: r for r in rows}
    assert len(rows) == 4  # union of all sessions, sorted

    # deep history: 5-min NBBO + EOD → five_min clock, ivol quote
    assert by["2019-03-11"]["clock_resolution"] == resolution.CLOCK_FIVE_MIN
    assert by["2019-03-11"]["quote_resolution"] == resolution.QUOTE_IVOL_5MIN
    assert by["2019-03-11"]["ivs_present"] is True

    # EOD only → daily clock, eod quote
    assert by["2020-05-04"]["clock_resolution"] == resolution.CLOCK_NONE
    assert by["2020-05-04"]["quote_resolution"] == resolution.QUOTE_EOD_ONLY

    # full stack: minute clock; quote precedence is QUALITY (D2 amendment 1)
    # — ivol_5min outranks the finer-but-delayed recorder
    assert by["2026-07-02"]["clock_resolution"] == resolution.CLOCK_MINUTE
    assert by["2026-07-02"]["quote_resolution"] == resolution.QUOTE_IVOL_5MIN
    assert by["2026-07-02"]["minute_contract_count"] == 70
    assert by["2026-07-02"]["uw_families_present"] == 24

    # the real 2026-07-06 edge: minute + recorder, iVol not yet banked
    assert by["2026-07-06"]["clock_resolution"] == resolution.CLOCK_MINUTE
    assert by["2026-07-06"]["quote_resolution"] == resolution.QUOTE_CBOE_2MIN


def test_minute_only_session_has_no_quote() -> None:
    rows = _derive(minute_contracts_by_session={"2026-07-06": 12})
    (row,) = rows
    assert row["clock_resolution"] == resolution.CLOCK_MINUTE
    assert row["quote_resolution"] == resolution.QUOTE_NONE
    assert row["has_eod_chain"] is False


def test_zero_contract_minute_sessions_do_not_count() -> None:
    rows = _derive(
        minute_contracts_by_session={"2026-07-02": 0},
        ivol_5min_sessions=["2026-07-02"],
    )
    (row,) = rows
    assert row["clock_resolution"] == resolution.CLOCK_FIVE_MIN
    assert row["minute_contract_count"] == 0


def test_recorder_only_session_is_five_min_clock() -> None:
    rows = _derive(recorder_sessions=["2026-07-06"])
    (row,) = rows
    assert row["clock_resolution"] == resolution.CLOCK_FIVE_MIN
    assert row["quote_resolution"] == resolution.QUOTE_CBOE_2MIN


def test_timeline_runs_compression() -> None:
    # AABBB → two runs with hand-counted spans
    rows = _derive(
        ivol_5min_sessions=["2026-06-01", "2026-06-02"],
        eod_sessions=["2026-06-01", "2026-06-02", "2026-06-03", "2026-06-04",
                      "2026-06-05"],
    )
    runs = resolution.timeline_runs(rows)
    assert len(runs) == 2
    assert runs[0] == {"first": "2026-06-01", "last": "2026-06-02", "sessions": 2,
                       "clock": "five_min", "quote": "ivol_5min"}
    assert runs[1] == {"first": "2026-06-03", "last": "2026-06-05", "sessions": 3,
                       "clock": "none", "quote": "eod_only"}


# ── read side: artifact → summary ────────────────────────────────────────────
def test_summary_from_banked_artifact(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = _derive(
        minute_contracts_by_session={"2026-07-02": 70},
        ivol_5min_sessions=["2026-06-30", "2026-07-01", "2026-07-02"],
        eod_sessions=["2026-06-30", "2026-07-01", "2026-07-02"],
    )
    frame = pd.DataFrame(rows)
    monkeypatch.setattr(
        r2, "get_parquet",
        lambda s3, key: frame if key == "state/resolution_map/ticker=SPY.parquet" else None,
    )
    out = resolution.summary(None, "SPY")
    assert out is not None
    assert out["sessions"] == 3
    assert out["clock"] == {"five_min": 2, "minute": 1}
    assert out["minute_window"] == {"first": "2026-07-02", "last": "2026-07-02",
                                    "sessions": 1}
    assert out["five_min_window"] == {"first": "2026-06-30", "last": "2026-07-01",
                                      "sessions": 2}
    assert len(out["timeline"]) == 2


def test_summary_absent_artifact_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(r2, "get_parquet", lambda s3, key: None)
    assert resolution.summary(None, "SPY") is None


# ── FX.1: minute-clock eligibility (clock minute AND the 1-min grid) ────────
def test_has_minute_underlying_column() -> None:
    rows = _derive(
        minute_contracts_by_session={"2026-07-01": 70, "2026-07-02": 70},
        ivol_5min_sessions=["2026-07-01", "2026-07-02"],
        minute_underlying_sessions=["2026-07-01"],
    )
    by = {r["session"]: r for r in rows}
    assert by["2026-07-01"]["has_minute_underlying"] is True
    assert by["2026-07-02"]["has_minute_underlying"] is False


def test_minute_clock_sessions_requires_grid(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = _derive(
        minute_contracts_by_session={"2026-07-01": 70, "2026-07-02": 70},
        ivol_5min_sessions=["2026-06-30", "2026-07-01", "2026-07-02"],
        minute_underlying_sessions=["2026-07-01"],
    )
    frame = pd.DataFrame(rows)
    monkeypatch.setattr(r2, "get_parquet", lambda s3, key: frame)
    # minute clock needs BOTH the UW minute data AND the bars_1m grid
    assert resolution.minute_clock_sessions(None, "SPY") == {"2026-07-01"}


def test_minute_clock_sessions_degrades_on_old_map(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # a pre-FX.1 map artifact (no has_minute_underlying column) → empty set,
    # honest degrade; the next nightly rebuild upgrades it automatically
    rows = _derive(minute_contracts_by_session={"2026-07-01": 70})
    frame = pd.DataFrame(rows).drop(columns=["has_minute_underlying"])
    monkeypatch.setattr(r2, "get_parquet", lambda s3, key: frame)
    assert resolution.minute_clock_sessions(None, "SPY") == set()
    monkeypatch.setattr(r2, "get_parquet", lambda s3, key: None)
    resolution.clear_cache()
    assert resolution.minute_clock_sessions(None, "SPY") == set()  # map absent
