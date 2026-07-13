"""Fill-model calibration (D3d) — hand-computed.

The measured quantity is the engine's own slip parameter: the fraction of
the half-spread a real print conceded from mid toward the adverse quote.
Fixtures use quote 2.00/2.10 → mid 2.05, half-spread 0.05, so slips are
exact by hand. The per-print side comes from the tape's `tags` token
(review 2026-07-13: the *_vol columns are cumulative contract-day
counters — building sides from them mislabeled a third of prints and
excluded 98% of a session as "multi-leg"). Exclusions are counted, never
silently dropped; a session with nothing measurable still banks its
accounting as a context-only row.
"""

from __future__ import annotations

import pandas as pd

from app.data.fill_calibration import (
    BIN_COLUMNS,
    _quantile_from_bins,
    calibrate_session,
    pooled_summary,
)


def _print_row(price: float, size: int = 1, tags: str = "{ask_side,etf}",
               bid: float = 2.00, ask: float = 2.10) -> dict:
    # tape columns are strings in the real parquet — fixtures match that
    return {"price": str(price), "size": str(size), "nbbo_bid": str(bid),
            "nbbo_ask": str(ask), "tags": tags}


class TestCalibrateSession:
    def test_hand_computed_slips_and_bins(self) -> None:
        prints = pd.DataFrame([
            # ask-side, size 1: slip = (price − 2.05) / 0.05
            _print_row(2.05),           # slip 0.0  → b_le0 (mid-or-better)
            _print_row(2.06),           # slip 0.2  → b_025
            _print_row(2.10),           # slip 1.0  → b_100 (at the touch)
            _print_row(2.12),           # slip 1.4  → b_150 (beyond touch)
            # bid-side, size 60: slip = (2.05 − price) / 0.05
            _print_row(2.01, size=60, tags="{bid_side,bearish,etf}"),
        ])
        rows = calibrate_session(prints)
        assert rows is not None
        by_key = {(r["side"], r["size_bucket"]): r for r in rows}
        ask = by_key[("ask", "1")]
        assert ask["n"] == 4
        assert (ask["b_le0"], ask["b_025"], ask["b_100"], ask["b_150"]) == (1, 1, 1, 1)
        assert ask["slip_median"] == round((0.2 + 1.0) / 2, 4)  # 0.6
        bid = by_key[("bid", "51-250")]
        # slip 0.8 ∈ (0.75, 1.0] — bins are named by their UPPER edge
        assert bid["n"] == 1 and bid["b_100"] == 1
        assert bid["slip_median"] == 0.8

    def test_exclusions_are_counted_never_binned(self) -> None:
        prints = pd.DataFrame([
            _print_row(2.05),                                   # measurable
            _print_row(2.05, tags="{mid_side,neutral,etf}"),    # mid print
            _print_row(2.05, tags="{no_side,etf}"),             # no aggressor
            _print_row(2.05, bid=2.10, ask=2.10),               # locked
            _print_row(2.05, bid=2.20, ask=2.10),               # crossed
            {"price": "", "size": "1", "nbbo_bid": "2.00",
             "nbbo_ask": "2.10", "tags": "{ask_side}"},         # unparseable
        ])
        rows = calibrate_session(prints)
        assert rows is not None and len(rows) == 1
        r = rows[0]
        assert r["n"] == 1
        assert r["tape_prints"] == 6
        assert r["n_unparseable"] == 1 and r["n_bad_quote"] == 2
        assert r["n_mid_side"] == 1 and r["n_no_side"] == 1
        assert sum(r[c] for c in BIN_COLUMNS) == 1

    def test_nothing_measurable_still_banks_accounting(self) -> None:
        # every print excluded → a context-only row, NOT None: the frozen
        # tape session must never be re-read and its exclusions must bank
        prints = pd.DataFrame([
            _print_row(2.05, tags="{mid_side,etf}"),
            _print_row(2.05, bid=2.20, ask=2.10),
        ])
        rows = calibrate_session(prints)
        assert rows is not None and len(rows) == 1
        r = rows[0]
        assert r["side"] == "none" and r["n"] == 0
        assert r["n_mid_side"] == 1 and r["n_bad_quote"] == 1
        assert sum(r[c] for c in BIN_COLUMNS) == 0

    def test_unrecognized_shape_is_none(self) -> None:
        assert calibrate_session(pd.DataFrame({"x": [1]})) is None
        assert calibrate_session(pd.DataFrame()) is None
        # tags is REQUIRED — without it no side is knowable
        assert calibrate_session(pd.DataFrame(
            [{"price": "2.05", "size": "1", "nbbo_bid": "2.00",
              "nbbo_ask": "2.10"}])) is None


class TestPooledSummary:
    def _row(self, d: str, **over) -> dict:
        row = {"date": d, "side": "ask", "size_bucket": "1", "n": 2,
               "b_le0": 1, "b_025": 0, "b_050": 0, "b_075": 0,
               "b_100": 1, "b_150": 0, "b_gt150": 0, "slip_median": 0.5,
               "tape_prints": 10, "n_unparseable": 0, "n_bad_quote": 0,
               "n_mid_side": 2, "n_no_side": 0}
        row.update(over)
        return row

    def test_bins_sum_and_quantiles_estimate(self) -> None:
        # two sessions of ask/1: bins sum to (2,0,0,0,2,0,0) over n=4 —
        # p50's target 2.0 is reached inside b_le0 → clamps to 0.0; p75
        # (target 3.0) lands halfway through b_100 → 0.75 + 0.5·0.25
        out = pooled_summary(pd.DataFrame(
            [self._row("2026-07-01"), self._row("2026-07-02")]))
        assert out is not None
        assert out["sessions"] == 2 and out["first"] == "2026-07-01"
        b = out["buckets"][0]
        assert b["prints"] == 4
        assert b["slip_p50_est"] == 0.0
        assert b["slip_p75_est"] == round(0.75 + 0.5 * 0.25, 4)
        assert b["share_mid_or_better"] == 0.5
        assert b["share_beyond_touch"] == 0.0
        assert out["excluded"]["n_mid_side"] == 4  # summed once per date
        assert out["tape_prints"] == 20

    def test_context_only_rows_add_accounting_but_no_bucket(self) -> None:
        rows = [self._row("2026-07-01"),
                self._row("2026-07-02", side="none", size_bucket="none",
                          n=0, b_le0=0, b_100=0, slip_median=None,
                          n_bad_quote=7)]
        out = pooled_summary(pd.DataFrame(rows))
        assert out is not None
        assert out["sessions"] == 2
        assert len(out["buckets"]) == 1  # the n=0 row never becomes a bucket
        assert out["excluded"]["n_bad_quote"] == 7

    def test_unrecognized_shape_is_none(self) -> None:
        assert pooled_summary(pd.DataFrame()) is None
        assert pooled_summary(None) is None
        assert pooled_summary(pd.DataFrame({"side": ["ask"]})) is None


class TestQuantileFromBins:
    def test_open_bins_clamp_to_finite_edges(self) -> None:
        assert _quantile_from_bins([4, 0, 0, 0, 0, 0, 0], 0.5) == 0.0
        assert _quantile_from_bins([0, 0, 0, 0, 0, 0, 4], 0.5) == 1.5

    def test_linear_within_a_bin(self) -> None:
        # all 10 prints in (0.25, 0.5]: p50 target = 5 → frac 0.5 → 0.375
        assert _quantile_from_bins([0, 0, 10, 0, 0, 0, 0], 0.5) == 0.375

    def test_empty_is_none(self) -> None:
        assert _quantile_from_bins([0] * 7, 0.5) is None
