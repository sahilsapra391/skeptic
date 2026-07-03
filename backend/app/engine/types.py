"""Engine core types. mypy runs strict on this package (CLAUDE.md)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

MULT = 100  # options contract multiplier


@dataclass(frozen=True)
class ContractKey:
    expiration: date
    right: str  # "call" | "put"
    strike: float


@dataclass(frozen=True)
class Quote:
    bid: float | None
    ask: float | None
    delta: float | None
    iv: float | None = None


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
