"""Event-driven simulation, one event at a time.

The loop handles events in time order. An execution event is one bar or one quote spanning
[start, end). Before an event is handled, every signal bar closed by its start is shown to the
strategy, so a decision can only use bars that have finished, and any resulting order can only
fill on a later event.

Within an execution bar the path is unknown, so the rule is conservative:
  1. Pending orders are handled at the bar's open.
  2. A gap through a stop fills at the open, not at the stop.
  3. If a bar touches both stop and target, the stop counts.
Long positions buy at the ask and sell at the bid. Shorts do the reverse. So stops and targets are
tested against the side that would actually fill.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

import numpy as np
import pandas as pd

from quantresearch.sim.costs import OZ_PER_LOT, CostModel, MissingCostData

LOT_STEP = 0.01


@dataclass
class OrderIntent:
    """What a strategy or skill wants. It has no effect until the engine and risk gate accept it."""
    side: int  # +1 long, -1 short
    lots: float
    kind: str = "market"  # market, limit or stop
    price: float | None = None
    stop: float | None = None
    target: float | None = None
    expires_at: pd.Timestamp | None = None
    tag: str = ""
    reduce_only_id: int | None = None


@dataclass
class Order:
    id: int
    intent: OrderIntent
    submitted_at: pd.Timestamp
    remaining: float
    status: str = "pending"


@dataclass
class Position:
    id: int
    side: int
    lots: float
    entry_time: pd.Timestamp
    entry_price: float
    stop: float | None
    target: float | None
    tag: str
    order_id: int
    initial_lots: float
    initial_stop: float | None
    commission: float = 0.0
    swap: float = 0.0
    realized: float = 0.0
    cost_source: str = ""
    entry_spread: float = 0.0
    last_swap_day: Any = None


@dataclass
class Trade:
    position_id: int
    tag: str
    side: int
    lots: float
    entry_time: pd.Timestamp
    entry_price: float
    exit_time: pd.Timestamp
    exit_price: float
    reason: str
    gross: float
    commission: float
    swap: float
    net: float
    r_multiple: float | None
    cost_source: str
    entry_spread: float
    stop: float | None
    target: float | None


@dataclass
class Account:
    balance: float
    equity: float
    peak_equity: float

    @property
    def drawdown(self) -> float:
        return 0.0 if self.peak_equity <= 0 else 1 - self.equity / self.peak_equity


@dataclass
class EngineConfig:
    initial_equity: float = 100_000.0
    costs: CostModel = field(default_factory=CostModel)
    stop_first: bool = True
    allow_same_bar_exit: bool = True
    record_equity_every: int = 1
    log_signal_bars: bool = True


@dataclass
class Decision:
    """A risk gate's answer: approved, reduced or rejected, with a reason."""
    approved: bool
    lots: float | None = None
    reason: str = ""


class Context:
    """What a strategy may see at a signal bar's close: closed bars only."""

    def __init__(self, engine: "Engine"):
        self._e = engine

    @property
    def now(self) -> pd.Timestamp:
        return self._e.now

    @property
    def index(self) -> int:
        return self._e.signal_index

    @property
    def bar(self) -> dict:
        return self._e.signal_row(self._e.signal_index)

    def history(self, n: int | None = None) -> pd.DataFrame:
        stop = self._e.signal_index + 1
        start = 0 if n is None else max(0, stop - n)
        return self._e.signals.iloc[start:stop]

    @property
    def account(self) -> Account:
        return self._e.account

    @property
    def positions(self) -> list[Position]:
        return list(self._e.positions)

    @property
    def orders(self) -> list[Order]:
        return list(self._e.pending)

    def submit(self, intent: OrderIntent) -> Order | None:
        return self._e.submit(intent)

    def buy(self, lots: float, **kw) -> Order | None:
        return self.submit(OrderIntent(+1, lots, **kw))

    def sell(self, lots: float, **kw) -> Order | None:
        return self.submit(OrderIntent(-1, lots, **kw))

    def close(self, position: Position, reason: str = "strategy") -> None:
        self._e.request_close(position, reason)

    def modify(self, position: Position, stop: float | None = None, target: float | None = None) -> None:
        self._e.modify(position, stop, target)

    def cancel(self, order: Order) -> None:
        self._e.cancel(order)

    def log(self, message: str, **data) -> None:
        self._e.log("note", message=message, **data)


class Strategy:
    """Strategies implement on_bar. They may hold state but get no access to the future."""
    name = "strategy"

    def on_start(self, ctx: Context) -> None:
        pass

    def on_bar(self, ctx: Context) -> None:
        pass


def _clean(value: Any) -> Any:
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, (np.floating, float)):
        return None if np.isnan(value) else round(float(value), 8)
    if isinstance(value, (np.integer,)):
        return int(value)
    return value


class Engine:
    def __init__(self, execution: pd.DataFrame, signals: pd.DataFrame | None, strategy: Strategy,
                 config: EngineConfig | None = None,
                 risk_gate: Callable[[OrderIntent, "Engine"], Decision] | None = None):
        """`execution` holds bars or quotes. `signals` holds bars with any features attached.

        For quotes, pass columns ts, bid, ask. For bars, the warehouse's columns.
        """
        self.config = config or EngineConfig()
        self.costs = self.config.costs
        self.strategy = strategy
        self.risk_gate = risk_gate
        self.is_ticks = "bid" in execution.columns and "bid_open" not in execution.columns
        self._load(execution)
        self.signals = signals if signals is not None else execution.iloc[0:0]
        self._signal_close = (pd.DatetimeIndex(self.signals["close_ts"]).asi8 if len(self.signals)
                              else np.empty(0, dtype="int64"))
        self._signal_stamps = pd.DatetimeIndex(self.signals["close_ts"]) if len(self.signals) else None
        self.signal_index = -1
        self.cursor = 0
        self.now = pd.Timestamp(execution["ts"].iloc[0]) if len(execution) else pd.Timestamp(0, tz="UTC")
        self.account = Account(self.config.initial_equity, self.config.initial_equity, self.config.initial_equity)
        self.positions: list[Position] = []
        self.pending: list[Order] = []
        self.trades: list[Trade] = []
        self.events: list[dict] = []
        self.equity_times: list[pd.Timestamp] = []
        self.equity_values: list[float] = []
        self._next_id = 1
        self._close_requests: list[tuple[int, str]] = []
        self.ctx = Context(self)
        self._last_bid = self._last_ask = np.nan
        self.strategy.on_start(self.ctx)

    # ------------------------------------------------------------ data
    def _load(self, df: pd.DataFrame) -> None:
        self._ts = pd.DatetimeIndex(df["ts"])
        self._n = len(df)
        if self.is_ticks:
            bid, ask = df["bid"].to_numpy(float), df["ask"].to_numpy(float)
            self._bo, self._bh, self._bl, self._bc = bid, bid, bid, bid
            self._ao, self._ah, self._al, self._ac = ask, ask, ask, ask
            self._end = self._ts
            self._volume = None
            self._source = "observed_tick"
        else:
            self._bo, self._bh, self._bl, self._bc = (df[f"bid_{x}"].to_numpy(float) for x in ("open", "high", "low", "close"))
            self._ao, self._ah, self._al, self._ac = (df[f"ask_{x}"].to_numpy(float) for x in ("open", "high", "low", "close"))
            self._end = pd.DatetimeIndex(df["close_ts"])
            self._volume = (df["bid_volume"].fillna(0) + df["ask_volume"].fillna(0)).to_numpy(float)
            self._source = "observed_ask_candle"

    def signal_row(self, i: int) -> dict:
        return self.signals.iloc[i].to_dict()

    def _ask_prices(self, i: int) -> tuple[float, float, float, float, str]:
        """Ask OHLC at event i with the extra spread. Falls back to an assumed spread, labelled."""
        extra = self.costs.extra_spread_usd
        a = self._ao[i], self._ah[i], self._al[i], self._ac[i]
        if np.isnan(a[0]) or np.isnan(a[3]):
            if self.costs.assumed_spread_usd is None:
                raise MissingCostData(f"No ask at {self._ts[i]} and no assumed spread is configured")
            s = self.costs.assumed_spread_usd + extra
            return (self._bo[i] + s, self._bh[i] + s, self._bl[i] + s, self._bc[i] + s, "assumed")
        return (a[0] + extra, a[1] + extra, a[2] + extra, a[3] + extra, self._source)

    # ------------------------------------------------------------ orders
    def log(self, event_name: str, /, **data) -> None:
        self.events.append({"seq": len(self.events), "time": self.now.isoformat(), "event": event_name,
                            **{k: _clean(v) for k, v in data.items()}})

    def submit(self, intent: OrderIntent) -> Order | None:
        lots = round(intent.lots / LOT_STEP) * LOT_STEP
        if lots <= 0:
            self.log("order_rejected", reason="size rounds to zero", tag=intent.tag)
            return None
        reference = intent.price
        if intent.stop is not None and intent.target is not None and intent.side * (intent.target - intent.stop) <= 0:
            self.log("order_rejected", reason="target is on the wrong side of the stop", tag=intent.tag)
            return None
        if self.risk_gate is not None:
            decision = self.risk_gate(intent, self)
            self.log("risk_decision", approved=decision.approved, reason=decision.reason, lots=decision.lots,
                     requested=lots, side=intent.side, tag=intent.tag, price=reference, stop=intent.stop)
            if not decision.approved:
                return None
            if decision.lots is not None:
                lots = round(decision.lots / LOT_STEP) * LOT_STEP
                if lots <= 0:
                    return None
        intent.lots = lots
        order = Order(self._next_id, intent, self.now, lots)
        self._next_id += 1
        self.pending.append(order)
        self.log("order_submitted", order=order.id, side=intent.side, lots=lots, kind=intent.kind,
                 price=intent.price, stop=intent.stop, target=intent.target, tag=intent.tag)
        return order

    def cancel(self, order: Order) -> None:
        if order in self.pending:
            self.pending.remove(order)
            order.status = "cancelled"
            self.log("order_cancelled", order=order.id)

    def request_close(self, position: Position, reason: str) -> None:
        self._close_requests.append((position.id, reason))
        self.log("close_requested", position=position.id, reason=reason)

    def modify(self, position: Position, stop: float | None, target: float | None) -> None:
        if stop is not None:
            position.stop = stop
        if target is not None:
            position.target = target
        self.log("position_modified", position=position.id, stop=position.stop, target=position.target)

    # ------------------------------------------------------------ fills
    def _commission(self, lots: float) -> float:
        return self.costs.commission_per_lot * lots

    def _open(self, order: Order, price: float, lots: float, source: str, spread: float, i: int) -> None:
        it = order.intent
        pos = Position(self._next_id, it.side, lots, self._ts[i], price, it.stop, it.target, it.tag, order.id,
                       lots, it.stop, cost_source=source, entry_spread=spread,
                       last_swap_day=self._ts[i].date())
        self._next_id += 1
        pos.commission = self._commission(lots)
        self.account.balance -= pos.commission
        self.positions.append(pos)
        self.log("fill", kind="entry", order=order.id, position=pos.id, side=it.side, lots=lots, price=price,
                 cost_source=source, spread=spread, stop=it.stop, target=it.target, tag=it.tag)

    def _close(self, pos: Position, price: float, reason: str, i: int, when: pd.Timestamp | None = None) -> None:
        when = self._ts[i] if when is None else when
        gross = pos.side * (price - pos.entry_price) * pos.lots * OZ_PER_LOT
        exit_commission = self._commission(pos.lots)
        net = gross - pos.commission - exit_commission + pos.swap
        self.account.balance += gross - exit_commission + pos.swap
        risk = None
        if pos.initial_stop is not None:
            per_oz = pos.side * (pos.entry_price - pos.initial_stop)
            if per_oz > 0:
                risk = net / (per_oz * pos.lots * OZ_PER_LOT)
        self.trades.append(Trade(pos.id, pos.tag, pos.side, pos.lots, pos.entry_time, pos.entry_price, when, price, reason,
                                 gross, pos.commission + exit_commission, pos.swap, net, risk, pos.cost_source,
                                 pos.entry_spread, pos.initial_stop, pos.target))
        self.positions.remove(pos)
        self.log("fill", kind="exit", position=pos.id, side=-pos.side, lots=pos.lots, price=price, reason=reason, net=net,
                 tag=pos.tag)

    def _process_orders(self, i: int) -> None:
        if not self.pending:
            return
        start = self._ts[i]
        ao, ah, al, ac, source = self._ask_prices(i)
        bo, bh, bl = self._bo[i], self._bh[i], self._bl[i]
        slip = self.costs.slippage_usd
        delay = pd.Timedelta(seconds=self.costs.delay_seconds)
        survivors = []
        for order in self.pending:
            it = order.intent
            if it.expires_at is not None and start >= it.expires_at:
                order.status = "expired"
                self.log("order_expired", order=order.id)
                continue
            if order.submitted_at + delay > start:
                survivors.append(order)
                continue
            buy = it.side > 0
            spread = ao - bo
            price = None
            if it.kind == "market":
                price = (ao + slip) if buy else (bo - slip)
            elif it.kind == "limit":
                if buy and al <= it.price:
                    price = min(it.price, ao)
                elif not buy and bh >= it.price:
                    price = max(it.price, bo)
            elif it.kind == "stop":
                if buy and ah >= it.price:
                    price = max(it.price, ao) + slip
                elif not buy and bl <= it.price:
                    price = min(it.price, bo) - slip
            else:
                raise ValueError(f"Unknown order kind {it.kind}")
            if price is None:
                survivors.append(order)
                continue
            lots = order.remaining
            cap = self.costs.max_volume_participation
            if cap is not None and self._volume is not None:
                # Volume units are treated as ounces of provider activity, a rough liquidity proxy.
                available = cap * self._volume[i] / OZ_PER_LOT
                lots = min(lots, np.floor(available / LOT_STEP + 1e-9) * LOT_STEP)
            if lots < LOT_STEP:
                survivors.append(order)
                continue
            self._open(order, price, lots, source, spread, i)
            order.remaining = round(order.remaining - lots, 8)
            if order.remaining >= LOT_STEP:
                order.status = "partial"
                self.log("partial_fill", order=order.id, filled=lots, remaining=order.remaining)
                survivors.append(order)
            else:
                order.status = "filled"
        self.pending = survivors

    def _process_exits(self, i: int) -> None:
        if not self.positions:
            return
        ao, ah, al, _, _ = self._ask_prices(i)
        bo, bh, bl = self._bo[i], self._bh[i], self._bl[i]
        slip = self.costs.slippage_usd
        for pos in list(self.positions):
            if not self.config.allow_same_bar_exit and pos.entry_time == self._ts[i]:
                continue
            if pos.side > 0:
                open_, high, low = bo, bh, bl
            else:
                open_, high, low = ao, ah, al
            hit_stop = pos.stop is not None and (low <= pos.stop if pos.side > 0 else high >= pos.stop)
            hit_target = pos.target is not None and (high >= pos.target if pos.side > 0 else low <= pos.target)
            if hit_stop and (not hit_target or self.config.stop_first):
                gap = open_ <= pos.stop if pos.side > 0 else open_ >= pos.stop
                # A gap fills at the open, which can be well past the stop.
                base = open_ if gap else pos.stop
                price = base - slip if pos.side > 0 else base + slip
                self._close(pos, price, "stop_gap" if gap else "stop", i)
            elif hit_target:
                gap = open_ >= pos.target if pos.side > 0 else open_ <= pos.target
                # Limit orders fill at the target, or better on a gap.
                self._close(pos, open_ if gap else pos.target, "target", i)

    def _apply_swap(self, i: int) -> None:
        if not self.positions:
            return
        day = self._ts[i].date()
        for pos in self.positions:
            if pos.last_swap_day is not None and day > pos.last_swap_day:
                nights = (day - pos.last_swap_day).days
                rate = self.costs.swap_long_per_lot_day if pos.side > 0 else self.costs.swap_short_per_lot_day
                pos.swap += rate * pos.lots * nights
                pos.last_swap_day = day

    def _mark(self, i: int) -> None:
        bid = self._bc[i]
        ask = self._ask_prices(i)[3] if self.positions else self._ac[i]
        self._last_bid, self._last_ask = bid, ask
        unrealized = 0.0
        for pos in self.positions:
            mark = bid if pos.side > 0 else ask
            unrealized += pos.side * (mark - pos.entry_price) * pos.lots * OZ_PER_LOT + pos.swap
        self.account.equity = self.account.balance + unrealized
        self.account.peak_equity = max(self.account.peak_equity, self.account.equity)

    # ------------------------------------------------------------ the loop
    def _fire_signals(self, upto: int) -> None:
        while self.signal_index + 1 < len(self._signal_close) and self._signal_close[self.signal_index + 1] <= upto:
            self.signal_index += 1
            self.now = self._signal_stamps[self.signal_index]
            if self.config.log_signal_bars:
                self.log("signal_bar", index=self.signal_index)
            self.strategy.on_bar(self.ctx)

    @property
    def finished(self) -> bool:
        return self.cursor >= self._n

    def step(self) -> bool:
        """Handle one execution event. Returns False when no data remains."""
        if self.finished:
            return False
        i = self.cursor
        self._fire_signals(self._ts.asi8[i])
        self.now = self._ts[i]
        self._apply_swap(i)
        self._process_exits_before_orders(i)
        self._process_orders(i)
        self._process_exits(i)
        self.now = self._end[i]
        self._mark(i)
        if i % self.config.record_equity_every == 0:
            self.equity_times.append(self._end[i])
            self.equity_values.append(self.account.equity)
        self.cursor += 1
        return True

    def _process_exits_before_orders(self, i: int) -> None:
        # Requested closes fill at this event's open, before new entries.
        if self._close_requests:
            ao = self._ask_prices(i)[0]
            bo = self._bo[i]
            slip = self.costs.slippage_usd
            requests, self._close_requests = self._close_requests, []
            for pid, reason in requests:
                pos = next((p for p in self.positions if p.id == pid), None)
                if pos is not None:
                    self._close(pos, (bo - slip) if pos.side > 0 else (ao + slip), reason, i)

    def run(self, until: pd.Timestamp | None = None) -> "Engine":
        limit = None if until is None else pd.Timestamp(until).value
        while not self.finished:
            if limit is not None and self._ts.asi8[self.cursor] >= limit:
                break
            self.step()
        if until is None and len(self._signal_close):
            # Bars closing exactly at the end of the data still get their decision.
            self._fire_signals(self._signal_close[-1])
        return self

    def close_all(self, reason: str = "end_of_data") -> None:
        if not self._n:
            return
        i = min(self.cursor, self._n) - 1
        if i < 0:
            return
        for pos in list(self.positions):
            bid = self._bc[i]
            ask = self._ask_prices(i)[3]
            self._close(pos, bid if pos.side > 0 else ask, reason, i, when=self._end[i])
        self._mark(i)
        if self.equity_values:
            self.equity_values[-1] = self.account.equity

    # ------------------------------------------------------------ results
    def trades_frame(self) -> pd.DataFrame:
        return pd.DataFrame([asdict(t) for t in self.trades])

    def equity_curve(self) -> pd.Series:
        return pd.Series(self.equity_values, index=pd.DatetimeIndex(self.equity_times), name="equity")

    def digest(self, upto: pd.Timestamp | None = None) -> str:
        """Fingerprint of events up to a time, for reproducibility and look-ahead checks."""
        limit = None if upto is None else pd.Timestamp(upto).isoformat()
        rows = [e for e in self.events if limit is None or e["time"] <= limit]
        return hashlib.sha256(json.dumps(rows, sort_keys=True, default=str).encode()).hexdigest()
