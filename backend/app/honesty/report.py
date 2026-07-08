"""HonestyReport — the single output of the gauntlet (TECH-SPEC §6) and
the ONLY input the verdict writer ever sees (guardrail #4)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class OosSplit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    split_date: str
    is_sharpe: float | None
    oos_sharpe: float | None
    is_return: float | None
    oos_return: float | None
    is_trades: int
    oos_trades: int
    degradation: float | None  # oos_sharpe / is_sharpe when both computable
    sign_flip: bool
    flagged: bool  # oos < 50% of is, or sign flip (TECH-SPEC §6.1)


class WalkForwardFold(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: str
    end: str
    ret: float
    trades: int
    # FX.4 (owner decision: disclosure lives in the RUN, not only docs) —
    # the share of this fold's sessions that ran the MINUTE grid. A fold
    # whose out-performance coincides with a high minute share must be
    # readable as resolution-flavored, never silently as regime robustness.
    # None on runs without a resolution mix (bit-identical off finest).
    minute_share: float | None = None


class WalkForward(BaseModel):
    model_config = ConfigDict(extra="forbid")

    meaningful: bool
    note: str | None = None
    test_sessions: int = 0
    folds: list[WalkForwardFold] = []
    consistency: float | None = None  # fraction of profitable folds


class MonteCarlo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resamples: int
    block: int
    seed: int
    trades: int
    terminal_p5: float | None
    terminal_p50: float | None
    terminal_p95: float | None
    max_drawdown_p50: float | None
    max_drawdown_p95: float | None
    p_loss: float | None  # fraction of paths ending below initial capital
    # percentile equity paths for the fan chart (downsampled)
    fan_p5: list[float] = []
    fan_p50: list[float] = []
    fan_p95: list[float] = []


class ParamSweep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    values: list[float]
    sharpes: list[float | None]
    base_index: int
    classification: str | None  # "plateau" | "cliff" | None (not classifiable)


class Sensitivity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    params: list[ParamSweep] = []
    verdict: str | None = None  # plateau | cliff | None
    # D2d: at the 5-min clock the sweep re-runs on a bounded trailing window
    # (gauntlet cost is benchmark-bound; docs/HONESTY.md has the arithmetic).
    # Disclosed here so the verdict can say so.
    window_note: str | None = None
    # F8: which entry conditions were NOT swept and why (sign tests /
    # cost cap) — surfaced so absence is never misread as an oversight.
    conditions_note: str | None = None


class Dsr(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trials: int
    daily_sharpe: float | None
    expected_max_sharpe: float | None
    dsr: float | None  # P(true SR > 0 | N trials); < 0.5 = likely mined


class RegimeSample(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trades: int
    days_low_vix: int  # VIX < 15
    days_mid_vix: int  # 15–20
    days_high_vix: int  # > 20
    regimes_present: int  # buckets holding ≥ 10% of days
    capped: bool
    cap_reason: str | None


class Coverage(BaseModel):
    """Requested window vs. sessions that actually carried a usable chain.
    A multi-year request backed by a handful of chain dates is the
    seventeen-fills self-deception (diagnostics/SEVENTEEN.md); when coverage
    is materially short the trust level is capped at insufficient_evidence."""

    model_config = ConfigDict(extra="forbid")

    requested_start: str
    requested_end: str
    effective_start: str
    effective_end: str
    requested_sessions: int  # underlying sessions in the requested window
    chain_sessions: int  # of those, how many carried a usable options chain
    coverage_ratio: float  # chain_sessions / requested_sessions (0..1)
    materially_short: bool
    reason: str | None


class LiquidityProfile(BaseModel):
    """How real this run's fills were (D1b). Reporting only in D1 — the
    profile discloses, it does not cap trust. Every number is counted at
    fill time by the engine; unknown liquidity (missing OI) is disclosed,
    never punished."""

    model_config = ConfigDict(extra="forbid")

    mode: str  # "skip" | "stress"
    max_spread_pct: float  # gate config, percent of mid
    min_open_interest: int
    min_volume: int
    option_leg_fills: int  # entry + close leg fills priced by the model
    median_spread_pct: float | None  # fraction of mid at fill time
    penalized_share: float | None  # filled at OI-scaled slip above base
    stressed_share: float | None  # gated but filled at full adverse (stress mode)
    unknown_liquidity_share: float | None  # OI unknown at fill time
    skipped_illiquid: int  # entry candidates refused by the gates
    # F5: fill quantity vs displayed NBBO depth on the traded side —
    # REPORTED, never scored (disclosure-first; a price-impact model must
    # be earned by the D3d calibration loop). depth_known_share is of ALL
    # option-leg fills; beyond_depth_share is of the DEPTH-KNOWN ones.
    depth_known_share: float | None
    beyond_depth_share: float | None
    # raw counts as NUMERIC fields so the grounding harvester admits them —
    # the note says "15 of 228" and a verdict/Q&A echoing those numbers must
    # never be flagged ungrounded (review finding F5 #1; the WF-fold class)
    fills_depth_known: int
    fills_beyond_depth: int
    material: bool
    note: str | None


class PairConfidence(BaseModel):
    """One source-pair's agreement over THIS run's window — REPORTED,
    never scored (owner decision 2026-07-08: rates travel with their
    audited-share denominators; no blended score — weights across
    incommensurable pairs would be an invented convention wearing a
    number; trust consequences wait until accumulated history earns
    thresholds, the D3d staging). All counts are numeric fields so the
    grounding harvester admits every number the caveat quotes."""

    model_config = ConfigDict(extra="forbid")

    pair: str  # e.g. "dolthub_vs_alpaca"
    audited_sessions: int  # sessions of this run's window with a record
    window_sessions: int  # the run's total sessions (the denominator)
    joined: int  # contracts/rows present in both sources
    checked: int  # rows where a comparison was honest
    within_band: int  # rows agreeing within the documented tolerance
    agreement_rate: float | None  # within_band / checked (None if unchecked)
    worst_session_rate: float | None  # min per-session rate in the window
    worst_session: str | None  # the session that produced it


class DataConfidence(BaseModel):
    """Cross-source validation over the run's own window (F7)."""

    model_config = ConfigDict(extra="forbid")

    pairs: list[PairConfidence]
    note: str | None


class Concentration(BaseModel):
    """Is the P&L a distribution or a handful of days? (D1d). Reported flag
    + verdict reason only — promoting it to a trust cap is a future reviewed
    threshold change, not this model's job."""

    model_config = ConfigDict(extra="forbid")

    meaningful: bool
    note: str | None = None
    top_days: int = 0  # the top 5% of marked sessions by |daily P&L|
    top_share: float | None = None  # their share of gross |daily P&L|
    gamma_coincidence: float | None = None  # fraction of those days in top-decile |gamma|
    flagged: bool = False


class SessionBucket(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trades: int = 0
    wins: int = 0
    pl: float = 0.0


class ResolutionBucket(BaseModel):
    """One resolution subset of a mixed run (FX.4)."""

    model_config = ConfigDict(extra="forbid")

    sessions: int = 0
    trades: int = 0  # closed trades realized on this subset's sessions
    pl: float = 0.0
    sharpe: float | None = None
    first: str | None = None
    last: str | None = None


class ResolutionSplit(BaseModel):
    """Mixed-resolution defense (FX.4, masterplan owner decision 4a): the
    headline recomputed on the 5-MIN-ONLY sub-window from recorded returns
    and fills — cheap, no re-run. A SIGN FLIP (full-run edge positive,
    5-min-only negative) is a DATA-VALIDITY finding, not a robustness
    signal: the edge appears only on the recent minute slice and reverses
    on the resolution the deep history was tested at — a granularity
    mirage until proven otherwise → hard cap (insufficient_evidence),
    refused not weakly blessed. The cap only ARMS on real evidence
    (judged=True: both subsets ≥ 15 sessions AND the 5-min subset ≥
    MIN_TRADES closed trades); below the floors the run carries a
    "too thin to cross-check" caveat instead — disclosed, never a
    noise-cap and never a silent pass."""

    model_config = ConfigDict(extra="forbid")

    meaningful: bool
    note: str | None = None
    judged: bool = False
    full_sharpe: float | None = None
    five_min: ResolutionBucket = ResolutionBucket()
    minute: ResolutionBucket = ResolutionBucket()
    eod_fallback_sessions: int = 0  # covered by neither grid (gap days)
    sign_flip: bool = False

    @property
    def caps_trust(self) -> bool:
        return self.judged and self.sign_flip


class SessionSplit(BaseModel):
    """Where in the session the entries earn (D2d): open (09:30–10:30),
    mid (10:30–15:00), close (15:00–16:15) ET. Reported, never scored —
    a strategy whose whole edge is one hour of the day should have to
    say so out loud."""

    model_config = ConfigDict(extra="forbid")

    meaningful: bool
    note: str | None = None
    open_: SessionBucket = SessionBucket()
    mid: SessionBucket = SessionBucket()
    close: SessionBucket = SessionBucket()


class UnlockNeed(BaseModel):
    model_config = ConfigDict(extra="forbid")

    has: float
    needs: float


class UnlockConditions(BaseModel):
    """What a REFUSED verdict is waiting for (D3a) — the same numbers the
    refusal text shows, stored structured so the nightly auto-unlock scan
    can compare them against the coverage ledger instead of parsing prose.
    Only the binding constraints are present."""

    model_config = ConfigDict(extra="forbid")

    ticker: str
    clock: str
    requested_start: str
    requested_end: str
    coverage: UnlockNeed | None = None  # chain-coverage ratio of the window
    trades: UnlockNeed | None = None  # closed trades vs MIN_TRADES
    regimes: UnlockNeed | None = None  # volatility regimes present vs 2
    # coverage state when refused — the delta baseline for "N new sessions"
    sessions_at_refusal: int = 0


class LadderTier(BaseModel):
    """One basket-size tier: baskets grouped by the MAX rung depth they
    reached (D5b). The iVol P&L-by-ladder-depth table, and then some."""

    model_config = ConfigDict(extra="forbid")

    depth: int  # rungs reached = deepest fired rung index + 1
    threshold: float  # the deepest reached rung's condition value (the tier label)
    baskets: int
    wins: int
    win_rate: float | None
    contracts: int  # total contracts across baskets in this tier
    total_pl: float
    avg_pl: float
    pct_gross_profit: float | None  # share of gross profit across all baskets
    pct_gross_loss: float | None  # share of gross loss across all baskets


class LadderRung(BaseModel):
    """Marginal analysis: the P&L attributable to fills added AT this rung
    depth (not just baskets that reached it) — answers 'are the deep adds
    themselves net negative', the question that kills or saves a martingale."""

    model_config = ConfigDict(extra="forbid")

    rung_index: int
    threshold: float
    add_contracts: int  # the spec's per-fire add at this rung
    fires: int  # how many baskets fired this rung
    contracts: int  # total contracts added at this depth
    marginal_pl: float  # P&L attributable to fills at this depth (ties out to total)
    net_negative: bool


class LadderDepth(BaseModel):
    """Depth attribution for a scale-in ladder (D5b, the crown jewel).
    Present only on ladder runs; the per-tier totals AND the per-rung
    marginals each sum to `realized_total` (tie-out, tested)."""

    model_config = ConfigDict(extra="forbid")

    baskets: int  # closed baskets attributed
    realized_total: float  # sum of closed basket P&L — the tie-out anchor
    tiers: list[LadderTier]  # ordered by depth ascending
    rungs: list[LadderRung]  # ordered by rung_index
    deepest_net_negative: bool  # are the deepest-reached adds net negative?


class ScaleInHonesty(BaseModel):
    """Martingale defenses specific to a scale-in ladder (D5c). Two HARD caps
    (each refuses the run at insufficient_evidence) plus a reported
    concentration signal. This chunk LIFTS the D5a interlock: a ladder that
    clears these can be blessed like any strategy; one that trips them can't."""

    model_config = ConfigDict(extra="forbid")

    baskets: int

    # ruin-tail Monte Carlo: resample the basket P&L sequence, measure the
    # account-drawdown tail. A ladder that is fine on average but has a fat
    # ruin tail gets it surfaced AND capped.
    resamples: int
    seed: int
    max_basket_contracts: int  # the largest basket the ladder actually built
    worst_basket_loss: float | None  # most negative single-basket P&L
    ruin_threshold: float  # account-drawdown fraction that counts as ruin
    ruin_max_drawdown_p95: float | None
    ruin_max_drawdown_p99: float | None
    p_ruin: float | None  # P(resampled max drawdown > ruin_threshold)
    ruin_flagged: bool  # fat ruin tail → HARD cap

    # deep-rung dependency: recompute realized P&L WITHOUT the deepest rung's
    # fills (from the recorded marginals — cheap, no re-run). If a positive
    # edge flips negative without the deepest, riskiest adds, the edge DEPENDS
    # on them — a martingale sign-flip → HARD cap.
    deepest_threshold: float
    realized_total: float
    total_without_deepest: float
    deep_rung_sign_flip: bool  # positive edge depends on the deepest rung → HARD cap
    deep_rung_flagged: bool  # deepest rung materially moves the total (reported)

    # basket-size concentration: one deep-basket day dominating P&L is the
    # martingale tell (reported, never a cap on its own — D1d posture).
    top_basket_share: float | None  # share of gross |basket P&L| from the top basket
    concentration_flagged: bool

    caps_trust: bool  # ruin_flagged OR deep_rung_sign_flip
    reasons: list[str]  # human-readable cap/flag reasons


class Trust(BaseModel):
    model_config = ConfigDict(extra="forbid")

    level: int | None  # 1..5; None when capped
    label: str  # noise | weak | suggestive | robust | proven | insufficient_evidence
    survived: dict[str, bool]  # oos / walk_forward / monte_carlo / sensitivity / sample
    survived_count: int
    reasons: list[str]


class HonestyReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    oos: OosSplit
    walk_forward: WalkForward
    monte_carlo: MonteCarlo
    sensitivity: Sensitivity
    dsr: Dsr
    regime_sample: RegimeSample
    coverage: Coverage
    liquidity: LiquidityProfile | None = None  # None only on pre-D1b reports
    concentration: Concentration | None = None  # None only on pre-D1d reports
    session_split: SessionSplit | None = None  # 5-min clock only (D2d)
    resolution_split: ResolutionSplit | None = None  # mixed-resolution runs (FX.4)
    data_confidence: DataConfidence | None = None  # cross-source validation (F7)
    ladder_depth: LadderDepth | None = None  # scale-in runs only (D5b)
    scale_in: ScaleInHonesty | None = None  # scale-in martingale defenses (D5c)
    fill_sources: dict[str, int] = {}  # per-leg fill provenance (D2b/D2d)
    trust: Trust
    metrics: dict[str, float | None]
    effective_start: str
    effective_end: str
    seed: int
