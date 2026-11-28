"""What the backtest predicts for the forward period: the same seven strategies, the same decision core, the same
risk engine limits, cap, priority and news blackout, replayed over the period's M1 bars with the backtest's
observed cost model (the observed spread, no added slippage, no commission or swap).

Fills: an entry signalled at a bar's close fills at the next M1 open, as the backtest fills at the next bar's open.
Stops and targets fill at their level when the M1 path crosses it, or at the market on a gap, as in the research
simulator. Within an M1 bar the path is open, the nearer-to-close extreme last, then close.

The only intended differences from the live demo are therefore execution: real fills, slippage, spreads, swap,
and signals missed because the PC, API or terminal was off.
"""
from __future__ import annotations

import json
from datetime import datetime

import numpy as np
import pandas as pd

from quantresearch.core.config import Paths
from quantresearch.execution.broker import BrokerPosition, OrderResult, PaperBroker, Quote
from quantresearch.execution.router import AuditLog, Mode, OrderRouter
from quantresearch.forward import plan
from quantresearch.forward.journal import Journal
from quantresearch.forward.portfolio import BarSignal, Portfolio, RouterExecutor
from quantresearch.forward.source import SignalSource, atr_at, held_bars, strategy_tf
from quantresearch.risk.engine import RiskEngine, RiskState

OZ = 100.0


class ReplayBroker(PaperBroker):
    """The paper broker on a simulated clock, in memory, with level fills for stops and targets."""

    def __init__(self, balance: float):
        self.records: list[dict] = []
        super().__init__(None, balance)

    def _save(self) -> None:  # Nothing to persist in a replay.
        pass

    def history(self, days: int = 30) -> list[dict]:
        return list(reversed(self.records))

    def _close(self, p: BrokerPosition, price: float, lots: float | None, reason: str) -> OrderResult:
        size = p.lots if lots is None else min(lots, p.lots)
        gross = p.side * (price - p.entry) * size * OZ
        self.balance += gross
        p.lots = round(p.lots - size, 8)
        if p.lots <= 0:
            self._positions.remove(p)
        self.records.append({"closed_at": self._quote.time, "ticket": p.ticket, "side": p.side, "lots": size, "entry": p.entry,
                             "exit": price, "net": gross, "reason": reason, "comment": p.comment})
        return OrderResult(True, p.ticket, price, size, reason)

    def on_quote(self, bid: float, ask: float, time: str = "", continuous: bool = False) -> list[OrderResult]:
        self._quote = Quote(bid, ask, time)
        out = []
        for p in list(self._positions):
            mark = bid if p.side > 0 else ask
            if p.stop is not None and (mark <= p.stop if p.side > 0 else mark >= p.stop):
                out.append(self._close(p, p.stop if continuous else mark, None, "stop"))
            elif p.target is not None and (mark >= p.target if p.side > 0 else mark <= p.target):
                out.append(self._close(p, p.target if continuous else mark, None, "target"))
        return out


def replay(p: Paths, start: dict, end: pd.Timestamp, loader, news_schedule) -> dict:
    """`start` is the forward test's start record (at, equity, load_start)."""
    t0, end = pd.Timestamp(start["at"]), pd.Timestamp(end)
    frames, h1_atr = SignalSource(p, pd.Timestamp(start["load_start"]), loader).frames(end)
    m1 = loader("m1", t0.floor("min"), end)
    m1 = m1[(m1["ts"] >= t0) & (m1["close_ts"] <= end)].reset_index(drop=True)
    broker = ReplayBroker(float(start["equity"]))
    clock = {"now": t0.to_pydatetime()}
    risk = RiskEngine(plan.limits(), RiskState(), news_schedule=news_schedule)
    router = OrderRouter(broker, risk, AuditLog(), Mode.PAPER, clock=lambda: clock["now"])
    journal = Journal()
    pf = Portfolio(journal, RouterExecutor(router))

    due: dict[pd.Timestamp, list[tuple[str, int]]] = {}
    for name, f in frames.items():
        for i in np.flatnonzero((f["close_ts"] > t0).to_numpy()):
            due.setdefault(pd.Timestamp(f["close_ts"].iloc[i]), []).append((name, int(i)))
    closes = sorted(due)
    k = 0
    cols = {c: m1[c].to_numpy(dtype="float64") for c in ("bid_open", "bid_high", "bid_low", "bid_close", "ask_open", "ask_close")}
    spread_last = 0.0
    for j, ts in enumerate(m1["ts"]):
        when = pd.Timestamp(ts)
        so = cols["ask_open"][j] - cols["bid_open"][j]
        sc = cols["ask_close"][j] - cols["bid_close"][j]
        so = so if np.isfinite(so) else spread_last
        sc = sc if np.isfinite(sc) else so
        spread_last = sc
        iso = when.isoformat()
        broker.on_quote(cols["bid_open"][j], cols["bid_open"][j] + so, iso, continuous=False)
        while k < len(closes) and closes[k] <= when:
            clock["now"] = when.to_pydatetime()
            batch = []
            for name, i in due[closes[k]]:
                f = frames[name]
                s = BarSignal.from_row(name, strategy_tf(name), f.iloc[i])
                t = journal.open_trade(name)
                if t is not None:
                    s.held = held_bars(f, t["signal_close_ts"], s.close_ts)
                batch.append(s)
            pf.step(batch, atr_at(h1_atr, closes[k]), None)
            eq = broker.account().equity
            journal.sample(iso, eq, eq, broker.balance)
            k += 1
        lo, hi, cl = cols["bid_low"][j], cols["bid_high"][j], cols["bid_close"][j]
        path = (lo, hi) if cl >= cols["bid_open"][j] else (hi, lo)
        for px in (*path, cl):
            broker.on_quote(px, px + sc, iso, continuous=True)
    if len(m1):
        eq = broker.account().equity
        journal.sample(pd.Timestamp(m1["close_ts"].iloc[-1]).isoformat(), eq, eq, broker.balance)
    return {"journal": journal, "frames_end": str(end), "bars": int(len(m1))}


def summarise(journal: Journal, start_equity: float) -> dict:
    trades = journal.trades()
    curve = journal.equity_curve("ledger")
    per = {}
    for name in plan.STRATEGIES:
        mine = [t for t in trades if t["strategy"] == name]
        closed = [t for t in mine if t["status"] == "closed"]
        nets = [(t["profit"] or 0) + (t["commission"] or 0) + (t["swap"] or 0) for t in closed]
        per[name] = {"trades": len(closed), "open": len(mine) - len(closed), "net": float(sum(nets)),
                     "wins": int(sum(1 for n in nets if n > 0))}
    return {"per_strategy": per, "trades": [{k: t[k] for k in ("strategy", "side", "lots", "fill_at", "fill_price", "exit_at", "exit_price", "exit_reason", "profit", "status")} for t in trades],
            "equity": [[i.isoformat(), float(v)] for i, v in curve.items()], "missed": journal.missed_by_reason(),
            "net": float(sum(v["net"] for v in per.values())), "start_equity": start_equity}


def to_json(obj) -> str:
    def default(x):
        if isinstance(x, (pd.Timestamp, datetime)):
            return x.isoformat()
        if isinstance(x, np.generic):
            return x.item()
        return str(x)
    return json.dumps(obj, default=default)
