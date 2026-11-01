"""Small reference strategies for testing the engine. They are not trading recommendations."""
from __future__ import annotations

import math

from quantresearch.sim.engine import Context, Strategy


class SmaCross(Strategy):
    """Long above the slow average, short below, sized by ATR risk. Exercises orders and stops."""
    name = "sma_cross"

    def __init__(self, risk_lots: float = 0.1, stop_atr: float = 2.0, target_atr: float = 4.0):
        self.lots, self.stop_atr, self.target_atr = risk_lots, stop_atr, target_atr
        self.last = 0

    def on_bar(self, ctx: Context) -> None:
        bar = ctx.bar
        fast, slow, atr = bar.get("sma_20"), bar.get("sma_50"), bar.get("atr_14")
        if fast is None or slow is None or atr is None or any(map(math.isnan, (fast, slow, atr))):
            return
        state = 1 if fast > slow else -1
        if state == self.last:
            return
        self.last = state
        for position in ctx.positions:
            ctx.close(position, "signal_flip")
        close = bar["close"]
        stop = close - state * self.stop_atr * atr
        target = close + state * self.target_atr * atr
        (ctx.buy if state > 0 else ctx.sell)(self.lots, stop=stop, target=target, tag=self.name)
