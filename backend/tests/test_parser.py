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


class _RawResp:
    """A 200 whose content is NOT the contract's JSON — earns a retry."""

    status_code = 200

    def __init__(self, content: str) -> None:
        self._content = content

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


def test_atm_legs_normalize_to_50_delta_on_every_leg(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ATM is the 50-delta strike, normalized by StrikeSelection itself so
    EVERY ingress gets it. A model emitting method "atm" on BOTH legs of a
    spread (the zero-fills bug: both legs collide on the spot strike) must
    land as delta 0.5 on every leg."""
    spec = _valid_spec()
    spec["position"]["structure"] = "put_credit_spread"
    spec["position"]["legs"] = [
        {"right": "put", "side": "short", "ratio": 1,
         "strike_selection": {"method": "atm", "value": 0}},
        {"right": "put", "side": "long", "ratio": 1,
         "strike_selection": {"method": "atm", "value": 0}},
    ]
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(
        requests, "post", lambda *a, **k: _FakeResp({"result": "spec", "spec": spec})
    )
    out = parser_module.parse_strategy("sell an ATM put spread on SPY, close at 50% profit")
    assert out is not None and out.status == "spec" and out.spec is not None
    for leg in out.spec["position"]["legs"]:
        assert leg["strike_selection"]["method"] == "delta"
        assert leg["strike_selection"]["value"] == 0.5


def test_malformed_position_falls_back_to_questions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A model reply whose "position" is null must never escape as an
    exception — it retries and degrades to questions like any invalid spec."""
    spec = _valid_spec()
    spec["position"] = None
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(
        requests, "post", lambda *a, **k: _FakeResp({"result": "spec", "spec": spec})
    )
    out = parser_module.parse_strategy("sell a put on SPY, close at 50% profit")
    assert out is not None and out.status == "questions"


# ------------------------------------------------ upstream failure honesty
def test_upstream_failure_raises_not_a_fake_question(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An upstream LLM failure is an ERROR the route reports as a 503 — it
    must never come back as a fake clarifying question (it rendered as
    "QUESTION 1 OF 1 — I DON'T GUESS" and entered the provenance record)."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

    def _boom(*a: object, **k: object) -> None:
        raise requests.Timeout("upstream took too long")

    monkeypatch.setattr(requests, "post", _boom)
    with pytest.raises(parser_module.ParserUnavailableError, match="upstream error"):
        parser_module.parse_strategy("sell a put on SPY, close at 50% profit")


def test_budget_exhausted_retry_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """The attempt loop answers inside PARSE_BUDGET_SECONDS: when a contract
    violation earns a retry the budget can't fund, the parser refuses while
    it can still say so — the proxy must never 504 a healthy engine."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    # deadline calc → attempt-1 timeout calc → retry budget check
    clock = iter([0.0, 0.5, 88.0])
    monkeypatch.setattr(parser_module, "_monotonic", lambda: next(clock))
    calls: list[dict] = []

    def _garbage(*a: object, **k: object) -> _RawResp:
        calls.append(dict(k))
        return _RawResp("no json here")

    monkeypatch.setattr(requests, "post", _garbage)
    with pytest.raises(parser_module.ParserUnavailableError, match="ran out of time"):
        parser_module.parse_strategy("sell a put on SPY, close at 50% profit")
    assert len(calls) == 1  # the doomed retry was never launched


def test_attempt_timeout_shrinks_to_the_remaining_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each upstream call gets only what's left of the budget, so two
    attempts can never add up past the proxy's leash."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    # deadline calc → attempt-1 timeout → retry check → attempt-2 timeout
    clock = iter([0.0, 0.0, 50.0, 50.0])
    monkeypatch.setattr(parser_module, "_monotonic", lambda: next(clock))
    timeouts: list[float] = []
    replies = iter(
        [_RawResp("no json here"), _FakeResp({"result": "spec", "spec": _valid_spec()})]
    )

    def _post(*a: object, **k: object) -> object:
        timeouts.append(k["timeout"])
        return next(replies)

    monkeypatch.setattr(requests, "post", _post)
    out = parser_module.parse_strategy("sell a 30 delta put on SPY, close at 50%")
    assert out is not None and out.status == "spec"
    assert timeouts == [60.0, 40.0]


# --------------------------------------------------------- D5d: scale-in
def test_required_spec_version_detects_v3() -> None:
    """The version is server-computed from the vocabulary used — scale_in or
    close_at_time lifts it to 3, never trusted from the LLM."""
    rsv = parser_module._required_spec_version
    assert rsv({"entry": {"scale_in": {"mode": "signal_ladder"}}}) == 3
    assert rsv({"exit": {"close_at_time": "15:45"}}) == 3
    assert rsv({"backtest": {"clock": "5min"}}) == 2
    assert rsv({"exit": {"profit_target_pct": 50}}) == 1


def test_required_spec_version_detects_v4() -> None:
    """FX.5: intraday_scan or backtest.resolution lifts to 4 — including
    when v3 vocabulary is ALSO present (highest wins)."""
    rsv = parser_module._required_spec_version
    assert rsv({"entry": {"intraday_scan": "every_setup"}}) == 4
    assert rsv({"backtest": {"resolution": "finest"}}) == 4
    assert rsv({"entry": {"intraday_scan": "every_setup"},
                "exit": {"close_at_time": "15:45"}}) == 4
    assert rsv({"backtest": {"resolution": "finest", "clock": "5min"}}) == 4


def test_required_spec_version_detects_v5() -> None:
    """F4: a vol-surface indicator lifts to 5 — including when v4
    vocabulary is ALSO present (the version is the MAX the vocabulary
    needs, checked highest-first)."""
    rsv = parser_module._required_spec_version
    skew = {"indicator": "skew_25d", "operator": ">", "value": 5}
    term = {"indicator": "term_structure_slope", "operator": "<", "value": 0}
    assert rsv({"entry": {"conditions": [skew]}}) == 5
    assert rsv({"exit": {"conditions": [term]}}) == 5
    assert rsv({"entry": {"conditions": [skew]},
                "backtest": {"resolution": "finest"}}) == 5
    # the VRP alias maps to hv_iv_spread_30d — v2 vocabulary, NOT v5
    vrp = {"indicator": "hv_iv_spread_30d", "operator": ">", "value": 4}
    assert rsv({"entry": {"conditions": [vrp]}}) == 2
    # review finding: ladder rungs and the rearm are conditions too
    assert rsv({"entry": {"scale_in": {"rungs": [skew]}}}) == 5
    assert rsv({"entry": {"scale_in": {"rungs": [
        {"indicator": "rsi", "operator": "<", "value": 30}],
        "rearm": term}}}) == 5


def test_required_spec_version_detects_v6() -> None:
    """F1: dealer-positioning vocabulary lifts to 6 — the MAX wins even
    when v5/v4 vocabulary is also present, and ladder conditions count."""
    rsv = parser_module._required_spec_version
    gex = {"indicator": "gex_level", "operator": ">", "value": 0}
    rank = {"indicator": "dex_rank_1y", "operator": "<", "value": 10}
    skew = {"indicator": "skew_25d", "operator": ">", "value": 5}
    assert rsv({"entry": {"conditions": [gex]}}) == 6
    assert rsv({"exit": {"conditions": [rank]}}) == 6
    assert rsv({"entry": {"conditions": [gex, skew]}}) == 6
    assert rsv({"entry": {"conditions": [gex]},
                "backtest": {"resolution": "finest"}}) == 6
    assert rsv({"entry": {"scale_in": {"rungs": [gex]}}}) == 6


def test_required_spec_version_detects_v7() -> None:
    """F2/F3: flow/pin vocabulary lifts to 7 — MAX wins over v6/v5,
    and ladder conditions count."""
    rsv = parser_module._required_spec_version
    flow = {"indicator": "net_premium_level", "operator": ">", "value": 0}
    tide = {"indicator": "market_tide_rank_1y", "operator": ">", "value": 75}
    mpd = {"indicator": "max_pain_distance_pct", "operator": "<", "value": 1}
    gex = {"indicator": "gex_level", "operator": ">", "value": 0}
    assert rsv({"entry": {"conditions": [flow]}}) == 7
    assert rsv({"exit": {"conditions": [tide]}}) == 7
    assert rsv({"entry": {"conditions": [mpd, gex]}}) == 7
    assert rsv({"entry": {"scale_in": {"rungs": [flow]}}}) == 7


def test_required_spec_version_detects_v8() -> None:
    """Parity Tier 3: the standardized IVX form lifts to 8 — MAX wins over
    v7/v6, and ladder conditions count."""
    rsv = parser_module._required_spec_version
    z = {"indicator": "ivx_zscore_1y", "operator": ">", "value": 1.5}
    flow = {"indicator": "net_premium_level", "operator": ">", "value": 0}
    assert rsv({"entry": {"conditions": [z]}}) == 8
    assert rsv({"exit": {"conditions": [z]}}) == 8
    assert rsv({"entry": {"conditions": [z, flow]}}) == 8
    assert rsv({"entry": {"scale_in": {"rungs": [z]}}}) == 8


def _ladder_spec_raw() -> dict:
    # what the LLM emits (spec_version 1, ATM on the leg) — the server
    # normalizes and recomputes the version
    return {
        "spec_version": 1,
        "meta": {"name": "SPY 1DTE RSI ladder", "description_raw": "a paraphrase"},
        "underlying": {"ticker": "SPY"},
        "position": {
            "structure": "long_call",
            "legs": [{"right": "call", "side": "long", "ratio": 1,
                      "strike_selection": {"method": "atm", "value": 0}}],
            "expiration_selection": {"target_dte": 1, "min_dte": 0, "max_dte": 2},
        },
        "entry": {
            "schedule": {"frequency": "signal_only"}, "conditions": [],
            "max_concurrent_positions": 1,
            "scale_in": {
                "mode": "signal_ladder", "basket": True,
                "rungs": [
                    {"indicator": "rsi", "period": 14, "timeframe": "5min",
                     "operator": "<=", "value": 30, "add_contracts": 2},
                    {"indicator": "rsi", "period": 14, "timeframe": "5min",
                     "operator": "<=", "value": 25, "add_contracts": 3},
                ],
                "rearm": {"indicator": "rsi", "period": 14, "timeframe": "5min",
                          "operator": ">", "value": 30},
                "max_total_contracts": 20,
            },
        },
        "exit": {"profit_target_pct": 40, "stop_loss_pct": 75, "close_at_time": "15:45"},
        "sizing": {"method": "fixed_contracts", "value": 1},
        "costs": {"commission_per_contract": 0.65, "slippage_half_spread_fraction": 0.5,
                                                   "slippage_half_spread_fraction_sell": 0.5},
        "backtest": {"start": None, "end": None, "initial_capital": 25000,
                     "seed": 42, "clock": "5min"},
    }


def test_scale_in_ladder_flows_through_and_recomputes_to_v3(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ladder the model emits (as v1) validates as a v3 spec: the server
    recomputes the version, keeps the rungs/cap, and normalizes ATM → .50Δ.
    Proves the parser's server-side guarantees for the D5 primitive without
    the live model."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(
        requests, "post",
        lambda *a, **k: _FakeResp({"result": "spec", "spec": _ladder_spec_raw()}),
    )
    out = parser_module.parse_strategy(
        "SPY 1DTE ATM call, scale in on 5-min RSI(14): 2 at 30, 3 at 25, cap 20, "
        "40% profit, 75% stop, flatten by 3:45"
    )
    assert out is not None and out.status == "spec" and out.spec is not None
    spec = out.spec
    assert spec["spec_version"] == 3  # server recomputed from the vocabulary
    si = spec["entry"]["scale_in"]
    assert len(si["rungs"]) == 2 and si["max_total_contracts"] == 20
    assert si["rungs"][0]["add_contracts"] == 2
    assert spec["exit"]["close_at_time"] == "15:45"
    assert spec["backtest"]["clock"] == "5min"
    # ATM normalized on the leg, like every ingress
    assert spec["position"]["legs"][0]["strike_selection"]["value"] == 0.5
