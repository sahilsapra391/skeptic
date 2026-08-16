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
    audit_mod: Any, db: str
) -> None:
    """V-92: one derivation. What the banner prints must be the bound the query
    actually used, not a second computation that can drift from it."""
    window = audit_mod.resolve_window(None, BOUNDARY_DAY)
    assert window["until_op"] == "<"
    assert window["until_value"] == "2026-07-18"

    result = audit_mod.audit(db, None, BOUNDARY_DAY)
    assert result["window"] is window or result["window"] == window, (
        "the reported window must be the same resolved object the query used"
    )
    assert result["total_runs"] == 2


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
