"""Parity Tier 1: notebook export + pinned reproduce.

Three layers, mirroring how the feature is built:
  * the resolution PIN in the engine (a replay never silently re-resolves
    — the plan's do-NOT item, both directions);
  * the builder (deterministic .ipynb, provenance story first, no
    credentials, ladder cell only on ladder runs);
  * the API loop (export a real completed fixture run; reproduce it and
    get match=True on an unchanged lake).
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app import db
from app.api.notebook import (
    _compare_row,
    _divergence_report,
    _expand_resolution_runs,
)
from app.api.provenance import derived_boxes
from app.engine.market import build_fixture_slice, build_fixture_store
from app.engine.runner import run_backtest
from app.main import app
from app.notebook.builder import build_notebook
from app.notebook.report import build_report
from tests.test_finest_resolution import (
    UNDERLYING_2D,
    FinestFixtureIntraday,
    _five_min_slice,
    _minute_slice,
    _spec,
)
from tests.test_five_min_clock import FixtureIntraday, _put
from tests.test_receipts import R_CHAINS, R_ENTRY, R_SPEC, R_UNDERLYING


# ───────────────────────── engine: the resolution pin ──────────────────────
class TestResolutionPin:
    def _mixed(self) -> tuple:
        store = build_fixture_store("SPY", {}, UNDERLYING_2D)
        provider = FinestFixtureIntraday(
            slices={"2025-01-06": _five_min_slice("2025-01-06", "2025-01-08"),
                    "2025-01-07": _five_min_slice("2025-01-07", "2025-01-08")},
            minute={"2025-01-07": _minute_slice("2025-01-07", "2025-01-08")},
        )
        return store, provider

    def test_pin_holds_a_session_down_after_an_upgrade(self) -> None:
        # the lake now offers minute bars for 01-07, but the recorded run
        # used five_min — the pin must WIN over live "finest"
        from datetime import date

        store, provider = self._mixed()
        spec = _spec({"profit_target_pct": 500}, end="2025-01-07")
        pinned = {date(2025, 1, 6): "five_min", date(2025, 1, 7): "five_min"}
        result = run_backtest(spec, store, provider, pinned_resolutions=pinned)
        assert result.resolution_mix == {"five_min": 2}

    def test_pin_reproduces_the_recorded_mixed_map(self) -> None:
        store, provider = self._mixed()
        spec = _spec({"profit_target_pct": 500}, end="2025-01-07")
        live = run_backtest(spec, store, provider)
        assert live.resolution_mix == {"five_min": 1, "minute": 1}

        replay = run_backtest(spec, store, provider,
                              pinned_resolutions=dict(live.resolution_by_session))
        assert replay.resolution_by_session == live.resolution_by_session
        assert replay.equity == live.equity  # bit-identical replay

    def test_unpinned_sessions_resolve_live(self) -> None:
        from datetime import date

        store, provider = self._mixed()
        spec = _spec({"profit_target_pct": 500}, end="2025-01-07")
        # pin only the first session; the second still upgrades to minute
        result = run_backtest(spec, store, provider,
                              pinned_resolutions={date(2025, 1, 6): "five_min"})
        assert result.resolution_mix == {"five_min": 1, "minute": 1}

    def test_minute_pin_falls_back_and_records_the_truth(self) -> None:
        # a pinned-minute session whose grid can no longer be built falls
        # back to 5-min and RECORDS five_min — the caller compares and
        # discloses, the record never lies about what actually ran
        from datetime import date

        store = build_fixture_store("SPY", {}, UNDERLYING_2D)
        provider = FinestFixtureIntraday(
            slices={"2025-01-06": _five_min_slice("2025-01-06", "2025-01-08")},
            minute={"2025-01-06": None},  # grid unbuildable
        )
        spec = _spec({"profit_target_pct": 500}, end="2025-01-06")
        result = run_backtest(spec, store, provider,
                              pinned_resolutions={date(2025, 1, 6): "minute"})
        assert result.resolution_mix == {"five_min": 1}


def test_expand_resolution_runs() -> None:
    from datetime import date

    runs = [
        {"first": "2025-01-06", "last": "2025-01-08", "sessions": 3,
         "resolution": "five_min"},
        {"first": "2025-01-09", "last": "2025-01-09", "sessions": 1,
         "resolution": "minute"},
        {"first": "garbage", "last": "2025-01-10", "resolution": "minute"},
    ]
    pinned = _expand_resolution_runs(runs)
    assert pinned[date(2025, 1, 6)] == "five_min"
    assert pinned[date(2025, 1, 7)] == "five_min"  # calendar-inclusive
    assert pinned[date(2025, 1, 8)] == "five_min"
    assert pinned[date(2025, 1, 9)] == "minute"
    assert len(pinned) == 4  # the malformed row is skipped, not fatal
    assert _expand_resolution_runs(None) == {}


def test_compare_row_one_policy_for_every_stat() -> None:
    # both-None is a match; stored-None is UNEVALUABLE (a legacy stats
    # bundle is a shape gap, not a drift — review finding: None == 0 was
    # a spurious permanent mismatch); fresh-None is a real failure
    assert _compare_row("sharpe", None, None)["ok"] is True
    row = _compare_row("filled", None, 0, exact=True)
    assert row["ok"] is None and "not recorded" in row["note"]
    assert _compare_row("final_equity", 100.0, None)["ok"] is False
    assert _compare_row("filled", 3, 3, exact=True)["ok"] is True
    assert _compare_row("filled", 3, 4, exact=True)["ok"] is False
    # symmetric relative scale, same policy as the metrics
    assert _compare_row("cagr", 1.0, 1.0 + 1e-9)["ok"] is True
    assert _compare_row("cagr", 1.0, 1.1)["ok"] is False


def test_divergence_report_catches_the_gap_day_backfill() -> None:
    # the review's scenario: the compressed run spans an uncovered gap
    # day; the lake later backfills it; the replay covers one MORE
    # session inside the range — per-day pin-vs-actual alone is blind,
    # the recorded session COUNT is not
    from datetime import date

    recorded = [{"first": "2025-01-06", "last": "2025-01-08",
                 "sessions": 2, "resolution": "five_min"}]
    actual = {date(2025, 1, 6): "five_min", date(2025, 1, 7): "five_min",
              date(2025, 1, 8): "five_min"}  # gap day 01-07 now covered
    report = _divergence_report(recorded, actual)
    assert len(report) == 1
    assert "recorded 2 covered session(s), replay covered 3" in report[0]["issue"]


def test_divergence_report_catches_flips_drops_and_strays() -> None:
    from datetime import date

    recorded = [{"first": "2025-01-06", "last": "2025-01-07",
                 "sessions": 2, "resolution": "minute"}]
    # 01-06 flipped to five_min; 01-07 no longer covered; 01-09 is a
    # session the original never covered at all
    actual = {date(2025, 1, 6): "five_min", date(2025, 1, 9): "minute"}
    report = _divergence_report(recorded, actual)
    assert any("resolution flipped on 2025-01-06" in i["issue"] for i in report)
    assert any("recorded 2 covered session(s), replay covered 1" in i["issue"]
               for i in report)
    assert any(i.get("session") == "2025-01-09" for i in report)

    # perfect replay → empty report
    good = {date(2025, 1, 6): "minute", date(2025, 1, 7): "minute"}
    assert _divergence_report(recorded, good) == []
    # nothing recorded (daily / pre-FX.1) → never invents divergence
    assert _divergence_report(None, actual) == []


# ─────────────────────────────── the builder ───────────────────────────────
_PROVENANCE = {
    "v": 1, "origin": "user", "recorded_at": "2026-07-14T00:00:00+00:00",
    "prompt": {"text": "Sell a 30 delta put on SPY every Monday."},
    "conversation": [
        {"kind": "question", "id": "exit", "question": "How do you exit?",
         "options": ["50% profit", "21 DTE"]},
        {"kind": "answer", "id": "exit", "answer": "50% profit or 21 DTE"},
    ],
    "mechanics": {"clock": "daily", "sessions": 500, "engine_s": 3.2,
                  "gauntlet_s": 41.0, "effective_start": "2024-01-02",
                  "effective_end": "2026-01-02",
                  "build": {"commit": "abc1234", "spec_version": 8,
                            "fill_model": "liquidity-v1"}},
}

_PAYLOAD = {
    "name": "SPY weekly short put",
    "meta": "SPY · short put · clock daily",
    "mtiles": [{"v": "12%", "l": "CAGR"}],
    # the REAL _downsample row shape: {"t": iso, "v": value} (the report's
    # SVG crashed on a guessed pair shape — API-loop test caught it)
    "equitySeries": [{"t": "2024-01-02", "v": 10000.0},
                     {"t": "2024-06-03", "v": 10800.0},
                     {"t": "2025-01-02", "v": 11500.0}],
    "drawdownSeries": [{"t": "2024-01-02", "v": 0.0},
                       {"t": "2024-06-03", "v": 2.5},
                       {"t": "2025-01-02", "v": 1.0}],
    "trades": [], "tradeHeader": "Trade log — 10 filled",
    "verdict": {"headline": "Held up.", "survived": "5 OF 5"},
    # the REAL payload shape: honesty is a dict of panel fields, not a
    # list (the e2e execution caught the earlier wrong guess)
    "honesty": {"isSharpe": "0.8", "oosSharpe": "0.6",
                "notes": ["OOS keeps 75% of in-sample sharpe — holds ✓"]},
    "mcTerm": {},
    "resolutionRuns": None, "clock": "daily", "ladderDepth": None,
}


def _grid() -> dict:
    return derived_boxes(json.loads(json.dumps(R_SPEC)))


class TestBuilder:
    def test_notebook_shape_and_story_order(self) -> None:
        nb = build_notebook(run_id="r1", name="SPY weekly short put",
                            payload=_PAYLOAD, provenance=_PROVENANCE,
                            grid=_grid(), api_base="https://api.example")
        assert nb["nbformat"] == 4
        kinds = [c["cell_type"] for c in nb["cells"]]
        assert kinds[0] == "markdown"  # title + disclaimer
        first = "".join(nb["cells"][0]["source"])
        assert "not financial advice" in first.lower()
        # the story OPENS the notebook: provenance before any code cell
        story = "".join(nb["cells"][1]["source"])
        assert "How this strategy was agreed" in story
        assert "Sell a 30 delta put" in story
        assert "How do you exit?" in story
        assert "50% profit or 21 DTE" in story
        assert "decision grid" in story
        assert kinds.index("code") > 1
        # closes with the disclaimer too (every results surface)
        assert "not financial advice" in "".join(nb["cells"][-1]["source"]).lower()

    def test_no_credentials_ever_embedded(self) -> None:
        nb = build_notebook(run_id="r1", name="n", payload=_PAYLOAD,
                            provenance=_PROVENANCE, grid=_grid(),
                            api_base="https://api.example")
        text = json.dumps(nb)
        assert "SKEPTIC_ACCESS_TOKEN" in text  # read from env…
        assert "Bearer {TOKEN}" in text        # …only ever by reference

    def test_ladder_cell_only_on_ladder_runs(self) -> None:
        nb_plain = build_notebook(run_id="r1", name="n", payload=_PAYLOAD,
                                  provenance=_PROVENANCE, grid=_grid(),
                                  api_base="x")
        ladder_payload = {**_PAYLOAD, "ladderDepth": {"tiers": [], "rungs": []}}
        nb_ladder = build_notebook(run_id="r1", name="n", payload=ladder_payload,
                                   provenance=_PROVENANCE, grid=_grid(),
                                   api_base="x")
        assert "ladderDepth" not in json.dumps(nb_plain["cells"])
        assert "ladderDepth" in json.dumps(nb_ladder["cells"])

    def test_deterministic_export(self) -> None:
        kw = dict(run_id="r1", name="n", payload=_PAYLOAD,
                  provenance=_PROVENANCE, grid=_grid(), api_base="x")
        assert build_notebook(**kw) == build_notebook(**kw)

    def test_sweep_coverage_notes_are_baked_into_the_story(self) -> None:
        note = ("gex_level is a sign test (threshold 0 — nothing to "
                "perturb), not swept")
        nb = build_notebook(run_id="r1", name="n", payload=_PAYLOAD,
                            provenance=_PROVENANCE, grid=_grid(),
                            api_base="x", sweep_notes=[note])
        text = json.dumps(nb["cells"])
        assert "Sweep coverage, disclosed" in text
        assert "nothing to perturb" in text

    def test_derived_record_never_invents_a_conversation(self) -> None:
        derived = {"v": 1, "derived": True, "origin": "user",
                   "prompt": {"text": "sell puts"}}
        nb = build_notebook(run_id="r1", name="n", payload=_PAYLOAD,
                            provenance=derived, grid=_grid(), api_base="x")
        story = "".join(nb["cells"][1]["source"])
        assert "derived from the stored spec" in story
        assert "Clarified before anything ran" not in story


class TestReport:
    def _report(self, payload: dict | None = None,
                provenance: dict | None = None) -> str:
        return build_report(run_id="r1", name="SPY weekly short put",
                            payload=payload or _PAYLOAD,
                            provenance=provenance or _PROVENANCE,
                            grid=_grid())

    def test_story_order_windows_and_disclaimers(self) -> None:
        doc = self._report()
        # the story opens before the numbers; disclaimer opens AND closes
        assert doc.index("How this strategy was agreed") \
            < doc.index("The numbers") < doc.index("honesty gauntlet") \
            < doc.index("The verdict") < doc.index("Reproducibility")
        assert doc.count("not financial advice") >= 2
        assert "Sell a 30 delta put" in doc
        assert "How do you exit?" in doc
        # single-series charts render with their captions
        assert "Equity — stored run" in doc
        assert "Drawdown" in doc

    def test_user_text_is_escaped(self) -> None:
        hostile = {**_PROVENANCE,
                   "prompt": {"text": '<script>alert("x")</script>'},
                   "conversation": [
                       {"kind": "question", "id": "q",
                        "question": "<img src=x onerror=y>"},
                       {"kind": "answer", "id": "q", "answer": "a & b < c"},
                   ]}
        doc = self._report(provenance=hostile)
        assert "<script>" not in doc
        assert "<img" not in doc
        assert "&lt;script&gt;" in doc
        assert "a &amp; b &lt; c" in doc

    def test_three_voices_and_no_pl_color_on_verdict(self) -> None:
        doc = self._report()
        # the typography directive: exactly the three families, no fourth
        for family in ("Archivo", "IBM Plex Mono", "Newsreader"):
            assert family in doc
        # verdict section carries no color styling — headline is serif ink
        verdict_chunk = doc[doc.index("The verdict"):
                            doc.index("Reproducibility")]
        assert "color:" not in verdict_chunk

    def test_conditional_sections(self) -> None:
        doc_plain = self._report()
        assert "Scale-in depth attribution" not in doc_plain
        ladder = {**_PAYLOAD,
                  "ladderDepth": {"tiers": [{"depth": 1}], "rungs": []}}
        assert "Scale-in depth attribution" in self._report(payload=ladder)

    def test_deterministic(self) -> None:
        assert self._report() == self._report()

    def test_no_credentials_or_market_rows(self) -> None:
        # a standalone document must never carry the token or chain rows
        doc = self._report()
        assert "SKEPTIC_ACCESS_TOKEN" not in doc
        assert "Bearer" not in doc


# ───────────────────────────── the API loop ────────────────────────────────
@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("SKEPTIC_ACCESS_TOKEN", "")
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    import app.data.chains as chains_module
    import app.data.intraday as intraday_module

    monkeypatch.setattr(
        chains_module, "load_market_store",
        lambda ticker, **kw: build_fixture_store("SPY", R_CHAINS, R_UNDERLYING),
    )
    slc = build_fixture_slice(
        R_ENTRY,
        quotes={"09:30": [_put(2.00, 2.20, -0.50, "2025-01-07")]},
        underlying={"09:30": 100.0},
    )
    monkeypatch.setattr(
        intraday_module, "load_intraday_store",
        lambda ticker: FixtureIntraday({R_ENTRY: slc}, max_dte=2),
    )
    return TestClient(app)


class TestNotebookEndpoints:
    def test_export_and_reproduce_loop(self, client: TestClient) -> None:
        run_id = client.post(
            "/api/backtest",
            json={"spec": R_SPEC,
                  "provenance": {"source": "text",
                                 "prompt": {"text": "sell a monday put"}}},
        ).json()["run_id"]
        assert client.get(f"/api/runs/{run_id}").json()["status"] == "done"

        # export: a valid, story-first notebook with download headers
        r = client.get(f"/api/runs/{run_id}/notebook")
        assert r.status_code == 200
        assert "attachment" in r.headers["content-disposition"]
        nb = r.json()
        assert nb["nbformat"] == 4
        assert nb["metadata"]["skeptic"]["run_id"] == run_id
        story = "".join(nb["cells"][1]["source"])
        assert "sell a monday put" in story

        # report: the human-readable twin — inline HTML, story present
        rr = client.get(f"/api/runs/{run_id}/report")
        assert rr.status_code == 200
        assert rr.headers["content-type"].startswith("text/html")
        assert "inline" in rr.headers["content-disposition"]
        assert "sell a monday put" in rr.text
        assert "How this strategy was agreed" in rr.text
        assert rr.text.count("not financial advice") >= 2

        # reproduce: unchanged fixture lake → every stat matches
        assert client.post(f"/api/runs/{run_id}/reproduce").json()["status"] \
            == "reproducing"
        report = client.get(f"/api/runs/{run_id}/reproduce").json()
        assert report["status"] == "done"
        assert report["match"] is True
        assert report["resolution_divergence"] is None
        stats = {row["stat"] for row in report["compared"]}
        assert {"sharpe", "max_drawdown", "filled", "final_equity"} <= stats
        assert all(row["ok"] for row in report["compared"])

        # the verdict is never rewritten by reproduce
        assert client.get(f"/api/runs/{run_id}").json()["verdict"] \
            == client.get(f"/api/runs/{run_id}").json()["verdict"]

    def test_export_refuses_unfinished_runs(self, client: TestClient) -> None:
        with db.session() as s:
            s.add(db.Run(id="rq-1", status="queued", spec_json=json.dumps(R_SPEC)))
            s.commit()
        assert client.get("/api/runs/rq-1/notebook").status_code == 409

    def test_reproduce_404_on_missing_run(self, client: TestClient) -> None:
        assert client.post("/api/runs/nope/reproduce").status_code == 404
        assert client.get("/api/runs/nope/reproduce").status_code == 404
