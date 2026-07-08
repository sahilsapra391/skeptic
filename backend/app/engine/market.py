"""Point-in-time market access (guardrail #2).

`MarketStore` holds everything loaded for a run; strategy logic can only
touch it through `MarketView(store, as_of)`, whose every accessor is
hard-bounded by `as_of`. Requesting anything after `as_of` raises
`LookaheadError` — the canary test asserts this.

D2a adds the minute scale: `SessionSlice` holds ONE session's 5-minute
intraday record (bars, per-bar option quotes for the short-DTE ATM slice,
per-bar underlying) and `IntradayView(slice, bar_ts)` bounds every read by
the current bar. Bar timestamps are ET wall-clock, tz-naive, exactly as
the lake stores them. The minute canary asserts the boundary.

Stores are built by loaders (chains.py / intraday.py, fixture loaders
below); the engine itself never sees pandas or the network.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Protocol

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
    # vendor IVX / HV 30d series (decimals), 2005+ — spec-v2 filters (D1c)
    ivx_dates: list[date] = field(default_factory=list)
    ivx_30d: dict[date, float] = field(default_factory=dict)
    hv_dates: list[date] = field(default_factory=list)
    hv_30d: dict[date, float] = field(default_factory=dict)
    # IVS-derived surface signals (VOL POINTS), 2007+ — spec-v5 filters (F4)
    skew_dates: list[date] = field(default_factory=list)
    skew_25d: dict[date, float] = field(default_factory=dict)
    term_dates: list[date] = field(default_factory=list)
    term_slope: dict[date, float] = field(default_factory=dict)
    # UW dealer positioning (VENDOR UNITS — sign/rank vocabulary only),
    # 2025-07-08+ — spec-v6 filters (F1)
    gex_dates: list[date] = field(default_factory=list)
    net_gex: dict[date, float] = field(default_factory=dict)
    dex_dates: list[date] = field(default_factory=list)
    net_dex: dict[date, float] = field(default_factory=dict)
    # UW flow/sentiment/pin EOD reductions, 2026-02-24+ — spec-v7 (F2/F3).
    # market_tide is MARKET-WIDE: one series regardless of ticker.
    flow_dates: list[date] = field(default_factory=list)
    net_premium: dict[date, float] = field(default_factory=dict)
    pcr_dates: list[date] = field(default_factory=list)
    put_call_ratio: dict[date, float] = field(default_factory=dict)
    nope_dates: list[date] = field(default_factory=list)
    nope_eod: dict[date, float] = field(default_factory=dict)
    mpd_dates: list[date] = field(default_factory=list)
    max_pain_dist: dict[date, float] = field(default_factory=dict)
    tide_dates: list[date] = field(default_factory=list)
    market_tide: dict[date, float] = field(default_factory=dict)
    # Forward-record splices (2026-07-08): series name → first session served
    # by the in-house derivation instead of the frozen vendor feed. Values
    # never mix WITHIN a session; this records WHERE the convention changes
    # so runs crossing a seam disclose it (chains.py writes it; the engine's
    # data_provenance reads it). Empty = single-convention series only.
    splices: dict[str, date] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.sessions = sorted(self.sessions)
        self.chain_dates = sorted(self.chain_dates)
        self.ivx_dates = sorted(self.ivx_dates)
        self.hv_dates = sorted(self.hv_dates)
        self.skew_dates = sorted(self.skew_dates)
        self.term_dates = sorted(self.term_dates)
        self.gex_dates = sorted(self.gex_dates)
        self.dex_dates = sorted(self.dex_dates)
        self.flow_dates = sorted(self.flow_dates)
        self.pcr_dates = sorted(self.pcr_dates)
        self.nope_dates = sorted(self.nope_dates)
        self.mpd_dates = sorted(self.mpd_dates)
        self.tide_dates = sorted(self.tide_dates)
        self._closes: list[float] = [self.underlying_close[d] for d in self.sessions]


class MarketViewLike(Protocol):
    """The surface strategy logic reads through. Satisfied by MarketView
    (daily close) and the engine's BarView (one intraday bar) — the SAME
    entry/exit/fill code runs at every clock (D2 architecture)."""

    @property
    def as_of(self) -> date: ...
    @property
    def is_indicator_stamp(self) -> bool: ...
    @property
    def has_chain(self) -> bool: ...
    @property
    def fill_source(self) -> str: ...
    def chain(self) -> dict[ContractKey, Quote]: ...
    def quote(self, key: ContractKey) -> Quote | None: ...
    def close(self, d: date | None = None) -> float | None: ...
    def closes_upto(self) -> list[float]: ...
    def vix(self) -> float | None: ...
    def atm_iv_history(self) -> list[float]: ...
    def ivx_30d(self) -> float | None: ...
    def ivx_30d_history(self) -> list[float]: ...
    def hv_30d(self) -> float | None: ...
    def skew_25d(self) -> float | None: ...
    def term_structure_slope(self) -> float | None: ...
    def gex_level(self) -> float | None: ...
    def gex_history(self) -> list[float]: ...
    def dex_level(self) -> float | None: ...
    def dex_history(self) -> list[float]: ...
    def net_premium_level(self) -> float | None: ...
    def net_premium_history(self) -> list[float]: ...
    def market_tide_level(self) -> float | None: ...
    def market_tide_history(self) -> list[float]: ...
    def nope_level(self) -> float | None: ...
    def nope_history(self) -> list[float]: ...
    def put_call_ratio(self) -> float | None: ...
    def max_pain_distance_pct(self) -> float | None: ...
    # D2c: the run's rolling 5-minute underlying lasts (≤ current bar,
    # across sessions) and the session-anchored VWAP at the current bar.
    # The daily view has no bars: empty / None. Implementations return AT
    # MOST the trailing INTRADAY_LOOKBACK_BARS values — indicators never
    # read deeper, and an unbounded per-bar prefix copy is O(bars²) over a
    # run (the 2026-07-06 OOM incident).
    def intraday_closes_upto(self) -> list[float]: ...
    def intraday_vwap(self) -> float | None: ...


class IntradayProvider(Protocol):
    """Per-session access to the 5-minute record (app/data/intraday.py's
    IntradayStore in production; plain fixtures in tests)."""

    @property
    def slice_max_trading_dte(self) -> int: ...
    def sessions(self) -> list[date]: ...
    def slice_for(self, session: date) -> SessionSlice | None: ...
    # FX.1 (resolution="finest"): sessions eligible for the minute grid per
    # the F0 resolution map, and the 1-min slice itself. A provider with no
    # minute data returns empty/None — the engine falls back to 5-min and
    # RECORDS the session as five_min (honest degrade, never an error).
    def minute_sessions(self) -> set[date]: ...
    def minute_slice_for(self, session: date) -> SessionSlice | None: ...


class MarketView:
    """All reads bounded by as_of. No accessor can see past it."""

    def __init__(self, store: MarketStore, as_of: date) -> None:
        self._store = store
        self._as_of = as_of

    @property
    def as_of(self) -> date:
        return self._as_of

    @property
    def is_indicator_stamp(self) -> bool:
        # daily bars ARE the daily series' own samples
        return True

    @property
    def fill_source(self) -> str:
        return "eod_chain"  # daily fills come from the EOD chain record

    # the daily view has no intraday bars — 5min-timeframe conditions are
    # unevaluable here (and validation forbids them at clock="daily")
    def intraday_closes_upto(self) -> list[float]:
        return []

    def intraday_vwap(self) -> float | None:
        return None

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

    # ------------------------------------------------------ ivx / hv (v2)
    # bounded by construction, like vix(): the last observation ≤ as_of.
    def ivx_30d(self) -> float | None:
        idx = bisect_right(self._store.ivx_dates, self._as_of)
        if idx == 0:
            return None
        return self._store.ivx_30d[self._store.ivx_dates[idx - 1]]

    def ivx_30d_history(self) -> list[float]:
        """All IVX observations ≤ as_of, oldest → newest (rank input)."""
        idx = bisect_right(self._store.ivx_dates, self._as_of)
        return [self._store.ivx_30d[d] for d in self._store.ivx_dates[:idx]]

    def hv_30d(self) -> float | None:
        idx = bisect_right(self._store.hv_dates, self._as_of)
        if idx == 0:
            return None
        return self._store.hv_30d[self._store.hv_dates[idx - 1]]

    # F4: IVS-derived surface signals (VOL POINTS) — most recent
    # observation at or before as_of, like every daily analytic series
    def skew_25d(self) -> float | None:
        idx = bisect_right(self._store.skew_dates, self._as_of)
        if idx == 0:
            return None
        return self._store.skew_25d[self._store.skew_dates[idx - 1]]

    def term_structure_slope(self) -> float | None:
        idx = bisect_right(self._store.term_dates, self._as_of)
        if idx == 0:
            return None
        return self._store.term_slope[self._store.term_dates[idx - 1]]

    # F1: UW dealer positioning (vendor units) — most recent observation
    # at or before as_of; histories are the bounded rank inputs
    def gex_level(self) -> float | None:
        idx = bisect_right(self._store.gex_dates, self._as_of)
        if idx == 0:
            return None
        return self._store.net_gex[self._store.gex_dates[idx - 1]]

    def gex_history(self) -> list[float]:
        """All net-GEX observations ≤ as_of, oldest → newest (rank input)."""
        idx = bisect_right(self._store.gex_dates, self._as_of)
        return [self._store.net_gex[d] for d in self._store.gex_dates[:idx]]

    def dex_level(self) -> float | None:
        idx = bisect_right(self._store.dex_dates, self._as_of)
        if idx == 0:
            return None
        return self._store.net_dex[self._store.dex_dates[idx - 1]]

    def dex_history(self) -> list[float]:
        """All net-DEX observations ≤ as_of, oldest → newest (rank input)."""
        idx = bisect_right(self._store.dex_dates, self._as_of)
        return [self._store.net_dex[d] for d in self._store.dex_dates[:idx]]

    # F2/F3: flow/sentiment/pin EOD reductions — same PIT shape as every
    # daily analytic series (most recent ≤ as_of; bounded histories)
    def net_premium_level(self) -> float | None:
        idx = bisect_right(self._store.flow_dates, self._as_of)
        if idx == 0:
            return None
        return self._store.net_premium[self._store.flow_dates[idx - 1]]

    def net_premium_history(self) -> list[float]:
        idx = bisect_right(self._store.flow_dates, self._as_of)
        return [self._store.net_premium[d] for d in self._store.flow_dates[:idx]]

    def market_tide_level(self) -> float | None:
        idx = bisect_right(self._store.tide_dates, self._as_of)
        if idx == 0:
            return None
        return self._store.market_tide[self._store.tide_dates[idx - 1]]

    def market_tide_history(self) -> list[float]:
        idx = bisect_right(self._store.tide_dates, self._as_of)
        return [self._store.market_tide[d] for d in self._store.tide_dates[:idx]]

    def nope_level(self) -> float | None:
        idx = bisect_right(self._store.nope_dates, self._as_of)
        if idx == 0:
            return None
        return self._store.nope_eod[self._store.nope_dates[idx - 1]]

    def nope_history(self) -> list[float]:
        idx = bisect_right(self._store.nope_dates, self._as_of)
        return [self._store.nope_eod[d] for d in self._store.nope_dates[:idx]]

    def put_call_ratio(self) -> float | None:
        idx = bisect_right(self._store.pcr_dates, self._as_of)
        if idx == 0:
            return None
        return self._store.put_call_ratio[self._store.pcr_dates[idx - 1]]

    def max_pain_distance_pct(self) -> float | None:
        idx = bisect_right(self._store.mpd_dates, self._as_of)
        if idx == 0:
            return None
        return self._store.max_pain_dist[self._store.mpd_dates[idx - 1]]


@dataclass
class SessionSlice:
    """One session's 5-minute intraday record for the short-DTE ATM slice.

    `quote_source` is per-SESSION, not per-quote: a session is served by its
    best available source (ivol_5min: true NBBO; cboe_minute: ~15-min
    delayed, forward coverage only — owner amendment 1) and every fill made
    from it inherits that provenance. Sources are never mixed within a
    session — a blend would blur what the verdict must disclose."""

    session: date
    bars: list[datetime]  # ordered decision bars, ET wall-clock tz-naive
    quotes: dict[datetime, dict[ContractKey, Quote]]  # per-bar option slice
    underlying: dict[datetime, float]  # per-bar underlying last
    quote_source: str  # "ivol_5min" | "cboe_minute"
    # per-bar underlying volume (D2c, session-anchored VWAP input); empty
    # when the source carries none (pre-2026-07-08 CBOE snapshots) — VWAP
    # is honestly unevaluable then.
    # A bar ABSENT from a populated dict means its volume is UNKNOWN (e.g. an
    # unparseable vendor cell): the bar sits out of session VWAP — consumers
    # read it as 0 weight, never as a fabricated value.
    underlying_volume: dict[datetime, float] = field(default_factory=dict)
    # FX.1: the session's bar grid ("5min" | "1min"). Minute grids carry the
    # SAME 5-min NBBO quote stamps — bars between stamps have no chain and
    # can fill nothing (guardrail #1); they refine when the engine LOOKS.
    bar_resolution: str = "5min"
    # FX.1: on a minute grid, the 5-MIN underlying stamps — the ONLY bars
    # whose underlying value enters the rolling timeframe-"5min" indicator
    # series (identical inputs to the 5-min grid; resolution never changes
    # signal meaning). None = every underlying bar samples (the 5-min grid).
    indicator_stamps: set[datetime] | None = None

    def __post_init__(self) -> None:
        self.bars = sorted(self.bars)


class IntradayView:
    """All reads bounded by the current bar (guardrail #2 at minute scale).

    quote_at() serves ONLY the current bar — there is deliberately no
    timestamp parameter to ask for another bar's quote. History accessors
    (bars_upto / underlying_history) end at bar_ts. Anything else raises."""

    def __init__(self, slc: SessionSlice, bar_ts: datetime) -> None:
        self._slice = slc
        self._bar_ts = bar_ts

    @property
    def bar_ts(self) -> datetime:
        return self._bar_ts

    @property
    def session(self) -> date:
        return self._slice.session

    @property
    def quote_source(self) -> str:
        return self._slice.quote_source

    def _check(self, ts: datetime) -> None:
        if ts > self._bar_ts:
            raise LookaheadError(
                f"bar {self._bar_ts} attempted to read {ts} — lookahead is banned"
            )

    def chain(self) -> dict[ContractKey, Quote]:
        """The current bar's option slice (empty when the bar carried none)."""
        return self._slice.quotes.get(self._bar_ts, {})

    def quote_at(self, key: ContractKey) -> tuple[Quote, str] | None:
        """(quote, fill_source) at the CURRENT bar, or None — a gap is a gap
        (no synthetic quotes in D2; owner decision)."""
        q = self._slice.quotes.get(self._bar_ts, {}).get(key)
        if q is None:
            return None
        return q, self._slice.quote_source

    def underlying_last(self, ts: datetime | None = None) -> float | None:
        ts = ts or self._bar_ts
        self._check(ts)
        return self._slice.underlying.get(ts)

    def bars_upto(self) -> list[datetime]:
        """Decision bars of this session ≤ the current bar."""
        idx = bisect_right(self._slice.bars, self._bar_ts)
        return self._slice.bars[:idx]

    def underlying_history(self) -> list[float]:
        """Underlying lasts at bars ≤ the current bar (intraday indicator
        input, D2c). Bars without an underlying print are skipped — never
        interpolated."""
        out: list[float] = []
        for b in self.bars_upto():
            v = self._slice.underlying.get(b)
            if v is not None:
                out.append(v)
        return out


def build_fixture_slice(
    session: str,
    quotes: dict[str, list[dict[str, object]]],
    underlying: dict[str, float],
    quote_source: str = "ivol_5min",
    volumes: dict[str, float] | None = None,
    bar_resolution: str = "5min",
    indicator_stamps: list[str] | None = None,
) -> SessionSlice:
    """Fixture loader: {'HH:MM': rows} → SessionSlice (same shape the real
    loader produces). Bar keys are ET wall-clock times within `session`.
    On a 1min grid, indicator_stamps defaults to the %5-minute underlying
    bars (the fixture stand-in for the real loader's 5-min-frame stamps)."""

    def _ts(hhmm: str) -> datetime:
        d = date.fromisoformat(session)
        h, m = hhmm.split(":")
        return datetime(d.year, d.month, d.day, int(h), int(m))

    def _f(row: dict[str, object], k: str) -> float | None:
        v = row.get(k)
        return None if v is None else float(v)  # type: ignore[arg-type]

    quote_map: dict[datetime, dict[ContractKey, Quote]] = {}
    for hhmm, rows in quotes.items():
        per: dict[ContractKey, Quote] = {}
        for row in rows:
            key = ContractKey(
                expiration=date.fromisoformat(str(row["expiration"])),
                right=str(row["right"]),
                strike=float(row["strike"]),  # type: ignore[arg-type]
            )
            per[key] = Quote(
                bid=_f(row, "bid"),
                ask=_f(row, "ask"),
                delta=_f(row, "delta"),
                iv=_f(row, "iv"),
                gamma=_f(row, "gamma"),
                theta=_f(row, "theta"),
                vega=_f(row, "vega"),
                volume=None if row.get("volume") is None
                else int(row["volume"]),  # type: ignore[call-overload]
                greeks_source="vendor",
                bid_size=None if row.get("bid_size") is None
                else int(row["bid_size"]),  # type: ignore[call-overload]
                ask_size=None if row.get("ask_size") is None
                else int(row["ask_size"]),  # type: ignore[call-overload]
            )
        quote_map[_ts(hhmm)] = per

    bars = sorted(set(quote_map) | {_ts(k) for k in underlying})
    stamps: set[datetime] | None = None
    if indicator_stamps is not None:
        stamps = {_ts(k) for k in indicator_stamps}
    elif bar_resolution == "1min":
        stamps = {_ts(k) for k in underlying if int(k.split(":")[1]) % 5 == 0}
    return SessionSlice(
        session=date.fromisoformat(session),
        bars=bars,
        quotes=quote_map,
        underlying={_ts(k): float(v) for k, v in underlying.items()},
        underlying_volume={_ts(k): float(v) for k, v in (volumes or {}).items()},
        quote_source=quote_source,
        bar_resolution=bar_resolution,
        indicator_stamps=stamps,
    )


def build_fixture_store(
    ticker: str,
    chains: dict[str, list[dict[str, object]]],
    underlying: dict[str, tuple[float, float]],
    vix: dict[str, float] | None = None,
    ivx_30d: dict[str, float] | None = None,
    hv_30d: dict[str, float] | None = None,
    skew_25d: dict[str, float] | None = None,
    term_slope: dict[str, float] | None = None,
    net_gex: dict[str, float] | None = None,
    net_dex: dict[str, float] | None = None,
    net_premium: dict[str, float] | None = None,
    put_call_ratio: dict[str, float] | None = None,
    nope_eod: dict[str, float] | None = None,
    max_pain_dist: dict[str, float] | None = None,
    market_tide: dict[str, float] | None = None,
) -> MarketStore:
    """Fixture loader: plain dicts → MarketStore (same shape the real
    loader produces, so fixtures exercise the identical engine path)."""
    def _f(row: dict[str, object], k: str) -> float | None:
        v = row.get(k)
        return None if v is None else float(v)  # type: ignore[arg-type]

    def _i(row: dict[str, object], k: str) -> int | None:
        v = row.get(k)
        return None if v is None else int(v)  # type: ignore[call-overload]

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
                bid=_f(row, "bid"),
                ask=_f(row, "ask"),
                delta=_f(row, "delta"),
                iv=_f(row, "iv"),
                gamma=_f(row, "gamma"),
                theta=_f(row, "theta"),
                vega=_f(row, "vega"),
                rho=_f(row, "rho"),
                volume=_i(row, "volume"),
                open_interest=_i(row, "open_interest"),
                last=_f(row, "last"),
                greeks_source=None if row.get("greeks_source") is None
                else str(row["greeks_source"]),
            )
        chain_map[d] = per

    sessions = sorted(date.fromisoformat(k) for k in underlying)
    vix = vix or {}
    vix_map = {date.fromisoformat(k): v for k, v in vix.items()}
    ivx_map = {date.fromisoformat(k): v for k, v in (ivx_30d or {}).items()}
    hv_map = {date.fromisoformat(k): v for k, v in (hv_30d or {}).items()}
    skew_map = {date.fromisoformat(k): v for k, v in (skew_25d or {}).items()}
    term_map = {date.fromisoformat(k): v for k, v in (term_slope or {}).items()}
    gex_map = {date.fromisoformat(k): v for k, v in (net_gex or {}).items()}
    dex_map = {date.fromisoformat(k): v for k, v in (net_dex or {}).items()}
    flow_map = {date.fromisoformat(k): v for k, v in (net_premium or {}).items()}
    pcr_map = {date.fromisoformat(k): v for k, v in (put_call_ratio or {}).items()}
    nope_map = {date.fromisoformat(k): v for k, v in (nope_eod or {}).items()}
    mpd_map = {date.fromisoformat(k): v for k, v in (max_pain_dist or {}).items()}
    tide_map = {date.fromisoformat(k): v for k, v in (market_tide or {}).items()}
    return MarketStore(
        ticker=ticker,
        sessions=sessions,
        underlying_open={date.fromisoformat(k): v[0] for k, v in underlying.items()},
        underlying_close={date.fromisoformat(k): v[1] for k, v in underlying.items()},
        chains=chain_map,
        chain_dates=sorted(chain_map),
        vix_dates=sorted(vix_map),
        vix_close=vix_map,
        ivx_dates=sorted(ivx_map),
        ivx_30d=ivx_map,
        hv_dates=sorted(hv_map),
        hv_30d=hv_map,
        skew_dates=sorted(skew_map),
        skew_25d=skew_map,
        term_dates=sorted(term_map),
        term_slope=term_map,
        gex_dates=sorted(gex_map),
        net_gex=gex_map,
        dex_dates=sorted(dex_map),
        net_dex=dex_map,
        flow_dates=sorted(flow_map),
        net_premium=flow_map,
        pcr_dates=sorted(pcr_map),
        put_call_ratio=pcr_map,
        nope_dates=sorted(nope_map),
        nope_eod=nope_map,
        mpd_dates=sorted(mpd_map),
        max_pain_dist=mpd_map,
        tide_dates=sorted(tide_map),
        market_tide=tide_map,
    )
