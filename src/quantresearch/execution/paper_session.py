"""Paper trading end to end: market data -> decision engine -> risk engine -> paper broker -> journal.

Feeds are interchangeable: replayed warehouse bars for testing, or live quotes when a terminal is available.
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Iterable

import numpy as np
import pandas as pd

from quantresearch.decision.engine import DecisionPolicy, Vote, decide
from quantresearch.execution.broker import PaperBroker
from quantresearch.execution.router import OrderRequest, OrderRouter


class PaperSession:
    def __init__(self, router: OrderRouter, frame: pd.DataFrame, voters: dict[str, pd.DataFrame], policy: DecisionPolicy | None = None,
                 max_hold: int | None = 48):
        if not isinstance(router.broker, PaperBroker):
            raise TypeError("PaperSession only runs on the paper broker")
        self.router, self.frame, self.voters = router, frame, voters
        self.policy = policy or DecisionPolicy()
        self.max_hold = max_hold
        self.opened_at: dict[int, int] = {}
        self.log: list[dict] = []

    def _votes(self, i: int) -> list[Vote]:
        out = []
        for name, s in self.voters.items():
            side = 1 if s["long_entry"].iloc[i] else (-1 if s["short_entry"].iloc[i] else 0)
            distance = float(s["stop_dist"].iloc[i])
            if side and np.isfinite(distance) and distance > 0:
                target = float(s["target_dist"].iloc[i])
                out.append(Vote(name, side, distance, target if np.isfinite(target) else None))
        return out

    def on_bar(self, i: int) -> None:
        row = self.frame.iloc[i]
        broker = self.router.broker
        spread = row["ask_close"] - row["bid_close"]
        ask = row["ask_close"] if np.isfinite(spread) else row["bid_close"]
        # Intrabar extremes first, so stops and targets trigger on the path, then the close.
        for bid, ask_px in ((row["bid_low"], row["bid_low"] + (spread if np.isfinite(spread) else 0)),
                            (row["bid_high"], row["bid_high"] + (spread if np.isfinite(spread) else 0)),
                            (row["bid_close"], ask)):
            for result in broker.on_quote(float(bid), float(ask_px), str(row["close_ts"])):
                self.log.append({"time": str(row["close_ts"]), "event": "exit", "reason": result.message, "price": result.price})
        for position in broker.positions():
            start = self.opened_at.setdefault(position.ticket, i)
            if self.max_hold is not None and i - start >= self.max_hold:
                self.router.close(position.ticket, "time")
        if broker.positions():
            return
        votes = self._votes(i)
        if not votes:
            return
        decision = decide(votes, None, self.policy, broker.account().equity, str(row["close_ts"]))
        self.log.append({"time": str(row["close_ts"]), "event": "decision", **{k: v for k, v in asdict(decision).items() if k in ("action", "reasons", "lots")}})
        if decision.action == "none":
            return
        side = 1 if decision.action == "long" else -1
        price = float(ask if side > 0 else row["bid_close"])
        target = None if decision.target_dist is None else price + side * decision.target_dist
        result = self.router.submit(OrderRequest(side, round(decision.lots, 2), price, price - side * decision.stop_dist, target,
                                                 "paper_session", float(row["atr_14"]) if np.isfinite(row["atr_14"]) else None))
        self.log.append({"time": str(row["close_ts"]), "event": "route", "status": result.status, "message": result.message})

    def run(self, indexes: Iterable[int] | None = None) -> dict:
        for i in (range(len(self.frame)) if indexes is None else indexes):
            self.on_bar(i)
        broker = self.router.broker
        return {"balance": broker.balance, "equity": broker.account().equity, "open": len(broker.positions()), "trades": len(broker.history()), "events": len(self.log)}
