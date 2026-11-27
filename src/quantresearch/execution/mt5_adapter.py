"""MetaTrader 5 adapter.

Runtime requirements: Windows, the MetaTrader5 Python package, and an installed terminal that the
package can attach to. Credentials are optional if the terminal is already logged in; if needed they
come from secrets.py and are never logged.

Placing orders is off unless the router has explicitly enabled it, and the adapter separately refuses
real-money accounts unless live trading has been activated. Read-only calls are always allowed.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from quantresearch.execution.broker import (AccountInfo, BrokerError, BrokerPosition, OrderResult, PendingOrder, Quote,
                                            SymbolSpec)
from quantresearch.execution.secrets import Mt5Credentials

SUCCESS_CODES = {10008, 10009, 10010}  # placed, done, done partially
TRANSIENT_CODES = {10004, 10006, 10020, 10021, 10024, 10031}  # requote, rejected, changed, no quotes, too many, no connection
RETRYABLE_SEND = {10004, 10020, 10021}  # Safe to resend: the price changed before any order was accepted.


def _import_mt5():
    try:
        import MetaTrader5  # type: ignore
    except ImportError as exc:
        raise BrokerError("The MetaTrader5 package is not installed") from exc
    return MetaTrader5


class Mt5Broker:
    name = "mt5"
    is_simulated = False

    def __init__(self, symbol: str = "XAUUSD", credentials: Mt5Credentials | None = None, module: Any = None,
                 audit: Callable[[str, dict], None] | None = None, deviation: int = 30, magic: int = 880011,
                 max_reconnects: int = 3, sleep: Callable[[float], None] = time.sleep):
        self.symbol_name = symbol
        self.credentials = credentials
        self.mt5 = module
        self.audit = audit or (lambda event, data: None)
        self.deviation, self.magic = deviation, magic
        self.max_reconnects = max_reconnects
        self.sleep = sleep
        self.orders_enabled = False
        self.live_activated = False
        self._connected = False

    # ------------------------------------------------------------ connection
    def _module(self):
        if self.mt5 is None:
            self.mt5 = _import_mt5()
        return self.mt5

    def connect(self) -> bool:
        mt5 = self._module()
        kwargs = {}
        if self.credentials:
            kwargs = self.credentials.as_kwargs()
        ok = mt5.initialize(**kwargs)
        self._connected = bool(ok)
        if not ok:
            self.audit("mt5_connect_failed", {"error": str(mt5.last_error())})
            return False
        mt5.symbol_select(self.symbol_name, True)
        self.audit("mt5_connected", {"symbol": self.symbol_name})
        return True

    def connected(self) -> bool:
        if not self._connected or self.mt5 is None:
            return False
        info = self.mt5.terminal_info()
        return bool(info is not None and getattr(info, "connected", False))

    def disconnect(self) -> None:
        if self.mt5 is not None and self._connected:
            self.mt5.shutdown()
        self._connected = False

    def ensure_connected(self) -> None:
        if self.connected():
            return
        for attempt in range(self.max_reconnects):
            self.audit("mt5_reconnecting", {"attempt": attempt + 1})
            if self.mt5 is not None:
                try:
                    self.mt5.shutdown()
                except Exception:
                    pass
            if self.connect() and self.connected():
                return
            self.sleep(min(30.0, 2.0 * 2 ** attempt))
        raise BrokerError("MT5 is not connected and reconnecting failed")

    # ------------------------------------------------------------ reads
    def account(self) -> AccountInfo:
        self.ensure_connected()
        info = self.mt5.account_info()
        if info is None:
            raise BrokerError(f"account_info failed: {self.mt5.last_error()}")
        return AccountInfo(info.balance, info.equity, info.margin_free, info.currency,
                           info.trade_mode == self.mt5.ACCOUNT_TRADE_MODE_REAL, bool(info.trade_allowed))

    def account_key(self) -> str:
        """Which account the equity belongs to ("mt5:<login>@<server>"), for the risk engine's baselines."""
        self.ensure_connected()
        info = self.mt5.account_info()
        if info is None:
            raise BrokerError(f"account_info failed: {self.mt5.last_error()}")
        return f"mt5:{info.login}@{info.server}"

    def quote(self) -> Quote:
        self.ensure_connected()
        tick = self.mt5.symbol_info_tick(self.symbol_name)
        if tick is None:
            raise BrokerError(f"No quote for {self.symbol_name}: {self.mt5.last_error()}")
        return Quote(tick.bid, tick.ask, datetime.fromtimestamp(tick.time, timezone.utc).isoformat())

    def symbol(self) -> SymbolSpec:
        self.ensure_connected()
        s = self.mt5.symbol_info(self.symbol_name)
        if s is None:
            raise BrokerError(f"No symbol {self.symbol_name}")
        return SymbolSpec(self.symbol_name, s.digits, s.volume_min, s.volume_max, s.volume_step, s.trade_contract_size,
                          s.point, s.trade_stops_level)

    def bars_m1(self, start: datetime, end: datetime) -> list[dict]:
        """Closed and forming M1 bars with start <= open time < end, read-only. Times are UTC.

        MT5 stamps bars in the broker's server time. QR_MT5_UTC_OFFSET_HOURS says how far that is ahead of UTC
        (0 for Exness). `spread` is the broker's spread for the bar in points; `point` converts it to price.
        """
        import os

        self.ensure_connected()
        offset = timedelta(hours=float(os.environ.get("QR_MT5_UTC_OFFSET_HOURS", "0")))
        rows = self.mt5.copy_rates_range(self.symbol_name, self.mt5.TIMEFRAME_M1, start + offset, end + offset)
        if rows is None:
            raise BrokerError(f"copy_rates_range failed for {self.symbol_name}: {self.mt5.last_error()}")
        info = self.mt5.symbol_info(self.symbol_name)
        point = float(info.point) if info is not None else 0.0
        out = []
        for r in rows:
            opened = datetime.fromtimestamp(int(r["time"]), timezone.utc) - offset
            if start <= opened < end:
                out.append({"ts": opened, "open": float(r["open"]), "high": float(r["high"]), "low": float(r["low"]), "close": float(r["close"]),
                            "tick_volume": float(r["tick_volume"]), "spread": float(r["spread"]) * point})
        return out

    def rates(self, symbol: str, timeframe: str = "h1", count: int = 99_999) -> list[dict]:
        """Every bar the terminal has for any symbol, newest last, read-only. Times are UTC bar open times.

        Used for cross-market series. MT5 serves at most the terminal's "max bars in chart" setting and rejects a
        count equal to it, so the request stays one below. Prices are the symbol's bid; `spread` is in price units.
        """
        import os

        self.ensure_connected()
        if not self.mt5.symbol_select(symbol, True):
            raise BrokerError(f"The broker does not offer {symbol}: {self.mt5.last_error()}")
        frames = {"m1": self.mt5.TIMEFRAME_M1, "h1": self.mt5.TIMEFRAME_H1, "d1": self.mt5.TIMEFRAME_D1}
        info = self.mt5.terminal_info()
        if info is not None and getattr(info, "maxbars", 0):
            count = min(count, int(info.maxbars) - 1)
        rows = self.mt5.copy_rates_from_pos(symbol, frames[timeframe], 0, count)
        if rows is None or len(rows) == 0:
            raise BrokerError(f"No {timeframe} bars for {symbol}: {self.mt5.last_error()}")
        spec = self.mt5.symbol_info(symbol)
        point = float(spec.point) if spec is not None else 0.0
        offset = timedelta(hours=float(os.environ.get("QR_MT5_UTC_OFFSET_HOURS", "0")))
        return [{"ts": datetime.fromtimestamp(int(r["time"]), timezone.utc) - offset, "open": float(r["open"]), "high": float(r["high"]),
                 "low": float(r["low"]), "close": float(r["close"]), "tick_volume": float(r["tick_volume"]), "spread": float(r["spread"]) * point}
                for r in rows]

    def spread(self) -> float:
        q = self.quote()
        return q.ask - q.bid

    def positions(self) -> list[BrokerPosition]:
        self.ensure_connected()
        rows = self.mt5.positions_get(symbol=self.symbol_name) or ()
        return [BrokerPosition(r.ticket, 1 if r.type == self.mt5.POSITION_TYPE_BUY else -1, r.volume, r.price_open,
                               r.sl or None, r.tp or None, r.profit, r.comment) for r in rows]

    def pending_orders(self) -> list[PendingOrder]:
        self.ensure_connected()
        rows = self.mt5.orders_get(symbol=self.symbol_name) or ()
        kinds = {self.mt5.ORDER_TYPE_BUY_LIMIT: (1, "limit"), self.mt5.ORDER_TYPE_SELL_LIMIT: (-1, "limit"),
                 self.mt5.ORDER_TYPE_BUY_STOP: (1, "stop"), self.mt5.ORDER_TYPE_SELL_STOP: (-1, "stop")}
        out = []
        for r in rows:
            if r.type in kinds:
                side, kind = kinds[r.type]
                out.append(PendingOrder(r.ticket, side, r.volume_current, kind, r.price_open, r.sl or None, r.tp or None))
        return out

    def history(self, days: int = 30) -> list[dict]:
        self.ensure_connected()
        end = datetime.now(timezone.utc)
        rows = self.mt5.history_deals_get(end - timedelta(days=days), end, group=f"*{self.symbol_name}*") or ()
        return [{"ticket": d.ticket, "position": d.position_id, "time": d.time, "side": 1 if d.type == self.mt5.DEAL_TYPE_BUY else -1,
                 "lots": d.volume, "price": d.price, "profit": d.profit, "commission": d.commission, "swap": d.swap,
                 "comment": d.comment} for d in rows]

    # ------------------------------------------------------------ writes
    def _guard(self) -> OrderResult | None:
        if not self.orders_enabled:
            return OrderResult(False, message="Order sending is disabled")
        account = self.account()
        if account.is_real and not self.live_activated:
            self.audit("mt5_real_account_blocked", {})
            return OrderResult(False, message="This is a real-money account and live trading is not activated")
        if not account.trade_allowed:
            return OrderResult(False, message="The broker does not allow trading on this account")
        return None

    def _send(self, request: dict) -> OrderResult:
        safe = {k: v for k, v in request.items() if k != "magic"}
        for attempt in range(3):
            self.audit("mt5_order_send", {"attempt": attempt + 1, **safe})
            result = self.mt5.order_send(request)
            if result is None:
                self.audit("mt5_order_error", {"error": str(self.mt5.last_error())})
                # An unknown outcome is never resent: the order may have reached the broker.
                return OrderResult(False, message=f"order_send returned nothing: {self.mt5.last_error()}")
            self.audit("mt5_order_result", {"code": result.retcode, "order": result.order, "price": result.price,
                                            "volume": result.volume, "comment": result.comment})
            if result.retcode in SUCCESS_CODES:
                return OrderResult(True, result.order, result.price, result.volume, result.comment, result.retcode)
            if result.retcode in RETRYABLE_SEND and attempt < 2 and request.get("action") == self.mt5.TRADE_ACTION_DEAL:
                tick = self.mt5.symbol_info_tick(self.symbol_name)
                if tick is not None:
                    request["price"] = tick.ask if request["type"] == self.mt5.ORDER_TYPE_BUY else tick.bid
                self.sleep(0.25)
                continue
            return OrderResult(False, None, None, 0.0, result.comment, result.retcode)
        return OrderResult(False, message="Requotes exhausted")

    def market_order(self, side, lots, stop=None, target=None, comment="") -> OrderResult:
        blocked = self._guard()
        if blocked:
            return blocked
        q = self.quote()
        request = {
            "action": self.mt5.TRADE_ACTION_DEAL, "symbol": self.symbol_name, "volume": float(lots),
            "type": self.mt5.ORDER_TYPE_BUY if side > 0 else self.mt5.ORDER_TYPE_SELL,
            "price": q.ask if side > 0 else q.bid, "sl": float(stop or 0.0), "tp": float(target or 0.0),
            "deviation": self.deviation, "magic": self.magic, "comment": comment[:31],
            "type_time": self.mt5.ORDER_TIME_GTC, "type_filling": self.mt5.ORDER_FILLING_IOC,
        }
        return self._send(request)

    def pending_order(self, side, lots, kind, price, stop=None, target=None, comment="") -> OrderResult:
        blocked = self._guard()
        if blocked:
            return blocked
        types = {(1, "limit"): self.mt5.ORDER_TYPE_BUY_LIMIT, (-1, "limit"): self.mt5.ORDER_TYPE_SELL_LIMIT,
                 (1, "stop"): self.mt5.ORDER_TYPE_BUY_STOP, (-1, "stop"): self.mt5.ORDER_TYPE_SELL_STOP}
        if (side, kind) not in types:
            return OrderResult(False, message=f"Unknown order kind {kind}")
        return self._send({
            "action": self.mt5.TRADE_ACTION_PENDING, "symbol": self.symbol_name, "volume": float(lots),
            "type": types[(side, kind)], "price": float(price), "sl": float(stop or 0.0), "tp": float(target or 0.0),
            "magic": self.magic, "comment": comment[:31], "type_time": self.mt5.ORDER_TIME_GTC,
            "type_filling": self.mt5.ORDER_FILLING_RETURN,
        })

    def modify_position(self, ticket, stop=None, target=None) -> OrderResult:
        blocked = self._guard()
        if blocked:
            return blocked
        current = next((p for p in self.positions() if p.ticket == ticket), None)
        if current is None:
            return OrderResult(False, message=f"No position {ticket}")
        return self._send({
            "action": self.mt5.TRADE_ACTION_SLTP, "position": ticket, "symbol": self.symbol_name,
            "sl": float(stop if stop is not None else (current.stop or 0.0)),
            "tp": float(target if target is not None else (current.target or 0.0)), "magic": self.magic,
        })

    def close_position(self, ticket, lots=None, comment="") -> OrderResult:
        # Closing reduces risk, so the only requirement here is that orders are enabled at all.
        if not self.orders_enabled:
            return OrderResult(False, message="Order sending is disabled")
        current = next((p for p in self.positions() if p.ticket == ticket), None)
        if current is None:
            return OrderResult(False, message=f"No position {ticket}")
        q = self.quote()
        return self._send({
            "action": self.mt5.TRADE_ACTION_DEAL, "position": ticket, "symbol": self.symbol_name,
            "volume": float(lots if lots is not None else current.lots),
            "type": self.mt5.ORDER_TYPE_SELL if current.side > 0 else self.mt5.ORDER_TYPE_BUY,
            "price": q.bid if current.side > 0 else q.ask, "deviation": self.deviation, "magic": self.magic,
            "comment": comment[:31], "type_time": self.mt5.ORDER_TIME_GTC, "type_filling": self.mt5.ORDER_FILLING_IOC,
        })

    def cancel_order(self, ticket) -> OrderResult:
        if not self.orders_enabled:
            return OrderResult(False, message="Order sending is disabled")
        return self._send({"action": self.mt5.TRADE_ACTION_REMOVE, "order": ticket, "magic": self.magic})
