"""Re-run a candidate through the platform's authoritative event engine.

`SpecStrategy` executes a compiled spec inside `sim.engine.Engine`, with the engine's own order handling, risk
sizing from equity, lot rounding and compounding. It is used to prove that the fast kernel matches the engine,
and to confirm finalists, including on M1 execution bars where the path inside each signal bar is observed.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from quantresearch.discovery import spec as S
from quantresearch.discovery.expr import Costs, Evaluator, compile_spec
from quantresearch.sim.costs import OZ_PER_LOT, CostModel
from quantresearch.sim.engine import Context, Engine, EngineConfig, OrderIntent, Strategy


class SpecStrategy(Strategy):
    def __init__(self, spec: dict, compiled, offset: int, risk_pct: float = 0.5, max_lots: float = 50.0):
        s = S.normalize(spec)
        self.name = "discovery"
        self.c, self.off = compiled, offset
        self.risk_pct, self.max_lots = risk_pct, max_lots
        self.p = compiled.params
        self.close_px: np.ndarray | None = None
        self.entry_index: dict[int, int] = {}
        self.order_bar: dict[int, int] = {}
        self.kind = s["entry"]["type"]

    def on_start(self, ctx: Context) -> None:
        self.close_px = ctx._e.signals["close"].to_numpy(float)

    def on_bar(self, ctx: Context) -> None:
        i = ctx.index
        j = i + self.off  # row in the compiled arrays
        a, p = self.c.arrays, self.p
        w = 1 if self.c.want_long[j] else (-1 if self.c.want_short[j] else 0)
        positions = ctx.positions
        closing = False
        for pos in positions:
            started = self.entry_index.setdefault(pos.id, i)
            held = i - started
            reason = None
            if p["max_hold"] > 0 and held >= p["max_hold"]:
                reason = "time"
            elif (a["exit_l"][j] if pos.side > 0 else a["exit_s"][j]):
                reason = "signal_exit"
            elif p["flip"] and w == -pos.side:
                reason = "flip"
            elif p["use_day_end"] and a["day_end"][j]:
                reason = "day_end"
            if reason:
                ctx.close(pos, reason)
                closing = True
                continue
            stop = pos.stop
            if not np.isnan(a["trail"][j]):
                cand = self.close_px[i] - pos.side * a["trail"][j]
                if (pos.side > 0 and cand > stop) or (pos.side < 0 and cand < stop):
                    stop = cand
            if not np.isnan(p["be_r"]):
                if pos.side * (self.close_px[i] - pos.entry_price) >= p["be_r"] * abs(pos.entry_price - pos.initial_stop):
                    if (pos.side > 0 and pos.entry_price > stop) or (pos.side < 0 and pos.entry_price < stop):
                        stop = pos.entry_price
            if stop != pos.stop:
                ctx.modify(pos, stop=stop)
        for order in ctx.orders:
            if order.intent.kind != "market" and i - self.order_bar.get(order.id, i) >= p["expiry"]:
                ctx.cancel(order)
        if w == 0 or (positions and not closing) or ctx.orders:
            return
        dist = a["stop_l"][j] if w > 0 else a["stop_s"][j]
        if np.isnan(dist) or dist <= 0:
            return
        tp = a["tgt_l"][j] if w > 0 else a["tgt_s"][j]
        close = self.close_px[i]
        if self.kind == "market":
            ref, kind, price = close, "market", None
        elif self.kind == "stop":
            ref = price = close + w * a["offset"][j]
            kind = "stop"
        else:
            ref = price = close - w * a["offset"][j]
            kind = "limit"
        lots = float(min(self.max_lots, ctx.account.equity * self.risk_pct / 100.0 / (dist * OZ_PER_LOT)))
        order = ctx.submit(OrderIntent(w, lots, kind=kind, price=price, stop=ref - w * dist,
                                       target=None if np.isnan(tp) else ref + w * tp, tag="discovery"))
        if order is not None:
            self.order_bar[order.id] = i


def store_frame(store, lo: int, hi: int) -> pd.DataFrame:
    p = store.prices
    frame = pd.DataFrame({k: np.asarray(p[k][lo:hi], float) for k in ("bid_open", "bid_high", "bid_low", "bid_close", "ask_open", "ask_high",
                                                                       "ask_low", "ask_close", "close")})
    frame.insert(0, "ts", pd.to_datetime(np.asarray(store.open_ns[lo:hi]), utc=True))
    frame["close_ts"] = pd.to_datetime(np.asarray(store.close_ns[lo:hi]), utc=True)
    frame["bid_volume"] = 0.0
    frame["ask_volume"] = 0.0
    return frame


def engine_run(spec: dict, ev: Evaluator, costs: Costs, execution: pd.DataFrame | None = None, risk_pct: float = 0.5,
               swap_per_lot_day: tuple[float, float] = (0.0, 0.0)) -> Engine:
    """Run on signal bars [ev.start, ev.end). `execution` may be finer bars (M1) over the same period."""
    compiled = compile_spec(spec, ev)
    signals = store_frame(ev.store, ev.start, ev.end)
    model = CostModel(extra_spread_usd=costs.extra_spread, slippage_usd=costs.slippage, commission_per_lot=costs.commission_per_lot,
                      delay_seconds=0.0, swap_long_per_lot_day=swap_per_lot_day[0], swap_short_per_lot_day=swap_per_lot_day[1])
    engine = Engine(signals if execution is None else execution, signals,
                    SpecStrategy(spec, compiled, ev.start - ev.lo, risk_pct),
                    EngineConfig(costs=model, log_signal_bars=False, intrabar_entry_target=False))
    engine.run()
    engine.close_all()
    return engine
