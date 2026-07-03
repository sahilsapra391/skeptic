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
