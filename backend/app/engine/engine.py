"""Event-driven daily EOD engine (TECH-SPEC §5).

Daily order of operations:
  1. unwind assignment stock at today's OPEN (scheduled yesterday)
  2. exits at today's close quotes — priority: stop → profit target →
     time exit → condition exits
  3. expiration settlement for positions expiring today
  4. entries (schedule + conditions + capacity + chain availability);
     anything unfillable is a SKIP with a reason code
  5. mark-to-market at conservative liquidation prices → equity point

Positions are marked and exited with the SAME fill model used to open
them (guardrail #1). Days without a chain snapshot mark stale and cannot
fill exits — honest behavior on checkpoint-marked history (DOLTHUB-EVAL
§7); the settlement path still works because it uses underlying closes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from app.engine import fills
from app.engine.conditions import all_conditions_pass
from app.engine.market import MarketStore, MarketView
from app.engine.selection import select_expiration, select_legs
from app.engine.types import MULT, ContractKey, OpenLeg, Position, RunResult, TradeEvent
from app.models.spec import Frequency, Side, SizingMethod, StrategySpec, Structure


@dataclass
class _State:
    cash: float
    positions: list[Position]
    trades: list[TradeEvent]
    next_pid: int = 1
    last_entry_month: tuple[int, int] | None = None


def _leg_desc(leg: OpenLeg) -> str:
    sign = "-" if leg.side == "short" else "+"
    r = "P" if leg.key.right == "put" else "C"
    return f"{sign}{leg.qty}{r}{leg.key.strike:g}"


def _position_desc(pos: Position) -> str:
    return " ".join(_leg_desc(leg) for leg in pos.legs)


def _liq_value_per_share(pos: Position, view: MarketView, slip: float) -> float | None:
    """Signed liquidation value per contract-set per share, at today's
    quotes: closing longs sells (+), closing shorts buys back (−).
    None when any unsettled leg lacks a usable quote today."""
    total = 0.0
    for leg in pos.legs:
        if leg.settled:
            continue
        q = view.quote(leg.key)
        if q is None:
            return None
        px = fills.fill_price(q, fills.close_action(leg.side), slip)
        if px is None:
            return None
        ratio = leg.qty // max(pos.contracts, 1)
        total += px * ratio if leg.side == "long" else -px * ratio
    return total


def _refresh_marks(pos: Position, view: MarketView, slip: float) -> None:
    for leg in pos.legs:
        if leg.settled:
            continue
        q = view.quote(leg.key)
        if q is None:
            continue
        px = fills.fill_price(q, fills.close_action(leg.side), slip)
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


def _try_entry(spec: StrategySpec, state: _State, view: MarketView, equity_now: float) -> None:
    day = view.as_of
    slip = spec.costs.slippage_half_spread_fraction
    commission = spec.costs.commission_per_contract

    def skip(reason: str, detail: str = "") -> None:
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
    if spec.entry.conditions and not all_conditions_pass(view, spec.entry.conditions):
        skip("conditions_not_met")
        return

    chain = view.chain()
    spot = view.close()
    if spot is None:
        skip("no_underlying_close")
        return

    expiration = select_expiration(chain, day, spec.position.expiration_selection)
    if expiration is None:
        skip("no_expiration_in_window")
        return

    keys, reason = select_legs(chain, expiration, spec.position.legs, spot)
    if keys is None:
        skip(reason or "selection_failed")
        return

    # validate quotes + compute per-share entry fills
    entry_fills: list[float] = []
    for key, leg in zip(keys, spec.position.legs, strict=True):
        action = fills.open_action(leg.side.value)
        q = chain.get(key)
        problem = fills.quote_problem(q, action)
        if problem is not None:
            skip(problem, detail=f"{key.right} {key.strike:g} exp {key.expiration}")
            return
        assert q is not None
        px = fills.fill_price(q, action, slip)
        if px is None:  # pragma: no cover — quote_problem gates this
            skip("missing_quote")
            return
        entry_fills.append(px)

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

    state.positions.append(pos)
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


def _close_position(
    pos: Position, view: MarketView, state: _State, spec: StrategySpec, reason: str
) -> bool:
    """Close all unsettled option legs at today's quotes. False if any leg
    lacks a usable quote (the attempt is retried on later sessions)."""
    slip = spec.costs.slippage_half_spread_fraction
    commission = spec.costs.commission_per_contract
    liq = _liq_value_per_share(pos, view, slip)
    if liq is None:
        return False
    for leg in pos.legs:
        if leg.settled:
            continue
        q = view.quote(leg.key)
        assert q is not None
        px = fills.fill_price(q, fills.close_action(leg.side), slip)
        assert px is not None
        cash_delta = px * leg.qty * MULT if leg.side == "long" else -px * leg.qty * MULT
        cash_delta -= commission * leg.qty
        state.cash += cash_delta
        pos.cash_flow += cash_delta
        leg.settled = True
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


def _check_exits(spec: StrategySpec, state: _State, view: MarketView) -> None:
    slip = spec.costs.slippage_half_spread_fraction
    exit_rules = spec.exit
    for pos in state.positions:
        if pos.closed or all(leg.settled for leg in pos.legs):
            continue
        liq = _liq_value_per_share(pos, view, slip)
        base = abs(pos.premium)
        profit_pct: float | None = None
        if liq is not None and base > 0:
            profit_pct = (pos.premium + liq) / base * 100.0

        # priority: stop → profit target → time → condition exits
        if (
            exit_rules.stop_loss_pct is not None
            and profit_pct is not None
            and -profit_pct >= exit_rules.stop_loss_pct
        ):
            _close_position(pos, view, state, spec, "stop_loss")
            continue
        if (
            exit_rules.profit_target_pct is not None
            and profit_pct is not None
            and profit_pct >= exit_rules.profit_target_pct
        ):
            _close_position(pos, view, state, spec, "profit_target")
            continue
        if exit_rules.time_exit_dte is not None and exit_rules.time_exit_dte > 0:
            dte = min((leg.key.expiration - view.as_of).days for leg in pos.legs if not leg.settled)
            if dte <= exit_rules.time_exit_dte:
                _close_position(pos, view, state, spec, "time_exit")
                continue
        if exit_rules.conditions and all_conditions_pass(view, exit_rules.conditions):
            _close_position(pos, view, state, spec, "condition_exit")


def _settle_expirations(spec: StrategySpec, state: _State, view: MarketView) -> None:
    day = view.as_of
    close_px = view.close()
    if close_px is None:
        return
    for pos in state.positions:
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
    for pos in state.positions:
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


def run_engine(spec: StrategySpec, store: MarketStore) -> RunResult:
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

    state = _State(cash=spec.backtest.initial_capital, positions=[], trades=[])
    slip = spec.costs.slippage_half_spread_fraction
    result = RunResult(
        ticker=spec.underlying.ticker.value,
        effective_start=clock[0],
        effective_end=clock[-1],
        seed=spec.backtest.seed,
    )

    for day in clock:
        view = MarketView(store, day)
        _unwind_pending_stock(state, view)
        _check_exits(spec, state, view)
        _settle_expirations(spec, state, view)

        if day <= last_chain and _schedule_matches(spec, state, day):
            equity_now = state.cash + sum(
                _position_value(p, view.close() or 0.0)
                for p in state.positions
                if not p.closed
            )
            _try_entry(spec, state, view, equity_now)

        # mark to market
        close_px = view.close()
        if close_px is not None:
            open_positions = [p for p in state.positions if not p.closed]
            for pos in open_positions:
                _refresh_marks(pos, view, slip)
            equity = state.cash + sum(_position_value(p, close_px) for p in open_positions)
            result.dates.append(day)
            result.equity.append(round(equity, 2))
            if open_positions:
                result.days_in_market += 1

    result.trades = state.trades
    result.filled = sum(1 for t in state.trades if t.action == "OPEN")
    result.skipped = sum(1 for t in state.trades if t.action == "SKIP")
    result.sessions_with_chain = sum(1 for d in clock if d in store.chains)
    return result
