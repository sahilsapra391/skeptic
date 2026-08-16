"""V-92 / V-97 / V-98: boundary semantics for the V-24 audit script.

This date handling has been wrong three separate times, so those three defects
ARE the specification and each has a named regression test below:

  1. `created_at <= '<date>'` compared a timestamp to a date and dropped every
     run on the boundary day.
  2. The fix for (1) flipped EVERY input to `<`, silently making a named
     instant exclusive.
  3. The fix for (2) inferred "timestamp" from a failed date parse, so
     `--until 2026-7-16` was used verbatim in a string comparison and silently
     matched everything while the banner claimed a bound.

All three shared one root cause: the window was derived by guessing from what
a parser happened to reject. The rule it now settles on, with rejection before
any parsing:

    YYYY-MM-DD                     that whole day, inclusive
    YYYY-MM-DD HH:MM:SS[.ffffff]   that exact instant, inclusive
    anything else                  refused, non-zero exit, no banner

Both accepted forms are tested against a fixture holding a run at exactly the
boundary, so a fourth rewrite fails loudly instead of depending on care.
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "audit_strike_width_rewrites.py"

# the boundary day and the exact instant a run sits on
BOUNDARY_DAY = "2026-07-17"
BOUNDARY_INSTANT = "2026-07-17 18:53:22.305000"


def _load_script() -> Any:
    """Import the audit script by path — `scripts/` is not a package."""
    if not SCRIPT.is_file():
        pytest.fail(f"audit script not found at {SCRIPT}")
    spec = importlib.util.spec_from_file_location("audit_strike_width", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["audit_strike_width"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def audit_mod() -> Any:
    return _load_script()


@pytest.fixture()
def db(tmp_path: Path) -> str:
    """A runs table with one run BEFORE the boundary day, one ON it at a known
    instant, and one the day AFTER."""
    path = tmp_path / "runs.db"
    con = sqlite3.connect(path)
    con.execute(
        "create table runs (id text primary key, created_at text, "
        "spec_json text, provenance_json text)"
    )
    con.executemany(
        "insert into runs values (?, ?, ?, ?)",
        [
            ("before", "2026-07-16 09:00:00.000000", "{}", None),
            ("boundary", BOUNDARY_INSTANT, "{}", None),
            ("after", "2026-07-18 00:00:00.000000", "{}", None),
        ],
    )
    con.commit()
    con.close()
    return f"sqlite:///{path}"


def _ids(mod: Any, url: str, since: str | None, until: str | None) -> set[str]:
    rows, _newest = mod._read(url, mod.resolve_window(since, until))
    return {r[0] for r in rows}


def test_bare_date_until_covers_the_whole_boundary_day(audit_mod: Any, db: str) -> None:
    """The original bug: `--until 2026-07-17` dropped every run that day."""
    assert _ids(audit_mod, db, None, BOUNDARY_DAY) == {"before", "boundary"}


def test_bare_date_until_excludes_the_next_day(audit_mod: Any, db: str) -> None:
    assert "after" not in _ids(audit_mod, db, None, BOUNDARY_DAY)


def test_since_is_inclusive_of_its_whole_day(audit_mod: Any, db: str) -> None:
    assert _ids(audit_mod, db, BOUNDARY_DAY, None) == {"boundary", "after"}


def test_equal_bounds_select_that_day(audit_mod: Any, db: str) -> None:
    """V-107: the tightest boundary there is, and the one that should have been
    in V-98. `--since X --until X` means that whole day, and it is a legal
    window — refusing it as "empty or inverted" was historical defect 4."""
    assert _ids(audit_mod, db, BOUNDARY_DAY, BOUNDARY_DAY) == {"boundary"}


@pytest.mark.parametrize("flag", ["since", "until"])
def test_iso_timestamp_is_rejected_and_says_why(audit_mod: Any, flag: str) -> None:
    """V-104: a valid ISO timestamp is refused now, and the message says
    dates-only and offers the date to use, so someone pasting a stamp the
    script itself printed learns the rule rather than guessing at it."""
    args = {flag: BOUNDARY_INSTANT}
    with pytest.raises(audit_mod.WindowArgumentError) as exc:
        audit_mod.resolve_window(args.get("since"), args.get("until"))
    message = str(exc.value)
    assert "dates only" in message.lower(), "the message must name the rule"
    assert BOUNDARY_DAY in message, "and offer the date to pass instead"


def test_unbounded_returns_everything(audit_mod: Any, db: str) -> None:
    assert _ids(audit_mod, db, None, None) == {"before", "boundary", "after"}


def test_window_is_resolved_once_and_reported_as_queried(
    audit_mod: Any, db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """V-92 / V-99: ONE derivation, asserted by identity rather than equality.

    The previous version of this test used `is ... or ==`, which short-circuits
    to the equality arm and therefore could not fail on the thing it names. A
    second derivation producing an equal-but-distinct dict would have passed,
    which is the drift V-92 exists to pin. Capture the object the query was
    actually handed and assert the reported one IS it.
    """
    derived: list[dict[str, Any]] = []
    real = audit_mod.resolve_window

    def spy(since: str | None, until: str | None) -> dict[str, Any]:
        window = real(since, until)
        derived.append(window)
        return window

    monkeypatch.setattr(audit_mod, "resolve_window", spy)
    result = audit_mod.audit(db, None, BOUNDARY_DAY)

    assert len(derived) == 1, f"the window was derived {len(derived)} times, not once"
    assert result["window"] is derived[0], (
        "the reported window must be the SAME object the query used, not an "
        "equal copy from a second derivation"
    )
    assert result["total_runs"] == 2


# --- V-97 / V-98: rejection before parsing -----------------------------------
#
# This function has now been wrong in three distinct ways. Those three defects
# ARE the specification, so each is a named regression case below.


@pytest.mark.parametrize(
    "bad",
    [
        pytest.param("2026-7-16", id="not_zero_padded"),
        pytest.param("2026-13-01", id="impossible_month"),
        pytest.param("2026-07-32", id="impossible_day"),
        pytest.param("garbage", id="unparseable"),
        pytest.param("", id="empty_string"),
        pytest.param("2026-07", id="year_month_only"),
        pytest.param("07-16-2026", id="us_ordering"),
    ],
)
def test_malformed_until_is_rejected_not_inferred(audit_mod: Any, bad: str) -> None:
    """V-97: no fall-through and no inference from a failed parse. Every input
    that is not one of the two accepted forms is refused outright."""
    with pytest.raises(audit_mod.WindowArgumentError) as exc:
        audit_mod.resolve_window(None, bad)
    message = str(exc.value)
    assert repr(bad) in message or bad in message, "the offending value is named"
    assert "YYYY-MM-DD" in message, "the one accepted form is shown"


@pytest.mark.parametrize("bad", ["2026-7-16", "garbage", ""])
def test_malformed_since_is_rejected_too(audit_mod: Any, bad: str) -> None:
    """--since gets the same treatment; it reaches the same comparison."""
    with pytest.raises(audit_mod.WindowArgumentError):
        audit_mod.resolve_window(bad, None)


def test_since_after_until_is_rejected(audit_mod: Any) -> None:
    """An inverted window silently returns nothing, which reads as 'no runs
    detected' — the most dangerous possible false negative for this audit."""
    with pytest.raises(audit_mod.WindowArgumentError) as exc:
        audit_mod.resolve_window("2026-07-18", "2026-07-16")
    assert "2026-07-18" in str(exc.value)


@pytest.mark.parametrize("bad", ["2026-7-16", "2026-13-01", "garbage"])
def test_cli_exits_non_zero_and_prints_no_banner(
    audit_mod: Any, bad: str, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """V-97: rejected input exits non-zero and the banner never prints. A
    confident 'WINDOW APPLIED' block above a wrong window is the whole problem."""
    monkeypatch.setattr(
        sys, "argv", ["audit_strike_width_rewrites.py", "--until", bad]
    )
    code = audit_mod.main()
    out = capsys.readouterr()
    assert code != 0, "a rejected window must exit non-zero"
    assert "WINDOW APPLIED" not in out.out, "the banner printed on a rejected input"
    # V-112: no preamble either. READ ONLY and the source line assert facts
    # about a run that is not happening, so stdout must be empty entirely.
    assert out.out == "", f"a rejected invocation printed preamble: {out.out!r}"
    assert bad in out.err


def test_regression_bare_date_no_longer_drops_its_own_day(
    audit_mod: Any, db: str
) -> None:
    """Historical defect 1 (the original V-24 bug): `created_at <= '<date>'`
    compared a timestamp to a date and dropped every run that day."""
    assert "boundary" in _ids(audit_mod, db, None, BOUNDARY_DAY)


def test_regression_the_bound_is_not_exclusive_of_the_days_contents(
    audit_mod: Any, db: str
) -> None:
    """Historical defect 2 (introduced by the first fix): flipping every input
    to `<` silently excluded the exact instant a bound named.

    Re-expressed for the dates-only form (V-104): `--until <day>` must include
    every instant during that day, and the fixture's boundary run sits late in
    it at 18:53. An off-by-one that made the bound exclusive of the day's
    contents rather than of the following midnight would drop it.
    """
    assert "boundary" in _ids(audit_mod, db, None, BOUNDARY_DAY)
    assert audit_mod.resolve_window(None, BOUNDARY_DAY)["until_value"] > BOUNDARY_INSTANT


def test_regression_malformed_input_cannot_widen_the_window(
    audit_mod: Any, db: str
) -> None:
    """Historical defect 3 (introduced by the second fix): anything that failed
    to parse as a date was used verbatim as a timestamp, so `--until 2026-7-16`
    silently matched everything while the banner claimed a bound."""
    with pytest.raises(audit_mod.WindowArgumentError):
        audit_mod.resolve_window(None, "2026-7-16")


def test_regression_equal_bounds_are_not_called_inverted(
    audit_mod: Any, db: str
) -> None:
    """Historical defect 4 (introduced by the third fix): the inverted-window
    check compared against an upper bound it treated as exclusive, which was
    only true of one of the two accepted forms, so an equal-bounds window was
    accepted as dates and refused as timestamps. With a single form the
    mismatch is unwritable; this pins that it stays so."""
    window = audit_mod.resolve_window(BOUNDARY_DAY, BOUNDARY_DAY)
    assert window["since_value"] < window["until_value"], (
        "equal bounds must resolve to a non-empty half-open window"
    )


def test_printed_until_value_round_trips_as_input(audit_mod: Any, db: str) -> None:
    """V-105: anything the script prints as copyable must be accepted by the
    script. The newest-run line prints a timestamp, so it also prints the date
    to pass to --until, and that date has to be valid input."""
    result = audit_mod.audit(db, None, None)
    suggested = audit_mod.until_for(result["newest_in_database"])
    assert audit_mod.resolve_window(None, suggested)["until"] == suggested
    # and it actually covers the newest run
    assert "after" in _ids(audit_mod, db, None, suggested)


def test_newest_is_reported_for_the_database_not_just_the_window(
    audit_mod: Any, db: str
) -> None:
    """V-91: deriving a cutoff from a bounded scan is what makes pre-fix runs
    read as new instances. The database-wide newest is window-independent."""
    result = audit_mod.audit(db, None, BOUNDARY_DAY)
    assert result["newest_in_window"] == BOUNDARY_INSTANT
    assert result["newest_in_database"] == "2026-07-18 00:00:00.000000"


def test_follow_up_invocation_is_byte_for_byte_runnable(
    audit_mod: Any, db: str
) -> None:
    """V-90: the printed command must carry every flag, resolved. An omitted
    --since is how the V-71 sanity check compares two different windows and
    reports a false alarm."""
    result = audit_mod.audit(db, "2026-07-16", BOUNDARY_DAY)
    sanity = audit_mod.follow_up_invocations(result)["sanity"]
    assert "--since 2026-07-16" in sanity
    assert f"--until {BOUNDARY_DAY}" in sanity
    # and it reproduces the same window when fed back in
    assert audit_mod.audit(db, "2026-07-16", BOUNDARY_DAY)["total_runs"] == 2


# --- V-116: the at-risk population, which is what makes the count readable ---


def _db_with(tmp_path: Path, rows: list[tuple[str, str, str, str | None]]) -> str:
    path = tmp_path / "at_risk.db"
    con = sqlite3.connect(path)
    con.execute(
        "create table runs (id text primary key, created_at text, "
        "spec_json text, provenance_json text)"
    )
    con.executemany("insert into runs values (?, ?, ?, ?)", rows)
    con.commit()
    con.close()
    return f"sqlite:///{path}"


def _spec(method: str) -> str:
    return json.dumps(
        {"position": {"legs": [{"strike_selection": {"method": method}}]}}
    )


def _prov(strike_label: str | None) -> str:
    return json.dumps({"confirmed": {"draft": {"strikeLabel": strike_label}}})


def test_at_risk_counts_only_drafts_with_a_non_delta_strike_label(
    audit_mod: Any, tmp_path: Path
) -> None:
    """Eligible means "has a stored draft". At risk means "that draft records a
    strike rule the dials could not express". Conflating them is what made
    "0 of 26 eligible" unreadable."""
    url = _db_with(
        tmp_path,
        [
            ("plain", "2026-07-16 09:00:00", _spec("delta"), _prov(None)),
            ("exposed", "2026-07-16 10:00:00", _spec("delta"), _prov("2% below spot")),
            ("nodraft", "2026-07-16 11:00:00", _spec("delta"), None),
        ],
    )
    result = audit_mod.audit(url, None, None)
    assert result["eligible_runs"] == 2, "both runs with a stored draft are eligible"
    assert result["at_risk_runs"] == 1, "only the one carrying a strikeLabel is at risk"
    # and that one IS the detected rewrite, since its final method is delta
    assert [d["run_id"] for d in result["detected"]] == ["exposed"]


def test_empty_at_risk_population_says_nothing_was_exposed(
    audit_mod: Any, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """V-116's whole point: 0 detected among 0 at risk must not read as "the bug
    did not fire". The output has to say which claim it is making."""
    url = _db_with(
        tmp_path, [("plain", "2026-07-16 09:00:00", _spec("delta"), _prov(None))]
    )
    audit_mod._print_text(audit_mod.audit(url, None, None))
    out = capsys.readouterr().out
    assert "at-risk population is EMPTY" in out
    assert "'nothing was exposed'" in out
    assert "VACUOUS RESULT" not in out, "eligible is non-zero, so that block is wrong here"


def test_at_risk_line_sits_under_the_set_it_counts(
    audit_mod: Any, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """at_risk is a subset of ELIGIBLE, so its line follows the eligible line.
    Printed after "not inspectable", "of those" pointed at the wrong number."""
    url = _db_with(
        tmp_path, [("plain", "2026-07-16 09:00:00", _spec("delta"), _prov(None))]
    )
    audit_mod._print_text(audit_mod.audit(url, None, None))
    lines = [ln for ln in capsys.readouterr().out.splitlines() if ln.strip()]
    eligible_at = next(i for i, ln in enumerate(lines) if "the eligible set" in ln)
    assert "AT RISK" in lines[eligible_at + 1], "the at-risk line must follow eligible"
