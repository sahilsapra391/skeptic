"""Point-in-time market access (guardrail #2).

`MarketStore` holds everything loaded for a run; strategy logic can only
touch it through `MarketView(store, as_of)`, whose every accessor is
hard-bounded by `as_of`. Requesting anything after `as_of` raises
`LookaheadError` — the canary test asserts this.

Stores are built by loaders (real R2 loader in `load.py`, fixture loader
below); the engine itself never sees pandas or the network.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field
from datetime import date

from app.engine.types import ContractKey, Quote


class LookaheadError(RuntimeError):
    """A simulation at date T tried to read data after T."""


@dataclass
class MarketStore:
    ticker: str
    sessions: list[date]  # ordered underlying sessions
    underlying_open: dict[date, float]
    underlying_close: dict[date, float]
    chains: dict[date, dict[ContractKey, Quote]]
    chain_dates: list[date]  # ordered
    vix_dates: list[date] = field(default_factory=list)
    vix_close: dict[date, float] = field(default_factory=dict)
    atm_iv: dict[date, float] = field(default_factory=dict)  # per chain session

    def __post_init__(self) -> None:
        self.sessions = sorted(self.sessions)
        self.chain_dates = sorted(self.chain_dates)
        self._closes: list[float] = [self.underlying_close[d] for d in self.sessions]


class MarketView:
    """All reads bounded by as_of. No accessor can see past it."""

    def __init__(self, store: MarketStore, as_of: date) -> None:
        self._store = store
        self._as_of = as_of

    @property
    def as_of(self) -> date:
        return self._as_of

    def _check(self, d: date) -> None:
        if d > self._as_of:
            raise LookaheadError(
                f"simulation at {self._as_of} attempted to read {d} — lookahead is banned"
            )

    # ------------------------------------------------------------- chains
    @property
    def has_chain(self) -> bool:
        return self._as_of in self._store.chains

    def chain(self) -> dict[ContractKey, Quote]:
        return self._store.chains.get(self._as_of, {})

    def quote(self, key: ContractKey) -> Quote | None:
        return self._store.chains.get(self._as_of, {}).get(key)

    # --------------------------------------------------------- underlying
    def close(self, d: date | None = None) -> float | None:
        d = d or self._as_of
        self._check(d)
        return self._store.underlying_close.get(d)

    def open_price(self, d: date | None = None) -> float | None:
        d = d or self._as_of
        self._check(d)
        return self._store.underlying_open.get(d)

    def closes_upto(self) -> list[float]:
        """Trailing closes ≤ as_of (full loaded history — legitimately
        observable; indicator warmup predating the sim window is fine)."""
        idx = bisect_right(self._store.sessions, self._as_of)
        return self._store._closes[:idx]

    # ---------------------------------------------------------------- vix
    def vix(self) -> float | None:
        idx = bisect_right(self._store.vix_dates, self._as_of)
        if idx == 0:
            return None
        return self._store.vix_close[self._store.vix_dates[idx - 1]]

    # ------------------------------------------------------------ atm iv
    def atm_iv_history(self) -> list[float]:
        idx = bisect_right(self._store.chain_dates, self._as_of)
        out: list[float] = []
        for d in self._store.chain_dates[:idx]:
            v = self._store.atm_iv.get(d)
            if v is not None:
                out.append(v)
        return out


def build_fixture_store(
    ticker: str,
    chains: dict[str, list[dict[str, object]]],
    underlying: dict[str, tuple[float, float]],
    vix: dict[str, float] | None = None,
) -> MarketStore:
    """Fixture loader: plain dicts → MarketStore (same shape the real
    loader produces, so fixtures exercise the identical engine path)."""
    chain_map: dict[date, dict[ContractKey, Quote]] = {}
    for ds, rows in chains.items():
        d = date.fromisoformat(ds)
        per: dict[ContractKey, Quote] = {}
        for row in rows:
            key = ContractKey(
                expiration=date.fromisoformat(str(row["expiration"])),
                right=str(row["right"]),
                strike=float(row["strike"]),  # type: ignore[arg-type]
            )
            per[key] = Quote(
                bid=None if row.get("bid") is None else float(row["bid"]),  # type: ignore[arg-type]
                ask=None if row.get("ask") is None else float(row["ask"]),  # type: ignore[arg-type]
                delta=None if row.get("delta") is None else float(row["delta"]),  # type: ignore[arg-type]
                iv=None if row.get("iv") is None else float(row["iv"]),  # type: ignore[arg-type]
            )
        chain_map[d] = per

    sessions = sorted(date.fromisoformat(k) for k in underlying)
    vix = vix or {}
    vix_map = {date.fromisoformat(k): v for k, v in vix.items()}
    return MarketStore(
        ticker=ticker,
        sessions=sessions,
        underlying_open={date.fromisoformat(k): v[0] for k, v in underlying.items()},
        underlying_close={date.fromisoformat(k): v[1] for k, v in underlying.items()},
        chains=chain_map,
        chain_dates=sorted(chain_map),
        vix_dates=sorted(vix_map),
        vix_close=vix_map,
    )
