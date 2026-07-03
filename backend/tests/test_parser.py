"""Parser unit tests (hermetic — LLM mocked). The live 12-case eval lives
in evals/run_parser_eval.py; these prove the server-side guarantees that
hold no matter what the model says."""

import json

import pytest
import requests

from app.parser import parse as parser_module
from tests.fixtures.synthetic_market import _spec


class _FakeResp:
    status_code = 200

    def __init__(self, payload: dict) -> None:
        self._content = json.dumps(payload)

    def json(self) -> dict:
        return {"choices": [{"message": {"content": self._content}}]}


def _valid_spec() -> dict:
    spec = _spec(0.30, 30, 50.0, 200.0)
    # the model "paraphrases" — the server must overwrite it verbatim
    spec["meta"]["description_raw"] = "a paraphrase the model made up"
    return spec


def test_no_key_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert parser_module.parse_strategy("sell a put") is None


def test_description_raw_is_verbatim(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(
        requests, "post", lambda *a, **k: _FakeResp({"result": "spec", "spec": _valid_spec()})
    )
    out = parser_module.parse_strategy("sell a 30 delta put on SPY, close at 50%")
    assert out is not None and out.status == "spec" and out.spec is not None
    assert out.spec["meta"]["description_raw"] == "sell a 30 delta put on SPY, close at 50%"


def test_questions_pass_through(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    reply = {
        "result": "questions",
        "questions": [
            {"id": "exit", "question": "How should the trade close?", "options": ["50% profit"]}
        ],
    }
    monkeypatch.setattr(requests, "post", lambda *a, **k: _FakeResp(reply))
    out = parser_module.parse_strategy("sell puts when the market dips")
    assert out is not None and out.status == "questions"
    assert out.spec is None
    assert out.questions[0].id == "exit"


def test_invalid_spec_falls_back_to_questions(monkeypatch: pytest.MonkeyPatch) -> None:
    """A spec that fails schema validation twice must become questions —
    never a half-valid spec, never an exception to the caller."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    bad = {"result": "spec", "spec": {"position": {"structure": "wheel"}}}
    monkeypatch.setattr(requests, "post", lambda *a, **k: _FakeResp(bad))
    out = parser_module.parse_strategy("do the wheel on QQQ")
    assert out is not None and out.status == "questions" and out.spec is None


def test_spec_to_draft_projects_dials() -> None:
    spec = _spec(0.30, 45, 50.0, 200.0)
    spec["meta"]["description_raw"] = "text"
    draft = parser_module.spec_to_draft(spec, "text")
    assert draft["ticker"] == "SPY"
    assert draft["structure"] == "short_put"
    assert draft["strikeDelta"] == 30
    assert draft["dte"] == 45
    assert draft["cadence"] == "weekly · mon"
    assert draft["exit"] == "50% profit · stop 200%"
