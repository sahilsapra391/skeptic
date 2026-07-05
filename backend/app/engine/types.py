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

    @property
    def is_credit(self) -> bool:
        return self.premium > 0


@dataclass
class TradeEvent:
    day: date
    action: str  # OPEN CLOSE EXPIRE SETTLE ASSIGN CALLED_AWAY STOCK_BUY STOCK_SELL SKIP
    detail: str
    pl: float | None = None
    reason: str | None = None
    position_id: int | None = None
    bar_time: str | None = None  # "HH:MM" ET when the event happened at a 5-min bar (D2d)


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
    # declared clock (D2b) + per-fill provenance: how many option-leg fills
    # came from each quote record (eod_chain / ivol_5min / cboe_minute)
    clock: str = "daily"
    fill_sources: dict[str, int] = field(default_factory=dict)
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
