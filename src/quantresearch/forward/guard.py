"""The hard safety guard: forward-test orders reach MT5 only while MT5 reports a demo account.

Every write (open, close, modify, pending, cancel) reads `account_info().trade_mode` fresh from the terminal first.
Anything other than ACCOUNT_TRADE_MODE_DEMO, including a contest account, a missing reading or an error, refuses
the order, writes `forward_test_real_account_refused` to the audit log and calls `on_refused`, which raises the
dashboard alert and pauses the test. There is no override. QR_LIVE_ENABLED and live activation play no part: this
broker never uses the live path, and the live path never uses this broker.
"""
from __future__ import annotations

from typing import Callable

from quantresearch.execution.broker import OrderResult
from quantresearch.execution.mt5_adapter import Mt5Broker
from quantresearch.forward import plan

REFUSED_EVENT = "forward_test_real_account_refused"


class DemoOnlyMt5Broker(Mt5Broker):
    name = "mt5_demo"
    # The money is virtual: this is a demo account, and _demo_check enforces that before every write. The order
    # router treats a simulated broker as paper, so its live gate is not involved.
    is_simulated = True

    def __init__(self, *args, on_refused: Callable[[dict], None] | None = None, **kwargs):
        kwargs.setdefault("magic", plan.MAGIC)
        super().__init__(*args, **kwargs)
        self.orders_enabled = True
        self.on_refused = on_refused or (lambda data: None)

    def _kind(self, info) -> str:
        mode = getattr(info, "trade_mode", None)
        if info is None or mode is None:
            return "unknown"
        demo = getattr(self.mt5, "ACCOUNT_TRADE_MODE_DEMO", None)  # No default: a module without it is never demo.
        if demo is not None and mode == demo:
            return "demo"
        if mode == getattr(self.mt5, "ACCOUNT_TRADE_MODE_REAL", 2):
            return "real"
        if mode == getattr(self.mt5, "ACCOUNT_TRADE_MODE_CONTEST", 1):
            return "contest"
        return "unknown"

    def account_type(self) -> str:
        """'demo', 'contest', 'real' or 'unknown', read from the terminal now."""
        try:
            self.ensure_connected()
            return self._kind(self.mt5.account_info())
        except Exception:
            return "unknown"

    def _demo_check(self, action: str) -> OrderResult | None:
        info, error = None, None
        try:
            self.ensure_connected()
            info = self.mt5.account_info()
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
        kind = self._kind(info)
        if kind != "demo":
            data = {"action": action, "account_type": kind, "trade_mode": getattr(info, "trade_mode", None),
                    "server": getattr(info, "server", None), "error": error}
            self.audit(REFUSED_EVENT, data)
            self.on_refused(data)
            return OrderResult(False, message=f"Refused: MT5 reports a {kind} account; forward-test orders are demo-only")
        if not getattr(info, "trade_allowed", False):
            return OrderResult(False, message="The broker does not allow trading on this account")
        return None

    def _guard(self) -> OrderResult | None:  # Used by market, pending and modify orders.
        return self._demo_check("order")

    def close_position(self, ticket, lots=None, comment=""):
        return self._demo_check("close") or super().close_position(ticket, lots, comment)

    def cancel_order(self, ticket):
        return self._demo_check("cancel") or super().cancel_order(ticket)

    def hedging(self) -> bool:
        info = self.mt5.account_info()
        return info is not None and getattr(info, "margin_mode", None) == getattr(self.mt5, "ACCOUNT_MARGIN_MODE_RETAIL_HEDGING", 2)

    def deals_for(self, ticket: int, days: int = 400) -> list[dict]:
        """Every deal of one position (open and close), with costs, from the terminal's history."""
        return [d for d in self.history(days) if d["position"] == ticket]
