"""Catalogue batch 7: the seven near-misses on slower bars (13 strategies), where retail costs matter far less.

Same rule functions and the same parameter values as the originals; only the bar changes, so "20 bars" means 20
days on D1 and 20 weeks on W1. Weekly bars run Saturday 00:00 to Saturday 00:00 UTC (one trading week each), built
from the warehouse's D1 bars. Each is a new variant and is counted in the trial book.
"""
from __future__ import annotations

from quantresearch.strategies.catalogue import batch1_trend as b1
from quantresearch.strategies.catalogue import batch2_reversion as b2
from quantresearch.strategies.catalogue import batch4_macro as b4
from quantresearch.strategies.catalogue.common import entry

B = 7
ATR = "the project's Wilder ATR(14) on the signal timeframe"
ORIGINALS = {e.name: e for e in [*b1.ENTRIES, *b2.ENTRIES, *b4.ENTRIES]}
NAMES = {"d1": "daily", "w1": "weekly"}


def slower(original: str, tf: str):
    o = ORIGINALS[original]
    d = o.defn
    base = original.rsplit("_", 1)[0]
    bar = NAMES[tf]
    return entry(f"{base}_{tf}", f"{d.title.replace(' on H4', '').replace(' on H1', '')} ({bar} bars)", tf, dict(d.defaults), d.prepare,
                 f"{original} unchanged, on {bar} bars. " + d.methodology,
                 family=o.family, batch=B, source=f"Slower version of {original}: {o.source}", logic=o.logic,
                 entry_rule=o.entry.replace("H4 ", "").replace("H1 ", "") + f" (on {bar} bars)", exit_rule=o.exit, stop_rule=f"{o.stop} On {bar} bars.".replace("..", "."),
                 data=[f"XAUUSD {tf.upper()} bars (mid prices)"] + o.data[1:], needs=d.needs, macro=o.macro,
                 limitations=[f"Same parameters as {original}; bar counts now mean {'days' if tf == 'd1' else 'weeks'}."])


ENTRIES = [
    *[slower(n, "d1") for n in ("cat_macd_zero_h4", "cat_chandelier_h4", "cat_bollinger_breakout_h4", "cat_ichimoku_h4",
                                 "cat_ema_cross_h1", "cat_calm_breakout_h1")],
    *[slower(n, "w1") for n in ("cat_macd_zero_h4", "cat_chandelier_h4", "cat_bollinger_breakout_h4", "cat_ichimoku_h4",
                                 "cat_ema_cross_h1", "cat_calm_breakout_h1", "cat_real_yield_trend_d1")],
]
