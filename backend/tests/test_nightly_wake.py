"""scripts/nightly_improve.py wakes a sleeping backend before it POSTs.

Railway answers a request to a sleeping service with a 502 while the container
boots. The nightly scan's submissions are POSTs that must land exactly once, so
it wakes the backend with a health check first and only then submits. It also
never wakes the backend on a night with nothing to submit, since waking it is
what costs money. No test here touches a network: requests is stubbed.
"""

from __future__ import annotations

import pytest
import requests

from scripts import nightly_improve as ni


class _Resp:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


def _decision(should_rerun: bool) -> ni.UnlockDecision:
    return ni.UnlockDecision(run_id="wake-test", ticker="SPY", clock="daily",
                             new_sessions=50, reason="test", should_rerun=should_rerun)


@pytest.fixture(autouse=True)
def _api(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SKEPTIC_API_URL", "http://api.test")
    monkeypatch.setattr(ni, "WAKE_POLL_SECONDS", 0.0)


def test_waits_through_the_boot_502s(monkeypatch: pytest.MonkeyPatch) -> None:
    answers = iter([502, 502, 200])
    calls: list[str] = []

    def fake_get(url: str, timeout: float | None = None) -> _Resp:
        calls.append(url)
        return _Resp(next(answers))

    monkeypatch.setattr(requests, "get", fake_get)
    assert ni.wake_backend("http://api.test") is True
    assert calls == ["http://api.test/api/health"] * 3


def test_a_refused_connection_is_just_another_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    answers: list[object] = [requests.ConnectionError("still booting"), 200]

    def fake_get(url: str, timeout: float | None = None) -> _Resp:
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        assert isinstance(answer, int)
        return _Resp(answer)

    monkeypatch.setattr(requests, "get", fake_get)
    assert ni.wake_backend("http://api.test") is True


def test_gives_up_loudly_when_the_backend_never_wakes(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(requests, "get", lambda url, timeout=None: _Resp(502))
    monkeypatch.setattr(ni, "WAKE_TIMEOUT_SECONDS", 0.0)
    with caplog.at_level("ERROR", logger="nightly"):
        assert ni.wake_backend("http://api.test") is False
    assert "did not wake" in caplog.text


def test_nothing_is_posted_to_a_backend_that_never_woke(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ni, "wake_backend", lambda base: False)
    monkeypatch.setattr(requests, "post",
                        lambda *a, **k: pytest.fail("posted to a sleeping backend"))
    assert ni.execute_unlocks([_decision(should_rerun=True)]) == 0


def test_a_night_with_nothing_ready_never_wakes_the_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ni, "wake_backend",
                        lambda base: pytest.fail("woke the backend with nothing to submit"))
    assert ni.execute_unlocks([_decision(should_rerun=False)]) == 0


def test_an_empty_receipt_queue_never_wakes_the_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ni, "eligible_for_receipt", lambda: [])
    monkeypatch.setattr(ni, "wake_backend",
                        lambda base: pytest.fail("woke the backend with no receipts to drain"))
    assert ni.drain_receipts(delay=0) == 0


def test_receipts_are_not_submitted_to_a_backend_that_never_woke(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ni, "eligible_for_receipt", lambda: ["run-with-no-receipt"])
    monkeypatch.setattr(ni, "wake_backend", lambda base: False)
    monkeypatch.setattr(requests, "post",
                        lambda *a, **k: pytest.fail("posted to a sleeping backend"))
    assert ni.drain_receipts(delay=0) == 0
