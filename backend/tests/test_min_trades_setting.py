"""The evidence bar is a user setting (owner 2026-07-14): default 15,
floor 1, never 0. New runs gauntlet at the caller's bar; SAVED runs
re-grade at read time in both directions; graded sub-15 samples always
carry the below-standard disclosure; the narration upgrade happens off
the critical path and swaps only the wording."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app import db
from app.engine.market import build_fixture_store
from app.honesty.report import RegimeSample, ResolutionBucket, ResolutionSplit
from app.honesty.stages import regrade_sample, rejudge_resolution
from app.main import app
from tests.fixtures.engine import fx_short_put_assigned as fx


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.delenv("SKEPTIC_ACCESS_TOKEN", raising=False)
    # hermetic: never let a configured key turn tests into live LLM calls
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    import app.data.chains as chains_module

    # two VIX regimes so the single-trade fixture is capped ONLY by the
    # trades bar — the regime guard is not the setting under test
    days = sorted(fx.UNDERLYING)
    vix = {d: (12.0 if i < len(days) // 2 else 25.0) for i, d in enumerate(days)}
    monkeypatch.setattr(
        chains_module,
        "load_market_store",
        lambda ticker, **kw: build_fixture_store("SPY", fx.CHAINS, fx.UNDERLYING, vix=vix),
    )
    return TestClient(app)


# ------------------------------------------------------------- unit: the gate
def _sample(trades: int, regimes: int = 3) -> RegimeSample:
    return RegimeSample(
        trades=trades, days_low_vix=50, days_mid_vix=50, days_high_vix=50,
        regimes_present=regimes, capped=False, cap_reason=None,
    )


def test_regrade_sample_honors_the_bar_both_directions() -> None:
    graded = regrade_sample(_sample(trades=13), 1)
    assert graded.capped is False and graded.min_trades == 1

    recapped = regrade_sample(_sample(trades=20), 300)
    assert recapped.capped is True
    assert recapped.cap_reason is not None and "minimum is 300" in recapped.cap_reason


def test_regrade_sample_never_lifts_the_regime_cap() -> None:
    s = regrade_sample(_sample(trades=13, regimes=1), 1)
    assert s.capped is True
    assert s.cap_reason is not None and "single volatility regime" in s.cap_reason


def test_old_regime_sample_dumps_default_to_the_standard_bar() -> None:
    stored = {
        "trades": 13, "days_low_vix": 10, "days_mid_vix": 10,
        "days_high_vix": 10, "regimes_present": 3,
        "capped": True, "cap_reason": "only 13 closed trades — minimum is 15",
    }
    assert RegimeSample.model_validate(stored).min_trades == 15


def test_rejudge_resolution_follows_the_bar() -> None:
    split = ResolutionSplit(
        meaningful=True, full_sharpe=1.0,
        five_min=ResolutionBucket(sessions=20, trades=9, sharpe=-0.5),
        minute=ResolutionBucket(sessions=20, trades=30, sharpe=1.2),
    )
    at_standard = rejudge_resolution(split, 15)
    assert at_standard.judged is False and at_standard.sign_flip is False
    assert at_standard.note is not None and "too thin" in at_standard.note

    at_five = rejudge_resolution(split, 5)
    assert at_five.judged is True and at_five.sign_flip is True
    assert at_five.note is None


# --------------------------------------------------------- API: run-time bar
def test_min_trades_zero_is_rejected(client: TestClient) -> None:
    r = client.post("/api/backtest", json={"spec": fx.SPEC, "min_trades": 0})
    assert r.status_code == 422


def test_get_run_min_trades_zero_is_rejected(client: TestClient) -> None:
    assert client.get("/api/runs/whatever?min_trades=0").status_code == 422


def test_bar_of_one_grades_a_single_trade(client: TestClient) -> None:
    r = client.post("/api/backtest", json={"spec": fx.SPEC, "min_trades": 1})
    run_id = r.json()["run_id"]
    payload = client.get(f"/api/runs/{run_id}").json()
    assert payload["status"] == "done"
    assert payload["verdict"]["refusal"] is False
    # a graded sub-15 sample ALWAYS discloses the lowered bar, both voices
    assert "Below-standard sample" in payload["verdict"]["caveat"]
    assert "lowered the bar" in payload["retail"]["caveat"]
    # hermetic run (no key): template narration, nothing pending
    assert payload["narrationPending"] is False
    assert "verdict: template" in payload["meta"]
    # graded → no unlock conditions stored for the nightly scan
    with db.session() as s:
        run = s.get(db.Run, run_id)
        assert run is not None and run.unlock_json is None


def test_custom_bar_rides_the_unlock_conditions(client: TestClient) -> None:
    r = client.post("/api/backtest", json={"spec": fx.SPEC, "min_trades": 20})
    run_id = r.json()["run_id"]
    payload = client.get(f"/api/runs/{run_id}").json()
    assert payload["verdict"]["refusal"] is True
    assert "≥ 20 trades (has 1)" in payload["verdict"]["refusalUnlock"]
    with db.session() as s:
        run = s.get(db.Run, run_id)
        assert run is not None and run.unlock_json is not None
        unlock = json.loads(run.unlock_json)
    assert unlock["trades"] == {"has": 1.0, "needs": 20.0}


# ---------------------------------------------------- read-time re-grading
def test_saved_refusal_unlocks_when_the_bar_drops(client: TestClient) -> None:
    # scored at the standard bar → refused (1 closed trade)
    run_id = client.post("/api/backtest", json={"spec": fx.SPEC}).json()["run_id"]
    stored = client.get(f"/api/runs/{run_id}").json()
    assert stored["verdict"]["refusal"] is True

    # the viewer's bar drops to 1 → the SAME stored run grades, disclosed
    regraded = client.get(f"/api/runs/{run_id}?min_trades=1").json()
    assert regraded["verdict"]["refusal"] is False
    assert regraded["regraded"] == {"bar": 1, "ranAt": 15}
    assert "re-graded at your minimum-trades setting of 1" in regraded["verdict"]["caveat"]
    assert "Below-standard sample" in regraded["verdict"]["caveat"]
    # unblessed stars come off the metric tiles on the re-graded view
    assert all(not t["l"].endswith("*") for t in regraded["mtiles"])

    # same bar as stored → the stored payload passes through untouched
    same = client.get(f"/api/runs/{run_id}?min_trades=15").json()
    assert same["verdict"]["refusal"] is True and "regraded" not in same

    # the stored row was never mutated by the re-graded view
    raw = client.get(f"/api/runs/{run_id}").json()
    assert raw["verdict"]["refusal"] is True


def test_saved_grade_recaps_when_the_bar_rises(client: TestClient) -> None:
    run_id = client.post(
        "/api/backtest", json={"spec": fx.SPEC, "min_trades": 1}
    ).json()["run_id"]
    stored = client.get(f"/api/runs/{run_id}").json()
    assert stored["verdict"]["refusal"] is False

    recapped = client.get(f"/api/runs/{run_id}?min_trades=300").json()
    assert recapped["verdict"]["refusal"] is True
    assert recapped["regraded"] == {"bar": 300, "ranAt": 1}
    assert "≥ 300 trades (has 1)" in recapped["verdict"]["refusalUnlock"]
    assert all(t["l"].endswith("*") for t in recapped["mtiles"])


# ------------------------------------------------- async narration upgrade
def _grounded_llm_verdict(*_a: object, **_k: object):
    from app.honesty.verdict import VerdictText

    return VerdictText(
        headline="The narrated headline.", evidence=[], breaks_where=[],
        caveats=["narrated caveat"], source="llm",
    )


def test_narration_lands_after_done_and_swaps_only_words(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.honesty.verdict as verdict_module

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(verdict_module, "_llm_narrate", _grounded_llm_verdict)

    run_id = client.post(
        "/api/backtest", json={"spec": fx.SPEC, "min_trades": 1}
    ).json()["run_id"]
    # TestClient runs background tasks synchronously — by now the narration
    # patch has already landed on the stored payload
    payload = client.get(f"/api/runs/{run_id}").json()
    assert payload["status"] == "done"
    assert payload["narrationPending"] is False
    assert payload["verdict"]["headline"] == "The narrated headline."
    assert "verdict: llm" in payload["meta"]
    # trust geometry survived the wording swap
    assert payload["verdict"]["refusal"] is False
    assert payload["verdict"]["band"] is not None
    # the below-standard disclosure outlives the LLM's own caveats
    assert "Below-standard sample" in payload["verdict"]["caveat"]
    # the library card quotes the narrated voice
    with db.session() as s:
        run = s.get(db.Run, run_id)
        assert run is not None and run.summary_json is not None
        summary = json.loads(run.summary_json)
        perf = json.loads(run.perf_json or "{}")
    assert "The narrated headline." in summary["quote"]
    assert "narration_s" in perf


def test_failed_narration_leaves_the_template_standing(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.honesty.verdict as verdict_module

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(verdict_module, "_llm_narrate",
                        lambda *a, **k: None)

    run_id = client.post("/api/backtest", json={"spec": fx.SPEC}).json()["run_id"]
    payload = client.get(f"/api/runs/{run_id}").json()
    assert payload["status"] == "done"
    assert payload["narrationPending"] is False
    assert "verdict: template" in payload["meta"]
    assert payload["verdict"]["refusal"] is True  # 1 trade at the standard bar
