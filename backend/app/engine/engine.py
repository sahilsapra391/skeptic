"""ONE event-driven engine with a declared clock (TECH-SPEC §5 + D2b).

Session order of operations (every clock):
  1. unwind assignment stock at today's OPEN (scheduled yesterday)
  2. decisions —
       clock="daily": exits at today's close quotes, then entries
       clock="5min":  per 5-minute bar, exits THEN entries (so a position
       opened at bar t is first exit-evaluated at bar t+1 — owner
       amendment 2: a stop can never fire on its own entry bar)
  3. expiration settlement at the close (0DTE settles same session)
  4. mark-to-market at conservative liquidation prices → ONE equity point
     per session at every clock (owner decision: daily-close equity keeps
     every honesty stage's semantics)

Exit priority, canonical at every clock (owner amendment 3): stop_loss →
delta_stop → profit_target → theta_harvest → time_exit → condition exits.
DTE basis: calendar days at clock="daily" (v1 semantics, bit-identical —
the pinned regression proves it); TRADING days at clock="5min".

Positions are marked and exited with the SAME fill model used to open
them (guardrail #1), from REAL quote records only — every leg fill logs
its provenance (fill_sources). Sessions without quotes mark stale and
cannot fill exits — honest behavior on checkpoint-marked history; the
settlement path still works because it uses underlying closes.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from app.engine import fills
from app.engine.conditions import INTRADAY_LOOKBACK_BARS, all_conditions_pass
from app.engine.market import (
    IntradayProvider,
    IntradayView,
    MarketStore,
    MarketView,
    MarketViewLike,
)
from app.engine.selection import select_expiration, select_legs
from app.engine.types import (
    MULT,
    RES_FIVE_MIN,
    RES_MINUTE,
    ContractKey,
    OpenLeg,
    Position,
    Quote,
    RungFill,
    RunResult,
    TradeEvent,
)
from app.models.spec import (
    Clock,
    Condition,
    Costs,
    Frequency,
    Indicator,
    IntradayScan,
    LiquidityMode,
    Resolution,
    ScaleIn,
    Side,
    SizingMethod,
    StrategySpec,
    Structure,
)

DteFn = Callable[[date], int]


class SliceCoverageError(ValueError):
    """The spec needs more than the intraday record covers — refused BEFORE
    running (owner amendment 4): a plain reason beats a zero-fill grind."""


# FX.2: continuous scanning removes the one-entry-per-session bound, so a
# pathological spec (e.g. an always-true condition exit cycling every bar)
# could mint hundreds of thousands of positions across a full-history run —
# unbounded payloads and hours of CPU inside the serialized engine lane.
# A run that hits this cap is REFUSED loudly (never silently truncated).
MAX_RUN_FILLS = 20_000


class RunFillCapError(RuntimeError):
    """The run opened more positions than the engine supports."""


@dataclass
class _State:
    cash: float
    positions: list[Position]
    trades: list[TradeEvent]
    next_pid: int = 1
    last_entry_month: tuple[int, int] | None = None
    # liquidity bookkeeping (D1b) — one record per option-LEG fill
    fill_spread_pcts: list[float] = field(default_factory=list)
    option_leg_fills: int = 0
    fills_penalized: int = 0
    fills_stressed: int = 0
    fills_unknown_liquidity: int = 0
    # F5: fill qty vs displayed NBBO depth on the traded side (disclosure)
    fills_depth_known: int = 0
    fills_beyond_depth: int = 0
    # F7: structured per-leg fills for the on-demand audit (pid joins the
    # trade log's bar_time); bounded by MAX_RUN_FILLS × legs
    fill_log: list[dict[str, Any]] = field(default_factory=list)
    fill_sources: dict[str, int] = field(default_factory=dict)  # provenance (D2b)
    rung_fills: list[RungFill] = field(default_factory=list)  # scale-in adds (D5a)
    # FX.2: every skip COUNTED with its reason (the trade log stays deduped;
    # the counts are the honest denominator behind "maximum honest fills")
    skip_counts: dict[str, int] = field(default_factory=dict)
    # FX.2 (review finding): the LIVE book — every hot per-bar path iterates
    # this, never the full historical positions list. Scanning makes position
    # count scale with BARS (a cycler can open thousands per run); iterating
    # history per bar would go quadratic (the OOM-guard directive). Swept
    # lazily (O(open)) once per bar / per daily session.
    live: list[Position] = field(default_factory=list)
    opens: int = 0  # total positions opened (the run fill-cap counter)


@dataclass
class _BasketState:
    """The scale-in ladder's live state (D5a). `basket` is the one active
    accumulating position (or None); `armed` gates whether a fresh basket may
    open — after a basket closes it is False until the rearm signal passes, so
    the ladder can't loop forever. At the 5-min clock a flat ladder re-arms at
    each covered session (a new oversold episode per day, matching how a human
    runs this); a basket carried overnight keeps accumulating."""

    armed: bool = True
    basket: Position | None = None


def _record_leg_fill(state: _State, q: Quote, eff_slip: float, base_slip: float,
                     stressed: bool, source: str, action: str,
                     qty: int) -> str | None:
    """Per-leg fill bookkeeping. Returns an F5 depth note ("qty 20 > ask
    size 3") when the fill quantity exceeded the traded side's displayed
    NBBO size — DISCLOSURE only, the price is untouched (owner decision
    2026-07-07: beyond-L1 liquidity exists; a model must be earned by
    calibration, a hard gate would be pessimism reality doesn't show)."""
    state.option_leg_fills += 1
    state.fill_sources[source] = state.fill_sources.get(source, 0) + 1
    sp = fills.spread_pct(q)
    if sp is not None:
        state.fill_spread_pcts.append(sp)
    if q.open_interest is None:
        state.fills_unknown_liquidity += 1
    if stressed:
        state.fills_stressed += 1
    elif eff_slip > base_slip + 1e-12:
        state.fills_penalized += 1
    depth = q.ask_size if action == "buy" else q.bid_size if action == "sell" else None
    if depth is None or qty <= 0:
        return None
    state.fills_depth_known += 1
    if qty > depth:
        state.fills_beyond_depth += 1
        side_name = "ask" if action == "buy" else "bid"
        return f"qty {qty} > {side_name} size {depth}"
    return None


def _leg_desc(leg: OpenLeg) -> str:
    sign = "-" if leg.side == "short" else "+"
    r = "P" if leg.key.right == "put" else "C"
    return f"{sign}{leg.qty}{r}{leg.key.strike:g}"


def _position_desc(pos: Position) -> str:
    return " ".join(_leg_desc(leg) for leg in pos.legs)


class BarView:
    """One intraday bar through the MarketViewLike surface — the SAME
    entry/exit/fill code runs at every clock. Daily-history reads
    (condition indicators, VIX, ATM-IV) are bounded at the PREVIOUS
    session's close: today's daily close does not exist yet at an
    intraday bar (guardrail #2)."""

    def __init__(
        self,
        iview: IntradayView,
        prev: MarketView,
        intraday_lasts: list[float] | None = None,
        lasts_len: int = 0,
        vwap: float | None = None,
        is_indicator_stamp: bool = True,
    ) -> None:
        self._iview = iview
        self._prev = prev
        # the run's rolling 5-min lasts: the engine appends as bars advance,
        # so a length snapshot bounds this view at the current bar (D2c)
        self._lasts = intraday_lasts if intraday_lasts is not None else []
        self._lasts_len = lasts_len
        self._vwap = vwap
        # FX.3: whether THIS bar is a 5-min indicator stamp (always True on
        # 5-min grids; on minute grids only the und5 stamps are). Off-stamp
        # bars evaluate price-vs conditions against the live print with the
        # latest SAMPLED value as prev — crosses stay stamp-anchored.
        self._is_stamp = is_indicator_stamp

    @property
    def as_of(self) -> date:
        return self._iview.session

    @property
    def is_indicator_stamp(self) -> bool:
        return self._is_stamp

    @property
    def has_chain(self) -> bool:
        return bool(self._iview.chain())

    @property
    def fill_source(self) -> str:
        return self._iview.quote_source

    def chain(self) -> dict[ContractKey, Quote]:
        return self._iview.chain()

    def quote(self, key: ContractKey) -> Quote | None:
        got = self._iview.quote_at(key)
        return None if got is None else got[0]

    def close(self, d: date | None = None) -> float | None:
        if d is not None and d != self._iview.session:
            return self._prev.close(d)
        return self._iview.underlying_last()

    def closes_upto(self) -> list[float]:
        return self._prev.closes_upto()

    def vix(self) -> float | None:
        return self._prev.vix()

    def atm_iv_history(self) -> list[float]:
        return self._prev.atm_iv_history()

    # IVX/HV are EOD series — today's observation doesn't exist yet at an
    # intraday bar, so these are bounded at the previous session too
    def ivx_30d(self) -> float | None:
        return self._prev.ivx_30d()

    def ivx_30d_history(self) -> list[float]:
        return self._prev.ivx_30d_history()

    def hv_30d(self) -> float | None:
        return self._prev.hv_30d()

    # F4: surface signals are EOD fits — bounded at the previous session
    # at intraday bars, like IVX (today's fit doesn't exist at 10:15)
    def skew_25d(self) -> float | None:
        return self._prev.skew_25d()

    def term_structure_slope(self) -> float | None:
        return self._prev.term_structure_slope()

    # F1: dealer positioning is an EOD series — previous session at
    # intraday bars, like IVX (stale-but-true beats fresh-but-leaky;
    # intraday spot_exposures is a deferred later chunk)
    def gex_level(self) -> float | None:
        return self._prev.gex_level()

    def gex_history(self) -> list[float]:
        return self._prev.gex_history()

    def dex_level(self) -> float | None:
        return self._prev.dex_level()

    def dex_history(self) -> list[float]:
        return self._prev.dex_history()

    # F2/F3: flow/pin reductions are EOD series — previous session at
    # intraday bars, like every daily analytic
    def net_premium_level(self) -> float | None:
        return self._prev.net_premium_level()

    def net_premium_history(self) -> list[float]:
        return self._prev.net_premium_history()

    def market_tide_level(self) -> float | None:
        return self._prev.market_tide_level()

    def market_tide_history(self) -> list[float]:
        return self._prev.market_tide_history()

    def nope_level(self) -> float | None:
        return self._prev.nope_level()

    def nope_history(self) -> list[float]:
        return self._prev.nope_history()

    def put_call_ratio(self) -> float | None:
        return self._prev.put_call_ratio()

    def max_pain_distance_pct(self) -> float | None:
        return self._prev.max_pain_distance_pct()

    def intraday_closes_upto(self) -> list[float]:
        # AT MOST the trailing lookback window. Copying the WHOLE prefix
        # here was O(bars²) across a run — a full-history 5-min backtest
        # spent most of its 40 minutes copying lists, and the multi-MB
        # per-bar churn ballooned the allocator until Railway OOM-killed
        # the process (incident 2026-07-06). No consumer reads deeper:
        # conditions trim to INTRADAY_LOOKBACK_BARS by contract.
        start = self._lasts_len - INTRADAY_LOOKBACK_BARS
        return self._lasts[max(0, start) : self._lasts_len]

    def intraday_vwap(self) -> float | None:
        return self._vwap


def _calendar_dte_fn(as_of: date) -> DteFn:
    def fn(exp: date) -> int:
        return (exp - as_of).days

    return fn


def _trading_dte_fn(store: MarketStore, as_of: date) -> DteFn:
    """Trading-day DTE (owner-confirmed 5-min basis): sessions strictly
    after `as_of` up to and including the expiry — 0DTE = 0, Friday's
    "1DTE" = Monday's expiry."""
    sessions = store.sessions

    def fn(exp: date) -> int:
        i = bisect_right(sessions, as_of)
        j = bisect_right(sessions, exp)
        return max(j - i, 0)

    return fn


# Quote records whose fills ALWAYS pay the full adverse price: modeled
# quotes (trade prints + modeled spread) carry no real NBBO — stress
# slippage stays on until quote sources accumulate (D2d, per the brief).
STRESSED_SOURCES = frozenset({"alpaca_modeled"})

# progress-callback cadence at the 5-min clock (~12 reports on a
# full-history run) — frequent enough to prove life, rare enough to
# stay off the hot path
PROGRESS_EVERY_SESSIONS = 250


def _close_slip(q: Quote, costs: Costs, action: str, stressed: bool = False) -> float:
    """The slip a close-side price uses: the SIDE-AWARE base (closing a
    long sells, closing a short buys back — each pays its own measured
    concession), OI-scaled when OI is known and thin (fills.effective_slip).
    Exit triggers, exit fills and marks all price through here, so open and
    close share one fill model. Modeled quotes are always stressed — slip
    1.0, both directions."""
    if stressed:
        return 1.0
    return fills.effective_slip(
        fills.base_slip(costs, action), q, costs.min_open_interest
    )


def _liq_value_per_share(pos: Position, view: MarketViewLike, costs: Costs) -> float | None:
    """Signed liquidation value per contract-set per share, at today's
    quotes: closing longs sells (+), closing shorts buys back (−).
    None when any unsettled leg lacks a usable quote today."""
    stressed = view.fill_source in STRESSED_SOURCES
    total = 0.0
    for leg in pos.legs:
        if leg.settled:
            continue
        q = view.quote(leg.key)
        if q is None:
            return None
        action = fills.close_action(leg.side)
        px = fills.fill_price(q, action, _close_slip(q, costs, action, stressed))
        if px is None:
            return None
        ratio = leg.qty // max(pos.contracts, 1)
        total += px * ratio if leg.side == "long" else -px * ratio
    return total


def _refresh_marks(pos: Position, view: MarketViewLike, costs: Costs) -> None:
    stressed = view.fill_source in STRESSED_SOURCES
    for leg in pos.legs:
        if leg.settled:
            continue
        q = view.quote(leg.key)
        if q is None:
            continue
        action = fills.close_action(leg.side)
        px = fills.fill_price(q, action, _close_slip(q, costs, action, stressed))
        if px is not None:
            leg.last_mark = px


def _position_value(pos: Position, close_px: float) -> float:
    """Mark for the equity curve: option legs at last liquidation marks,
    stock at today's close."""
    value = 0.0
    for leg in pos.legs:
        if leg.settled:
            continue
        signed = leg.last_mark if leg.side == "long" else -leg.last_mark
        value += signed * leg.qty * MULT
    value += pos.stock_shares * close_px
    return value


def _portfolio_greeks(
    positions: list[Position], view: MarketViewLike
) -> tuple[float | None, float | None, float | None, float | None]:
    """Aggregate (delta, gamma, theta, vega) of all open positions at
    today's quotes. Signed: long +, short −; option greeks × qty × MULT,
    stock at 1Δ per share. Per-greek honesty: a flat book is 0.0; if ANY
    open leg lacks a greek today, THAT aggregate is None — a partial sum
    would silently understate exposure."""
    delta = gamma = theta = vega = 0.0
    ok = {"delta": True, "gamma": True, "theta": True, "vega": True}
    for pos in positions:
        if pos.closed:
            continue
        for leg in pos.legs:
            if leg.settled:
                continue
            q = view.quote(leg.key)
            sign = 1.0 if leg.side == "long" else -1.0
            scale = sign * leg.qty * MULT
            for name, value in (
                ("delta", None if q is None else q.delta),
                ("gamma", None if q is None else q.gamma),
                ("theta", None if q is None else q.theta),
                ("vega", None if q is None else q.vega),
            ):
                if value is None:
                    ok[name] = False
                elif name == "delta":
                    delta += value * scale
                elif name == "gamma":
                    gamma += value * scale
                elif name == "theta":
                    theta += value * scale
                else:
                    vega += value * scale
        if pos.stock_shares:
            delta += float(pos.stock_shares)  # stock is always 1Δ per share
    return (
        delta if ok["delta"] else None,
        gamma if ok["gamma"] else None,
        theta if ok["theta"] else None,
        vega if ok["vega"] else None,
    )


def _schedule_matches(spec: StrategySpec, state: _State, day: date) -> bool:
    sched = spec.entry.schedule
    freq = sched.frequency
    if freq is Frequency.DAILY or freq is Frequency.SIGNAL_ONLY:
        return True
    if freq is Frequency.WEEKLY:
        target = (sched.day_of_week.value if sched.day_of_week else "monday")
        names = ["monday", "tuesday", "wednesday", "thursday", "friday"]
        return day.weekday() < 5 and names[day.weekday()] == target
    if freq is Frequency.MONTHLY:
        key = (day.year, day.month)
        if state.last_entry_month == key:
            return False
        if sched.day_of_month is not None and day.day < sched.day_of_month:
            return False
        return True
    return False


def _risk_per_contract(
    spec: StrategySpec, keys: list[ContractKey], premium: float, spot: float
) -> float:
    structure = spec.position.structure
    if structure is Structure.SHORT_PUT:
        return max((keys[0].strike - premium) * MULT, 0.0)
    if structure in (Structure.PUT_CREDIT_SPREAD, Structure.CALL_CREDIT_SPREAD):
        width = abs(keys[0].strike - keys[1].strike)
        return max((width - premium) * MULT, 0.0)
    if structure is Structure.IRON_CONDOR:
        width_put = abs(keys[0].strike - keys[1].strike)
        width_call = abs(keys[2].strike - keys[3].strike)
        return max((max(width_put, width_call) - premium) * MULT, 0.0)
    if structure is Structure.COVERED_CALL:
        return max((spot - premium) * MULT, 0.0)
    # long structures: risk = debit
    return max(-premium * MULT, 0.0)


def _count_skip(
    state: _State, skip_dedupe: set[str], day: date, reason: str
) -> None:
    """FX.2 loop-level skip: counted always, logged once per session
    (mirror of _try_entry's skip closure for skips the loop itself owns)."""
    state.skip_counts[reason] = state.skip_counts.get(reason, 0) + 1
    if reason in skip_dedupe:
        return
    skip_dedupe.add(reason)
    state.trades.append(
        TradeEvent(day=day, action="SKIP", detail="entry candidate", reason=reason)
    )


def _try_entry(
    spec: StrategySpec,
    state: _State,
    view: MarketViewLike,
    equity_now: float,
    dte_fn: DteFn | None = None,
    skip_dedupe: set[str] | None = None,
    skip_conditions: bool = False,
) -> None:
    """`skip_conditions=True` is the FX.2 armed-order path: the signal was
    validated at its trigger bar and the order fills at THIS bar's real
    quote even if the signal faded meanwhile (a submitted order can't be
    recalled because RSI ticked back) — everything else (quotes, liquidity
    gates, sizing) validates normally."""
    day = view.as_of
    commission = spec.costs.commission_per_contract

    def skip(reason: str, detail: str = "") -> None:
        # every skip is COUNTED (FX.2 — the run carries the distribution);
        # at the 5-min clock an entry is attempted at every bar, so the
        # LOG stays deduped per session, not 80 lines (dedupe set is
        # per-session, supplied by the bar loop; daily passes None)
        state.skip_counts[reason] = state.skip_counts.get(reason, 0) + 1
        if skip_dedupe is not None:
            if reason in skip_dedupe:
                return
            skip_dedupe.add(reason)
        state.trades.append(
            TradeEvent(day=day, action="SKIP", detail=detail or "entry candidate", reason=reason)
        )

    open_count = sum(1 for p in state.positions if not p.closed)
    if open_count >= spec.entry.max_concurrent_positions:
        skip("max_concurrent")
        return
    if not view.has_chain:
        skip("no_chain_data")
        return
    if (not skip_conditions and spec.entry.conditions
            and not all_conditions_pass(view, spec.entry.conditions)):
        skip("conditions_not_met")
        return

    chain = view.chain()
    spot = view.close()
    if spot is None:
        skip("no_underlying_close")
        return

    expiration = select_expiration(chain, day, spec.position.expiration_selection, dte_fn)
    if expiration is None:
        skip("no_expiration_in_window")
        return

    keys, reason = select_legs(chain, expiration, spec.position.legs, spot)
    if keys is None:
        skip(reason or "selection_failed")
        return

    # validate quotes + liquidity gates + compute per-share entry fills
    entry_fills: list[float] = []
    leg_quotes: list[Quote] = []
    leg_slips: list[float] = []
    leg_stressed: list[bool] = []
    for key, leg in zip(keys, spec.position.legs, strict=True):
        action = fills.open_action(leg.side.value)
        q = chain.get(key)
        problem = fills.quote_problem(q, action)
        if problem is not None:
            skip(problem, detail=f"{key.right} {key.strike:g} exp {key.expiration}")
            return
        assert q is not None
        gate = fills.liquidity_gate(q, spec.costs)
        stressed = view.fill_source in STRESSED_SOURCES  # modeled quotes: always
        if gate is not None and not stressed:
            if spec.costs.liquidity_mode is LiquidityMode.SKIP:
                skip(gate, detail=f"{key.right} {key.strike:g} exp {key.expiration}")
                return
            stressed = True  # stress mode: pay the full adverse quote instead
        base = fills.base_slip(spec.costs, action)
        eff = 1.0 if stressed else fills.effective_slip(base, q, spec.costs.min_open_interest)
        px = fills.fill_price(q, action, eff)
        if px is None:  # pragma: no cover — quote_problem gates this
            skip("missing_quote")
            return
        entry_fills.append(px)
        leg_quotes.append(q)
        leg_slips.append(eff)
        leg_stressed.append(stressed)

    # vega cap (spec v2, owner amendment 2): |NET vega| of the contract-set
    # in dollars per vol point — leg vegas sum SIGNED (long +, short −,
    # × ratio), so a spread's cancelling legs net. Missing vega data makes
    # the user's rule unevaluable → skip, never silently ignore it.
    cap = spec.position.max_vega_per_contract
    if cap is not None:
        net_vega = 0.0
        for q, leg in zip(leg_quotes, spec.position.legs, strict=True):
            if q.vega is None:
                skip("vega_unavailable")
                return
            signed = q.vega if leg.side is Side.LONG else -q.vega
            net_vega += signed * leg.ratio
        if abs(net_vega) * MULT > cap:
            skip("vega_cap_exceeded", detail=f"|net vega| ${abs(net_vega) * MULT:.2f} > ${cap:g}")
            return

    # net premium per contract-set per share: credits positive
    premium = 0.0
    for px, leg in zip(entry_fills, spec.position.legs, strict=True):
        premium += px * leg.ratio if leg.side is Side.SHORT else -px * leg.ratio

    # sizing
    if spec.sizing.method is SizingMethod.FIXED_CONTRACTS:
        contracts = int(spec.sizing.value)
    else:
        risk = _risk_per_contract(spec, keys, premium, spot)
        if risk <= 0:
            skip("risk_undefined")
            return
        contracts = int(equity_now * (spec.sizing.value / 100.0) / risk)
    if contracts < 1:
        skip("risk_size_zero")
        return

    pos = Position(
        pid=state.next_pid,
        structure=spec.position.structure.value,
        legs=[],
        contracts=contracts,
        opened=day,
        premium=premium,
    )
    state.next_pid += 1

    # covered call buys the shares first (reference close, no added costs —
    # documented approximation per TECH-SPEC §5)
    if spec.position.structure is Structure.COVERED_CALL:
        shares = 100 * contracts
        cost = shares * spot
        state.cash -= cost
        pos.stock_shares = shares
        pos.stock_basis = spot
        pos.cash_flow -= cost
        state.trades.append(
            TradeEvent(day=day, action="STOCK_BUY", detail=f"+{shares} sh @ {spot:.2f}",
                       position_id=pos.pid)
        )

    for key, leg, px in zip(keys, spec.position.legs, entry_fills, strict=True):
        qty = leg.ratio * contracts
        state.fill_log.append({
            "pid": pos.pid, "day": day.isoformat(),
            "action": fills.open_action(leg.side.value),
            "expiration": key.expiration.isoformat(), "right": key.right,
            "strike": key.strike, "qty": qty, "price": px,
            "source": view.fill_source,
        })
        cash_delta = px * qty * MULT if leg.side is Side.SHORT else -px * qty * MULT
        cash_delta -= commission * qty
        state.cash += cash_delta
        pos.cash_flow += cash_delta
        pos.legs.append(
            OpenLeg(key=key, side=leg.side.value, qty=qty, entry_price=px, last_mark=px)
        )

    depth_notes: list[str] = []
    for q, eff, was_stressed, leg in zip(leg_quotes, leg_slips, leg_stressed,
                                         spec.position.legs, strict=True):
        action = fills.open_action(leg.side.value)
        note = _record_leg_fill(
            state, q, eff, fills.base_slip(spec.costs, action), was_stressed,
            view.fill_source, action=action,
            qty=leg.ratio * contracts,
        )
        if note:
            depth_notes.append(note)

    state.positions.append(pos)
    state.live.append(pos)
    state.opens += 1
    if state.opens > MAX_RUN_FILLS:
        raise RunFillCapError(
            f"run exceeded {MAX_RUN_FILLS:,} filled positions — narrow "
            "the window or slow the entry cadence (intraday_scan "
            "every_setup fills every setup it can)"
        )
    if spec.entry.schedule.frequency is Frequency.MONTHLY:
        state.last_entry_month = (day.year, day.month)
    kind = "cr" if premium > 0 else "db"
    state.trades.append(
        TradeEvent(
            day=day,
            action="OPEN",
            detail=f"{_position_desc(pos)} · exp {expiration} · {kind} {abs(premium):.3f}"
                   + (" · " + "; ".join(depth_notes) if depth_notes else ""),
            position_id=pos.pid,
        )
    )


# ══════════════════════════════════════════════════════════════════════════
# SCALE-IN BASKET (D5a) — one accumulating position with a blended cost basis.
# Gated entirely behind spec.entry.scale_in: the non-ladder path never runs
# any of this, so daily-clock output stays bit-identical (pinned regression).
# ══════════════════════════════════════════════════════════════════════════

def _basket_skip(
    state: _State, view: MarketViewLike, session_skips: set[str] | None,
    reason: str, detail: str = "",
) -> None:
    if session_skips is not None:
        if reason in session_skips:
            return
        session_skips.add(reason)
    state.trades.append(
        TradeEvent(day=view.as_of, action="SKIP",
                   detail=detail or "scale-in rung", reason=reason)
    )


def _condition_passes(view: MarketViewLike, cond: Condition) -> bool:
    return all_conditions_pass(view, [cond])


def _fire_rungs(
    spec: StrategySpec,
    state: _State,
    view: MarketViewLike,
    basket: Position,
    opening: bool,
    bar_time: str | None,
    session_skips: set[str] | None,
) -> list[str]:
    """Fire every not-yet-fired rung whose condition passes at THIS bar, at the
    current bar's ASK (guardrail #1; D1b liquidity gates apply per fill). Adds
    are clamped to max_total_contracts (flagged cap_clamped); once the cap is
    reached no deeper rung fires. Rungs fired AT the opening bar fold into the
    OPEN event (opening=True → no ADD events); later fires emit ADD events.
    Every fill reads only the current bar's quote — the add can never peek at a
    future bar (the intraday lookahead canary asserts this)."""
    si = spec.entry.scale_in
    assert si is not None
    commission = spec.costs.commission_per_contract
    leg = basket.legs[0]
    key = leg.key
    action = fills.open_action(leg.side)  # "buy" for a long basket
    base = fills.base_slip(spec.costs, action)  # side-aware (D3d-earned)
    # F5 review finding #2: opening-bar rung fills fold into the OPEN event,
    # so their depth notes must travel back to _open_basket — a beyond-depth
    # FIRST rung (often the ladder's largest) is named, not just counted
    opening_notes: list[str] = []
    for idx, rung in enumerate(si.rungs):
        if idx in basket.fired_rungs:
            continue
        if not _condition_passes(view, rung):
            continue
        remaining = si.max_total_contracts - basket.contracts
        if remaining <= 0:
            break  # cap reached — deeper rungs cannot fire
        q = view.quote(key)
        problem = fills.quote_problem(q, action)
        if problem is not None:
            # a gap / zero quote: the rung stays unfired and retries next bar
            _basket_skip(state, view, session_skips, problem,
                         detail=f"{key.right} {key.strike:g} rung {rung.value:g}")
            continue
        assert q is not None
        gate = fills.liquidity_gate(q, spec.costs)
        stressed = view.fill_source in STRESSED_SOURCES
        if gate is not None and not stressed:
            if spec.costs.liquidity_mode is LiquidityMode.SKIP:
                _basket_skip(state, view, session_skips, gate,
                             detail=f"{key.right} {key.strike:g} rung {rung.value:g}")
                continue
            stressed = True  # stress mode: pay the full adverse quote
        eff = 1.0 if stressed else fills.effective_slip(base, q, spec.costs.min_open_interest)
        px = fills.fill_price(q, action, eff)
        if px is None:  # pragma: no cover — quote_problem gates this
            _basket_skip(state, view, session_skips, "missing_quote")
            continue

        qty = rung.add_contracts
        clamped = False
        if qty > remaining:
            qty = remaining
            clamped = True

        # commit the fill: long basket BUYS, so cash and premium go debit
        cash_delta = -px * qty * MULT - commission * qty
        state.cash += cash_delta
        basket.cash_flow += cash_delta
        basket.basket_cost += px * qty * MULT
        basket.contracts += qty
        leg.qty += qty
        # blended per-share cost basis: keeps the existing exit math
        # (profit_pct = (premium + liq)/|premium|) equal to value/cost − 1
        blended = basket.basket_cost / (MULT * basket.contracts)
        basket.premium = -blended  # long debit → negative premium
        leg.entry_price = blended
        leg.last_mark = px
        basket.fired_rungs.add(idx)
        state.fill_log.append({
            "pid": basket.pid, "day": view.as_of.isoformat(), "action": action,
            "expiration": key.expiration.isoformat(), "right": key.right,
            "strike": key.strike, "qty": qty, "price": px,
            "source": view.fill_source,
        })
        depth_note = _record_leg_fill(state, q, eff, base, stressed,
                                      view.fill_source, action=action, qty=qty)
        state.rung_fills.append(
            RungFill(
                basket_pid=basket.pid, day=view.as_of, bar_time=bar_time,
                rung_index=idx, threshold=rung.value, qty=qty, fill_price=px,
                fill_source=view.fill_source, cap_clamped=clamped,
            )
        )
        if opening and depth_note:
            opening_notes.append(depth_note)
        if not opening:
            tag = " cap_clamped" if clamped else ""
            if depth_note:
                tag += f" · {depth_note}"
            state.trades.append(
                TradeEvent(
                    day=view.as_of, action="ADD",
                    detail=f"+{qty}{'C' if leg.key.right == 'call' else 'P'}"
                           f"{leg.key.strike:g} @ {px:.3f} · rung {rung.value:g}{tag}",
                    position_id=basket.pid,
                    reason="cap_clamped" if clamped else None,
                )
            )
        if clamped:
            break  # the cap was hit exactly on this rung — stop deepening
    return opening_notes


def _open_basket(
    spec: StrategySpec,
    state: _State,
    view: MarketViewLike,
    bstate: _BasketState,
    dte_fn: DteFn | None,
    bar_time: str | None,
    session_skips: set[str] | None,
) -> None:
    """Open a fresh basket: select the single-leg contract once, then fire
    every already-satisfied rung at this bar. If nothing fills (gap / gated /
    zero quote) the provisional basket is discarded and the open retries next
    bar — an empty basket is never recorded."""
    day = view.as_of
    if not view.has_chain:
        _basket_skip(state, view, session_skips, "no_chain_data")
        return
    chain = view.chain()
    spot = view.close()
    if spot is None:
        _basket_skip(state, view, session_skips, "no_underlying_close")
        return
    expiration = select_expiration(chain, day, spec.position.expiration_selection, dte_fn)
    if expiration is None:
        _basket_skip(state, view, session_skips, "no_expiration_in_window")
        return
    keys, reason = select_legs(chain, expiration, spec.position.legs, spot)
    if keys is None:
        _basket_skip(state, view, session_skips, reason or "selection_failed")
        return

    leg = spec.position.legs[0]
    pos = Position(
        pid=state.next_pid,
        structure=spec.position.structure.value,
        legs=[OpenLeg(key=keys[0], side=leg.side.value, qty=0, entry_price=0.0, last_mark=0.0)],
        contracts=0, opened=day, premium=0.0, scale_in=True,
    )
    opening_notes = _fire_rungs(spec, state, view, pos, opening=True,
                                bar_time=bar_time, session_skips=session_skips)
    if pos.contracts <= 0:
        return  # nothing filled this bar — discard, retry next bar

    state.next_pid += 1
    state.positions.append(pos)
    state.live.append(pos)
    state.opens += 1
    if state.opens > MAX_RUN_FILLS:
        raise RunFillCapError(
            f"run exceeded {MAX_RUN_FILLS:,} filled positions — narrow "
            "the window or slow the entry cadence (intraday_scan "
            "every_setup fills every setup it can)"
        )
    bstate.basket = pos
    rungs_hit = len(pos.fired_rungs)
    state.trades.append(
        TradeEvent(
            day=day, action="OPEN",
            detail=f"{_position_desc(pos)} · exp {expiration} · basket db "
                   f"{abs(pos.premium):.3f} · {pos.contracts}ct · {rungs_hit} rung"
                   f"{'s' if rungs_hit != 1 else ''}"
                   + (" · " + "; ".join(opening_notes) if opening_notes else ""),
            position_id=pos.pid,
        )
    )


def _manage_basket(
    spec: StrategySpec,
    state: _State,
    view: MarketViewLike,
    bstate: _BasketState,
    dte_fn: DteFn | None,
    session_skips: set[str] | None,
    bar_time: str | None,
) -> None:
    """One bar of the ladder state machine. Runs AFTER exits, so an add at bar
    t is first exit-evaluated at t+1 (owner amendment 2 — a stop can never
    fire on its own add bar)."""
    si = spec.entry.scale_in
    assert si is not None
    if bstate.basket is not None and not bstate.basket.closed:
        _fire_rungs(spec, state, view, bstate.basket, opening=False,
                    bar_time=bar_time, session_skips=session_skips)
        return
    # flat: a closed basket must wait for the rearm signal to leave the zone
    if not bstate.armed:
        if _condition_passes(view, si.rearm):
            bstate.armed = True
        return
    # armed & flat: the first (shallowest) rung's condition opens the basket
    if _condition_passes(view, si.rungs[0]):
        _open_basket(spec, state, view, bstate, dte_fn, bar_time, session_skips)


def _force_flat(spec: StrategySpec, state: _State, view: MarketViewLike) -> None:
    """exit.close_at_time force-flat: close every open position at this bar
    (reason session_flat). A leg without a usable quote here can't fill — it
    carries and is retried next bar (honest, never synthetic)."""
    for pos in state.live:
        if pos.closed or all(leg.settled for leg in pos.legs):
            continue
        _close_position(pos, view, state, spec, "session_flat")


def _close_position(
    pos: Position, view: MarketViewLike, state: _State, spec: StrategySpec, reason: str
) -> bool:
    """Close all unsettled option legs at today's quotes. False if any leg
    lacks a usable quote (the attempt is retried on later sessions).
    Exits are never liquidity-gated — a position can always pay the quoted
    price to close — but thin known OI scales the slip like everywhere else."""
    commission = spec.costs.commission_per_contract
    liq = _liq_value_per_share(pos, view, spec.costs)
    if liq is None:
        return False
    stressed = view.fill_source in STRESSED_SOURCES
    depth_notes: list[str] = []
    for leg in pos.legs:
        if leg.settled:
            continue
        q = view.quote(leg.key)
        assert q is not None
        action = fills.close_action(leg.side)
        eff = _close_slip(q, spec.costs, action, stressed)
        px = fills.fill_price(q, action, eff)
        assert px is not None
        cash_delta = px * leg.qty * MULT if leg.side == "long" else -px * leg.qty * MULT
        cash_delta -= commission * leg.qty
        state.cash += cash_delta
        pos.cash_flow += cash_delta
        leg.settled = True
        state.fill_log.append({
            "pid": pos.pid, "day": view.as_of.isoformat(),
            "action": action,
            "expiration": leg.key.expiration.isoformat(), "right": leg.key.right,
            "strike": leg.key.strike, "qty": leg.qty, "price": px,
            "source": view.fill_source,
        })
        note = _record_leg_fill(
            state, q, eff, fills.base_slip(spec.costs, action),
            stressed=False, source=view.fill_source,
            action=action, qty=leg.qty,
        )
        if note:
            depth_notes.append(note)
    pos.closed = pos.stock_shares == 0
    state.trades.append(
        TradeEvent(
            day=view.as_of,
            action="CLOSE",
            detail=_position_desc(pos)
                   + (" · " + "; ".join(depth_notes) if depth_notes else ""),
            pl=round(pos.cash_flow, 2) if pos.closed else None,
            reason=reason,
            position_id=pos.pid,
        )
    )
    return True


def _delta_stop_hit(pos: Position, view: MarketViewLike, threshold: float) -> bool:
    """True when any WATCHED leg's |delta| reaches the threshold at today's
    quotes. Watched = short legs when the position has any, else all legs.
    Legs without a delta today are unevaluable — the rule waits on them,
    it never guesses (spec-v2 contract)."""
    unsettled = [leg for leg in pos.legs if not leg.settled]
    shorts = [leg for leg in unsettled if leg.side == "short"]
    watched = shorts if shorts else unsettled
    for leg in watched:
        q = view.quote(leg.key)
        if q is not None and q.delta is not None and abs(q.delta) >= threshold:
            return True
    return False


def _latch_note(pos: Position, day: date) -> str:
    """The trigger disclosure: dated when the fill lands on a later
    session (overnight/gap carry — review finding)."""
    if pos.latched_day is not None and pos.latched_day != day:
        return f"{pos.latched_day} {pos.latched_bar}"
    return pos.latched_bar or "?"


def _try_complete_latch(
    pos: Position, view: MarketViewLike, state: _State, spec: StrategySpec
) -> bool:
    """Complete a pending latched exit at THIS view's real quotes; on
    success the CLOSE event discloses the trigger. False = still no
    fillable quote (the latch persists — no expiry)."""
    if _close_position(pos, view, state, spec, pos.exit_latched or "condition_exit"):
        state.trades[-1].detail += f" · triggered {_latch_note(pos, view.as_of)}"
        return True
    return False


def _check_exits(
    spec: StrategySpec, state: _State, view: MarketViewLike, dte_fn: DteFn | None = None,
    latch: bool = False, bar_hhmm: str | None = None,
) -> None:
    """latch=True (FX.3, finest mode only) arms the latched-exit rule: a
    condition-exit trigger OBSERVED at a bar that cannot fill the close
    (no usable quotes, e.g. a minute bar between NBBO stamps) is
    remembered on the position and COMPLETED at the first quoted bar that
    can fill it, without re-evaluation. A seen touch counts; an exit
    order is never un-triggered by a bounce (directional honesty: a
    forgotten exit is optimism). Trigger + fill bars are disclosed."""
    if dte_fn is None:
        dte_fn = _calendar_dte_fn(view.as_of)
    exit_rules = spec.exit
    for pos in state.live:
        if pos.closed or all(leg.settled for leg in pos.legs):
            continue
        if pos.exit_latched is not None:
            # complete the latched exit before any re-evaluation; if this
            # bar still can't fill it, the latch persists (no expiry)
            _try_complete_latch(pos, view, state, spec)
            continue
        liq = _liq_value_per_share(pos, view, spec.costs)
        base = abs(pos.premium)
        profit_pct: float | None = None
        if liq is not None and base > 0:
            profit_pct = (pos.premium + liq) / base * 100.0

        # priority (owner-confirmed, D1c): stop → delta stop → profit
        # target → theta harvest → time exit → condition exits
        if (
            exit_rules.stop_loss_pct is not None
            and profit_pct is not None
            and -profit_pct >= exit_rules.stop_loss_pct
        ):
            _close_position(pos, view, state, spec, "stop_loss")
            continue
        if exit_rules.delta_stop_abs is not None and _delta_stop_hit(
            pos, view, exit_rules.delta_stop_abs
        ):
            _close_position(pos, view, state, spec, "delta_stop")
            continue
        if (
            exit_rules.profit_target_pct is not None
            and profit_pct is not None
            and profit_pct >= exit_rules.profit_target_pct
        ):
            _close_position(pos, view, state, spec, "profit_target")
            continue
        dte = min(dte_fn(leg.key.expiration) for leg in pos.legs if not leg.settled)
        th = exit_rules.theta_harvest
        if (
            th is not None
            and profit_pct is not None
            and th.dte_to <= dte <= th.dte_from
            and profit_pct >= th.profit_pct
        ):
            _close_position(pos, view, state, spec, "theta_harvest")
            continue
        if exit_rules.time_exit_dte is not None and exit_rules.time_exit_dte > 0:
            if dte <= exit_rules.time_exit_dte:
                _close_position(pos, view, state, spec, "time_exit")
                continue
        if exit_rules.conditions and all_conditions_pass(view, exit_rules.conditions):
            closed = _close_position(pos, view, state, spec, "condition_exit")
            if not closed and latch:
                # the trigger was OBSERVED but this bar cannot fill the
                # close — latch it (completed at the next fillable quote)
                pos.exit_latched = "condition_exit"
                pos.latched_bar = bar_hhmm
                pos.latched_day = view.as_of


def _settle_expirations(spec: StrategySpec, state: _State, view: MarketViewLike) -> None:
    day = view.as_of
    close_px = view.close()
    if close_px is None:
        return
    for pos in state.live:
        if pos.closed:
            continue
        had_latch = pos.exit_latched
        for leg in pos.legs:
            if leg.settled or leg.key.expiration != day:
                continue
            k = leg.key
            intrinsic = max(0.0, k.strike - close_px) if k.right == "put" else max(
                0.0, close_px - k.strike
            )
            if leg.side == "long":
                if intrinsic > 0:
                    proceeds = intrinsic * leg.qty * MULT
                    state.cash += proceeds
                    pos.cash_flow += proceeds
                    state.trades.append(
                        TradeEvent(day=day, action="SETTLE",
                                   detail=f"{_leg_desc(leg)} intrinsic {intrinsic:.2f}",
                                   position_id=pos.pid)
                    )
                else:
                    state.trades.append(
                        TradeEvent(day=day, action="EXPIRE", detail=f"{_leg_desc(leg)} otm",
                                   position_id=pos.pid)
                    )
                leg.settled = True
                leg.last_mark = 0.0
                continue

            # short legs
            if intrinsic <= 0:
                state.trades.append(
                    TradeEvent(day=day, action="EXPIRE", detail=f"{_leg_desc(leg)} otm",
                               position_id=pos.pid)
                )
                leg.settled = True
                leg.last_mark = 0.0
                continue

            if k.right == "put":
                # assigned: buy shares at strike, unwind next session open
                shares = 100 * leg.qty
                cost = shares * k.strike
                state.cash -= cost
                pos.cash_flow -= cost
                pos.stock_shares += shares
                pos.stock_basis = k.strike
                pos.pending_stock = 1
                state.trades.append(
                    TradeEvent(day=day, action="ASSIGN",
                               detail=f"{_leg_desc(leg)} → +{shares} sh @ {k.strike:g}",
                               position_id=pos.pid)
                )
            else:
                shares = 100 * leg.qty
                if pos.stock_shares >= shares:
                    # covered: shares called away at the strike
                    proceeds = shares * k.strike
                    state.cash += proceeds
                    pos.cash_flow += proceeds
                    pos.stock_shares -= shares
                    state.trades.append(
                        TradeEvent(day=day, action="CALLED_AWAY",
                                   detail=f"{_leg_desc(leg)} → −{shares} sh @ {k.strike:g}",
                                   pl=None, position_id=pos.pid)
                    )
                else:
                    # uncovered short call assigned: short the shares, cover next open
                    proceeds = shares * k.strike
                    state.cash += proceeds
                    pos.cash_flow += proceeds
                    pos.stock_shares -= shares
                    pos.pending_stock = 1
                    state.trades.append(
                        TradeEvent(day=day, action="ASSIGN",
                                   detail=f"{_leg_desc(leg)} → −{shares} sh @ {k.strike:g}",
                                   position_id=pos.pid)
                    )
            leg.settled = True
            leg.last_mark = 0.0

        if had_latch is not None and all(leg.settled for leg in pos.legs):
            # settlement won the race with a pending latched exit — never
            # silently swallow the trigger (review finding): disclose it on
            # the final settlement event and clear the latch
            state.trades[-1].detail += (
                f" · pending {had_latch} (triggered "
                f"{_latch_note(pos, day)}) superseded by settlement")
            pos.exit_latched = None
        _finalize_if_done(pos, state, day)


def _finalize_if_done(pos: Position, state: _State, day: date) -> None:
    if pos.closed:
        return
    if all(leg.settled for leg in pos.legs) and pos.stock_shares == 0 and pos.pending_stock == 0:
        pos.closed = True
        # attach realized P/L to the last event of this position
        for ev in reversed(state.trades):
            if ev.position_id == pos.pid:
                ev.pl = round(pos.cash_flow, 2)
                break


def _unwind_pending_stock(state: _State, view: MarketView) -> None:
    open_px = view.open_price()
    if open_px is None:
        return
    for pos in state.live:
        if pos.closed or pos.pending_stock == 0 or pos.stock_shares == 0:
            continue
        shares = pos.stock_shares
        proceeds = shares * open_px  # negative shares → buying to cover
        state.cash += proceeds
        pos.cash_flow += proceeds
        action = "STOCK_SELL" if shares > 0 else "STOCK_BUY"
        pos.stock_shares = 0
        pos.pending_stock = 0
        state.trades.append(
            TradeEvent(day=view.as_of, action=action,
                       detail=f"{-shares:+d} sh @ open {open_px:.2f}" if shares < 0
                       else f"-{shares} sh @ open {open_px:.2f}",
                       position_id=pos.pid)
        )
        _finalize_if_done(pos, state, view.as_of)


def _check_slice_coverage(spec: StrategySpec, intraday: IntradayProvider) -> None:
    """Owner amendment 4: refuse BEFORE running when the spec needs more
    than the intraday record covers — a plain reason, never a zero-fill
    grind. Until D2d, intraday quotes are the short-DTE ATM capture slice."""
    sel = spec.position.expiration_selection
    cap = intraday.slice_max_trading_dte
    if sel.min_dte > cap:
        raise SliceCoverageError(
            f"requested {sel.min_dte}–{sel.max_dte} DTE at the 5-minute clock; "
            f"intraday quotes cover 0–{cap} trading-DTE (ATM±$8, the short-DTE "
            f"slice) — use clock \"daily\" for longer tenors"
        )
    if not intraday.sessions():
        raise SliceCoverageError(
            f"no intraday sessions in the lake for {spec.underlying.ticker.value} — "
            "the 5-minute record has not reached this ticker yet; use clock \"daily\""
        )


# F1: which store series each coverage-capped indicator reads. A spec
# conditioned on one of these refuses pre-run when its window starts
# before the signal's first covered session (owner decision 2026-07-07):
# the uncovered stretch would sit in forced flat cash and CORRUPT the
# stats — Sharpe over zero-variance years, diluted drawdowns — a long
# window as costume. Detectable from spec + store alone, so prevention
# beats correction (the D2 slice-refusal precedent).
_SIGNAL_SERIES: dict[Indicator, tuple[str, str]] = {
    Indicator.GEX_LEVEL: ("dealer positioning (UW)", "gex_dates"),
    Indicator.GEX_RANK_1Y: ("dealer positioning (UW)", "gex_dates"),
    Indicator.DEX_LEVEL: ("dealer positioning (UW)", "dex_dates"),
    Indicator.DEX_RANK_1Y: ("dealer positioning (UW)", "dex_dates"),
    # F2/F3: flow/sentiment/pin reductions (UW, 2026-02-24+) — thinner
    # still than dealer positioning; the same cap machinery
    Indicator.NET_PREMIUM_LEVEL: ("options flow (UW)", "flow_dates"),
    Indicator.NET_PREMIUM_RANK_1Y: ("options flow (UW)", "flow_dates"),
    Indicator.MARKET_TIDE_LEVEL: ("market-wide tide (UW)", "tide_dates"),
    Indicator.MARKET_TIDE_RANK_1Y: ("market-wide tide (UW)", "tide_dates"),
    Indicator.NOPE_LEVEL: ("NOPE (UW)", "nope_dates"),
    Indicator.NOPE_RANK_1Y: ("NOPE (UW)", "nope_dates"),
    Indicator.PUT_CALL_FLOW_RATIO: ("options flow (UW)", "pcr_dates"),
    Indicator.MAX_PAIN_DISTANCE_PCT: ("max pain (UW)", "mpd_dates"),
}


def _spec_conditions(spec: StrategySpec) -> list[Condition]:
    conds: list[Condition] = list(spec.entry.conditions)
    conds += list(spec.exit.conditions or [])
    if spec.entry.scale_in is not None:
        conds += list(spec.entry.scale_in.rungs)
        if spec.entry.scale_in.rearm is not None:
            conds.append(spec.entry.scale_in.rearm)
    return conds


# rank indicators carry an extra evaluability bound: the D1 floor makes
# them unevaluable until 126 trailing observations exist, so the refusal
# names THAT date too — the offered window must not hide six structurally
# flat months inside itself (review finding F1 #2)
_RANK_INDICATORS = {Indicator.GEX_RANK_1Y, Indicator.DEX_RANK_1Y,
                    Indicator.NET_PREMIUM_RANK_1Y, Indicator.MARKET_TIDE_RANK_1Y,
                    Indicator.NOPE_RANK_1Y}

# Tail-staleness bound (owner decision 2026-07-08): the PIT reads serve the
# most recent observation ≤ as_of, so a signal whose feed DIED keeps
# forward-filling its last value into every later session — silently. A few
# sessions of vendor publishing lag is normal; past this many sessions the
# tail is a dead feed wearing a live filter, and the run refuses with the
# covered window named. 5 sessions ≈ one trading week.
STALE_TAIL_GRACE_SESSIONS = 5

# The spliced vol-family series get the SAME tail protection (review
# finding: the in-house continuation can die exactly like a vendor feed —
# recorder down, derive failing) but keep their historical START semantics:
# sessions before the series begins evaluate False (D1c warmup behavior),
# they are not start-refused like the UW coverage-capped families.
_STALENESS_ONLY_SERIES: dict[Indicator, tuple[tuple[str, str], ...]] = {
    Indicator.IVX_LEVEL_30D: (("30d IVX", "ivx_dates"),),
    Indicator.IVX_RANK_1Y: (("30d IVX", "ivx_dates"),),
    Indicator.IVX_ZSCORE_1Y: (("30d IVX", "ivx_dates"),),
    Indicator.HV_IV_SPREAD_30D: (("30d IVX", "ivx_dates"), ("30d HV", "hv_dates")),
    Indicator.SKEW_25D: (("25Δ skew", "skew_dates"),),
    Indicator.TERM_STRUCTURE_SLOPE: (("term-structure slope", "term_dates"),),
}


def _check_stale_tail(label: str, scope: str, indicator_name: str,
                      dates: list[date], sessions: list[date],
                      win_start: date, win_end: date) -> None:
    """Refuse when the window runs more than the grace past the series'
    last observation. Counts only sessions the run actually simulates
    (≥ win_start), and the offered window can never invert: a window lying
    entirely after coverage is offered the series' own covered window."""
    if not dates:
        return  # honest absence — warmup/evaluate-False semantics apply
    last = dates[-1]
    lo = max(bisect_right(sessions, last), bisect_left(sessions, win_start))
    hi = bisect_right(sessions, win_end)
    stale = hi - lo
    if stale <= STALE_TAIL_GRACE_SESSIONS:
        return
    first = dates[0]
    covered_start = max(win_start, first)
    if covered_start > last:
        # the whole window sits after the last observation — offering
        # "covered_start → last" would be inverted (the F1 #1 class)
        raise SliceCoverageError(
            f"{label} data{scope} was last observed {last.isoformat()}; the "
            f"requested window lies entirely after it — all {stale} sessions "
            f"would re-read that one stale observation. Run "
            f"{first.isoformat()} → {last.isoformat()} instead, or wait for "
            "the signal feed to catch up."
        )
    raise SliceCoverageError(
        f"{label} data{scope} was last observed {last.isoformat()}; "
        f"the requested window runs {stale} sessions past it — the "
        f"{indicator_name} filter would silently re-read that "
        f"one stale observation across the whole tail. Run "
        f"{covered_start.isoformat()} → {last.isoformat()} instead, "
        "or wait for the signal feed to catch up."
    )


def check_signal_coverage(spec: StrategySpec, store: MarketStore,
                          win_start: date, win_end: date) -> None:
    """Refuse BEFORE running when a condition's signal series starts after
    the (session-aligned) window does, or last observed more than
    STALE_TAIL_GRACE_SESSIONS before the window ends — plain reason,
    covered window offered. `win_start`/`win_end` are the run's first/last
    simulated sessions."""
    for cond in _spec_conditions(spec):
        for vol_label, vol_attr in _STALENESS_ONLY_SERIES.get(cond.indicator, ()):
            _check_stale_tail(
                vol_label, f" for {spec.underlying.ticker.value}",
                cond.indicator.value, getattr(store, vol_attr),
                store.sessions, win_start, win_end)
        entry = _SIGNAL_SERIES.get(cond.indicator)
        if entry is None:
            continue
        label, dates_attr = entry
        dates: list[date] = getattr(store, dates_attr)
        if not dates:
            raise SliceCoverageError(
                f"{label} data is not banked for {spec.underlying.ticker.value} "
                f"yet — the {cond.indicator.value} filter cannot be evaluated "
                "on any session"
            )
        first = dates[0]
        last = dates[-1]
        # a market-wide series isn't "for SPY" — drop the ticker from the
        # phrasing (review finding F2/F3 #10)
        scope = ("" if label.startswith("market-wide")
                 else f" for {spec.underlying.ticker.value}")
        if win_start >= first:
            _check_stale_tail(label, scope, cond.indicator.value, dates,
                              store.sessions, win_start, win_end)
            continue
        rank_note = ""
        if cond.indicator in _RANK_INDICATORS:
            unlock = (dates[125].isoformat() if len(dates) > 125
                      else "once 126 sessions accrue")
            rank_note = (" Rank filters additionally need 126 trailing "
                         f"observations — evaluable from {unlock}.")
        if win_end < first:
            # the requested window lies ENTIRELY before coverage — offering
            # "first → win_end" would be an inverted, impossible window
            # (review finding F1 #1); offer the real covered window instead
            raise SliceCoverageError(
                f"{label} data{scope} starts {first.isoformat()}; the "
                f"requested window ends {win_end.isoformat()} — entirely "
                f"before coverage begins. Run {first.isoformat()} → "
                f"{last.isoformat()} instead.{rank_note}"
            )
        # the offered end is bounded by the series' own last observation so
        # the offer can never itself trip the tail-staleness refusal
        raise SliceCoverageError(
            f"{label} data{scope} starts {first.isoformat()}; the "
            f"requested window starts {win_start.isoformat()} — the uncovered "
            f"stretch would sit in flat cash and corrupt the stats. Run "
            f"{first.isoformat()} → {min(win_end, last).isoformat()} "
            f"instead.{rank_note}"
        )


# Forward-record provenance (2026-07-08): which store splice seams each
# indicator can cross. A spliced series serves vendor values through the
# seam and the in-house continuation after it — runs whose window reaches
# the seam disclose the convention change in their payload (guardrail #6:
# a surface showing results shows what they were computed on).
_PROVENANCE_SERIES: dict[Indicator, tuple[str, ...]] = {
    Indicator.IVX_RANK_1Y: ("ivx_30d",),
    Indicator.IVX_LEVEL_30D: ("ivx_30d",),
    Indicator.IVX_ZSCORE_1Y: ("ivx_30d",),
    Indicator.HV_IV_SPREAD_30D: ("ivx_30d", "hv_30d"),
    Indicator.SKEW_25D: ("skew_25d",),
    Indicator.TERM_STRUCTURE_SLOPE: ("term_structure_slope",),
    Indicator.PUT_CALL_FLOW_RATIO: ("put_call_ratio",),
    Indicator.MAX_PAIN_DISTANCE_PCT: ("max_pain_distance_pct",),
}

_SPLICE_LABELS: dict[str, tuple[str, str]] = {
    "ivx_30d": ("iVolatility IVX 30d",
                "in-house 30d ATM IV from the CBOE close chain"),
    "hv_30d": ("iVolatility 30d HV",
               "in-house 30-return HV from our own dailies"),
    "skew_25d": ("iVolatility fitted-surface 25Δ skew",
                 "in-house chain-interpolated 25Δ skew (CBOE close)"),
    "term_structure_slope": ("iVolatility fitted-surface term slope",
                             "in-house chain-interpolated term slope (CBOE close)"),
    "put_call_ratio": ("Unusual Whales flow-volume put/call ratio",
                       "chain session-volume put/call ratio (CBOE close)"),
    "max_pain_distance_pct": ("Unusual Whales max-pain table",
                              "in-house OI max pain (CBOE close)"),
}


def data_provenance(spec: StrategySpec, store: MarketStore,
                    win_start: date, win_end: date) -> list[dict[str, str]]:
    """Convention-seam disclosures for the run payload: one entry per
    spliced series the spec's conditions read, when the window reaches the
    seam. Windows ending before every seam return [] — pre-splice runs are
    bit-identical AND undecorated."""
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for cond in _spec_conditions(spec):
        for key in _PROVENANCE_SERIES.get(cond.indicator, ()):
            seam = store.splices.get(key)
            if seam is None or key in seen or win_end < seam:
                continue
            seen.add(key)
            # a series spliced in chains.py before its label lands here
            # must degrade to a generic disclosure, never a KeyError at
            # payload-build time (review finding; the sync test pins the
            # maps together)
            vendor, inhouse = _SPLICE_LABELS.get(
                key, (f"the {key} vendor series", "in-house continuation"))
            out.append({
                "series": key,
                "inhouse_from": seam.isoformat(),
                "note": (f"{vendor} froze before this window ended; sessions "
                         f"from {seam.isoformat()} read the {inhouse} — a "
                         "disclosed convention change, measured on the vendor "
                         "overlap by cross-validation"),
            })
    return sorted(out, key=lambda r: r["series"])


def _prev_session_view(store: MarketStore, day: date) -> MarketView:
    """Daily context an intraday bar may see: strictly BEFORE today's close."""
    i = bisect_right(store.sessions, day) - 1
    if i > 0:
        return MarketView(store, store.sessions[i - 1])
    return MarketView(store, day - timedelta(days=1))


def run_engine(
    spec: StrategySpec,
    store: MarketStore,
    intraday: IntradayProvider | None = None,
    entry_shift_bars: int = 0,
    progress: Callable[[int, int], None] | None = None,
    pinned_resolutions: dict[date, str] | None = None,
) -> RunResult:
    """`entry_shift_bars` is the honesty layer's entry-time-nudge lever
    (D2d): shifts the session's entry WINDOW by N 5-minute bars. Positive
    delays entries past the session start / time_of_day; negative moves a
    time_of_day gate earlier (clamped to the session start). It is not
    spec vocabulary — runs made with it are gauntlet probes.

    `progress(done_sessions, total_sessions)` fires every
    PROGRESS_EVERY_SESSIONS covered 5-min sessions — a full-history
    intraday run takes minutes and a silent stage is indistinguishable
    from a dead one (incident 2026-07-06). Gauntlet probes pass None.

    `pinned_resolutions` (Tier 1 notebook reproduce): the RECORDED
    per-session bar resolution of an earlier run — {session: "minute" |
    "five_min"}. A pinned session serves exactly the recorded grid even
    when the lake has since upgraded it (D3 receipts semantics: a fresh
    run may resolve finer, a REPLAY never silently re-resolves). Sessions
    absent from the map (none, when the window is pinned too) resolve
    live. A "minute" pin whose grid can no longer be built falls back to
    5-min and is RECORDED as five_min — the caller compares recorded maps
    and discloses the divergence rather than trusting the pin blindly."""
    five_min = spec.backtest.clock is Clock.FIVE_MIN

    if five_min:
        if intraday is None:
            raise SliceCoverageError(
                "the 5-minute clock needs the intraday store and none was provided"
            )
        _check_slice_coverage(spec, intraday)
        covered = [d for d in intraday.sessions() if d in store.underlying_close]
        if not covered:
            raise SliceCoverageError(
                "no intraday sessions overlap the underlying record — nothing to simulate"
            )
        req_start = spec.backtest.start or covered[0]
        req_end = spec.backtest.end or covered[-1]
        eff_start = max(req_start, covered[0])
        eff_end = min(req_end, store.sessions[-1])
        last_chain = covered[-1]
    else:
        if not store.chain_dates:
            raise ValueError("no options coverage for this window — nothing to simulate")
        first_chain, last_chain = store.chain_dates[0], store.chain_dates[-1]
        req_start = spec.backtest.start or first_chain
        req_end = spec.backtest.end or store.sessions[-1]
        eff_start = max(req_start, first_chain)
        eff_end = min(req_end, store.sessions[-1])

    clock = [d for d in store.sessions if eff_start <= d <= eff_end]
    if not clock:
        raise ValueError("effective window is empty after bounding by coverage")
    # F1: coverage-capped signal filters refuse windows the signal can't
    # honestly cover — at BOTH clocks, before any simulation work
    check_signal_coverage(spec, store, clock[0], clock[-1])

    # what the user asked to test, so the honesty layer can compare it against
    # the sessions that actually carried quotes (the seventeen-fills gap —
    # at the 5-min clock "carried quotes" means an intraday slice)
    requested_sessions = sum(1 for d in store.sessions if req_start <= d <= req_end)

    state = _State(cash=spec.backtest.initial_capital, positions=[], trades=[])
    result = RunResult(
        ticker=spec.underlying.ticker.value,
        effective_start=clock[0],
        effective_end=clock[-1],
        seed=spec.backtest.seed,
        requested_start=req_start,
        requested_end=req_end,
        requested_sessions=requested_sessions,
        clock=spec.backtest.clock.value,
    )

    # FX.1: per-session bar resolution. FINEST asks the provider which
    # sessions the F0 resolution map marks minute-eligible (an O(1) artifact
    # lookup done ONCE per run); everything else — and every run without the
    # v4 field — steps the 5-min grid exactly as D2 shipped it. The chosen
    # resolution of every covered session is recorded for disclosure,
    # receipts, and FX.4's mixed-resolution honesty.
    finest = five_min and spec.backtest.resolution is Resolution.FINEST
    minute_days: set[date] = set()
    if finest and intraday is not None:
        minute_days = intraday.minute_sessions()
    if spec.backtest.resolution is not None:
        result.resolution_mode = spec.backtest.resolution.value
    session_resolutions: list[tuple[date, str]] = []
    covered_sessions = 0
    intraday_lasts: list[float] = []  # rolling 5-min lasts across the run (D2c)
    tod = spec.entry.schedule.time_of_day
    tod_minutes: int | None = None
    if tod is not None:
        h, m = tod.split(":")
        tod_minutes = int(h) * 60 + int(m) + entry_shift_bars * 5

    # scale-in ladder (D5a). `bstate` persists across the run; a basket
    # carried overnight keeps accumulating, while a flat ladder re-arms at
    # each covered 5-min session. `flat_minutes` is the general session
    # force-flat (exit.close_at_time, 5-min clock only).
    scale_in: ScaleIn | None = spec.entry.scale_in
    bstate = _BasketState()
    flat_minutes: int | None = None
    if spec.exit.close_at_time is not None:
        fh, fm = spec.exit.close_at_time.split(":")
        flat_minutes = int(fh) * 60 + int(fm)

    # FX.2: continuous opportunity scanning (spec v4 entry.intraday_scan).
    # One entry per SIGNAL EPISODE (conditions false→true arm exactly one);
    # condition-less strategies treat the position LIFECYCLE as the episode
    # (arm at the window start and again when a position closes). An armed
    # order is valid until the next QUOTED bar: it fills there at the real
    # NBBO even if the signal faded (a submitted order can't be recalled),
    # and is consumed fill-or-skip — never re-armed on the same dip.
    scanning = five_min and spec.entry.intraday_scan is IntradayScan.EVERY_SETUP
    has_conditions = bool(spec.entry.conditions)

    for day in clock:
        view = MarketView(store, day)
        _unwind_pending_stock(state, view)

        slc = None
        if five_min and intraday is not None:
            pin = pinned_resolutions.get(day) if pinned_resolutions else None
            if pin is not None:
                # replay: the recorded resolution wins over live "finest"
                # in BOTH directions — a since-upgraded session stays on
                # its recorded 5-min grid (never silently re-resolve)
                want_minute = pin == RES_MINUTE
            else:
                want_minute = finest and day in minute_days
            if want_minute:
                # minute grid if the provider can actually build it; a
                # missing grid falls back to 5-min and is RECORDED as such
                slc = intraday.minute_slice_for(day)
            if slc is None or not slc.bars:
                slc = intraday.slice_for(day)
        if slc is not None and slc.bars:
            # -------------------- intraday bar loop (the declared clock;
            # bar size is a PER-SESSION value under resolution="finest")
            is_minute = slc.bar_resolution == "1min"
            session_resolutions.append(
                (day, RES_MINUTE if is_minute else RES_FIVE_MIN))
            covered_sessions += 1
            if progress is not None and covered_sessions % PROGRESS_EVERY_SESSIONS == 0:
                progress(covered_sessions, len(clock))
            prev_view = _prev_session_view(store, day)
            dte_fn = _trading_dte_fn(store, day)
            session_skips: set[str] = set()
            opened_this_session = False
            # FX.2 per-session scan state: edges tracked from the window's
            # first bar (a signal standing at the open IS a setup); armed
            # orders die with the session (no overnight orders)
            scan_armed = False
            scan_armed_bar: str | None = None
            scan_cond_prev = False
            scan_window_seen = False
            session_pv = 0.0  # session-anchored VWAP accumulators (D2c)
            session_vol = 0.0
            # nudge: delay the window. entry_shift_bars counts 5-MIN bars
            # (the D2d lever's unit); a minute grid has 5 bars per unit.
            entry_from = max(entry_shift_bars, 0) * (5 if is_minute else 1)
            # a flat ladder re-arms for a fresh oversold episode each session
            if scale_in is not None and bstate.basket is None:
                bstate.armed = True
            for bar_idx, bar in enumerate(slc.bars):
                last = slc.underlying.get(bar)
                at_stamp = slc.indicator_stamps is None or bar in slc.indicator_stamps
                if last is not None:
                    # timeframe-"5min" indicators mean ONE thing at every
                    # session of a run: on a minute grid the rolling series
                    # samples ONLY the slice's 5-min underlying stamps —
                    # the same artifact, values and session bounds the 5-min
                    # grid reads (owner decision 4: resolution must never
                    # silently change signal meaning). Minute bars carry no
                    # volume, so session VWAP is stamp-identical too; they
                    # only refine the PRICE between stamps.
                    if at_stamp:
                        intraday_lasts.append(last)
                    vol = slc.underlying_volume.get(bar, 0.0)
                    session_pv += last * vol
                    session_vol += vol
                vwap_now = session_pv / session_vol if session_vol > 0 else None
                bview = BarView(IntradayView(slc, bar), prev_view,
                                intraday_lasts, len(intraday_lasts), vwap_now,
                                is_indicator_stamp=at_stamp)
                events_before = len(state.trades)
                fills_before = len(state.fill_log)
                bar_minutes = bar.hour * 60 + bar.minute
                bar_hhmm = bar.strftime("%H:%M")
                past_flat = flat_minutes is not None and bar_minutes >= flat_minutes
                if past_flat:
                    # close_at_time overrides everything at/after its bar: flat
                    # the book, mint nothing (no exits/adds beyond the flatten,
                    # and any armed order dies unfilled — counted once)
                    if scanning and scan_armed:
                        _count_skip(state, session_skips, day, "no_quote_this_bar")
                    scan_armed = False
                    if finest:
                        # a pending latched exit first fillable here closes
                        # under its OWN reason, not session_flat — the
                        # trade log must not misattribute a triggered exit
                        # (review finding)
                        for pos in state.live:
                            if (pos.exit_latched is not None and not pos.closed
                                    and not all(leg.settled for leg in pos.legs)):
                                _try_complete_latch(pos, bview, state, spec)
                    _force_flat(spec, state, bview)
                    if scale_in is not None and (
                        bstate.basket is not None and bstate.basket.closed
                    ):
                        bstate.basket = None
                        bstate.armed = False
                else:
                    # exits BEFORE entries at every bar: a position opened at
                    # bar t is first evaluated at bar t+1 (owner amendment 2 —
                    # a stop can never fire on its own entry bar)
                    _check_exits(spec, state, bview, dte_fn,
                                 latch=finest, bar_hhmm=bar_hhmm)
                    # event-based (O(events-this-bar), never O(positions) —
                    # post-OOM rule); only the condition-less lifecycle
                    # re-arm consumes it. NOTE: a covered-call CLOSE keeps
                    # its stock (slot not freed) — the fresh episode is then
                    # consumed as max_concurrent, honest accounting noise.
                    closed_this_bar = scanning and not has_conditions and any(
                        ev.action == "CLOSE" for ev in state.trades[events_before:]
                    )
                    in_window = (
                        _schedule_matches(spec, state, day)
                        and bar_idx >= entry_from
                        and (tod_minutes is None or bar_minutes >= tod_minutes)
                    )
                    if scale_in is not None:
                        if bstate.basket is not None and bstate.basket.closed:
                            bstate.basket = None
                            bstate.armed = False
                        if in_window:
                            _manage_basket(spec, state, bview, bstate, dte_fn,
                                           session_skips, bar_hhmm)
                    elif scanning and in_window:
                        # ---- FX.2 continuous scanning (episode/armed) ----
                        first_window_bar = not scan_window_seen
                        scan_window_seen = True
                        if has_conditions:
                            passes = all_conditions_pass(bview, spec.entry.conditions)
                            if passes and not scan_cond_prev:
                                if not scan_armed:
                                    scan_armed = True  # a fresh setup — one entry
                                    scan_armed_bar = bar_hhmm
                                else:
                                    # one working order at a time: an edge
                                    # arriving while an order is armed is a
                                    # REAL missed setup — counted, disclosed
                                    _count_skip(state, session_skips, day,
                                                "order_in_flight")
                            scan_cond_prev = passes
                        elif not scan_armed and (first_window_bar or closed_this_bar):
                            # condition-less: the position lifecycle is the
                            # episode — arm at the window start and re-arm
                            # when a position closes (cycling)
                            scan_armed = True
                            scan_armed_bar = bar_hhmm
                        if scan_armed:
                            open_count = sum(1 for p in state.live if not p.closed)
                            if open_count >= spec.entry.max_concurrent_positions:
                                _count_skip(state, session_skips, day, "max_concurrent")
                                scan_armed = False  # consumed — never re-armed
                            elif not bview.has_chain:
                                # one-QUOTED-bar validity: the armed order
                                # WAITS through quote-less bars — waiting is
                                # not a skip; the episode is counted only if
                                # it dies unfilled (session end / flatten)
                                pass
                            else:
                                equity_now = state.cash + sum(
                                    _position_value(p, bview.close() or 0.0)
                                    for p in state.live
                                    if not p.closed
                                )
                                before = len(state.positions)
                                _try_entry(spec, state, bview, equity_now, dte_fn,
                                           session_skips, skip_conditions=True)
                                if (len(state.positions) > before
                                        and scan_armed_bar is not None
                                        and scan_armed_bar != bar_hhmm):
                                    # disclose the gap: signal bar → fill bar
                                    state.trades[-1].detail += (
                                        f" · armed {scan_armed_bar}")
                                scan_armed = False  # consumed fill-or-skip
                                scan_armed_bar = None
                    elif (
                        not scanning
                        and not opened_this_session
                        and in_window
                    ):
                        equity_now = state.cash + sum(
                            _position_value(p, bview.close() or 0.0)
                            for p in state.live
                            if not p.closed
                        )
                        before = len(state.positions)
                        _try_entry(spec, state, bview, equity_now, dte_fn, session_skips)
                        opened_this_session = len(state.positions) > before
                # every fill stays inspectable: stamp this bar's time onto
                # the events it produced (the log is day-granular otherwise)
                for ev in state.trades[events_before:]:
                    if ev.action in ("OPEN", "CLOSE", "ADD"):
                        ev.bar_time = bar_hhmm
                        ev.detail += f" · {ev.bar_time}"
                # F7 review #4: the structured fill log gets ITS OWN bar
                # time the same way — a CLOSE fill must be audited in a
                # window around the CLOSE bar, never the OPEN's
                for row in state.fill_log[fills_before:]:
                    row["bar_time"] = bar_hhmm
                # retire closed positions from the live book — O(open) per
                # bar keeps every hot path bounded however many positions a
                # scanning run mints (OOM-guard directive)
                state.live[:] = [p for p in state.live if not p.closed]
            if scanning and scan_armed:
                # an armed order that never met a quoted bar dies with the
                # session — counted once per episode, never per waiting bar
                _count_skip(state, session_skips, day, "no_quote_this_bar")
            _settle_expirations(spec, state, view)  # 0DTE settles at the close
            state.live[:] = [p for p in state.live if not p.closed]
            close_px = view.close()
            if close_px is not None:
                open_positions = [p for p in state.live if not p.closed]
                last_bar = BarView(IntradayView(slc, slc.bars[-1]), prev_view)
                for pos in open_positions:
                    _refresh_marks(pos, last_bar, spec.costs)
                equity = state.cash + sum(_position_value(p, close_px) for p in open_positions)
                result.dates.append(day)
                result.equity.append(round(equity, 2))
                pd_, pg, pt, pv = _portfolio_greeks(open_positions, last_bar)
                result.portfolio_delta.append(None if pd_ is None else round(pd_, 2))
                result.portfolio_gamma.append(None if pg is None else round(pg, 4))
                result.portfolio_theta.append(None if pt is None else round(pt, 2))
                result.portfolio_vega.append(None if pv is None else round(pv, 2))
                if open_positions:
                    result.days_in_market += 1
            continue

        # ------------------------------ daily close path (also the 5-min
        # clock's fallback on sessions without an intraday slice: exits,
        # settlement and marks use the REAL EOD chain — coarser timing,
        # never synthetic — and NO new entries are minted)
        _check_exits(spec, state, view)
        _settle_expirations(spec, state, view)
        state.live[:] = [p for p in state.live if not p.closed]

        if scale_in is not None and not five_min:
            # daily-clock ladder (the degenerate case: adds land on successive
            # SESSIONS as the daily signal deepens). Only on the true daily
            # clock — a 5-min gap session never mints a basket.
            if day <= last_chain and _schedule_matches(spec, state, day):
                if bstate.basket is not None and bstate.basket.closed:
                    bstate.basket = None
                    bstate.armed = False
                _manage_basket(spec, state, view, bstate, None, None, None)
        elif not five_min and day <= last_chain and _schedule_matches(spec, state, day):
            equity_now = state.cash + sum(
                _position_value(p, view.close() or 0.0)
                for p in state.live
                if not p.closed
            )
            _try_entry(spec, state, view, equity_now)

        # mark to market
        close_px = view.close()
        if close_px is not None:
            open_positions = [p for p in state.live if not p.closed]
            for pos in open_positions:
                _refresh_marks(pos, view, spec.costs)
            equity = state.cash + sum(_position_value(p, close_px) for p in open_positions)
            result.dates.append(day)
            result.equity.append(round(equity, 2))
            pd_, pg, pt, pv = _portfolio_greeks(open_positions, view)
            result.portfolio_delta.append(None if pd_ is None else round(pd_, 2))
            result.portfolio_gamma.append(None if pg is None else round(pg, 4))
            result.portfolio_theta.append(None if pt is None else round(pt, 2))
            result.portfolio_vega.append(None if pv is None else round(pv, 2))
            if open_positions:
                result.days_in_market += 1

    result.trades = state.trades
    result.filled = sum(1 for t in state.trades if t.action == "OPEN")
    result.skipped = sum(1 for t in state.trades if t.action == "SKIP")
    result.sessions_with_chain = (
        covered_sessions if five_min else sum(1 for d in clock if d in store.chains)
    )
    result.fill_spread_pcts = state.fill_spread_pcts
    result.option_leg_fills = state.option_leg_fills
    result.fills_penalized = state.fills_penalized
    result.fills_stressed = state.fills_stressed
    result.fills_unknown_liquidity = state.fills_unknown_liquidity
    result.fills_depth_known = state.fills_depth_known
    result.fills_beyond_depth = state.fills_beyond_depth
    result.fill_sources = state.fill_sources
    result.fill_log = state.fill_log
    result.rung_fills = state.rung_fills
    result.skip_reasons = state.skip_counts
    if session_resolutions:
        mix: dict[str, int] = {}
        for _, res in session_resolutions:
            mix[res] = mix.get(res, 0) + 1
        result.resolution_mix = mix
        result.resolution_runs = _compress_resolutions(session_resolutions)
        result.resolution_by_session = dict(session_resolutions)
    return result


def _compress_resolutions(
    session_resolutions: list[tuple[date, str]],
) -> list[dict[str, object]]:
    """Consecutive same-resolution covered sessions → compact runs (the
    payload/receipts shape; a multi-year run stays a handful of rows)."""
    runs: list[dict[str, object]] = []
    for day, res in session_resolutions:
        if runs and runs[-1]["resolution"] == res:
            runs[-1]["last"] = day.isoformat()
            runs[-1]["sessions"] = int(runs[-1]["sessions"]) + 1  # type: ignore[call-overload]
        else:
            runs.append({"first": day.isoformat(), "last": day.isoformat(),
                         "sessions": 1, "resolution": res})
    return runs
