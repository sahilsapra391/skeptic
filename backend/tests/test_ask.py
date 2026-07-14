"""Grounded Q&A unit tests: the numeric validator gates every answer —
an ungrounded number means refusal, never a shipped hallucination."""

import pytest
import requests

from app.honesty import ask as ask_module

STATS = {"metrics": {"sharpe": 1.23, "cagr": 0.186}, "filled": 232}


class _FakeResp:
    status_code = 200

    def __init__(self, content: str) -> None:
        self._content = content

    def json(self) -> dict:
        return {"choices": [{"message": {"content": self._content}}]}


def test_grounded_answer_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(
        requests, "post", lambda *a, **k: _FakeResp("Sharpe was 1.23 across 232 fills.")
    )
    out = ask_module.answer_question("what was the sharpe?", STATS)
    assert out == "Sharpe was 1.23 across 232 fills."


def test_ungrounded_answer_is_refused_after_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    calls = {"n": 0}

    def fake_post(*a, **k):
        calls["n"] += 1
        return _FakeResp("It returned 777% in the best year.")

    monkeypatch.setattr(requests, "post", fake_post)
    out = ask_module.answer_question("best year?", STATS)
    assert out == ask_module.REFUSAL
    assert calls["n"] == 2  # one retry naming the violations, then refusal


def test_no_key_means_no_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert ask_module.answer_question("anything", STATS) is None


def test_budget_exhausted_retry_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """/ask runs behind the same 100s proxy leash as /parse — a grounding
    retry the wall-clock budget can't fund is refused, never launched (or
    the proxy would 504 a healthy engine mid-retry)."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    # deadline calc → attempt-1 remaining → attempt-2 remaining (5s left);
    # the last reading repeats so extra clock reads can't exhaust the fake
    readings = [0.0, 0.5, 85.0]
    monkeypatch.setattr(
        ask_module, "_monotonic",
        lambda: readings.pop(0) if len(readings) > 1 else readings[0],
    )
    calls = {"n": 0}

    def fake_post(*a, **k):
        calls["n"] += 1
        assert isinstance(k["timeout"], tuple)  # per-phase (connect, read)
        return _FakeResp("It returned 777% in the best year.")  # earns retry

    monkeypatch.setattr(requests, "post", fake_post)
    out = ask_module.answer_question("best year?", STATS)
    assert out == ask_module.REFUSAL
    assert calls["n"] == 1  # the doomed retry was never launched
