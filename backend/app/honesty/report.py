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
    material: bool
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
    ladder_depth: LadderDepth | None = None  # scale-in runs only (D5b)
    fill_sources: dict[str, int] = {}  # per-leg fill provenance (D2b/D2d)
    trust: Trust
    metrics: dict[str, float | None]
    effective_start: str
    effective_end: str
    seed: int
