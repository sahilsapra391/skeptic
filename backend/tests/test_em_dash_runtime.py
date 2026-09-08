"""The runtime half of the em-dash ban.

Sweeping the source tree handles text people typed. It cannot reach the
verdict narration LLM, the grounded-answer LLM, the parser's clarifying
questions, or the runs already sitting in the database with em-dashes inside
their stored verdict text. Those are covered by four layers, and this file is
what proves all of them still work:

  WRITE time, in app/honesty/verdict.py, app/honesty/ask.py and
  app/parser/parse.py, applied on receipt and ahead of validation. The
  parser also cleans the MODEL's own run name here, which is what keeps a
  stored spec clean without ever rewriting what a person typed.

  READ time, in app/api/payload.py, applied to outbound prose only. The
  stored row keeps its bytes; the wire gets house punctuation.

  EXPORT time, in app/notebook/, where the server renders the HTML report
  and the .ipynb. Those are terminal artifacts (a browser navigates
  straight to the backend HTML, and nothing in either file is ever
  submitted back), so they normalize EVERYTHING they render, the user's own
  prompt included. That is the owner's rule read literally: no em-dash on
  any page, including a visitor's own words quoted back at them.

Parity with the TypeScript normalizer the browser runs is the fifth layer
and lives in its own file, `test_normalizer_parity.py`, which executes the
real `frontend/lib/punctuation.ts` through node. Nothing here duplicates
that harness: one guard for one question, so the two cannot drift into
disagreeing about what agreement means.

The character never appears literally below, in this file or in any file it
tests, and neither does any HTML spelling of it: every one is built from
chr(0x2014) so the guard that fails the build on an em-dash has no exception
to make for its own test suite.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
import requests
from fastapi.testclient import TestClient

from app import db
from app.api.payload import normalize_payload_prose
from app.honesty import ask as ask_module
from app.honesty.verdict import _NUM_RE
from app.main import app
from app.notebook.builder import build_notebook
from app.notebook.report import build_report
from app.text import EM_DASH, normalize, normalize_mapping, normalize_tree

DASH = chr(0x2014)

# The HTML spellings, assembled rather than written, for the same reason the
# character is. _AMP is an ampersand, _HASH a number sign.
_AMP = chr(0x26)
_HASH = chr(0x23)
_DEC = str(ord(DASH))
_HEX = format(ord(DASH), "04x")
NAMED_ENTITY = _AMP + "mdash;"
DECIMAL_ENTITY = _AMP + _HASH + _DEC + ";"
HEX_ENTITY = _AMP + _HASH + "x" + _HEX + ";"
# how a dash looks in JSON serialized with ensure_ascii, which is how the
# notebook export would carry one without anyone seeing a dash-shaped thing
JSON_ESCAPE = chr(0x5C) + "u" + _HEX

# every way an em-dash can reach a reader. Nothing rendered may hold one of
# these, and nothing the normalizer emits may either.
RENDERED_SPELLINGS = (DASH, NAMED_ENTITY, DECIMAL_ENTITY, HEX_ENTITY, JSON_ESCAPE)

# every dash-shaped character the normalizer is forbidden to emit, plus the
# textual escapes that would smuggle one through an HTML surface
FORBIDDEN = (
    chr(0x2014),  # em-dash
    chr(0x2013),  # en-dash
    chr(0x2015),  # horizontal bar
    chr(0x2212),  # minus sign
    chr(0x2010),  # hyphen
    chr(0x2011),  # non-breaking hyphen
    chr(0x2012),  # figure dash
    "--",
    NAMED_ENTITY,
    DECIMAL_ENTITY,
    HEX_ENTITY,
)

# Every spelling of the entity that renders as a dash to a reader: the three
# forms, either case, with the leading zeros and the missing semicolon a
# browser forgives. All of them fold into the character before anything else
# happens, so there is exactly one code path deciding what replaces a dash.
ENTITY_SPELLINGS = [
    NAMED_ENTITY,
    NAMED_ENTITY.upper(),
    _AMP + "mdash",
    DECIMAL_ENTITY,
    _AMP + _HASH + "0" + _DEC + ";",
    _AMP + _HASH + _DEC,
    HEX_ENTITY,
    HEX_ENTITY.upper(),
    _AMP + _HASH + "x0" + _HEX + ";",
    _AMP + _HASH + "X" + _HEX.upper() + ";",
]

SAMPLES = [
    f"Sharpe 1.20 in-sample {DASH} 0.30 out-of-sample",
    f"Sharpe 1.20 in-sample{DASH}0.30 out-of-sample",
    f"{DASH} the edge fades",
    f"the edge fades {DASH}",
    f"withheld{DASH}, then graded",
    f"withheld,{DASH} then graded",
    f"38 of 52 windows profitable{DASH}the rest flat",
    f"one line\n{DASH} a bullet\nanother line",
    f"a trailing dash {DASH}\nand a new line",
    f"a double dash {DASH}{DASH} here",
    DASH,
    "nothing to do here",
    "",
]

# the other two spellings, in the same positions
ENTITY_SAMPLES = [
    f"Sharpe 1.20 in-sample {entity} 0.30 out-of-sample" for entity in ENTITY_SPELLINGS
] + [
    f"{NAMED_ENTITY} the edge fades",
    f"the edge fades {NAMED_ENTITY}",
    f"withheld,{DECIMAL_ENTITY} then graded",
    f"38 of 52 windows profitable{HEX_ENTITY}the rest flat",
]

DOUBLE_HYPHEN_SAMPLES = [
    "the edge fades -- badly",
    "collapsed   --   here",
    "withheld, -- then graded",
    "one line\n -- a bullet\nanother line",
]

# every input the substitution must fully clean. The command lines below are
# deliberately NOT in here: a surviving `--project` is the correct answer.
CLEANED_SAMPLES = SAMPLES + ENTITY_SAMPLES + DOUBLE_HYPHEN_SAMPLES

# the repo's own documented commands, which run through this function the
# moment one appears in a prompt, a clarifying question or a stored note. A
# normalizer that edits a command line is a bug wearing a fix's clothes.
COMMAND_LINES = [
    "uv run --project backend python -m pytest backend/tests",
    "uv sync --project backend",
    "npm --prefix frontend run lint",
    "node --no-warnings --experimental-strip-types harness.mjs",
    "the dev server needs --reload; production does not",
    "cd collector && uv run python collect.py --mode eod",
    "a markdown rule --- stays put",
    "trailing --",
    "-- leading",
]


def test_the_constant_is_the_character_it_claims_to_be() -> None:
    """The module writes the dash as an escape. If that escape ever stops
    naming U+2014, every layer below silently becomes a no-op."""
    assert EM_DASH == DASH
    assert len(EM_DASH) == 1


@pytest.mark.parametrize("sample", CLEANED_SAMPLES + COMMAND_LINES)
def test_normalize_is_idempotent(sample: str) -> None:
    once = normalize(sample)
    assert normalize(once) == once


@pytest.mark.parametrize("sample", CLEANED_SAMPLES)
def test_normalize_emits_no_dash_character_of_any_kind(sample: str) -> None:
    out = normalize(sample)
    for bad in FORBIDDEN:
        assert bad not in out, f"{bad!r} survived in {out!r}"


def test_a_spaced_dash_becomes_one_comma_and_one_space() -> None:
    assert normalize(f"the edge fades {DASH} badly") == "the edge fades, badly"
    assert normalize(f"the edge fades{DASH}badly") == "the edge fades, badly"
    # the whitespace run collapses: ", " is produced, never " , "
    assert " , " not in normalize(f"the edge fades   {DASH}   badly")


def test_line_edges_and_neighbouring_punctuation_stay_readable() -> None:
    assert normalize(f"{DASH} leading") == "leading"
    assert normalize(f"trailing {DASH}") == "trailing"
    assert normalize(f"withheld,{DASH} then graded") == "withheld, then graded"
    # The mirror image IS handled now. This assertion used to pin
    # "withheld, , then graded" as the known cost of keying the rule only on
    # the character BEFORE the dash, with a note that a mirror would have to
    # land on both sides of the wire in one change. It did: the rule now also
    # reads the character AFTER the dash, in app/text.py and in
    # frontend/lib/punctuation.ts together, and the parity table pins the
    # pair. A doubled comma is not something a reader should ever be shown.
    assert normalize(f"withheld{DASH}, then graded") == "withheld, then graded"
    assert normalize(f"the edge fades{DASH}. Next") == "the edge fades. Next"
    # a newline is a boundary, not padding: two lines never get joined
    assert normalize(f"first\n{DASH} second") == "first\nsecond"


# ------------------------------------------------- the canonical table, cell
# by cell. The table is the contract both normalizers implement, so every
# row of it gets an assertion rather than an inference from a sample corpus.


@pytest.mark.parametrize("entity", ENTITY_SPELLINGS)
def test_every_html_spelling_folds_into_the_character_first(entity: str) -> None:
    """A page renders these as a dash. A normalizer that only looked for the
    glyph would wave all three straight through, which is exactly how one
    reaches a reader through a surface nobody thought to check."""
    assert normalize(f"the edge fades {entity} badly") == "the edge fades, badly"
    assert normalize(f"{entity} leading") == "leading"
    assert normalize(f"trailing {entity}") == "trailing"
    assert normalize(f"withheld,{entity} then graded") == "withheld, then graded"


def test_a_spaced_double_hyphen_is_the_same_defect() -> None:
    assert normalize("the edge fades -- badly") == "the edge fades, badly"
    assert normalize("collapsed   --   here") == "collapsed, here"
    assert normalize("withheld, -- then graded") == "withheld, then graded"


@pytest.mark.parametrize("command", COMMAND_LINES)
def test_a_command_line_survives_untouched(command: str) -> None:
    """`--project` and `--reload` are flags, and this repo's own prose is
    full of them. Whitespace on BOTH sides is what separates the prose case
    from the command case, and nothing else may be taken as a defect."""
    assert normalize(command) == command


def test_a_flag_survives_a_sentence_that_carries_a_dash_as_well() -> None:
    """The two rules meet in one string: the dash goes, the flag stays."""
    assert (
        normalize(f"run it with --project backend {DASH} never bare")
        == "run it with --project backend, never bare"
    )
    assert (
        normalize("run it with --project backend -- never bare")
        == "run it with --project backend, never bare"
    )


@pytest.mark.parametrize("closer", list(",;:.!?"))
def test_a_closed_clause_takes_a_space_and_never_a_second_comma(closer: str) -> None:
    assert normalize(f"clause{closer} {DASH} next") == f"clause{closer} next"
    assert normalize(f"clause{closer}{DASH}next") == f"clause{closer} next"


def test_the_edges_drop_the_dash_and_the_whitespace_with_it() -> None:
    assert normalize(f"   {DASH}   start") == "start"
    assert normalize(f"end   {DASH}   ") == "end"


def test_no_output_ever_holds_another_dash_spelling() -> None:
    """Rule (e), stated once over everything: whatever went in, what comes
    out is house punctuation, never a different glyph for the same defect."""
    for sample in CLEANED_SAMPLES:
        out = normalize(sample)
        for spelling in RENDERED_SPELLINGS:
            assert spelling not in out, f"{spelling!r} survived in {out!r}"


@pytest.mark.parametrize("sample", SAMPLES)
def test_numeric_tokens_are_never_altered(sample: str) -> None:
    """Guardrail #4: every number in verdict text must exist in the stats
    payload. Normalization runs BEFORE that validator, so it may not add,
    drop, merge or split a numeric token. Read through the validator's own
    regex, over its own comma-stripped view of the text."""
    def tokens(text: str) -> list[str]:
        return _NUM_RE.findall(text.replace(",", ""))

    assert tokens(normalize(sample)) == tokens(sample)


def test_a_dash_between_two_numbers_does_not_merge_them() -> None:
    """The comma is the substitution, and a comma with no space after it
    would splice '1' and '2' into '12' the moment the validator strips
    commas. This is the case that makes the trailing space load-bearing."""
    out = normalize(f"folds 1{DASH}2 lost money")
    assert _NUM_RE.findall(out.replace(",", "")) == ["1", "2"]


def test_folding_an_entity_takes_its_digits_with_it() -> None:
    """The one case where a digit moves, and the direction is safe.

    The digits inside a decimal or hex entity spell the DASH, not a number.
    Folding removes them, so the numeric validator sees one fewer token than
    the raw LLM string carried. That can only make a verdict more grounded,
    never less: the validator's job is to reject a number with no home in
    the stats, and this takes one away rather than inventing one. The
    stats numbers around it are untouched, which is the part that matters.
    """
    out = normalize(f"Sharpe 1.20 in-sample {DECIMAL_ENTITY} 0.30 out")
    assert out == "Sharpe 1.20 in-sample, 0.30 out"
    tokens = _NUM_RE.findall(out.replace(",", ""))
    assert _DEC not in tokens
    assert tokens == _NUM_RE.findall("Sharpe 1.20 in-sample 0.30 out")


# ------------------------------------------------------- WRITE time: the LLMs
class _FakeResp:
    status_code = 200

    def __init__(self, content: str) -> None:
        self._content = content

    def json(self) -> dict[str, Any]:
        return {"choices": [{"message": {"content": self._content}}]}


class _StubReport:
    """_llm_narrate reads exactly one thing off the report: the JSON it sends
    as the user message. Everything else under test is the response path."""

    def model_dump_json(self) -> str:
        return json.dumps({"oos": {"is_sharpe": 1.2, "oos_sharpe": 0.3}, "trades": 22})


def test_verdict_narration_is_cleaned_on_receipt(monkeypatch: pytest.MonkeyPatch) -> None:
    """A narration full of em-dashes comes back clean AND grounded. The
    ordering is the point: normalization runs first, so the numeric validator
    and the English guard judge the exact text that ships."""
    from app.honesty import verdict as verdict_module

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    body = json.dumps(
        {
            "headline": f"Edge fades out-of-sample {DASH} what is left is thin.",
            "evidence": [f"Sharpe 1.20 in-sample{DASH}0.30 out"],
            "breaks_where": [f"{DASH} the walk-forward is inconsistent"],
            "caveats": [f"22 closed trades {DASH} one volatility regime"],
        }
    )
    calls = {"n": 0}

    def fake_post(*a: Any, **k: Any) -> _FakeResp:
        calls["n"] += 1
        return _FakeResp(body)

    monkeypatch.setattr(requests, "post", fake_post)

    out = verdict_module._llm_narrate(_StubReport(), {0.0, 1.2, 0.3, 22.0})  # type: ignore[arg-type]
    assert out is not None
    assert calls["n"] == 1  # accepted first time: grounding survived the rewrite
    assert out.headline == "Edge fades out-of-sample, what is left is thin."
    assert out.evidence == ["Sharpe 1.20 in-sample, 0.30 out"]
    assert out.breaks_where == ["the walk-forward is inconsistent"]
    assert out.caveats == ["22 closed trades, one volatility regime"]
    for field in (out.headline, *out.evidence, *out.breaks_where, *out.caveats):
        assert DASH not in field


def test_every_prompt_tells_its_model_not_to_emit_one() -> None:
    """Layer 3: advice, not enforcement. Worth asserting anyway, because a
    prompt that quietly loses the instruction pushes every rewrite onto the
    layers below and nothing else would notice."""
    from app.parser.parse import _SYSTEM as PARSER_SYSTEM

    prompts = [ask_module._SYSTEM, PARSER_SYSTEM, _verdict_system_prompt()]
    for prompt in prompts:
        assert "em-dash" in prompt
        assert "comma" in prompt
        assert DASH not in prompt  # the instruction never ships the glyph


def _verdict_system_prompt() -> str:
    """The verdict system prompt is built inside _llm_narrate, so read it off
    a captured request rather than duplicating the string here."""
    from app.honesty import verdict as verdict_module

    captured: dict[str, Any] = {}

    class _Recorder:
        status_code = 200

        def json(self) -> dict[str, Any]:
            return {"choices": [{"message": {"content": "{}"}}]}

    def fake_post(*a: Any, **k: Any) -> _Recorder:
        captured.update(k["json"])
        return _Recorder()

    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("OPENROUTER_API_KEY", "test-key")
        mp.setattr(requests, "post", fake_post)
        verdict_module._llm_narrate(_StubReport(), {0.0})  # type: ignore[arg-type]
    return str(captured["messages"][0]["content"])


def test_grounded_answer_is_cleaned_on_receipt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    stats = {"metrics": {"sharpe": 1.23}, "filled": 232}
    monkeypatch.setattr(
        requests,
        "post",
        lambda *a, **k: _FakeResp(f"Sharpe was 1.23 {DASH} across 232 fills."),
    )
    out = ask_module.answer_question("what was the sharpe?", stats)
    assert out == "Sharpe was 1.23, across 232 fills."
    assert DASH not in out


def test_clarifying_questions_are_cleaned_on_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The parser's questions are copied verbatim into the run's provenance
    record, so cleaning them on receipt is what keeps the stored story and
    the screen identical."""
    from app.parser import parse as parse_module

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    body = json.dumps(
        {
            "result": "questions",
            "questions": [
                {
                    "id": "tenor",
                    "question": f"How long to expiration {DASH} 0 DTE or 45 DTE?",
                    "options": [f"0 DTE{DASH}same day", "45 DTE"],
                }
            ],
        }
    )
    monkeypatch.setattr(requests, "post", lambda *a, **k: _FakeResp(body))

    outcome = parse_module.parse_strategy("sell puts on SPY")
    assert outcome is not None and outcome.status == "questions"
    question = outcome.questions[0]
    assert question.question == "How long to expiration, 0 DTE or 45 DTE?"
    assert question.options == ["0 DTE, same day", "45 DTE"]
    assert DASH not in question.question
    assert all(DASH not in option for option in question.options)


def test_the_run_name_is_cleaned_on_receipt_and_the_prompt_is_not(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`meta.name` is the parser MODEL's prose, and it is cleaned here.

    It has to be, and here specifically. Everything downstream reads it: the
    Library card, the run screen heading, the report and notebook titles,
    the lineage header, the argue-back sentence. The read-time layer cannot
    do the job, because it exempts the whole spec block on purpose, and
    rightly so: the spec round-trips back to the server as authoritative on
    the variant path, and a normalized value re-submitted would corrupt the
    stored record.

    `description_raw` in the same object is the counter-case in the same
    assertion. It is overwritten server-side with the user's verbatim text
    and keeps every character the person typed, dash included.
    """
    from app.parser import parse as parse_module
    from tests.fixtures.synthetic_market import _spec as _fixture_spec

    raw = _fixture_spec(0.30, 30, 50.0, 200.0)
    raw["meta"]["name"] = f"SPY .30 delta short put {DASH} weekly"
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(
        requests,
        "post",
        lambda *a, **k: _FakeResp(json.dumps({"result": "spec", "spec": raw})),
    )

    typed = f"sell a 30 delta put on SPY {DASH} weekly"
    outcome = parse_module.parse_strategy(typed)

    assert outcome is not None and outcome.status == "spec" and outcome.spec is not None
    assert outcome.spec["meta"]["name"] == "SPY .30 delta short put, weekly"
    assert outcome.spec["meta"]["description_raw"] == typed


# -------------------------------------------------- READ time: the legacy row
LEGACY_PAYLOAD: dict[str, Any] = {
    "id": "emdash-legacy",
    "demo": False,
    "status": "done",
    "stage": 6,
    "name": f"SPY .30 delta short put {DASH} v2",
    "meta": f"SPY - short put - seed 7 {DASH} verdict: llm",
    "spec": None,
    "verdict": {
        "kind": "graded",
        "refusal": False,
        "headline": f"Survives 3 of 5 attacks {DASH} treat as suggestive.",
        "survived": "3 OF 5 ATTACKS SURVIVED",
        "evidence": [f"Sharpe 1.20 in-sample{DASH}0.30 out-of-sample"],
        "breaks": [f"the walk-forward is thin {DASH} 12 of 30 windows"],
        "caveat": f"22 closed trades {DASH} 2 volatility regimes",
    },
    "retail": {
        "headline": f"Passed 3 of 5 stress tests {DASH} do not bet the house.",
        "evidence": [],
        "breaks": [],
        "caveat": f"22 finished trades {DASH} 2 market moods",
        "notes": [f"kept 25% of its training score {DASH} fail"],
        "recommendations": [f"more history {DASH} not more tuning"],
    },
    "recommendations": [f"widen the window {DASH} do not tune"],
    "trades": [{"d": "Jan 3 '24", "a": "SELL", "det": f"put{DASH}30d", "n": ""}],
    "provenance": {
        "v": 1,
        "prompt": {"text": f"sell a 30 delta put on SPY {DASH} weekly"},
        "conversation": [
            {
                "kind": "question",
                "id": "tenor",
                "question": f"Which tenor {DASH} 0 DTE or 45 DTE?",
                "options": [f"0 DTE{DASH}same day"],
            },
            {"kind": "answer", "id": "tenor", "answer": f"45 DTE {DASH} monthly"},
        ],
        "note": f"derived at read time {DASH} conversation not captured",
    },
}


def _prose_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for item in value.values() for s in _prose_strings(item)]
    if isinstance(value, list):
        return [s for item in value for s in _prose_strings(item)]
    return []


def test_a_stored_legacy_payload_renders_clean() -> None:
    out = normalize_payload_prose(LEGACY_PAYLOAD)
    assert out["verdict"]["headline"] == "Survives 3 of 5 attacks, treat as suggestive."
    assert out["verdict"]["caveat"] == "22 closed trades, 2 volatility regimes"
    assert out["retail"]["notes"] == ["kept 25% of its training score, fail"]
    assert out["recommendations"] == ["widen the window, do not tune"]
    assert out["trades"][0]["det"] == "put, 30d"
    assert out["provenance"]["conversation"][0]["question"] == "Which tenor, 0 DTE or 45 DTE?"
    assert out["provenance"]["note"] == "derived at read time, conversation not captured"


def test_the_users_own_words_are_not_rewritten() -> None:
    """House style governs what the product writes. The prompt a person typed
    and the answers they gave stay exactly as they gave them."""
    out = normalize_payload_prose(LEGACY_PAYLOAD)
    prov = out["provenance"]
    assert prov["prompt"]["text"] == LEGACY_PAYLOAD["provenance"]["prompt"]["text"]
    assert prov["conversation"][1]["answer"] == f"45 DTE {DASH} monthly"


def test_read_time_normalization_does_not_touch_the_input() -> None:
    """The stored record is the provenance. Normalization returns a copy."""
    before = json.dumps(LEGACY_PAYLOAD, sort_keys=True)
    normalize_payload_prose(LEGACY_PAYLOAD)
    assert json.dumps(LEGACY_PAYLOAD, sort_keys=True) == before


def test_read_time_normalization_is_idempotent() -> None:
    once = normalize_payload_prose(LEGACY_PAYLOAD)
    assert normalize_payload_prose(once) == once


# ------------------------------------------------------ READ time: end to end
@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    # the /api gate opens only when SKEPTIC_ACCESS_TOKEN is absent, and a
    # configured key would turn these into live LLM calls
    monkeypatch.delenv("SKEPTIC_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    return TestClient(app)


def _store_legacy_run(run_id: str) -> str:
    payload = {**LEGACY_PAYLOAD, "id": run_id}
    with db.session() as s:
        s.query(db.Run).filter(db.Run.id == run_id).delete()
        s.add(
            db.Run(
                id=run_id,
                status="done",
                spec_json=json.dumps({"meta": {"name": "legacy"}}),
                payload_json=json.dumps(payload),
                provenance_json=json.dumps(payload["provenance"]),
            )
        )
        s.commit()  # db.session() rolls back on exit; without this, nothing is stored
    return run_id


def test_get_run_serves_a_legacy_row_clean(client: TestClient) -> None:
    run_id = _store_legacy_run("emdash-read-1")
    body = client.get(f"/api/runs/{run_id}").json()
    assert DASH not in body["verdict"]["headline"]
    assert DASH not in body["verdict"]["caveat"]
    assert DASH not in body["provenance"]["conversation"][0]["question"]
    # the only em-dashes left on the wire are the ones the user typed
    survivors = [s for s in _prose_strings(body) if DASH in s]
    assert survivors == [
        f"sell a 30 delta put on SPY {DASH} weekly",
        f"45 DTE {DASH} monthly",
    ]


def test_the_stored_row_keeps_its_bytes_after_a_read(client: TestClient) -> None:
    """The database must keep the byte-exact record: normalization is a
    display transform on the way out, never a migration."""
    run_id = _store_legacy_run("emdash-read-2")
    with db.session() as s:
        run = s.get(db.Run, run_id)
        assert run is not None
        before = run.payload_json

    client.get(f"/api/runs/{run_id}")

    with db.session() as s:
        run = s.get(db.Run, run_id)
        assert run is not None
        assert run.payload_json == before
    # and the record really is the un-normalized one (json.dumps escapes the
    # character, so decode before looking for it)
    assert before is not None
    assert DASH in json.loads(before)["verdict"]["headline"]


# ------------------------------------------- EXPORT time: the two artifacts
#
# The HTML report and the .ipynb are the surfaces no other layer can reach.
# `frontend/components/results/export-actions.tsx` is a plain anchor, so the
# browser navigates straight to the backend and renders whatever the server
# wrote. Nothing in either file is ever submitted back, which is what makes
# full normalization correct here and only here: the prompt a person typed
# is rendered clean, the stored row still holds the words they used.

LEGACY_GRID: dict[str, Any] = {
    "ticker": "SPY",
    "structure": "short put",
    "clock": "daily",
    # a nested value, so the rendered path runs through json.dumps, where
    # ensure_ascii would write the character out as a backslash escape
    "conditions": [{"note": f"RSI under 30 {DASH} confirmed on the close"}],
    "window": {"start": "2024-01-02", "end": "2026-01-02"},
    "seed": 42,
}

LEGACY_SWEEP_NOTES = [f"gex_level is a sign test {DASH} not swept"]


def _legacy_copy() -> tuple[dict[str, Any], dict[str, Any]]:
    """A fresh deep copy of the stored record, so a mutation would show."""
    payload: dict[str, Any] = json.loads(json.dumps(LEGACY_PAYLOAD))
    return payload, payload["provenance"]


def _assert_no_spelling_survives(document: str, where: str) -> None:
    for spelling in RENDERED_SPELLINGS:
        assert spelling not in document, f"{spelling!r} reached the {where}"


def test_the_html_report_of_a_legacy_run_renders_clean() -> None:
    payload, provenance = _legacy_copy()
    doc = build_report(
        run_id="emdash-report",
        name=payload["name"],
        payload=payload,
        provenance=provenance,
        grid=LEGACY_GRID,
        sweep_notes=LEGACY_SWEEP_NOTES,
    )
    _assert_no_spelling_survives(doc, "HTML report")
    # the product's own prose, cleaned the way every other surface cleans it
    assert "Survives 3 of 5 attacks, treat as suggestive." in doc
    assert "gex_level is a sign test, not swept" in doc
    # a value that reaches the page through json.dumps, not through a string
    assert "RSI under 30, confirmed on the close" in doc
    # and the part that is only true here: the user's own words, normalized,
    # because this document is a terminal render and never a round trip
    assert "sell a 30 delta put on SPY, weekly" in doc
    assert "45 DTE, monthly" in doc


def test_the_notebook_export_of_a_legacy_run_renders_clean() -> None:
    payload, provenance = _legacy_copy()
    notebook = build_notebook(
        run_id="emdash-notebook",
        name=payload["name"],
        payload=payload,
        provenance=provenance,
        grid=LEGACY_GRID,
        api_base="https://api.example",
        sweep_notes=LEGACY_SWEEP_NOTES,
    )
    # serialized the way the export serializes it: with ensure_ascii, a dash
    # that survived would appear as the six-character escape rather than as
    # anything dash-shaped, which is why that spelling is checked too
    text = json.dumps(notebook)
    _assert_no_spelling_survives(text, "notebook export")
    story = "".join(notebook["cells"][1]["source"])
    assert "sell a 30 delta put on SPY, weekly" in story
    assert "Which tenor, 0 DTE or 45 DTE?" in story
    assert "RSI under 30, confirmed on the close" in story


def test_building_an_export_never_touches_the_record_it_read() -> None:
    """Rule 1, on the export path: the database keeps the byte-exact record.

    Both builders normalize a COPY. If either ever normalized in place, the
    caller (app/api/notebook.py, holding the merged payload straight off the
    run row) would hand a rewritten record to whatever ran next.
    """
    payload, provenance = _legacy_copy()
    grid = json.loads(json.dumps(LEGACY_GRID))
    notes = list(LEGACY_SWEEP_NOTES)
    before = json.dumps([payload, grid, notes], sort_keys=True)

    build_report(run_id="r", name="n", payload=payload, provenance=provenance,
                 grid=grid, sweep_notes=notes)
    build_notebook(run_id="r", name="n", payload=payload, provenance=provenance,
                   grid=grid, api_base="x", sweep_notes=notes)

    assert json.dumps([payload, grid, notes], sort_keys=True) == before
    assert DASH in payload["verdict"]["headline"]
    assert DASH in provenance["prompt"]["text"]


# --------------------------------------------------------------- dict keys
# A key is prose on at least one payload. `skipReasons` is keyed by the
# engine's own skip labels and `app/notebook/report.py` renders those keys as
# table rows, so a dash in a key reaches a page exactly like a dash in a value.
# The audit caught this by finding a skipReasons KEY that still carried the
# glyph while every value beside it was clean.


def test_dict_keys_are_normalized_like_any_other_string() -> None:
    payload = {f"chain too wide {DASH} skipped": 12, "clean key": 3}
    assert normalize_tree(payload) == {"chain too wide, skipped": 12, "clean key": 3}
    assert normalize_mapping(payload) == {"chain too wide, skipped": 12, "clean key": 3}


def test_nested_dict_keys_are_normalized() -> None:
    payload = {"outer": [{f"why {DASH} not": {f"deep {DASH} key": 1}}]}
    assert normalize_tree(payload) == {"outer": [{"why, not": {"deep, key": 1}}]}


def test_a_key_collision_keeps_both_rows() -> None:
    """Losing a row is worse than keeping a dash.

    Two distinct keys can normalize to the same string. Rewriting both would
    silently drop one, and a skip-reason count that vanishes is a worse lie
    than a dash, so on a collision both sides keep the key they arrived with.
    """
    payload = {f"a {DASH} b": 1, "a, b": 2}
    result = normalize_tree(payload)
    assert result == payload
    assert len(result) == 2, "a colliding key dropped a row"


def test_normalizing_keys_does_not_mutate_the_input() -> None:
    original = {f"why {DASH} skipped": 4}
    normalize_tree(original)
    assert list(original) == [f"why {DASH} skipped"], "the stored record was rewritten"


# ------------------------------------------------- the dash-before-closer rule
# Pinned in backend/tests/test_normalizer_parity.py for BOTH implementations;
# repeated here because the numeric half is a guardrail #4 concern, not a
# punctuation one.


def test_no_doubled_comma_when_the_clause_is_already_closed() -> None:
    assert normalize(f"withheld {DASH}, then graded") == "withheld, then graded"
    assert ", ," not in normalize(f"withheld {DASH}, then graded")


def test_dropping_the_dash_never_invents_a_number() -> None:
    """Guardrail #4: every number in verdict text must exist in the stats.

    Collapsing the dash joins its neighbours, and a digit, a decimal point and
    a digit would fuse 1 and .1 into the token 1.1, a figure no stats payload
    contains. The validator would reject it, and it should never be built.
    """
    assert normalize(f"1{DASH}.1") == "1, .1"
    assert normalize(f"2.5 {DASH} .75") == "2.5, .75"
    for text in (f"1{DASH}.1", f"2.5 {DASH} .75"):
        before = _NUM_RE.findall(text)
        assert _NUM_RE.findall(normalize(text)) == before, "a numeric token moved"


def test_a_sentence_period_after_a_digit_still_collapses() -> None:
    """The guard is narrow: only a decimal point with a digit behind it."""
    assert normalize(f"only 15 {DASH}. Thin") == "only 15. Thin"

