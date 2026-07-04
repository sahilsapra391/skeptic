"""Language grounding for the LLM verdict (guardrail #4, honesty layer).

The numeric validator proves every number is real but is blind to language.
A DeepSeek-class model handed a numbers-only payload will write a fully
grounded verdict in Chinese; these tests pin that it is rejected and the
English deterministic template ships instead.
"""

import json
from typing import Any

from app.honesty.report import (
    Coverage,
    Dsr,
    HonestyReport,
    MonteCarlo,
    OosSplit,
    RegimeSample,
    Sensitivity,
    Trust,
    WalkForward,
)
from app.honesty.verdict import (
    _llm_narrate,
    allowed_numbers,
    is_english,
    non_latin_ratio,
    write_verdict,
)

# the actual headline from the DeepSeek incident (2026-07)
INCIDENT_HEADLINE = "该策略经交易顺序重排后有23%的模拟路径亏钱，极端情况下最大回撤达1.70"


def _minimal_report() -> HonestyReport:
    """Smallest valid report — enough for the verdict writer to run."""
    return HonestyReport(
        oos=OosSplit(
            split_date="2023-01-01",
            is_sharpe=0.51,
            oos_sharpe=0.47,
            is_return=0.10,
            oos_return=0.09,
            is_trades=20,
            oos_trades=10,
            degradation=0.92,
            sign_flip=False,
            flagged=False,
        ),
        walk_forward=WalkForward(meaningful=False, note="short history"),
        monte_carlo=MonteCarlo(
            resamples=1000,
            block=5,
            seed=42,
            trades=13,
            terminal_p5=-6773.94,
            terminal_p50=500.0,
            terminal_p95=1200.0,
            max_drawdown_p50=0.10,
            max_drawdown_p95=1.70,
            p_loss=0.23,
        ),
        sensitivity=Sensitivity(verdict="plateau"),
        dsr=Dsr(trials=13, daily_sharpe=0.48, expected_max_sharpe=0.60, dsr=0.28),
        regime_sample=RegimeSample(
            trades=13,
            days_low_vix=100,
            days_mid_vix=100,
            days_high_vix=100,
            regimes_present=3,
            capped=False,
            cap_reason=None,
        ),
        coverage=Coverage(
            requested_start="2020-01-06",
            requested_end="2026-07-02",
            effective_start="2020-01-06",
            effective_end="2026-07-02",
            requested_sessions=1000,
            chain_sessions=1000,
            coverage_ratio=1.0,
            materially_short=False,
            reason=None,
        ),
        trust=Trust(
            level=3,
            label="suggestive",
            survived={
                "oos": True,
                "walk_forward": True,
                "monte_carlo": False,
                "sensitivity": True,
                "sample": True,
            },
            survived_count=4,
            reasons=["monte carlo: 23% of resampled paths lose money"],
        ),
        metrics={"cagr": 0.104},
        effective_start="2020-01-06",
        effective_end="2026-07-02",
        seed=42,
    )


# ------------------------------------------------------------- the guard itself
def test_guard_flags_chinese_and_passes_english() -> None:
    assert not is_english(INCIDENT_HEADLINE)
    assert non_latin_ratio(INCIDENT_HEADLINE) > 0.9

    english = "Survives 4 of 5 attacks. Treat as suggestive, not proven."
    assert is_english(english)
    assert non_latin_ratio(english) == 0.0


def test_guard_does_not_trip_on_template_typography() -> None:
    """The deterministic template uses — · → ’ and arrows; none are letters,
    so an all-English verdict full of them must still read as English."""
    glyphy = (
        "Survives 4 of 5 attacks — window 2020-01-06 → 2026-07-02 · "
        "it kept 92% of its training score; don’t bet the house."
    )
    assert is_english(glyphy)


# --------------------------------------------- grounded-but-Chinese is rejected
class _FakeResp:
    status_code = 200

    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def json(self) -> dict[str, Any]:
        content = json.dumps(self._payload, ensure_ascii=False)
        return {"choices": [{"message": {"content": content}}]}


def test_grounded_chinese_verdict_is_rejected_and_falls_back(monkeypatch: Any) -> None:
    import requests

    # every number here IS in the report (23% = p_loss, 1.70 = dd_p95) — so the
    # numeric validator would bless it. Only the language guard can catch this.
    chinese = {
        "headline": "该策略有23%的模拟路径亏钱，最大回撤达1.70",
        "evidence": ["经过13次试验，去膨胀后的风险调整分数只有0.28"],
        "breaks_where": ["当交易顺序被打乱时，赚钱能力大幅下降"],
        "caveats": ["仅供研究，不构成投资建议"],
    }

    def fake_post(
        url: str, headers: Any = None, json: Any = None, timeout: Any = None
    ) -> _FakeResp:
        return _FakeResp(chinese)

    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

    report = _minimal_report()

    # the LLM layer refuses the Chinese candidate on every retry → gives up
    assert _llm_narrate(report, allowed_numbers(report)) is None

    # …and the public writer ships the English deterministic template instead
    verdict = write_verdict(report)
    assert verdict.source == "template"
    joined = " ".join(
        [verdict.headline, *verdict.evidence, *verdict.breaks_where, *verdict.caveats]
    )
    assert is_english(joined)
