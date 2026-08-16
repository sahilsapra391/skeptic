"""V-92: boundary semantics for the V-24 audit script get ONE derivation and a
test, not two call sites and a convention.

This date handling has been written three times and been wrong twice: first
`created_at <= '<date>'` silently dropped the whole boundary day, then the fix
flipped every input to `<` and silently made a full-timestamp bound exclusive
where it had been inclusive. The rule it settles on:

    --until <date>            INCLUSIVE of that entire day
    --until <full timestamp>  INCLUSIVE of that exact instant (unchanged by
                              PR-0 — the semantics it always had)

Both forms are tested against a fixture holding a run at exactly the boundary,
so the fourth rewrite fails loudly instead of depending on care.
"""

from __future__ import annotations

import importlib.util
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
    rows = mod._rows(url, mod.resolve_window(since, until))
    return {r[0] for r in rows}


def test_bare_date_until_covers_the_whole_boundary_day(audit_mod: Any, db: str) -> None:
    """The original bug: `--until 2026-07-17` dropped every run that day."""
    assert _ids(audit_mod, db, None, BOUNDARY_DAY) == {"before", "boundary"}


def test_bare_date_until_excludes_the_next_day(audit_mod: Any, db: str) -> None:
    assert "after" not in _ids(audit_mod, db, None, BOUNDARY_DAY)


def test_full_timestamp_until_includes_that_exact_instant(
    audit_mod: Any, db: str
) -> None:
    """The regression the first fix introduced: flipping every input to `<`
    made a timestamp bound exclusive of the instant it names. A caller who
    passes the exact stamp of a run means to include that run."""
    assert _ids(audit_mod, db, None, BOUNDARY_INSTANT) == {"before", "boundary"}


def test_full_timestamp_until_excludes_anything_after_it(
    audit_mod: Any, db: str
) -> None:
    assert _ids(audit_mod, db, None, "2026-07-17 18:53:22.304999") == {"before"}


def test_since_is_inclusive_of_its_own_instant(audit_mod: Any, db: str) -> None:
    assert _ids(audit_mod, db, BOUNDARY_INSTANT, None) == {"boundary", "after"}


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
    assert "YYYY-MM-DD" in message, "the accepted date form is shown"
    assert "HH:MM:SS" in message, "the accepted timestamp form is shown"


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
    assert bad in (out.err + out.out)


def test_regression_bare_date_no_longer_drops_its_own_day(
    audit_mod: Any, db: str
) -> None:
    """Historical defect 1 (the original V-24 bug): `created_at <= '<date>'`
    compared a timestamp to a date and dropped every run that day."""
    assert "boundary" in _ids(audit_mod, db, None, BOUNDARY_DAY)


def test_regression_timestamp_is_not_made_exclusive(audit_mod: Any, db: str) -> None:
    """Historical defect 2 (introduced by the first fix): flipping every input
    to `<` silently excluded the exact instant a timestamp names."""
    assert "boundary" in _ids(audit_mod, db, None, BOUNDARY_INSTANT)


def test_regression_malformed_input_cannot_widen_the_window(
    audit_mod: Any, db: str
) -> None:
    """Historical defect 3 (introduced by the second fix): anything that failed
    to parse as a date was used verbatim as a timestamp, so `--until 2026-7-16`
    silently matched everything while the banner claimed a bound."""
    with pytest.raises(audit_mod.WindowArgumentError):
        audit_mod.resolve_window(None, "2026-7-16")


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
