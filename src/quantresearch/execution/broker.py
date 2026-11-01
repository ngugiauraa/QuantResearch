"""The broker interface, and a paper broker that implements it with simulated fills."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

OZ_PER_LOT = 100.0


@dataclass
class Quote:
    bid: float
    ask: float
    time: str = ""


@dataclass
class BrokerPosition:
    ticket: int
    side: int
    lots: float
    entry: float
    stop: float | None = None
    target: float | None = None
    profit: float = 0.0
    comment: str = ""


@dataclass
class PendingOrder:
    ticket: int
    side: int
    lots: float
    kind: str
    price: float
    stop: float | None = None
    target: float | None = None


@dataclass
class AccountInfo:
    balance: float
    equity: float
    margin_free: float | None = None
    currency: str = "USD"
    is_real: bool = False
    trade_allowed: bool = True


@dataclass
class SymbolSpec:
    symbol: str
    digits: int = 3
    lot_min: float = 0.01
    lot_max: float = 100.0
    lot_step: float = 0.01
    contract_size: float = 100.0
    point: float = 0.001
    stops_level: int = 0


@dataclass
class OrderResult:
    ok: bool
    ticket: int | None = None
    price: float | None = None
    lots: float = 0.0
    message: str = ""
    code: int | None = None


class BrokerError(RuntimeError):
    pass


class Broker(Protocol):
    name: str
    is_simulated: bool

    def connect(self) -> bool: ...
    def connected(self) -> bool: ...
    def account(self) -> AccountInfo: ...
    def quote(self) -> Quote: ...
    def symbol(self) -> SymbolSpec: ...
    def positions(self) -> list[BrokerPosition]: ...
    def pending_orders(self) -> list[PendingOrder]: ...
    def history(self, days: int = 30) -> list[dict]: ...
    def market_order(self, side: int, lots: float, stop=None, target=None, comment: str = "") -> OrderResult: ...
    def pending_order(self, side: int, lots: float, kind: str, price: float, stop=None, target=None, comment: str = "") -> OrderResult: ...
    def modify_position(self, ticket: int, stop=None, target=None) -> OrderResult: ...
    def close_position(self, ticket: int, lots: float | None = None, comment: str = "") -> OrderResult: ...
    def cancel_order(self, ticket: int) -> OrderResult: ...


class PaperBroker:
    """Simulated account with persistence. Market orders buy at the ask and sell at the bid."""
    name = "paper"
    is_simulated = True

    def __init__(self, path: str | Path | None = None, balance: float = 100_000.0, commission_per_lot: float = 0.0,
                 slippage_usd: float = 0.0):
        self.path = str(path) if path else ":memory:"
        if path:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.con = sqlite3.connect(self.path, check_same_thread=False)
        self.con.execute("CREATE TABLE IF NOT EXISTS paper(key TEXT PRIMARY KEY, value TEXT)")
        self.con.execute(
            "CREATE TABLE IF NOT EXISTS paper_trades(id INTEGER PRIMARY KEY AUTOINCREMENT, closed_at TEXT, record TEXT)"
        )
        self.commission_per_lot = commission_per_lot
        self.slippage_usd = slippage_usd
        saved = self._load("account")
        self.balance = saved["balance"] if saved else float(balance)
        self.next_ticket = saved["next_ticket"] if saved else 1
        self._positions = [BrokerPosition(**x) for x in (self._load("positions") or [])]
        self._orders = [PendingOrder(**x) for x in (self._load("orders") or [])]
        self._quote = Quote(**saved["quote"]) if saved and saved.get("quote") else None
        self._connected = True
        self._save()

    def _load(self, key: str):
        row = self.con.execute("SELECT value FROM paper WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def _save(self) -> None:
        for key, value in (("account", {"balance": self.balance, "next_ticket": self.next_ticket,
                                        "quote": asdict(self._quote) if self._quote else None}),
                           ("positions", [asdict(x) for x in self._positions]),
                           ("orders", [asdict(x) for x in self._orders])):
            self.con.execute("INSERT OR REPLACE INTO paper VALUES(?,?)", (key, json.dumps(value)))
        self.con.commit()

    def connect(self) -> bool:
        self._connected = True
        return True

    def connected(self) -> bool:
        return self._connected

    def _require_quote(self) -> Quote:
        if self._quote is None:
            raise BrokerError("No quote yet: the paper broker has seen no market data")
        return self._quote

    def _pnl(self, pos: BrokerPosition) -> float:
        q = self._require_quote()
        mark = q.bid if pos.side > 0 else q.ask
        return pos.side * (mark - pos.entry) * pos.lots * OZ_PER_LOT

    def account(self) -> AccountInfo:
        open_pnl = sum(self._pnl(p) for p in self._positions) if self._quote else 0.0
        return AccountInfo(self.balance, self.balance + open_pnl, None, "USD", False, True)

    def quote(self) -> Quote:
        return self._require_quote()

    def symbol(self) -> SymbolSpec:
        return SymbolSpec("XAUUSD")

    def positions(self) -> list[BrokerPosition]:
        for p in self._positions:
            if self._quote:
                p.profit = self._pnl(p)
        return list(self._positions)

    def pending_orders(self) -> list[PendingOrder]:
        return list(self._orders)

    def history(self, days: int = 30) -> list[dict]:
        rows = self.con.execute("SELECT closed_at, record FROM paper_trades ORDER BY id DESC LIMIT 500").fetchall()
        return [{"closed_at": a, **json.loads(b)} for a, b in rows]

    def _ticket(self) -> int:
        self.next_ticket += 1
        return self.next_ticket - 1

    def market_order(self, side, lots, stop=None, target=None, comment="") -> OrderResult:
        q = self._require_quote()
        price = (q.ask + self.slippage_usd) if side > 0 else (q.bid - self.slippage_usd)
        pos = BrokerPosition(self._ticket(), side, lots, price, stop, target, 0.0, comment)
        self._positions.append(pos)
        self.balance -= self.commission_per_lot * lots
        self._save()
        return OrderResult(True, pos.ticket, price, lots, "filled")

    def pending_order(self, side, lots, kind, price, stop=None, target=None, comment="") -> OrderResult:
        if kind not in ("limit", "stop"):
            return OrderResult(False, message=f"Unknown pending kind {kind}")
        order = PendingOrder(self._ticket(), side, lots, kind, price, stop, target)
        self._orders.append(order)
        self._save()
        return OrderResult(True, order.ticket, price, lots, "placed")

    def modify_position(self, ticket, stop=None, target=None) -> OrderResult:
        for p in self._positions:
            if p.ticket == ticket:
                if stop is not None:
                    p.stop = stop
                if target is not None:
                    p.target = target
                self._save()
                return OrderResult(True, ticket, message="modified")
        return OrderResult(False, message=f"No position {ticket}")

    def close_position(self, ticket, lots=None, comment="") -> OrderResult:
        q = self._require_quote()
        for p in self._positions:
            if p.ticket == ticket:
                price = (q.bid - self.slippage_usd) if p.side > 0 else (q.ask + self.slippage_usd)
                return self._close(p, price, lots, comment or "manual")
        return OrderResult(False, message=f"No position {ticket}")

    def _close(self, p: BrokerPosition, price: float, lots: float | None, reason: str) -> OrderResult:
        size = p.lots if lots is None else min(lots, p.lots)
        gross = p.side * (price - p.entry) * size * OZ_PER_LOT
        cost = self.commission_per_lot * size
        self.balance += gross - cost
        p.lots = round(p.lots - size, 8)
        if p.lots <= 0:
            self._positions.remove(p)
        self.con.execute("INSERT INTO paper_trades(closed_at,record) VALUES(?,?)", (
            datetime.now(timezone.utc).isoformat(),
            json.dumps({"ticket": p.ticket, "side": p.side, "lots": size, "entry": p.entry, "exit": price, "net": gross - cost,
                        "reason": reason, "comment": p.comment}),
        ))
        self._save()
        return OrderResult(True, p.ticket, price, size, reason)

    def cancel_order(self, ticket) -> OrderResult:
        for o in self._orders:
            if o.ticket == ticket:
                self._orders.remove(o)
                self._save()
                return OrderResult(True, ticket, message="cancelled")
        return OrderResult(False, message=f"No order {ticket}")

    def on_quote(self, bid: float, ask: float, time: str = "") -> list[OrderResult]:
        """Feed a new quote. Triggers pending orders, stops and targets. Stops are checked first."""
        self._quote = Quote(bid, ask, time)
        results = []
        for order in list(self._orders):
            hit = ((order.side > 0 and ask <= order.price) or (order.side < 0 and bid >= order.price)) if order.kind == "limit" else \
                  ((order.side > 0 and ask >= order.price) or (order.side < 0 and bid <= order.price))
            if hit:
                self._orders.remove(order)
                results.append(self.market_order(order.side, order.lots, order.stop, order.target, f"pending {order.ticket}"))
        for p in list(self._positions):
            mark = bid if p.side > 0 else ask
            if p.stop is not None and (mark <= p.stop if p.side > 0 else mark >= p.stop):
                # A gap fills at the market, which can be worse than the stop.
                results.append(self._close(p, mark, None, "stop"))
            elif p.target is not None and (mark >= p.target if p.side > 0 else mark <= p.target):
                results.append(self._close(p, mark, None, "target"))
        self._save()
        return results
