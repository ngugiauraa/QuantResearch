"""The decision core shared by the demo runner and the prediction replay.

Per strategy it mirrors the research simulator's SignalStrategy exactly: act at a bar's close; one position per
strategy; exit on the time limit, a signal exit or a flip; enter in the signal's direction when flat; stop and
target measured from the signal bar's close; size from the stop distance. Across strategies it adds what the
pre-registration adds: exits before entries, priority by table order, and the 1% same-direction risk cap.

Orders go through an Executor, which wraps the order router (and so the independent risk engine). The live
executor's broker is the demo-only MT5 broker; the replay's is a paper broker fed with historical bars.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from quantresearch.execution.router import OrderRequest, OrderRouter
from quantresearch.forward import plan
from quantresearch.forward.journal import Journal

OZ = 100.0
MT5_REASON = {3: "broker_stop", 4: "broker_stop", 5: "broker_target", 6: "stop_out"}  # MT5 DEAL_REASON_SL / TP / SO


def _f(x) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return float("nan")
    return v


@dataclass
class BarSignal:
    strategy: str
    tf: str
    close_ts: pd.Timestamp
    close: float
    long: bool
    short: bool
    stop_dist: float
    target_dist: float
    max_hold: float
    exit_long: bool
    exit_short: bool
    held: int | None = None  # Bars since the open trade's fill bar; None when flat.
    late: bool = False

    @property
    def wants(self) -> int:
        return 1 if self.long else (-1 if self.short else 0)

    def flags(self) -> dict:
        return {"long": self.long, "short": self.short, "exit_long": self.exit_long, "exit_short": self.exit_short,
                "stop_dist": self.stop_dist, "target_dist": self.target_dist, "close": self.close}

    @classmethod
    def from_row(cls, strategy: str, tf: str, row) -> "BarSignal":
        return cls(strategy, tf, pd.Timestamp(row["close_ts"]), _f(row["close"]), bool(row["long_entry"]), bool(row["short_entry"]),
                   _f(row["stop_dist"]), _f(row["target_dist"]), _f(row["max_hold"]), bool(row["exit_long"]), bool(row["exit_short"]))


class RouterExecutor:
    """Orders through the order router, so every entry passes the risk engine."""

    def __init__(self, router: OrderRouter):
        self.router = router
        self.broker = router.broker

    def now(self) -> datetime:
        return self.router.clock()

    def quote(self) -> tuple[float, float]:
        q = self.broker.quote()
        return float(q.bid), float(q.ask)

    def equity(self) -> float:
        return float(self.broker.account().equity)

    def balance(self) -> float:
        return float(self.broker.account().balance)

    def positions(self) -> dict[int, object]:
        return {int(p.ticket): p for p in self.broker.positions()}

    def open(self, strategy: str, side: int, lots: float, ref: float, stop: float, target: float | None, atr: float | None):
        return self.router.submit(OrderRequest(side, lots, ref, stop, target, plan.comment(strategy), atr))

    def close(self, ticket: int, strategy: str):
        return self.router.close(ticket, plan.comment(strategy))

    def exit_details(self, ticket: int, trade: dict) -> dict | None:
        """Exit price, time, costs and reason of a closed position, from the broker's own record."""
        if hasattr(self.broker, "deals_for"):  # MT5: the position's deals.
            deals = sorted(self.broker.deals_for(ticket), key=lambda d: d["time"])
            outs = [d for d in deals if d.get("entry") in (1, 2, 3)] or deals[1:]
            if not outs:
                return None
            last = outs[-1]
            return {"exit_price": float(last["price"]), "exit_at": datetime.fromtimestamp(last["time"], timezone.utc).isoformat(),
                    "profit": float(sum(d["profit"] for d in deals)), "commission": float(sum(d["commission"] for d in deals)),
                    "swap": float(sum(d["swap"] for d in deals)), "reason": MT5_REASON.get(last.get("reason"), "broker_exit")}
        rec = next((h for h in self.broker.history() if int(h["ticket"]) == ticket), None)  # Paper broker.
        if rec is None:
            return None
        return {"exit_price": float(rec["exit"]), "exit_at": rec.get("closed_at"), "profit": float(rec["net"]), "commission": 0.0,
                "swap": 0.0, "reason": {"stop": "broker_stop", "target": "broker_target"}.get(rec.get("reason"), rec.get("reason"))}


class Portfolio:
    def __init__(self, journal: Journal, executor: RouterExecutor):
        self.journal = journal
        self.ex = executor

    def _at(self) -> str:
        return pd.Timestamp(self.ex.now()).isoformat()

    # ------------------------------------------------------------ broker-side exits
    def sync(self) -> list[int]:
        """Record positions the broker closed by itself (stop or target). Returns their tickets."""
        mine = self.journal.trades("open")
        if not mine:
            return []
        live = self.ex.positions()
        done = []
        for t in mine:
            if t["ticket"] in live:
                continue
            d = self.ex.exit_details(t["ticket"], t)
            if d is None:
                continue  # History not visible yet; tried again next time.
            ref = t["stop"] if d["reason"] == "broker_stop" else (t["target"] if d["reason"] == "broker_target" else None)
            slip = None if ref is None else float(-t["side"] * (d["exit_price"] - ref))
            self.journal.update_trade(t["ticket"], status="closed", exit_reason=d["reason"], exit_ref=ref, exit_price=d["exit_price"],
                                      exit_at=d["exit_at"], slippage_exit=slip, profit=d["profit"], commission=d["commission"], swap=d["swap"])
            self.journal.event("exit", t["strategy"], self._at(), ticket=t["ticket"], reason=d["reason"], price=d["exit_price"], profit=d["profit"])
            done.append(t["ticket"])
        return done

    # ------------------------------------------------------------ one bar close
    def step(self, signals: list[BarSignal], atr_h1: float | None, paused: str | None = None) -> None:
        """All signals that closed at the same instant, in priority order."""
        order = {name: i for i, name in enumerate(plan.STRATEGIES)}
        signals = sorted(signals, key=lambda s: order[s.strategy])
        self.sync()
        for s in signals:
            if s.wants or s.exit_long or s.exit_short:
                self.journal.event("signal", s.strategy, s.close_ts.isoformat(), late=s.late, **s.flags())
        for s in signals:  # Exits first, so they free room under the cap.
            t = self.journal.open_trade(s.strategy)
            if t is None:
                continue
            timed_out = np.isfinite(s.max_hold) and s.held is not None and s.held >= s.max_hold
            signal_exit = s.exit_long if t["side"] > 0 else s.exit_short
            flipped = s.wants == -t["side"]
            if timed_out or signal_exit or flipped:
                self._close(t, "time" if timed_out else ("flip" if flipped else "signal_exit"), s)
        for s in signals:
            if s.wants:
                self._enter(s, atr_h1, paused)

    def _enter(self, s: BarSignal, atr_h1: float | None, paused: str | None) -> None:
        if self.journal.open_trade(s.strategy) is not None:
            return
        def missed(reason: str, **extra):
            self.journal.event("missed", s.strategy, s.close_ts.isoformat(), reason=reason, side=s.wants, **extra)
        if s.late:
            return missed("late")
        if paused:
            return missed("paused", message=paused)
        if not np.isfinite(s.stop_dist) or s.stop_dist <= 0:
            return missed("no_stop")
        try:
            equity = self.ex.equity()
            bid, ask = self.ex.quote()
        except Exception as exc:
            return missed("broker_unavailable", message=f"{type(exc).__name__}: {exc}")
        lots = int(min(50.0, equity * plan.RISK_PCT / 100 / (s.stop_dist * OZ)) / 0.01 + 1e-9) * 0.01
        lots = round(lots, 2)
        if lots < 0.01:
            return missed("below_min_lot", equity=equity, stop_dist=s.stop_dist)
        same = sum(t["lots"] * OZ * abs(t["fill_price"] - t["stop"]) for t in self.journal.trades("open") if t["side"] == s.wants)
        if same + lots * OZ * s.stop_dist > plan.DIRECTION_CAP_PCT / 100 * equity + 1e-6:
            return missed("portfolio_cap", open_risk=same, equity=equity)
        ref = ask if s.wants > 0 else bid
        stop = s.close - s.wants * s.stop_dist
        target = s.close + s.wants * s.target_dist if np.isfinite(s.target_dist) else None
        self.journal.event("order", s.strategy, s.close_ts.isoformat(), side=s.wants, lots=lots, ref=ref, stop=stop, target=target, bid=bid, ask=ask)
        res = self.ex.open(s.strategy, s.wants, lots, ref, stop, target, atr_h1)
        if res.status != "sent" or res.broker is None or res.broker.ticket is None:
            return missed("risk_rejected" if res.status == "rejected" else "order_failed", message=res.message, status=res.status)
        fill = float(res.broker.price if res.broker.price else ref)
        self.journal.add_trade(ticket=int(res.broker.ticket), strategy=s.strategy, side=s.wants, lots=float(res.broker.lots or res.lots),
                               signal_close_ts=s.close_ts.isoformat(), signal_close=s.close, stop=stop, target=target, ref_price=ref,
                               fill_price=fill, fill_at=self._at(), spread_entry=ask - bid, slippage_entry=float(s.wants * (fill - ref)))
        self.journal.event("fill", s.strategy, self._at(), ticket=int(res.broker.ticket), side=s.wants, lots=res.broker.lots, price=fill,
                           slippage=float(s.wants * (fill - ref)), spread=ask - bid)

    def _close(self, t: dict, reason: str, s: BarSignal) -> None:
        try:
            bid, ask = self.ex.quote()
        except Exception as exc:
            self.journal.event("exit_failed", t["strategy"], s.close_ts.isoformat(), ticket=t["ticket"], message=str(exc))
            return
        ref = bid if t["side"] > 0 else ask
        res = self.ex.close(t["ticket"], t["strategy"])
        if res.status != "sent":
            if t["ticket"] in self.sync():  # The broker had already closed it (stop or target): recorded as such.
                return
            self.journal.event("exit_failed", t["strategy"], s.close_ts.isoformat(), ticket=t["ticket"], reason=reason, message=res.message)
            return
        price = float(res.broker.price) if res.broker and res.broker.price else ref
        d = self.ex.exit_details(t["ticket"], t) or {}
        self.journal.update_trade(t["ticket"], status="closed", exit_reason=reason, exit_signal_ts=s.close_ts.isoformat(), exit_ref=ref,
                                  exit_price=price, exit_at=self._at(), spread_exit=ask - bid, slippage_exit=float(-t["side"] * (price - ref)),
                                  profit=d.get("profit", float(t["side"] * (price - t["fill_price"]) * t["lots"] * OZ)),
                                  commission=d.get("commission", 0.0), swap=d.get("swap", 0.0), late_exit=int(s.late))
        self.journal.event("exit", t["strategy"], self._at(), ticket=t["ticket"], reason=reason, price=price, late=s.late)

    # ------------------------------------------------------------ equity
    def ledger_equity(self, start_equity: float) -> float:
        """Starting equity plus this test's own closed and open P&L, so other activity on the account is excluded."""
        closed = sum((t["profit"] or 0) + (t["commission"] or 0) + (t["swap"] or 0) for t in self.journal.trades("closed"))
        tickets = {t["ticket"] for t in self.journal.trades("open")}
        floating = sum(float(getattr(p, "profit", 0.0)) for k, p in self.ex.positions().items() if k in tickets)
        return float(start_equity + closed + floating)
