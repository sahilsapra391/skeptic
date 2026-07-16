"""The intraday lake-size guard: an optional runaway-write cap, off by default.

Regression context (2026-07-16): the recorder paused a whole live session
because the options_intraday/ lake (6.1 GB) crossed a stale 6.0 GB *default*
cap left over from the R2 free-tier era. The bucket is paid now, so the guard
defaults to OFF (unset / <= 0). When it is off the recorder must not even list
the bucket; when it is set it must still pause a lake grown past the cap and
report the measured size.
"""

from __future__ import annotations

import logging
import sys

import intraday


def _tracking_prefix_gb(value: float, calls: list[str]):
    def prefix_gb(s3, prefix: str) -> float:
        calls.append(prefix)
        return value
    return prefix_gb


def test_nonpositive_cap_disables_guard_without_listing(monkeypatch):
    """<= 0 means uncapped: returns None and never touches R2, so an
    intentionally unbounded lake pays no per-session LIST that grows with the
    object count."""
    calls: list[str] = []
    monkeypatch.setattr(intraday, "prefix_gb", _tracking_prefix_gb(9_999.0, calls))
    for cap in (0.0, -1.0):
        assert intraday.lake_over_cap(object(), cap) is None
    assert calls == []  # the listing was skipped entirely


def test_cap_not_exceeded_returns_none(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(intraday, "prefix_gb", _tracking_prefix_gb(6.1, calls))
    assert intraday.lake_over_cap(object(), 100.0) is None
    assert calls == [intraday.PREFIX]  # it did look once, at the right prefix


def test_cap_exceeded_returns_measured_size(monkeypatch):
    """Over the cap returns the measured GB so the pause log can report it."""
    monkeypatch.setattr(intraday, "prefix_gb", lambda s3, prefix: 6.1)
    assert intraday.lake_over_cap(object(), 6.0) == 6.1


def test_default_cap_blank_or_malformed_is_uncapped(monkeypatch, caplog):
    """A blank or non-numeric INTRADAY_MAX_GB resolves to 0 (uncapped) and must
    never crash startup — a crash-loop that records nothing is the exact trap
    the guard exists to avoid."""
    monkeypatch.delenv("INTRADAY_MAX_GB", raising=False)
    assert intraday.default_lake_cap_gb() == 0.0
    for blank in ("", "   "):
        monkeypatch.setenv("INTRADAY_MAX_GB", blank)
        assert intraday.default_lake_cap_gb() == 0.0
    with caplog.at_level(logging.WARNING, logger="intraday"):
        monkeypatch.setenv("INTRADAY_MAX_GB", "100gb")
        assert intraday.default_lake_cap_gb() == 0.0
    assert any("not a number" in r.getMessage() for r in caplog.records)


def test_default_cap_reads_positive_value(monkeypatch):
    monkeypatch.setenv("INTRADAY_MAX_GB", "100")
    assert intraday.default_lake_cap_gb() == 100.0


def _capture_cap(captured: dict):
    def run_loop(yahoo_every, dry_run, max_lake_gb):
        captured["cap"] = max_lake_gb
        return 0
    return run_loop


def test_main_defaults_to_uncapped_when_env_unset(monkeypatch):
    """The 2026-07-16 trap: with INTRADAY_MAX_GB unset the recorder resolves to
    uncapped (0), not a low hardcoded default."""
    monkeypatch.delenv("INTRADAY_MAX_GB", raising=False)
    monkeypatch.setattr(sys, "argv", ["intraday.py", "--dry-run"])
    captured: dict = {}
    monkeypatch.setattr(intraday, "run_loop", _capture_cap(captured))
    assert intraday.main() == 0
    assert captured["cap"] == 0.0


def test_main_reads_cap_from_env(monkeypatch):
    """A positive INTRADAY_MAX_GB flows through as the active cap."""
    monkeypatch.setenv("INTRADAY_MAX_GB", "100")
    monkeypatch.setattr(sys, "argv", ["intraday.py", "--dry-run"])
    captured: dict = {}
    monkeypatch.setattr(intraday, "run_loop", _capture_cap(captured))
    assert intraday.main() == 0
    assert captured["cap"] == 100.0
