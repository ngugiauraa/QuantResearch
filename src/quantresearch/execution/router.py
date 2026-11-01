"""Trading modes and the only path an order can take to a broker.

    proposal -> mode gate -> independent risk engine -> approval (assisted) -> broker -> audit

Modes: research and simulation send nothing. Paper uses the paper broker. Assisted needs a typed yes
for one exact order. Automated runs without a person, so it stays off unless deliberately activated.

Live activation needs all three: the QR_LIVE_ENABLED=1 environment variable, the exact activation
phrase in this process, and a real-account check at the broker. It is never saved, so every restart
returns to paper. A background process cannot turn it on by itself.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from quantresearch.execution.broker import Broker, BrokerError, OrderResult
from quantresearch.execution.secrets import redact
from quantresearch.risk.engine import Account, Market, Proposal, RiskEngine

ACTIVATION_PHRASE = "ENABLE LIVE TRADING AT MY OWN RISK"


class Mode(str, Enum):
    RESEARCH = "research"
    SIMULATION = "simulation"
    PAPER = "paper"
    ASSISTED = "assisted"
    AUTOMATED = "automated"


class AuditLog:
    """Append-only log with a hash chain, so later edits to history can be detected."""

    def __init__(self, path: str | Path | None = None):
        self.path = str(path) if path else ":memory:"
        if path:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.con = sqlite3.connect(self.path, check_same_thread=False)
        self.con.execute("CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT, event TEXT, data TEXT, prev TEXT, hash TEXT)")
        self.con.commit()

    def write(self, event: str, data: dict | None = None) -> None:
        body = json.dumps(redact(data or {}), sort_keys=True, default=str)
        at = datetime.now(timezone.utc).isoformat()
        row = self.con.execute("SELECT hash FROM audit ORDER BY id DESC LIMIT 1").fetchone()
        prev = row[0] if row else ""
        digest = hashlib.sha256(f"{prev}|{at}|{event}|{body}".encode()).hexdigest()
        self.con.execute("INSERT INTO audit(at,event,data,prev,hash) VALUES(?,?,?,?,?)", (at, event, body, prev, digest))
        self.con.commit()

    def rows(self, limit: int = 200) -> list[dict]:
        out = self.con.execute("SELECT id,at,event,data FROM audit ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [{"id": a, "at": b, "event": c, "data": json.loads(d)} for a, b, c, d in out]

    def verify(self) -> bool:
        prev = ""
        for at, event, body, stored_prev, digest in self.con.execute("SELECT at,event,data,prev,hash FROM audit ORDER BY id"):
            if stored_prev != prev or hashlib.sha256(f"{prev}|{at}|{event}|{body}".encode()).hexdigest() != digest:
                return False
            prev = digest
        return True


@dataclass
class OrderRequest:
    side: int
    lots: float
    entry: float
    stop: float | None = None
    target: float | None = None
    source: str = "manual"
    atr: float | None = None

    def fingerprint(self) -> str:
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True).encode()).hexdigest()[:16]

    def ticket_text(self) -> str:
        words = "BUY" if self.side > 0 else "SELL"
        return f"{words} {self.lots} XAUUSD near {self.entry}, stop {self.stop}, target {self.target} ({self.source})"


@dataclass
class RouteResult:
    status: str  # sent, rejected, awaiting_approval, blocked_by_mode, failed
    message: str = ""
    lots: float = 0.0
    fingerprint: str = ""
    broker: OrderResult | None = None
    risk_reasons: list | None = None
    ticket: str = ""


def is_explicit_yes(reply: str, request: OrderRequest) -> bool:
    """Upstream's rule: a yes must be plain and for this one order. 'sure' or 'yes to all' do not count."""
    text = reply.strip().lower().rstrip(".!")
    if text in ("yes", "y", "yes place it", "yes, place it", "place it", "confirm"):
        return True
    if re.search(r"\b(all|every|everything|both)\b", text):
        return False
    if text.startswith("yes"):
        word = "buy" if request.side > 0 else "sell"
        return word in text and "xauusd" in text and ("buy" if request.side < 0 else "sell") not in text
    return False


class OrderRouter:
    def __init__(self, broker: Broker, risk: RiskEngine, audit: AuditLog | None = None, mode: Mode = Mode.PAPER):
        self.broker = broker
        self.risk = risk
        self.audit = audit or AuditLog()
        self.pending_approvals: dict[str, OrderRequest] = {}
        self.live_active = False
        self._mode = Mode.RESEARCH
        self.set_mode(mode)
        self.audit.write("router_started", {"mode": self._mode.value, "broker": broker.name})

    @property
    def mode(self) -> Mode:
        return self._mode

    def _is_real_money(self) -> bool:
        return not self.broker.is_simulated

    def set_mode(self, mode: Mode) -> None:
        """Assisted and automated on a real broker require activation first."""
        if mode in (Mode.ASSISTED, Mode.AUTOMATED) and self._is_real_money() and not self.live_active:
            self.audit.write("mode_refused", {"requested": mode.value})
            raise PermissionError("Live trading is not activated")
        if self._is_real_money() and hasattr(self.broker, "orders_enabled"):
            self.broker.orders_enabled = mode in (Mode.ASSISTED, Mode.AUTOMATED) and self.live_active
        if mode is Mode.PAPER and self._is_real_money():
            raise PermissionError("Paper mode needs the paper broker")
        self._mode = mode
        self.audit.write("mode_changed", {"mode": mode.value})

    def activate_live(self, phrase: str) -> bool:
        """Deliberately a separate step. Off after any restart because nothing here is persisted."""
        if os.environ.get("QR_LIVE_ENABLED") != "1":
            self.audit.write("live_activation_refused", {"reason": "environment switch is off"})
            return False
        if phrase != ACTIVATION_PHRASE:
            self.audit.write("live_activation_refused", {"reason": "wrong phrase"})
            return False
        self.live_active = True
        if hasattr(self.broker, "live_activated"):
            self.broker.live_activated = True
        self.audit.write("live_activated", {"broker": self.broker.name})
        return True

    def deactivate_live(self) -> None:
        self.live_active = False
        if hasattr(self.broker, "live_activated"):
            self.broker.live_activated = False
        if hasattr(self.broker, "orders_enabled"):
            self.broker.orders_enabled = False
        self._mode = Mode.RESEARCH if self._is_real_money() else Mode.PAPER
        self.audit.write("live_deactivated", {"mode": self._mode.value})

    # ------------------------------------------------------------ the order path
    def _risk_check(self, request: OrderRequest):
        account = self.broker.account()
        positions = self.broker.positions()
        quote = self.broker.quote()
        now = datetime.now(timezone.utc)
        self.risk.observe(account.equity, now)
        return self.risk.evaluate(
            Proposal(request.side, request.lots, request.entry, request.stop, request.target, request.source),
            Account(account.equity, account.balance, sum(p.lots for p in positions), len(positions)),
            Market(quote.bid, quote.ask, request.atr, now),
        )

    def submit(self, request: OrderRequest) -> RouteResult:
        fingerprint = request.fingerprint()
        self.audit.write("order_proposed", {"fingerprint": fingerprint, **asdict(request), "mode": self._mode.value})
        if self._mode in (Mode.RESEARCH, Mode.SIMULATION):
            return RouteResult("blocked_by_mode", f"{self._mode.value} mode sends no orders", fingerprint=fingerprint)
        try:
            decision = self._risk_check(request)
        except BrokerError as exc:
            self.audit.write("order_failed", {"fingerprint": fingerprint, "error": str(exc)})
            return RouteResult("failed", str(exc), fingerprint=fingerprint)
        self.audit.write("risk_decision", {"fingerprint": fingerprint, "approved": decision.approved, "lots": decision.lots,
                                           "reasons": decision.reasons, "checks": decision.checks})
        if not decision.approved:
            return RouteResult("rejected", "; ".join(decision.reasons), fingerprint=fingerprint, risk_reasons=decision.reasons)
        request.lots = decision.lots
        fingerprint = request.fingerprint()
        if self._mode is Mode.ASSISTED:
            self.pending_approvals[fingerprint] = request
            return RouteResult("awaiting_approval", "Reply yes to place this order", decision.lots, fingerprint,
                               ticket=request.ticket_text())
        return self._send(request, fingerprint)

    def approve(self, fingerprint: str, reply: str) -> RouteResult:
        request = self.pending_approvals.get(fingerprint)
        if request is None:
            return RouteResult("rejected", "No such order is waiting for approval", fingerprint=fingerprint)
        if not is_explicit_yes(reply, request):
            self.audit.write("approval_not_recorded", {"fingerprint": fingerprint, "reply": reply})
            return RouteResult("awaiting_approval", "That was not an explicit yes for this order", fingerprint=fingerprint)
        self.pending_approvals.pop(fingerprint)
        self.audit.write("approval_recorded", {"fingerprint": fingerprint, "reply": reply})
        # Markets move while a person reads: risk checks the same order again at send time.
        recheck = self._risk_check(request)
        if not recheck.approved:
            return RouteResult("rejected", "; ".join(recheck.reasons), fingerprint=fingerprint, risk_reasons=recheck.reasons)
        if recheck.lots != request.lots:
            request.lots = recheck.lots
        return self._send(request, fingerprint)

    def decline(self, fingerprint: str) -> None:
        self.pending_approvals.pop(fingerprint, None)
        self.audit.write("approval_declined", {"fingerprint": fingerprint})

    def _send(self, request: OrderRequest, fingerprint: str) -> RouteResult:
        try:
            result = self.broker.market_order(request.side, request.lots, request.stop, request.target, request.source)
        except BrokerError as exc:
            self.audit.write("order_failed", {"fingerprint": fingerprint, "error": str(exc)})
            return RouteResult("failed", str(exc), fingerprint=fingerprint)
        self.audit.write("order_result", {"fingerprint": fingerprint, "ok": result.ok, "ticket": result.ticket, "price": result.price,
                                          "lots": result.lots, "message": result.message, "code": result.code})
        if result.ok:
            self.risk.record_entry()
            return RouteResult("sent", result.message, result.lots, fingerprint, result)
        return RouteResult("failed", result.message, fingerprint=fingerprint, broker=result)

    def close(self, ticket: int, source: str = "manual") -> RouteResult:
        """Exits go through risk as well, but risk-reducing orders are always approved."""
        positions = self.broker.positions()
        current = next((p for p in positions if p.ticket == ticket), None)
        if current is None:
            return RouteResult("rejected", f"No position {ticket}")
        quote = self.broker.quote()
        mark = quote.bid if current.side > 0 else quote.ask
        decision = self.risk.evaluate(
            Proposal(-current.side, current.lots, mark, source=source, reduces_position=True),
            Account(self.broker.account().equity, None, current.lots, len(positions)),
            Market(quote.bid, quote.ask, None),
        )
        if not decision.approved:
            return RouteResult("rejected", "; ".join(decision.reasons))
        result = self.broker.close_position(ticket, decision.lots, comment=source)
        self.audit.write("position_closed", {"ticket": ticket, "ok": result.ok, "price": result.price, "message": result.message})
        return RouteResult("sent" if result.ok else "failed", result.message, result.lots, broker=result)

    def emergency_stop(self, flatten: bool = True, reason: str = "manual") -> dict:
        """Blocks new entries at once. Flatten attempts every position, so one failure stops no others."""
        self.risk.set_emergency_stop(True, reason)
        self.audit.write("emergency_stop", {"reason": reason, "flatten": flatten})
        closed, errors = [], []
        if flatten:
            for order in self._safe(self.broker.pending_orders, []):
                self._safe(lambda: self.broker.cancel_order(order.ticket), None)
            if hasattr(self.broker, "orders_enabled") and self._is_real_money():
                self.broker.orders_enabled = True  # Closing must work even if entries were switched off.
            for position in self._safe(self.broker.positions, []):
                result = self.close(position.ticket, "emergency_stop")
                (closed if result.status == "sent" else errors).append({"ticket": position.ticket, "message": result.message})
            if hasattr(self.broker, "orders_enabled") and self._is_real_money():
                self.broker.orders_enabled = False
        self.pending_approvals.clear()
        return {"closed": closed, "errors": errors}

    def _safe(self, call, default):
        try:
            return call()
        except BrokerError as exc:
            self.audit.write("broker_error", {"error": str(exc)})
            return default

    def reconcile(self) -> dict:
        """Compare the broker's view after a restart. The broker is the source of truth on positions."""
        positions = self._safe(self.broker.positions, None)
        account = self._safe(self.broker.account, None)
        state = {"connected": self.broker.connected(), "positions": 0 if positions is None else len(positions),
                 "equity": None if account is None else account.equity}
        self.audit.write("reconciled", state)
        return state
