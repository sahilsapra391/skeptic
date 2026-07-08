"""Engine core types. mypy runs strict on this package (CLAUDE.md)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

MULT = 100  # options contract multiplier


@dataclass(frozen=True, slots=True)
class ContractKey:
    expiration: date
    right: str  # "call" | "put"
    strike: float


@dataclass(frozen=True, slots=True)
class Quote:
    """One contract's quote row. A full SPY load holds ~5M of these, so the
    class uses slots and every field beyond the big four is optional.

    Greek unit conventions match the lake's vendor greeks (verified against
    DoltHub vendor rows, aggregate-diff probe 2026-07-05): theta is per
    calendar DAY, vega per 1 vol POINT (1%), rho per 1% rate move."""

    bid: float | None
    ask: float | None
    delta: float | None
    iv: float | None = None
    gamma: float | None = None
    theta: float | None = None  # $/share per calendar day
    vega: float | None = None  # $/share per 1 vol point
    rho: float | None = None  # $/share per 1% rate move
    volume: int | None = None
    open_interest: int | None = None
    last: float | None = None
    greeks_source: str | None = None  # "vendor" | "computed" | None
    # F5: displayed NBBO depth in CONTRACTS (iVol 5-min record only; EOD
    # rows carry None). Read for fill-vs-depth DISCLOSURE — never pricing
    # (owner decision 2026-07-07: disclose first, a price-impact model must
    # be EARNED by the D3d calibration loop later)
    bid_size: int | None = None
    ask_size: int | None = None


@dataclass
class OpenLeg:
    key: ContractKey
    side: str  # "long" | "short"
    qty: int  # contracts = position contracts × leg ratio
    entry_price: float  # per-share fill
    last_mark: float  # per-share liquidation mark, refreshed when quoted
    settled: bool = False  # resolved at expiration/close


@dataclass
class Position:
    pid: int
    structure: str
    legs: list[OpenLeg]
    contracts: int
    opened: date
    premium: float  # per-share net premium per contract-set: >0 credit, <0 debit
    cash_flow: float = 0.0  # realized dollars incl. commissions and stock flows
    stock_shares: int = 0
    stock_basis: float = 0.0
    pending_stock: int = 0  # shares to unwind at next session OPEN (+sell / −buy-to-cover)
    closed: bool = False
    # scale-in basket bookkeeping (D5a). A basket is ONE position whose single
    # leg's qty accumulates across rungs; `premium` stays the BLENDED per-share
    # cost, recomputed on each add, so the existing exit math (profit_pct =
    # (premium + liq)/|premium|) reduces to value/cost − 1 on the whole basket.
    scale_in: bool = False
    fired_rungs: set[int] = field(default_factory=set)  # rung indices already filled
    basket_cost: float = 0.0  # total premium $ paid across adds (excl commission)
    # FX.3: a LATCHED exit — the trigger was OBSERVED (e.g. a condition
    # touch at a quote-less minute bar) and the close must complete at the
    # first quoted bar that can fill it, without re-evaluation: a seen
    # touch counts (worse-path rule; fade-cancel would be optimism). The
    # trigger bar is disclosed on the CLOSE event. finest-mode only.
    exit_latched: str | None = None  # the exit reason to complete
    latched_bar: str | None = None  # HH:MM of the observed trigger
    latched_day: date | None = None  # trigger session (dated disclosure
    # when the fill lands on a LATER session — overnight/gap carry)

    @property
    def is_credit(self) -> bool:
        return self.premium > 0


@dataclass
class TradeEvent:
    day: date
    # OPEN CLOSE EXPIRE SETTLE ASSIGN CALLED_AWAY STOCK_BUY STOCK_SELL SKIP
    # ADD — a scale-in rung fill AFTER the basket-opening bar (D5a); ADDs are
    # NOT counted as trades (result.filled counts OPEN only), so a ladder can
    # never inflate its way to the 15-trade bar.
    action: str
    detail: str
    pl: float | None = None
    reason: str | None = None
    position_id: int | None = None
    bar_time: str | None = None  # "HH:MM" ET when the event happened at a 5-min bar (D2d)


@dataclass(frozen=True, slots=True)
class RungFill:
    """One scale-in rung fill (D5a) — the raw material D5b attributes P&L on.
    Records which rung, at what depth (threshold), how many contracts actually
    filled (post-clamp), and the real ask-side price/provenance."""

    basket_pid: int
    day: date
    bar_time: str | None  # "HH:MM" ET, None on the daily clock
    rung_index: int  # position in scale_in.rungs — the idempotency identity
    threshold: float  # the rung condition's value (the depth tier for D5b)
    qty: int  # contracts actually added (after any cap clamp)
    fill_price: float  # per-share ask-side fill
    fill_source: str
    cap_clamped: bool = False  # this rung was clamped to the remaining capacity


@dataclass
class RunResult:
    ticker: str
    effective_start: date
    effective_end: date
    seed: int
    dates: list[date] = field(default_factory=list)
    equity: list[float] = field(default_factory=list)
    trades: list[TradeEvent] = field(default_factory=list)
    filled: int = 0
    skipped: int = 0
    metrics: dict[str, float | None] = field(default_factory=dict)
    sessions_with_chain: int = 0
    days_in_market: int = 0
    # liquidity bookkeeping (D1b) — per option-LEG fill, entries and closes
    fill_spread_pcts: list[float] = field(default_factory=list)  # fraction of mid
    option_leg_fills: int = 0
    fills_penalized: int = 0  # filled at OI-scaled slip above base
    fills_stressed: int = 0  # gated contracts filled at slip 1.0 (stress mode)
    fills_unknown_liquidity: int = 0  # open interest unknown at fill time
    # F5: fill quantity vs displayed NBBO depth on the traded side —
    # disclosure only, prices unchanged (beyond-L1 liquidity exists; a
    # hard gate would be pessimistic in a way reality isn't — FX.2 doctrine)
    fills_depth_known: int = 0  # fills where the traded side's size was known
    fills_beyond_depth: int = 0  # fill qty exceeded the displayed size
    # declared clock (D2b) + per-fill provenance: how many option-leg fills
    # came from each quote record (eod_chain / ivol_5min / cboe_minute)
    clock: str = "daily"
    fill_sources: dict[str, int] = field(default_factory=dict)
    # FX.1: per-session bar-resolution record (owner decisions 2-4): which
    # policy ran, how many covered sessions stepped each grid, and the
    # compressed per-session timeline (consecutive same-resolution runs) —
    # the receipts loop and FX.4's mixed-resolution honesty read from here.
    # A re-run that changed because minute data newly arrived must be
    # explained as a RESOLUTION UPGRADE, never a silent shift.
    resolution_mode: str | None = None  # None (pre-v4) | "5min" | "finest"
    resolution_mix: dict[str, int] = field(default_factory=dict)
    resolution_runs: list[dict[str, object]] = field(default_factory=list)
    # FX.4: the full per-session record, in-process only (the payload keeps
    # the compressed runs) — the resolution_split stage and walk-forward
    # fold annotation read per-session labels from here.
    resolution_by_session: dict[date, str] = field(default_factory=dict)
    # FX.2: every skipped entry attempt counted by reason — the honest
    # denominator behind "maximum honest fills" (the trade log stays deduped)
    skip_reasons: dict[str, int] = field(default_factory=dict)
    # portfolio greeks per marked session (D1d), aligned with `dates`.
    # Units: delta/gamma in share-equivalents (greek × qty × 100, stock at
    # 1Δ/share), theta in $/day, vega in $/vol-point. A flat book is 0.0;
    # a day where any open leg lacks that greek is None — an honest gap,
    # never an interpolation.
    portfolio_delta: list[float | None] = field(default_factory=list)
    portfolio_gamma: list[float | None] = field(default_factory=list)
    portfolio_theta: list[float | None] = field(default_factory=list)
    portfolio_vega: list[float | None] = field(default_factory=list)
    # requested window (what the user asked for) vs the effective window
    # (what coverage allowed) — the gap is the seventeen-fills disclosure
    requested_start: date | None = None
    requested_end: date | None = None
    requested_sessions: int = 0
    # scale-in per-rung fills (D5a), flat across baskets (group by basket_pid).
    # The depth-attribution stage (D5b) is built entirely from these.
    rung_fills: list[RungFill] = field(default_factory=list)
