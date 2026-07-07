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

from bisect import bisect_right
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta

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
                     stressed: bool, source: str) -> None:
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
    ) -> None:
        self._iview = iview
        self._prev = prev
        # the run's rolling 5-min lasts: the engine appends as bars advance,
        # so a length snapshot bounds this view at the current bar (D2c)
        self._lasts = intraday_lasts if intraday_lasts is not None else []
        self._lasts_len = lasts_len
        self._vwap = vwap

    @property
    def as_of(self) -> date:
        return self._iview.session

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


def _close_slip(q: Quote, costs: Costs, stressed: bool = False) -> float:
    """The slip a close-side price uses: base, OI-scaled when OI is known
    and thin (fills.effective_slip). Exit triggers, exit fills and marks all
    price through here, so open and close share one fill model. Modeled
    quotes are always stressed — slip 1.0, both directions."""
    if stressed:
        return 1.0
    return fills.effective_slip(
        costs.slippage_half_spread_fraction, q, costs.min_open_interest
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
        px = fills.fill_price(q, fills.close_action(leg.side), _close_slip(q, costs, stressed))
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
        px = fills.fill_price(q, fills.close_action(leg.side), _close_slip(q, costs, stressed))
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
    slip = spec.costs.slippage_half_spread_fraction
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
        eff = 1.0 if stressed else fills.effective_slip(slip, q, spec.costs.min_open_interest)
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
        cash_delta = px * qty * MULT if leg.side is Side.SHORT else -px * qty * MULT
        cash_delta -= commission * qty
        state.cash += cash_delta
        pos.cash_flow += cash_delta
        pos.legs.append(
            OpenLeg(key=key, side=leg.side.value, qty=qty, entry_price=px, last_mark=px)
        )

    for q, eff, was_stressed in zip(leg_quotes, leg_slips, leg_stressed, strict=True):
        _record_leg_fill(state, q, eff, slip, was_stressed, view.fill_source)

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
            detail=f"{_position_desc(pos)} · exp {expiration} · {kind} {abs(premium):.3f}",
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
) -> None:
    """Fire every not-yet-fired rung whose condition passes at THIS bar, at the
    current bar's ASK (guardrail #1; D1b liquidity gates apply per fill). Adds
    are clamped to max_total_contracts (flagged cap_clamped); once the cap is
    reached no deeper rung fires. Rungs fired AT the opening bar fold into the
    OPEN event (opening=True → no ADD events); later fires emit ADD events.
    Every fill reads only the current bar's quote — the add can never peek at a
    future bar (the intraday lookahead canary asserts this)."""
    si = spec.entry.scale_in
    assert si is not None
    slip = spec.costs.slippage_half_spread_fraction
    commission = spec.costs.commission_per_contract
    leg = basket.legs[0]
    key = leg.key
    action = fills.open_action(leg.side)  # "buy" for a long basket
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
        eff = 1.0 if stressed else fills.effective_slip(slip, q, spec.costs.min_open_interest)
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
        _record_leg_fill(state, q, eff, slip, stressed, view.fill_source)
        state.rung_fills.append(
            RungFill(
                basket_pid=basket.pid, day=view.as_of, bar_time=bar_time,
                rung_index=idx, threshold=rung.value, qty=qty, fill_price=px,
                fill_source=view.fill_source, cap_clamped=clamped,
            )
        )
        if not opening:
            tag = " cap_clamped" if clamped else ""
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
    _fire_rungs(spec, state, view, pos, opening=True, bar_time=bar_time,
                session_skips=session_skips)
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
                   f"{'s' if rungs_hit != 1 else ''}",
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
    slip = spec.costs.slippage_half_spread_fraction
    commission = spec.costs.commission_per_contract
    liq = _liq_value_per_share(pos, view, spec.costs)
    if liq is None:
        return False
    stressed = view.fill_source in STRESSED_SOURCES
    for leg in pos.legs:
        if leg.settled:
            continue
        q = view.quote(leg.key)
        assert q is not None
        eff = _close_slip(q, spec.costs, stressed)
        px = fills.fill_price(q, fills.close_action(leg.side), eff)
        assert px is not None
        cash_delta = px * leg.qty * MULT if leg.side == "long" else -px * leg.qty * MULT
        cash_delta -= commission * leg.qty
        state.cash += cash_delta
        pos.cash_flow += cash_delta
        leg.settled = True
        _record_leg_fill(state, q, eff, slip, stressed=False, source=view.fill_source)
    pos.closed = pos.stock_shares == 0
    state.trades.append(
        TradeEvent(
            day=view.as_of,
            action="CLOSE",
            detail=_position_desc(pos),
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
            if _close_position(pos, view, state, spec, pos.exit_latched):
                if pos.latched_bar is not None:
                    state.trades[-1].detail += f" · triggered {pos.latched_bar}"
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


def _settle_expirations(spec: StrategySpec, state: _State, view: MarketViewLike) -> None:
    day = view.as_of
    close_px = view.close()
    if close_px is None:
        return
    for pos in state.live:
        if pos.closed:
            continue
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
) -> RunResult:
    """`entry_shift_bars` is the honesty layer's entry-time-nudge lever
    (D2d): shifts the session's entry WINDOW by N 5-minute bars. Positive
    delays entries past the session start / time_of_day; negative moves a
    time_of_day gate earlier (clamped to the session start). It is not
    spec vocabulary — runs made with it are gauntlet probes.

    `progress(done_sessions, total_sessions)` fires every
    PROGRESS_EVERY_SESSIONS covered 5-min sessions — a full-history
    intraday run takes minutes and a silent stage is indistinguishable
    from a dead one (incident 2026-07-06). Gauntlet probes pass None."""
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
            if finest and day in minute_days:
                # minute grid if the provider can actually build it; a
                # missing grid falls back to 5-min and is RECORDED as such
                slc = intraday.minute_slice_for(day)
            if slc is None or not slc.bars:
                slc = intraday.slice_for(day)
        if slc is not None and slc.bars:
            # -------------------- intraday bar loop (the declared clock;
            # bar size is a PER-SESSION value under resolution="finest")
            is_minute = slc.bar_resolution == "1min"
            session_resolutions.append((day, "minute" if is_minute else "five_min"))
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
                if last is not None:
                    # timeframe-"5min" indicators mean ONE thing at every
                    # session of a run: on a minute grid the rolling series
                    # samples ONLY the slice's 5-min underlying stamps —
                    # the same artifact, values and session bounds the 5-min
                    # grid reads (owner decision 4: resolution must never
                    # silently change signal meaning). Minute bars carry no
                    # volume, so session VWAP is stamp-identical too; they
                    # only refine the PRICE between stamps.
                    if slc.indicator_stamps is None or bar in slc.indicator_stamps:
                        intraday_lasts.append(last)
                    vol = slc.underlying_volume.get(bar, 0.0)
                    session_pv += last * vol
                    session_vol += vol
                vwap_now = session_pv / session_vol if session_vol > 0 else None
                bview = BarView(IntradayView(slc, bar), prev_view,
                                intraday_lasts, len(intraday_lasts), vwap_now)
                events_before = len(state.trades)
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
    result.fill_sources = state.fill_sources
    result.rung_fills = state.rung_fills
    result.skip_reasons = state.skip_counts
    if session_resolutions:
        mix: dict[str, int] = {}
        for _, res in session_resolutions:
            mix[res] = mix.get(res, 0) + 1
        result.resolution_mix = mix
        result.resolution_runs = _compress_resolutions(session_resolutions)
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
